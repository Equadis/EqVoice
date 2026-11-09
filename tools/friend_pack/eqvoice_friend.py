"""Helper for the EqVoice test pack. Run by the .bat files; Python standard library only (except `run`/`netcheck`,
which use the client package next to it).

    eqvoice_friend.py run         find the game, install the addon, start the EqVoice client
    eqvoice_friend.py netcheck    can this PC reach the test hub and a STUN server? what kind of NAT?
    eqvoice_friend.py logs        zip the logs (no passwords, no addresses) to send back
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def read_hub() -> tuple:
    """(signalling url, join code) from hub.txt: one line, 'URL CODE'."""
    try:
        parts = (HERE / "hub.txt").read_text(encoding="utf-8").split()
    except OSError:
        parts = []
    if not parts:
        raise SystemExit("hub.txt is missing or empty: this pack was not built for a hub. Ask whoever sent it.")
    return parts[0], (parts[1] if len(parts) > 1 else "")


# ----------------------------------------------------------------------------- the game

def running_wow_folder() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "(Get-Process WoW -ErrorAction SilentlyContinue | Select-Object -First 1).Path"],
                             capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(out).parent if out and Path(out).exists() else None


def is_wow_dir(p: Path) -> bool:
    return (p / "WoW.exe").exists()


def find_wow_dir(ask=input) -> Path:
    saved = HERE / "wowdir.txt"
    if saved.exists():
        p = Path(saved.read_text(encoding="utf-8").strip())
        if is_wow_dir(p):
            return p
    guesses = [running_wow_folder()] + [Path(x) for x in (
        r"C:\Program Files (x86)\World of Warcraft", r"C:\Program Files\World of Warcraft", r"C:\OctoWoW", r"D:\OctoWoW",
        r"C:\Games\OctoWoW", r"D:\Games\OctoWoW")]
    for g in guesses:
        if g and is_wow_dir(g):
            say("Found your game at %s" % g)
            saved.write_text(str(g), encoding="utf-8")
            return g
    say("")
    say("I could not find the game. Type the folder that contains WoW.exe (for example D:\\OctoWoW).")
    say("Tip: start WoW first and I will find it myself.")
    while True:
        p = Path(ask("Folder: ").strip().strip('"'))
        if is_wow_dir(p):
            saved.write_text(str(p), encoding="utf-8")
            return p
        say("There is no WoW.exe in %s. Try again." % p)


def install_addon(wow: Path) -> str:
    src = HERE / "addon" / "EqVoice"
    dst = wow / "Interface" / "AddOns" / "EqVoice"
    dst.mkdir(parents=True, exist_ok=True)
    changed = 0
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        target = dst / f.name
        data = f.read_bytes()
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
            changed += 1
    return "addon installed in %s (%d file(s) updated)" % (dst, changed) if changed else "addon already up to date in %s" % dst


# ----------------------------------------------------------------------------- commands

def cmd_run() -> int:
    url, code = read_hub()
    wow = find_wow_dir()
    say(install_addon(wow))
    if not (wow / "ClassicAPI.dll").exists():
        say("WARNING: no ClassicAPI.dll in %s. EqVoice looks other players up by name through it; without it nothing will work." % wow)
        say("         Use the same game client you normally play OctoWoW with.")
    say("")
    say("EqVoice is starting. A small window opens: leave it open while you play.")
    say("  push-to-talk key: F9  (change it in the window)")
    say("  in the game: log in, then type /reload once")
    say("")
    sys.path.insert(0, str(HERE / "client"))
    from eqvoice_client.__main__ import main
    args = ["--home", str(DATA), "--signalling", url, "--wow-dir", str(wow), "--ptt-code", "F9", "--log-level", "INFO"]
    if code:
        args += ["--join-code", code]
    return main(args)


def cmd_netcheck() -> int:
    url, code = read_hub()
    sys.path.insert(0, str(HERE / "client"))
    from eqvoice_client import netcheck
    rc = netcheck.run(url, code, False, DATA / "logs" / "netcheck.txt")
    say("")
    say("(Saved to %s. SEND-LOGS.bat includes it.)" % (DATA / "logs" / "netcheck.txt"))
    return rc


def system_text(wow: Path | None) -> str:
    url = ""
    try:
        url = read_hub()[0]
    except SystemExit:
        pass
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return "\n".join([
        "time: " + time.strftime("%Y-%m-%d %H:%M:%S"), "windows: " + platform.platform(), "python: " + sys.version.split()[0],
        "pack: " + (HERE / "VERSION").read_text(encoding="utf-8").strip() if (HERE / "VERSION").exists() else "pack: ?",
        "hub host: " + host, "wow folder found: %s" % bool(wow), "ClassicAPI.dll: %s" % bool(wow and (wow / "ClassicAPI.dll").exists()),
        "addon installed: %s" % bool(wow and (wow / "Interface" / "AddOns" / "EqVoice" / "EqVoice.toc").exists()), ""])


def cmd_logs() -> int:
    saved = HERE / "wowdir.txt"
    wow = Path(saved.read_text(encoding="utf-8").strip()) if saved.exists() else None
    out_dir = Path.home() / "Desktop"
    if not out_dir.exists():
        out_dir = HERE
    out = out_dir / ("EqVoice-logs-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", system_text(wow))
        for f in sorted((DATA / "logs").glob("*")) if (DATA / "logs").exists() else []:
            if f.is_file():
                z.write(f, "logs/" + f.name)
        # the addon's own notes (saved when the game logs out or reloads)
        if wow and (wow / "WTF" / "Account").exists():
            for f in (wow / "WTF" / "Account").glob("*/SavedVariables/EqVoice.lua"):
                z.write(f, "addon/" + f.parent.parent.name + "-EqVoice.lua")
    say("Logs saved: %s" % out)
    say("Send that one file back. It holds character names and the kind of network you have (private/public), nothing else.")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(out)])
    return 0


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["run"])[0]
    return {"run": cmd_run, "netcheck": cmd_netcheck, "logs": cmd_logs}.get(cmd, cmd_run)()


if __name__ == "__main__":
    sys.exit(main())
