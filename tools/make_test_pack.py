"""Build the zip a friend needs for a two-PC voice test. The hub address and join code are baked in, so there is nothing to type.

    python tools/make_test_pack.py --hub-url wss://example.trycloudflare.com/ws --join-code abc123
    python tools/make_test_pack.py                     (hub URL defaults to ws://127.0.0.1:8787/ws: only useful on this PC)

Output: dist/EqVoice-test-pack.zip. By default it holds dist/EqVoice.exe (built by tools/build_exe.py; rebuilt here when the
sources are newer), a hub.txt with the address, and a short README: the friend unzips and double-clicks. The friend needs Windows
(which has Edge) and the same OctoWoW client you use (it provides ClassicAPI.dll); no Python.

--python-pack makes the older pack instead (Python scripts and .bat files; the friend needs Python 3.10+).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK_SRC = ROOT / "tools" / "friend_pack"
sys.path.insert(0, str(ROOT / "eqvoice-client"))

SETUP_BAT = r"""@echo off
rem Creates a private Python environment next to this file the first time. Called by the other .bat files.
cd /d "%~dp0"
if exist venv\Scripts\python.exe exit /b 0
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto nopy
echo First run: setting EqVoice up. This takes about a minute and needs internet.
%PY% -m venv venv
if errorlevel 1 goto fail
venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r client\requirements.txt
if errorlevel 1 goto fail
exit /b 0
:nopy
echo Python is not installed, or is not on PATH.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then double-click this file again.
pause
exit /b 1
:fail
echo.
echo Setup failed. Are you online? Send a screenshot of this window.
rmdir /s /q venv >nul 2>nul
pause
exit /b 1
"""

START_BAT = r"""@echo off
title EqVoice test
cd /d "%~dp0"
call _setup.bat
if errorlevel 1 exit /b 1
set PYTHONPATH=%~dp0client
venv\Scripts\python.exe eqvoice_friend.py run
echo.
echo EqVoice stopped.
pause
"""

NETCHECK_BAT = r"""@echo off
title EqVoice network check
cd /d "%~dp0"
call _setup.bat
if errorlevel 1 exit /b 1
set PYTHONPATH=%~dp0client
venv\Scripts\python.exe eqvoice_friend.py netcheck
echo.
pause
"""

LOGS_BAT = r"""@echo off
title EqVoice logs
cd /d "%~dp0"
if exist venv\Scripts\python.exe (set PY=venv\Scripts\python.exe) else (set PY=python)
%PY% eqvoice_friend.py logs
echo.
pause
"""

README = """EqVoice voice test  -  about 10 minutes
==========================================

You need: Windows, Edge or Chrome, headphones + a microphone, Python 3.10 or newer (python.org; tick "Add python.exe to PATH"),
and the same OctoWoW game client you normally play with.

1. Unzip this folder anywhere (not in Program Files).

2. Double-click  NETCHECK.bat .  It should end with "ready for a voice test".
   If it does not, send me a screenshot (or run SEND-LOGS.bat).

3. Double-click  START-EqVoice.bat .  The first time it sets itself up (a minute) and installs the EqVoice addon into your game
   folder (it finds the folder itself if WoW is running, otherwise it asks for the folder with WoW.exe).
   A small EqVoice window opens. LEAVE IT OPEN. Choose your microphone and speakers in it. Push-to-talk key: F9.

4. Start WoW, log in, and type  /reload  once. The window should now show your character and "signalling: registered".

5. Walk up to my character (I will tell you where; stay within ~10 yards). Hold F9 and say something. I will answer.
   We should hear each other clearly. Try both ways.

6. Walk away from me in a straight line. My voice should fade out by about 40 yards (a shout carries to about 85). Walk back: it returns.

7. When you are done, or if anything failed: close the EqVoice window, double-click  SEND-LOGS.bat ,
   and send me the zip it makes on your Desktop.

Nothing is installed outside this folder and the addon folder. To remove everything: delete this folder and
<your game folder>\\Interface\\AddOns\\EqVoice.
"""


def build_python_pack(hub_url: str, join_code: str, out_dir: Path) -> Path:
    from eqvoice_client import __version__
    pack = out_dir / "EqVoice-test-pack"
    if pack.exists():
        shutil.rmtree(pack)
    (pack / "addon").mkdir(parents=True)
    shutil.copytree(ROOT / "eqvoice-addon" / "EqVoice", pack / "addon" / "EqVoice")
    shutil.copytree(ROOT / "eqvoice-client" / "eqvoice_client", pack / "client" / "eqvoice_client",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (pack / "client" / "requirements.txt").write_text("aiohttp>=3.9\ncryptography>=42\n", encoding="utf-8")
    shutil.copy(PACK_SRC / "eqvoice_friend.py", pack / "eqvoice_friend.py")
    for name, body in (("_setup.bat", SETUP_BAT), ("START-EqVoice.bat", START_BAT), ("NETCHECK.bat", NETCHECK_BAT),
                       ("SEND-LOGS.bat", LOGS_BAT)):
        (pack / name).write_bytes(body.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
    (pack / "README.txt").write_bytes(README.replace("\n", "\r\n").encode("ascii", "replace"))
    (pack / "hub.txt").write_text((hub_url + " " + join_code).strip() + "\n", encoding="utf-8")
    (pack / "VERSION").write_text("eqvoice %s, built %s\n" % (__version__, time.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    zpath = out_dir / "EqVoice-test-pack.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pack.rglob("*")):
            if f.is_file():
                z.write(f, "EqVoice-test-pack/" + f.relative_to(pack).as_posix())
    return zpath


EXE_README = """EqVoice  -  proximity voice chat for OctoWoW
=============================================

