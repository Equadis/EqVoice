"""Measure the cost of a crowd: N real media engines (headless Edge, fake microphones) all within a few yards of each other.

    python tools/mesh_bench.py [--sizes 2,3,4,5,6] [--window 20] [--json out.json]

For every N it measures three situations, each over --window seconds after a short settle:

    idle   nobody holds push-to-talk (connections up, nothing but silence flowing)
    one    one person talks
    all    everybody talks at once (the worst case for CPU and uplink: each engine encodes its microphone once per peer)

and reports connection set-up time, the CPU and memory of a media engine (the browser and all its helper processes),
the audio bitrates in and out of a client, and this Python process's CPU (all N clients run inside it, so divide by N).
Everything runs on one PC, so the numbers are what a single player's PC costs *plus* the other players' engines sharing it:
read the per-engine columns, not totals. Nothing here goes to the internet.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client" / "tests"))
sys.path.insert(0, str(ROOT / "eqvoice-client"))
sys.path.insert(0, str(ROOT / "eqvoice-signalling"))

from scene import Scene, until  # noqa: E402
from eqvoice_client.resources import ResourceMeter  # noqa: E402

NAMES = ["Ann", "Bobby", "Cyrus", "Dina", "Edda", "Fenn", "Gwen", "Hugh"]
CFG = {"ice_servers": [], "audio": {"mode": "ptt", "processing": False},
       "proximity": {"fade_in_ms": 200, "fade_out_ms": 300, "teardown_s": 3.0, "connect_timeout_s": 40.0, "retry_backoff_s": [2], "forget_s": 10}}


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


async def measure(apps: dict, window: float, py_meter: ResourceMeter) -> dict:
    """Read every engine and the mesh totals at the start and every 5 s for ``window`` seconds."""
    for a in apps.values():
        a.read_resources()
    py_meter.read("py", [os.getpid()])
    cpu, mem, procs, kin, kout = [], [], [], [], []
    t_end = time.time() + window
    while time.time() < t_end:
        await asyncio.sleep(min(5.0, max(0.1, t_end - time.time())))
        for a in apps.values():
            r = a.read_resources()
            cpu.append(r["engine"]["cpu_pct"])
            mem.append(r["engine"]["private_mb"])
            procs.append(r["engine"]["procs"])
            kin.append(r["mesh"]["in_kbps"])
            kout.append(r["mesh"]["out_kbps"])
    py = py_meter.read("py", [os.getpid()])
    return {"engine_cpu_pct": avg(cpu), "engine_cpu_pct_max": max([c for c in cpu if c is not None], default=None),
            "engine_mb": avg(mem), "engine_procs": avg(procs), "in_kbps": avg(kin), "out_kbps": avg(kout), "python_cpu_pct": py["cpu_pct"],
            "python_mb": py["private_mb"]}


async def run_size(n: int, window: float, tmp: Path) -> dict:
    names = NAMES[:n]
    out = {"n": n}
    async with Scene(tmp / ("n%d" % n), ice_servers=[]) as sc:
        apps = {}
        for i, name in enumerate(names):
            apps[name] = await sc.add(name, at=(3000.0 * (i + 1), 0.0), real_browser=True, cfg=CFG)
        await until(lambda: all(any(p.media for p in a.pages) and a.mic.get("state") == "ok" for a in apps.values()), 180, "engines")
        for i, name in enumerate(names):
            sc.world.move(name, 2.0 * i, 0.0)
        t0 = time.time()
        await until(lambda: all(a.peers.aggregate()["connected"] == n - 1 for a in apps.values()), 180, "mesh of %d" % n)
        out["mesh_s"] = round(time.time() - t0, 1)
        setups = [a.peers.aggregate() for a in apps.values()]
        out["setup_ms_avg"] = round(avg([s["setup_ms_avg"] for s in setups]))
        out["setup_ms_max"] = max(s["setup_ms_max"] for s in setups)
        py_meter = ResourceMeter()
        for label, talkers in (("idle", []), ("one", names[:1]), ("all", names)):
            for name, a in apps.items():
                a.ptt.virtual = name in talkers
                a.ptt.sample()
            await asyncio.sleep(4.0)
            out[label] = await measure(apps, window, py_meter)
            print("  N=%d %-4s engine cpu %5.1f%% (max %5.1f%%)  %5.0f MB  in %5.1f kbps  out %5.1f kbps  python %s%%" % (
                n, label, out[label]["engine_cpu_pct"] or 0, out[label]["engine_cpu_pct_max"] or 0, out[label]["engine_mb"] or 0,
                out[label]["in_kbps"] or 0, out[label]["out_kbps"] or 0, out[label]["python_cpu_pct"]), flush=True)
    return out


def table(results: list) -> str:
    rows = ["| N | links | mesh up (s) | set-up avg / max (ms) | situation | engine CPU avg / max (% of one core) | engine memory (MB, private) | audio in / out per client (kbit/s) | python CPU for all N (%) |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        n = r["n"]
        for i, label in enumerate(("idle", "one", "all")):
            w = r[label]
            rows.append("| %s | %s | %s | %s | %s | %.0f / %.0f | %.0f | %.1f / %.1f | %s |" % (
                n if i == 0 else "", n * (n - 1) // 2 if i == 0 else "", r["mesh_s"] if i == 0 else "",
                ("%d / %d" % (r["setup_ms_avg"], r["setup_ms_max"])) if i == 0 else "", label, w["engine_cpu_pct"] or 0,
                w["engine_cpu_pct_max"] or 0, w["engine_mb"] or 0, w["in_kbps"] or 0, w["out_kbps"] or 0, w["python_cpu_pct"]))
    return "\n".join(rows)


async def main_async(a) -> int:
    tmp = Path(a.tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    results = []
    for n in [int(x) for x in a.sizes.split(",")]:
        print("== %d engines ==" % n, flush=True)
        results.append(await asyncio.wait_for(run_size(n, a.window, tmp), 900))
    print()
    print(table(results))
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="2,3,4,5,6")
    ap.add_argument("--window", type=float, default=20.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--tmp", default=r"D:\Octowow\.eco-work\eqvoice-tmp\mesh-bench")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
