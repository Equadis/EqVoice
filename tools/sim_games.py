"""Pretend game clients that write the real addon files into a folder, for rehearsing tools/realtest.py without WoW.

    python tools/sim_games.py --dir D:\\some\\fake-game-folder --world world.json Alice Bobby

Each character behaves like the addon: it writes CustomData\\eqvoice.txt and eqvoice-<Character>.txt (frame plus
trace lines), reads eqvoice-in-<Character>.txt (the roster the desktop client writes), looks the names up in the
world file and reports distances. Edit the world with tools/sim_world.py. Ctrl+C to stop.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client"))
from eqvoice_client.bridge.sim import SimGame, SimWorld  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("characters", nargs="+")
    ap.add_argument("--dir", required=True, help="the pretend game folder (CustomData is created inside)")
    ap.add_argument("--world", required=True, help="world file shared with tools/sim_world.py")
    ap.add_argument("--realm", default="Testrealm")
    ap.add_argument("--interval", type=float, default=0.5)
    a = ap.parse_args(argv)
    world = SimWorld(a.world)
    for i, name in enumerate(a.characters):
        if name not in world.chars:
            world.add(name, 400.0 * i, 0.0)
    games = [SimGame(world, name, a.realm, a.dir, login_id=0x7000 + i, interval=a.interval, char_files=True)
             for i, name in enumerate(a.characters)]
    print("pretend games writing to %s\\CustomData: %s" % (a.dir, ", ".join(a.characters)), flush=True)
    try:
        while True:
            for g in games:
                g.step()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        for g in games:
            g.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
