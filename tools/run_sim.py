"""Local simulation: a signalling server plus two clients, each with a pretend game, no WoW needed.

    python tools/run_sim.py                 two app windows (Edge/Chrome), real microphones
    python tools/run_sim.py --fake-media    Chromium's built-in beep instead of your microphone (no feedback)
    python tools/run_sim.py --demo          also walk the characters in and out of range on a timer
    python tools/run_sim.py --headless      media engines run in hidden browsers; the printed UI URLs open a
                                            normal browser tab as a control panel (a second page is view-only)
    python tools/run_sim.py --hub ws://127.0.0.1:8787/ws     use a hub that is already running (for example
                                            `npx wrangler dev` in eqvoice-signalling-cf) instead of starting one

Two characters, Alice and Bobby, start 100 yards apart (far too far: no voice). Walk them with the Simulator
panel in either window, or from another terminal:

    python tools/sim_world.py <home>/world.json near Alice Bobby 20         within talking distance
    python tools/sim_world.py <home>/world.json pair Alice Bobby 18 19      Alice sees Bobby at 18, Bobby sees Alice at 19
    python tools/sim_world.py <home>/world.json see Alice Bobby 60          one side disagrees: the link ends
    python tools/sim_world.py <home>/world.json move Bobby 100 0            walk away

Two clients on one PC share one microphone and one pair of speakers: wear headphones, or use --fake-media.
Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client"))
from eqvoice_client.bridge.sim import SimWorld  # noqa: E402

# (seconds after start, Bobby's distance east of Alice): approach, talk, part, repeat
DEMO = [(0, 100), (8, 60), (16, 35), (26, 12), (40, 4), (52, 25), (62, 100), (74, 100)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake-media", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--demo", action="store_true", help="walk Bobby towards Alice and away again, forever")
    ap.add_argument("--home", type=Path, default=None, help="where the two clients keep config and logs (default: a temp folder)")
    ap.add_argument("--hub", default=None, metavar="URL", help="an already running hub (ws:// or wss:// URL); none is started")
    ap.add_argument("--signalling-port", type=int, default=8788)
    ap.add_argument("--ui-port", type=int, default=17441, help="first client's UI port; the second uses +1")
    a = ap.parse_args()

    home = a.home or Path(tempfile.mkdtemp(prefix="eqvoice-sim-"))
    home.mkdir(parents=True, exist_ok=True)
    world_path = home / "world.json"
    world = SimWorld(world_path)
    world.add("Alice", 0, 0).add("Bobby", 100, 0)
    env = dict(os.environ)
    procs = []

    def spawn(args, cwd, name):
        p = subprocess.Popen([sys.executable] + args, cwd=str(cwd), env=env)
        procs.append((name, p))
        return p

    if a.hub:
        sig_url = a.hub
        print("using the hub at %s" % sig_url, flush=True)
    else:
        sig_url = "ws://127.0.0.1:%d/ws" % a.signalling_port
        spawn(["-m", "eqvoice_signalling", "--port", str(a.signalling_port)], ROOT / "eqvoice-signalling", "signalling")
        time.sleep(1.0)
    common = ["-m", "eqvoice_client", "--signalling", sig_url, "--source", "sim", "--sim-world", str(world_path)]
    if a.fake_media:
        common.append("--fake-media")
    for i, name in enumerate(["Alice", "Bobby"]):
        port = a.ui_port + i
        spawn(common + ["--home", str(home / ("client-" + name.lower())), "--port", str(port), "--token", "sim%d" % i,
                        "--sim-character", name] + (["--headless"] if a.headless else []),
              ROOT / "eqvoice-client", "client-" + name.lower())
        print("%s UI: http://127.0.0.1:%d/?token=sim%d" % (name, port, i), flush=True)
    print("world file: %s" % world_path, flush=True)
    print("running; Ctrl+C to stop. logs in", home, flush=True)
    t0 = time.time()
    step = 0
    try:
        while all(p.poll() is None for _, p in procs):
            time.sleep(0.5)
            if a.demo:
                t = (time.time() - t0) % DEMO[-1][0] if DEMO[-1][0] else 0
                want = max((i for i, (when, _) in enumerate(DEMO) if when <= t), default=0)
                if want != step:
                    step = want
                    yards = DEMO[step][1]
                    world.move("Bobby", yards, 0)
                    print("[demo %3.0fs] Bobby is now %d yards from Alice" % (t, yards), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for name, p in procs:
            if p.poll() is None:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