1. Unzip this folder anywhere (right-click the zip, Extract All). Do not run EqVoice.exe from inside the zip.

2. Double-click  EqVoice.exe .
   Windows may say "Windows protected your PC". Click "More info", then "Run anyway": EqVoice is not code-signed yet.
   It finds your game by itself, adds its addon to it and puts its icon in the system tray (by the clock; Windows may keep a new
   icon under the little ^ arrow: drag it out to keep it in view). There is no taskbar button. Double-click the icon for the
   EqVoice window; closing that window does not turn voice off. To quit, right-click the icon and choose Quit EqVoice.

3. Start WoW and log in. If you are already in, type  /reload  once.
   If /eqvc says nothing, EqVoice is switched off for that character (the game does that for characters made before
   EqVoice was installed). Close WoW and double-click EqVoice.exe again and it switches itself on for all of them,
   or: at the character screen click AddOns (bottom left), tick EqVoice, click Okay.

4. A small microphone button appears just right of the row of little menu buttons at the bottom of the screen (character,
   spellbook and so on); if you have hidden that row it is at the top of the screen instead. Green means your microphone is
   live, red means muted, grey means EqVoice is not running. Click it, or type  /eqvc , to open the voice panel.
   Your microphone is open from the start. Right-click the button to mute. Hold Shift and drag to move it.
   A little speaker also appears beside the names of other EqVoice players on the target, party and player frames and on
   nameplates: click one on a unit frame to open the panel.

5. Press "Set up mic" in the panel once. It asks you to stay silent, talk normally and then shout (about 17 seconds) and sets
   the noise gate, which keeps your room, keyboard clicks and mouse clicks off the line, to suit your voice.

6. Walk up to someone else who uses EqVoice and talk. A voice is at full volume to 30 yards and fades to nothing at 85; right
   next to you it is up to twice as loud, easing back to normal by 10 yards (the Boost slider). Shouting carries further.
   Click a name in the panel to mute that player; the Blocklist button lists everyone you have blocked.

Something wrong?  In the EqVoice window, scroll down to Diagnostics: "Check my connection" tests your network, and
"Save report" puts a zip of logs on your Desktop to send to whoever is helping you.

You need the OctoWoW game client you normally play with: it must start with nampower.dll and ClassicAPI.dll loaded (they are
listed in dlls.txt in the game folder). If typing  /ev diag api  in the game says "file api: nil" or "UNAVAILABLE (needs
ClassicAPI)", your game was started without them, and EqVoice cannot work until it is started the way the OctoWoW launcher does.
To remove EqVoice: delete this folder and <your game folder>\\Interface\\AddOns\\EqVoice.
"""

DEFAULT_EXE = ROOT / "dist" / "EqVoice.exe"


def newest_source() -> float:
    """Modification time of the newest file that goes into the exe."""
    best = 0.0
    for root in (ROOT / "eqvoice-client" / "eqvoice_client", ROOT / "eqvoice-addon" / "EqVoice", ROOT / "eqvoice-signalling" / "eqvoice_signalling"):
        for f in root.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                best = max(best, f.stat().st_mtime)
    return max(best, (ROOT / "eqvoice-client" / "eqvoice_gui.py").stat().st_mtime)


def exe_is_stale(exe: Path) -> bool:
    return not exe.exists() or exe.stat().st_mtime < newest_source()


def build_exe_pack(hub_url: str, join_code: str, out_dir: Path, exe: Path = DEFAULT_EXE, rebuild: bool = True) -> Path:
    """EqVoice.exe + hub.txt + README.txt in a zip. The exe is rebuilt first when any of its sources is newer than it."""
    from eqvoice_client import __version__
    exe = Path(exe)
    if rebuild and exe_is_stale(exe):
        import build_exe
        print("EqVoice.exe is %s: building it (about half a minute)..." % ("missing" if not exe.exists() else "older than the sources"), flush=True)
        exe = build_exe.build(exe.parent, clean=False)
    if not exe.exists():
        raise SystemExit("%s does not exist: run python tools/build_exe.py first (or use --python-pack)" % exe)
    zpath = Path(out_dir) / "EqVoice-test-pack.zip"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, "EqVoice/EqVoice.exe")
        z.writestr("EqVoice/hub.txt", (hub_url + " " + join_code).strip() + "\n")
        z.writestr("EqVoice/README.txt", EXE_README.replace("\n", "\r\n"))
        z.writestr("EqVoice/VERSION", "eqvoice %s, built %s\n" % (__version__, time.strftime("%Y-%m-%d %H:%M")))
    return zpath


def build(hub_url: str, join_code: str, out_dir: Path, legacy: bool = False, exe: Path = DEFAULT_EXE) -> Path:
    """The pack for a friend: the exe pack, or (legacy=True) the Python one."""
    if legacy:
        return build_python_pack(hub_url, join_code, Path(out_dir))
    return build_exe_pack(hub_url, join_code, Path(out_dir), exe)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hub-url", default="ws://127.0.0.1:8787/ws")
    ap.add_argument("--join-code", default="")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--exe", default=str(DEFAULT_EXE), help="the EqVoice.exe to pack (default dist/EqVoice.exe)")
    ap.add_argument("--python-pack", action="store_true", help="the older pack of Python scripts and .bat files")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    z = build(a.hub_url, a.join_code, out, legacy=a.python_pack, exe=Path(a.exe))
    print("built %s (%.1f MB)" % (z, z.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
