"""What does a big roster cost? Hub, desktop client and bridge, at 50 / 100 / 250 / 500 / 1000 opted-in players on one realm.

    python tools/scale_bench.py [--sizes 50,100,250,500,1000] [--json out.json]

Measured here (no game needed):
  * hub        time to register N sessions, and what a newcomer receives (bytes, messages) to learn the roster
  * file       size of the roster file the client hands to the game, and how long writing it takes
  * desktop    time of one client tick (reading frames, matching readings to the roster, proximity reports, state) with that roster
  * bridge     size and decode time of the game's report frame with up to 200 nearby players
  * addon      LuaJIT timings of the same work the addon does with that roster (parse the file, one scan, build a frame): relative
               cost only, the game's Lua 5.0 is several times slower, and the real cost of the lookups themselves can only be
               measured in the game: /ev diag scale

The scheduling itself (how fast a stranger is found, lookups per second) is pinned by eqvoice-addon/tests/run.lua.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client" / "tests"))
sys.path.insert(0, str(ROOT / "eqvoice-client"))
sys.path.insert(0, str(ROOT / "eqvoice-signalling"))

from scene import REALM, PhantomConn, Scene, phantoms, until  # noqa: E402
from eqvoice_client.app import auto_checks  # noqa: E402
from eqvoice_client.bridge import frame as fr  # noqa: E402
from eqvoice_client.bridge.inbox import InboxWriter  # noqa: E402
from eqvoice_signalling.hub import Hub  # noqa: E402


def ms(x: float) -> float:
    return round(x * 1000, 2)


def hub_side(n: int) -> dict:
    hub = Hub()
    t0 = time.perf_counter()
    phantoms(hub, n)
    reg = time.perf_counter() - t0
    # a newcomer: how much does it receive to learn the whole roster?
    import json as j
    import secrets
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from eqvoice_signalling.hub import b64u, registration_message, unb64u
    c = PhantomConn("10.9.9.9")
    hub.connect(c)
    hello = c.out.pop(0)
    key = Ed25519PrivateKey.generate()
    sid = b64u(secrets.token_bytes(16))
    pub = b64u(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    sig = b64u(key.sign(registration_message(unb64u(hello["nonce"]), sid, REALM, "Newcomer")))
    t1 = time.perf_counter()
    hub.handle(c, j.dumps({"t": "register", "v": 2, "sid": sid, "realm": REALM, "character": "Newcomer", "pub": pub, "sig": sig}))
    for _ in range(50):                                     # the rest of a big roster arrives in follow-up chunks on later ticks
        if not any(m.get("t") == "registered" and m.get("more") for m in c.out[-1:]):
            break
        hub.tick()
    first = time.perf_counter() - t1
    msgs = [m for m in c.out]
    size = sum(len(j.dumps(m, separators=(",", ":"))) for m in msgs)
    names = sum(len(m.get("roster", m.get("add", []))) for m in msgs)
    return {"register_all_ms": ms(reg), "register_each_us": round(reg / n * 1e6, 1), "newcomer_first_reply_ms": ms(first),
            "newcomer_bytes": size, "newcomer_messages": len(msgs), "newcomer_names": names}


def file_side(n: int, tmp: Path) -> dict:
    names = ["Pl%s" % "".join(chr(97 + (i // 26 ** k) % 26) for k in range(4)) for i in range(n)]
    w = InboxWriter(tmp / ("eqvoice-in-%d.txt" % n), refresh_s=0.0)
    cfg = {"radius": 40, "interval": 0.5, "checks": auto_checks(n, 0.5), "report": 60}
    times = []
    for i in range(30):
        t0 = time.perf_counter()
        w.write("Me", names if i % 2 else names[::-1], cfg)         # alternate order so every write really writes
        times.append(time.perf_counter() - t0)
    return {"file_bytes": (tmp / ("eqvoice-in-%d.txt" % n)).stat().st_size, "file_write_ms": ms(statistics.median(times)),
            "checks_per_scan": cfg["checks"], "lookups_per_second": round(cfg["checks"] / 0.5), "sweep_s": round(n / cfg["checks"] * 0.5, 2)}


def frame_side() -> dict:
    results = [fr.BridgeResult("Pl%04d" % i, min(250, 10 + i), 1) for i in range(200)] if hasattr(fr, "BridgeResult") else []
    st = fr.make_state(character="Me", realm="Testrealm", login_id=1, wall=1700000000, results=results)
    t0 = time.perf_counter()
    for _ in range(200):
        data = fr.encode_state(st)
    enc = (time.perf_counter() - t0) / 200
    t0 = time.perf_counter()
    for _ in range(200):
        fr.decode_bytes(data)
    dec = (time.perf_counter() - t0) / 200
    return {"frame_bytes_200_results": len(data), "frame_encode_ms": ms(enc), "frame_decode_ms": ms(dec)}


async def desktop_side(n: int, tmp: Path) -> dict:
    async with Scene(tmp / ("scene%d" % n)) as sc:
        phantoms(sc.hub, n)
        a = await sc.add("Alice", at=(0, 0), page=False)
        await until(lambda: len(a.roster) >= n, 120, "the roster of %d reaches the client (has %d)" % (n, len(a.roster)))
        for i in range(10):                                           # ten people standing around her
            sc.world.add("Near%d" % i, 5.0 + i, 0.0)
        await asyncio.sleep(1.0)
        ticks = []
        for _ in range(40):
            t0 = time.perf_counter()
            a._tick()
            ticks.append(time.perf_counter() - t0)
            await asyncio.sleep(0.01)
        st = a.build_state()
        t0 = time.perf_counter()
        for _ in range(20):
            a.build_state()
        build = (time.perf_counter() - t0) / 20
        return {"roster_in_client": len(a.roster), "tick_ms_median": ms(statistics.median(ticks)), "tick_ms_max": ms(max(ticks)),
                "ui_state_ms": ms(build), "ui_state_bytes": len(json.dumps(st))}


def addon_side(sizes: list) -> dict:
    """LuaJIT timings from tests/scale_sample.lua (indicative only)."""
    out = {}
    luajit = "luajit"
    try:
        p = subprocess.run([luajit, str(ROOT / "eqvoice-addon" / "tests" / "scale_sample.lua")] + [str(s) for s in sizes],
                           capture_output=True, text=True, timeout=300, cwd=str(ROOT / "eqvoice-addon"))
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) == 6 and parts[0].isdigit():
            out[int(parts[0])] = {"lua_parse_ms": float(parts[1]), "lua_scan_ms": float(parts[2]), "lua_frame_ms": float(parts[3]),
                                  "lua_poll_ms": float(parts[4]), "lua_beat_ms": float(parts[5])}
    return out


def table(rows: list) -> str:
    cols = [("N", "n"), ("hub: register all (ms)", "register_all_ms"), ("newcomer gets (KB / msgs)", None), ("roster file (KB)", None),
            ("file write (ms)", "file_write_ms"), ("lookups/s (checks per scan)", None), ("full sweep (s)", "sweep_s"),
            ("client tick (ms, median / max)", None), ("UI state (KB)", None), ("addon (LuaJIT ms): new roster / heartbeat / scan / frame", None)]
    head = "| " + " | ".join(c[0] for c in cols) + " |\n|" + "---|" * len(cols)
    lines = [head]
    for r in rows:
        lines.append("| %d | %.1f | %.1f / %d | %.1f | %.2f | %d (%d) | %.2f | %.2f / %.2f | %.1f | %s |" % (
            r["n"], r["register_all_ms"], r["newcomer_bytes"] / 1024, r["newcomer_messages"], r["file_bytes"] / 1024, r["file_write_ms"],
            r["lookups_per_second"], r["checks_per_scan"], r["sweep_s"], r["tick_ms_median"], r["tick_ms_max"], r["ui_state_bytes"] / 1024,
            ("%.2f / %.3f / %.3f / %.2f" % (r["lua_poll_ms"], r["lua_beat_ms"], r["lua_scan_ms"], r["lua_frame_ms"])) if "lua_poll_ms" in r else "-"))
    return "\n".join(lines)


async def main_async(a) -> int:
    sizes = [int(x) for x in a.sizes.split(",")]
    tmp = Path(tempfile.mkdtemp(prefix="ov-scale-", dir=a.tmp))
    lua = addon_side(sizes)
    rows = []
    for n in sizes:
        row = {"n": n}
        row.update(hub_side(n))
        row.update(file_side(n, tmp))
        row.update(await desktop_side(n, tmp))
        row.update(lua.get(n, {}))
        rows.append(row)
        print("N=%d done: tick %.2f ms, file %.1f KB, newcomer %.1f KB" % (n, row["tick_ms_median"], row["file_bytes"] / 1024,
                                                                          row["newcomer_bytes"] / 1024), flush=True)
    fs = frame_side()
    print()
    print(table(rows))
    print()
    print("game report frame with 200 nearby players: %(frame_bytes_200_results)d bytes, encode %(frame_encode_ms).2f ms, decode %(frame_decode_ms).2f ms" % fs)
    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows, "frame": fs}, indent=2), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="50,100,250,500,1000")
    ap.add_argument("--json", default=None)
    ap.add_argument("--tmp", default=r"D:\Octowow\.eco-work\eqvoice-tmp")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
