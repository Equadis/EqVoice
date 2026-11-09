"""Edit a simulated game world from the command line (the file the --sim-world clients watch).

    python tools/sim_world.py WORLD.json show
    python tools/sim_world.py WORLD.json add Alice 0 0         put a character in the world (x y in yards)
    python tools/sim_world.py WORLD.json move Bobby 25 0       walk: 25 yards east of the origin
    python tools/sim_world.py WORLD.json near Alice Bobby 18   put Bobby 18 yards east of Alice
    python tools/sim_world.py WORLD.json see Alice Bobby 18    what ALICE's game reports for Bobby (overrides the map)
    python tools/sim_world.py WORLD.json pair Alice Bobby 18 19   Alice sees Bobby at 18, Bobby sees Alice at 19
    python tools/sim_world.py WORLD.json unsee Alice Bobby     drop the override again
    python tools/sim_world.py WORLD.json remove Bobby

Distances beyond 86 yards are "not loaded" for the observer, exactly like the real game's by-name lookup.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client"))
from eqvoice_client.bridge.sim import SimWorld  # noqa: E402


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    w = SimWorld(argv[0])
    w.refresh()
    cmd, args = argv[1], argv[2:]
    try:
        if cmd == "show":
            for n, (x, y) in sorted(w.chars.items()):
                print("%-12s at (%.1f, %.1f)" % (n, x, y))
            for (o, t), d in sorted(w.views.items()):
                print("%s sees %s at %.1f yd (forced)" % (o, t, d))
            for o in sorted(w.chars):
                for t in sorted(w.chars):
                    d = w.distance(o, t)
                    if o != t and d is not None:
                        print("  %s -> %s: %.1f yd" % (o, t, d))
        elif cmd == "add":
            w.add(args[0], float(args[1]) if len(args) > 1 else 0.0, float(args[2]) if len(args) > 2 else 0.0)
        elif cmd == "move":
            w.move(args[0], float(args[1]), float(args[2]) if len(args) > 2 else 0.0)
        elif cmd == "near":
            ax, ay = w.chars[args[0]]
            w.move(args[1], ax + float(args[2]), ay)
        elif cmd == "see":
            w.see(args[0], args[1], float(args[2]))
        elif cmd == "unsee":
            w.see(args[0], args[1], None)
        elif cmd == "pair":
            w.pair(args[0], args[1], float(args[2]), float(args[3]))
        elif cmd == "remove":
            w.remove(args[0])
        else:
            print(__doc__)
            return 2
    except (IndexError, ValueError, KeyError) as e:
        print("bad arguments (%s: %s)\n" % (type(e).__name__, e))
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
