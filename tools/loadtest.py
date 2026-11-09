"""Load the signalling server with N simulated clients on one realm and report what it cost.

    python tools/loadtest.py --clients 300 --seconds 20

Every client registers as a character (so the roster grows to N and every join is broadcast to everyone),
then clusters of 6 report each other as nearby (so mutual-proximity links form), keep changing their
distances and trading signalling messages a little faster than real clients do (a real WebRTC attempt is
about 15 messages per pair; this sends 2 per second per client for the whole run). Prints server CPU
seconds, peak memory, messages per second, what the roster itself cost, and signal delivery latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-signalling"))
from eqvoice_signalling.hub import b64u, registration_message, unb64u  # noqa: E402

REALM = "Loadrealm"


def name_for(i: int) -> str:
    """Letters only, 5 characters: the hub's character-name rule."""
    out, n = [], i
    for _ in range(3):
        out.append(chr(97 + n % 26))
        n //= 26
    return "Pl" + "".join(reversed(out))


def proc_stats(pid: int) -> dict:
    if sys.platform == "win32":
        ps = ("$p=Get-Process -Id %d; [pscustomobject]@{cpu=$p.CPU; ws=$p.WorkingSet64; peak=$p.PeakWorkingSet64}"
              " | ConvertTo-Json -Compress" % pid)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
        return json.loads(out)
    stat = Path("/proc/%d/stat" % pid).read_text().split()
    clk = os.sysconf("SC_CLK_TCK")
    rss = int(Path("/proc/%d/statm" % pid).read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return {"cpu": (int(stat[13]) + int(stat[14])) / clk, "ws": rss, "peak": rss}


class Stats:
    def __init__(self):
        self.sent = self.recv = self.errors = 0
        self.roster_msgs = self.roster_bytes = 0
        self.links_up = 0
        self.lat = []
        self.error_codes = {}


async def client(i: int, cluster: list, url: str, seconds: float, st: Stats, ready: asyncio.Event, go: asyncio.Event):
    me, sid = name_for(i), b64u(os.urandom(16))
    key = Ed25519PrivateKey.generate()
    pub = b64u(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    rnd = random.Random(i)
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url, heartbeat=25)
        hello = json.loads((await ws.receive()).data)
        nonce = unb64u(hello["nonce"])
        await ws.send_json({"t": "register", "v": 2, "sid": sid, "realm": REALM, "character": me, "pub": pub,
                            "sig": b64u(key.sign(registration_message(nonce, sid, REALM, me)))})
        ack = json.loads((await ws.receive()).data)
        assert ack["t"] == "registered", ack
        ready.set()
        await go.wait()
        sids = {}                                  # name -> sid, as the roster tells us
        linked = set()

        async def reader():
            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(m.data)
                st.recv += 1
                t = d["t"]
                if t == "roster_update":
                    st.roster_msgs += 1
                    st.roster_bytes += len(m.data)
                    for name, s_ in d.get("add", []):
                        sids[name] = s_
                elif t == "peer_available":
                    linked.add(d["sid"])
                    st.links_up += 1
                elif t == "peer_out_of_range":
                    linked.discard(d["sid"])
                elif t in ("offer", "answer", "ice"):
                    st.lat.append(time.perf_counter() - d["data"]["t"])
                elif t == "error":
                    st.errors += 1
                    st.error_codes[d.get("code")] = st.error_codes.get(d.get("code"), 0) + 1
        rt = asyncio.create_task(reader())
        for n, s_ in ack.get("roster", []):
            sids[n] = s_
        others = [name_for(j) for j in cluster if j != i]
        base = {o: 8.0 + rnd.random() * 20 for o in others}

        async def report(jitter: float):
            peers = [[o, round(max(1.0, base[o] + rnd.uniform(-jitter, jitter)), 1), int(time.time())] for o in others]
            await ws.send_json({"t": "proximity", "peers": peers})
            st.sent += 1
        await report(0)
        end = time.time() + seconds
        last_prox = last_ping = time.time()
        while time.time() < end:
            await asyncio.sleep(0.5 + rnd.random() * 0.1)
            now = time.time()
            if linked:
                await ws.send_json({"t": "ice", "to": rnd.choice(sorted(linked)), "ep": 1,
                                    "data": {"t": time.perf_counter(), "c": "candidate:" + "x" * 550}})
                st.sent += 1
            if now - last_prox > 1.0:                     # walking around: new distances every second
                last_prox = now
                await report(3.0)
            if now - last_ping > 10:
                last_ping = now
                await ws.send_json({"t": "ping", "ts": now})
                st.sent += 1
        rt.cancel()
        await ws.close()


async def main_async(a) -> None:
    port = a.port
    srv = subprocess.Popen([sys.executable, "-m", "eqvoice_signalling", "--port", str(port), "--max-conns-per-ip", "100000",
                            "--registers-per-minute", "100000", "--log-level", "WARNING"],
                           cwd=str(ROOT / "eqvoice-signalling"))
    try:
        await asyncio.sleep(1.5)
        url = "ws://127.0.0.1:%d/ws" % port
        st = Stats()
        go = asyncio.Event()
        tasks, readies = [], []
        t_join = time.time()
        for i in range(a.clients):
            c0 = (i // 6) * 6
            cluster = list(range(c0, min(c0 + 6, a.clients)))
            ev = asyncio.Event()
            readies.append(ev)
            tasks.append(asyncio.create_task(client(i, cluster, url, a.seconds, st, ev, go)))
            if i % 50 == 49:
                await asyncio.sleep(0.2)
        await asyncio.wait_for(asyncio.gather(*(r.wait() for r in readies)), 120)
        join_s = time.time() - t_join
        idle = proc_stats(srv.pid)
        t0 = time.time()
        go.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        dt = time.time() - t0
        after = proc_stats(srv.pid)
        lat = sorted(st.lat)
        print("clients                 %d on one realm (%d clusters of up to 6)" % (a.clients, -(-a.clients // 6)))
        print("join storm              %.1f s for %d registrations, each broadcast to everyone" % (join_s, a.clients))
        print("run time                %.1f s" % dt)
        print("server memory           %.1f MB idle with all connected, %.1f MB peak" % (idle["ws"] / 1e6, after["peak"] / 1e6))
        print("server cpu              %.2f s over the run = %.1f%% of one core" % (after["cpu"] - idle["cpu"], 100 * (after["cpu"] - idle["cpu"]) / dt))
        print("messages client->server %d (%.0f/s)" % (st.sent, st.sent / dt))
        print("messages server->client %d (%.0f/s)" % (st.recv, st.recv / dt))
        print("roster traffic          %d messages, %.1f kB in total, %.0f bytes per client per join" % (
            st.roster_msgs, st.roster_bytes / 1000.0, st.roster_bytes / max(1, a.clients)))
        print("links formed            %d (clusters of 6 -> 5 links per client)" % st.links_up)
        if lat:
            print("signal delivery latency median %.1f ms, p99 %.1f ms, max %.1f ms (n=%d)" % (
                statistics.median(lat) * 1000, lat[int(len(lat) * 0.99) - 1] * 1000, lat[-1] * 1000, len(lat)))
        print("errors                  %d %s" % (st.errors, st.error_codes or ""))
    finally:
        srv.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=300)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--port", type=int, default=8799)
    asyncio.run(main_async(ap.parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
