"""Build the public test package: dist/EqVoice-Test-Setup-<version>.exe, the one-click installer.

    python tools/build_installer.py --hub-url wss://voice.example/ws [--join-code CODE]

What it does: builds EqVoice.exe (the program, with the voice server's address built in), then builds the setup program with that exe and the addon
inside it. A player downloads the one file, double-clicks it, answers one question, and has EqVoice: see eqvoice_client/setup_app.py for what it does.

The voice server's address is required. A test build that does not know where the hub is cannot connect anybody, and nothing here deploys one
(eqvoice-signalling-cf/DEPLOY.md does, with the project owner's Cloudflare account). ``--allow-no-hub`` builds one anyway, for trying the installer.

The exe is not code-signed, so Windows SmartScreen says "unknown publisher" the first time: More info, then Run anyway. Send the file in a zip, or
from a page the players trust, so that mail and chat programs do not block a bare .exe.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_exe                                   # noqa: E402

ROOT = build_exe.ROOT


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the EqVoice test installer")
    ap.add_argument("--hub-url", default="", help="the voice server's address (wss://.../ws), built into the program")
    ap.add_argument("--join-code", default="")
    ap.add_argument("--no-join-code", action="store_true", help="the hub has no join code (the secret was deleted)")
    ap.add_argument("--allow-no-hub", action="store_true", help="build without a voice server address (the program will say it does not know one)")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--no-clean", action="store_true")
    a = ap.parse_args()
    if a.hub_url and not a.join_code and not a.no_join_code:
        print("The hub asks for a join code and this build has none: every player would be refused. Give --join-code CODE (or --no-join-code if the hub has none).")
        return 2
    if not a.hub_url and not a.allow_no_hub:
        print("A public test build needs the voice server's address: --hub-url wss://.../ws (or --allow-no-hub to build one that cannot connect).")
        return 2
    version = build_exe.client_version()
    payload_dir = ROOT / "build" / "installer-payload"
    if payload_dir.exists():
        shutil.rmtree(payload_dir)
    payload_dir.mkdir(parents=True)
    program = build_exe.build(payload_dir, a.hub_url, a.join_code, "EqVoice", clean=not a.no_clean)
    extra = [(program, "payload")]
    if a.hub_url:                                   # (the program has the address built in; the file beside it is for a later change of server)
        hub = payload_dir / "hub.txt"
        hub.write_text((a.hub_url + " " + a.join_code).strip() + "\n", encoding="utf-8")
        extra.append((hub, "payload"))
    name = "EqVoice-Test-Setup-%s" % version
    setup = build_exe.build(a.out, "", "", name, clean=False, entry=build_exe.CLIENT / "eqvoice_setup.py", extra_data=tuple(extra), window_data=False)
    print("\nthe test installer: %s" % setup)
    print("send this one file (in a zip, if mail or chat blocks bare .exe files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
