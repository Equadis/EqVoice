"""Smoke-test any running hub (Python server, `wrangler dev`, or a deployed Worker) with the real client code.

    python tools/hub_smoke.py ws://127.0.0.1:8787/ws
    python tools/hub_smoke.py wss://something.trycloudflare.com/ws --join-code CODE       (a hub that requires a join code)
    python tools/hub_smoke.py ws://127.0.0.1:8787/ws --idle 25 --no-ping
        sleep 25 s with both clients completely silent (no pings), which lets a Durable Object hibernate and
        proves the roster, sessions and signalling survive being rebuilt

Registers two characters, checks the roster, links them with agreeing proximity reports, relays an offer, ends
the link, and leaves. Prints each step; exits 1 on the first failure. Uses throwaway names and keys.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "eqvoice-client")]
from eqvoice_client import identity, signalling  # noqa: E402
from eqvoice_client.signalling import SignallingClient  # noqa: E402


class End:
    def __init__(self, url, home, name, join_code=""):
        self.events, self.name = [], name
        self.client = SignallingClient(url, join_code, self.events.append)
        self.ident = identity.load_or_create(Path(home) / name, "Smokerealm", name, 1) if (Path(home) / name).mkdir(
            parents=True, exist_ok=True) is None else None
        self.task = None

    def start(self):
        self.client.set_identity(self.ident)
        self.task = asyncio.create_task(self.client.run())

    async def wait(self, pred, timeout=10.0, what=""):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for e in self.events:
                if pred(e):
                    return e
            await asyncio.sleep(0.05)
        raise AssertionError("timed out waiting for %s; last events: %r; status %r" % (what, self.events[-5:], self.client.status()))

    def take(self, t):
        got = [e for e in self.events if e.get("t") == t]
        self.events[:] = [e for e in self.events if e.get("t") != t]
        return got

    async def stop(self):
        await self.client.stop()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except BaseException:                                  # noqa: BLE001
                pass


def step(msg):
    print("  " + msg, flush=True)


async def main(url: str, idle: float, join_code: str = "") -> int:
    home = tempfile.mkdtemp(prefix="ov-smoke-")
    a, b = End(url, home, "Smokea", join_code), End(url, home, "Smokeb", join_code)
    try:
        a.start()
        await a.wait(lambda e: e["t"] == "_registered", 15, "Smokea registered")
        step("Smokea registered (hello, nonce, Ed25519 signature accepted)")
        b.start()
        reg = await b.wait(lambda e: e["t"] == "_registered", 15, "Smokeb registered")
        assert [n for n, _ in reg["roster"]] == ["Smokea"], reg["roster"]
        step("Smokeb registered and received Smokea in its roster")
        up = await a.wait(lambda e: e["t"] == "roster_update" and e.get("add"), 5, "roster_update")
        assert up["add"][0][0] == "Smokeb"
        step("Smokea was told about Smokeb")
        if idle:
            step("idle for %.0f s (lets a Durable Object hibernate) ..." % idle)
            await asyncio.sleep(idle)
        a.client.report_proximity([("Smokeb", 18.0)])
        b.client.report_proximity([("Smokea", 19.0)])
        pa = await a.wait(lambda e: e["t"] == "peer_available", 10, "peer_available (a)")
        await b.wait(lambda e: e["t"] == "peer_available", 10, "peer_available (b)")
        assert pa["distance"] == 18.5, pa
        step("mutual proximity linked the pair at %.1f yd" % pa["distance"])
        assert a.client.send_signal(b.ident.sid, "offer", 7, {"sdp": "x"})
        got = await b.wait(lambda e: e["t"] == "offer", 5, "offer relayed")
        assert got["from"] == a.ident.sid and got["ep"] == 7
        step("an offer was relayed to the linked peer")
        b.client.report_proximity([])
        await a.wait(lambda e: e["t"] == "peer_out_of_range", 10, "peer_out_of_range")
        step("withdrawing one report ended the link (grace respected)")
        await b.stop()
        gone = await a.wait(lambda e: e["t"] == "roster_update" and e.get("remove"), 10, "roster removal")
        assert gone["remove"] == [b.ident.sid]
        step("Smokeb left cleanly and vanished from Smokea's roster")
        print("OK")
        return 0
    except AssertionError as e:
        print("FAIL: %s" % e)
        return 1
    finally:
        await a.stop()
        try:
            await b.stop()
        except Exception:                                          # noqa: BLE001
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--join-code", default="", help="the hub's join code, when it has one")
    ap.add_argument("--idle", type=float, default=0.0, help="seconds to sleep once both are registered")
    ap.add_argument("--no-ping", action="store_true", help="the clients send nothing while idle (so a hibernating hub really sleeps)")
    a = ap.parse_args()
    if a.no_ping:
        signalling.PING_EVERY = 10 ** 9
    sys.exit(asyncio.run(main(a.url, a.idle, a.join_code)))
