"""python -m eqvoice_client   (see --help)"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .app import App
from .config import Config, default_home
from .logbuf import setup_logging


def parse(argv=None):
    p = argparse.ArgumentParser(prog="eqvoice_client", description="EqVoice proximity voice client")
    p.add_argument("--home", type=Path, default=None, help="config/log folder (default: %%APPDATA%%\\EqVoice or $EQVOICE_HOME)")
    p.add_argument("--signalling", default=None, help="signalling server URL, e.g. wss://voice.example.org/ws")
    p.add_argument("--join-code", default=None)
    p.add_argument("--port", type=int, default=None, help="local UI port (0 = any free port)")
    p.add_argument("--token", default=None, help="local UI token (default: random per launch)")
    p.add_argument("--wow-dir", default=None, help="game folder: the bridge files live in its CustomData folder")
    p.add_argument("--character", default=None, metavar="NAME",
                   help="two game instances share one folder: read eqvoice-NAME.txt and write eqvoice-in-NAME.txt for this one")
    p.add_argument("--test-roster", default=None, metavar="NAME,NAME",
                   help="also ask the game to look these characters up (they need not be online here): checks the inbound bridge with one player")
    p.add_argument("--ptt-code", default=None, metavar="CODE",
                   help="push-to-talk key as a KeyboardEvent.code, e.g. F9 or Backquote (two clients on one PC need different keys)")
    p.add_argument("--source", choices=["auto", "optical", "file", "sim"], default=None)
    p.add_argument("--sim-character", default=None, help="simulation: the character this client pretends to be")
    p.add_argument("--sim-realm", default=None, help="simulation: the realm name")
    p.add_argument("--sim-folder", default=None, metavar="DIR", help="simulation: the pretend game folder (share one between clients to rehearse a shared folder)")
    p.add_argument("--sim-world", default=None, metavar="FILE",
                   help="simulation: a JSON world file shared by several simulated clients (see tools/sim_world.py)")
    p.add_argument("--no-browser", action="store_true", help="do not open the UI; print its URL")
    p.add_argument("--headless", action="store_true", help="run the media page in a headless browser (tests)")
    p.add_argument("--fake-media", action="store_true", help="use Chromium's fake microphone (tests, demos)")
    p.add_argument("--mute-audio", action="store_true")
    p.add_argument("--log-level", default=None)
    p.add_argument("--say-radius", type=float, default=None, metavar="YARDS",
                   help="how far voice carries (default 40; measure your server with /ev say, tools/say_calibrate.py)")
    p.add_argument("--full-radius", type=float, default=None, metavar="YARDS",
                   help="a voice is at its full volume up to here (default 30)")
    p.add_argument("--netcheck", action="store_true",
                   help="check the network for a voice test (hub reachable? STUN over UDP? what kind of NAT?) and exit")
    p.add_argument("--show-ips", action="store_true", help="--netcheck and logs: print real addresses (default: only their kind)")
    return p.parse_args(argv)


def overrides(args) -> dict:
    over: dict = {}
    if args.signalling:
        over["signalling_url"] = args.signalling
    if args.join_code is not None:
        over["join_code"] = args.join_code
    if args.port is not None:
        over.setdefault("ui", {})["port"] = args.port
    if args.token:
        over.setdefault("ui", {})["token"] = args.token
    if args.wow_dir:
        over.setdefault("bridge", {})["wow_dir"] = args.wow_dir
    if args.character:
        over.setdefault("bridge", {})["character"] = args.character
    if args.test_roster:
        over.setdefault("roster", {})["test_names"] = [n.strip() for n in args.test_roster.split(",") if n.strip()]
    if args.fake_media:
        over.setdefault("audio", {})["keyboard_filter"] = False       # the fake microphone beeps; the keyboard filter exists to drop short noises like that
    if args.ptt_code:
        over.setdefault("audio", {})["ptt_code"] = args.ptt_code
        over["audio"]["ptt_label"] = args.ptt_code
    sim = {k: v for k, v in (("character", args.sim_character), ("realm", args.sim_realm), ("world", args.sim_world), ("folder", args.sim_folder)) if v}
    if sim:
        over["sim"] = sim
        args.source = args.source or "sim"
    if args.source:
        over.setdefault("bridge", {})["source"] = args.source
    if args.log_level:
        over["log_level"] = args.log_level
    if args.say_radius is not None:
        prox = over.setdefault("proximity", {})
        prox["say_radius"] = float(args.say_radius)
    if args.full_radius is not None:
        over.setdefault("proximity", {})["full_radius"] = float(args.full_radius)
    if args.show_ips:
        over.setdefault("diagnostics", {})["log_ips"] = True
    return over


async def amain(args) -> int:
    home = args.home or default_home()
    cfg = Config.load(home, overrides(args))
    setup_logging(home, cfg.log_level)
    if args.netcheck:
        from . import netcheck
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: netcheck.run(cfg.signalling_url, cfg.join_code, args.show_ips, Path(home) / "logs" / "netcheck.txt"))
    app = App(cfg, headless=args.headless, fake_media=args.fake_media, mute_audio=args.mute_audio,
              launch_browser=not args.no_browser)
    url = await app.start()
    print("EqVoice UI: " + url, flush=True)
    try:
        await app.wait_closed()
    finally:
        await app.stop()
    return 0


def main(argv=None) -> int:
    args = parse(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
