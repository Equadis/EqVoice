"""Build dist/EqVoice.exe: one file, no Python needed on the player's PC, double-click to set up and run.

    python tools/build_exe.py                                  an exe that reads the server address from hub.txt beside it
    python tools/build_exe.py --hub-url wss://voice.example/ws --join-code abc    the address is built in (a release)
    python tools/build_exe.py --smoke                          build, then start it and check it serves its window

What goes inside: the client (Python and aiohttp), the window's files, the addon. What does not: Edge or Chrome (Windows has Edge)
and ClassicAPI (it comes with the OctoWoW client). PyInstaller does the packing; build files go to build/ (ignored by git).

Windows SmartScreen will say "unknown publisher" on a first run: the exe is not code-signed (that costs money). docs/11 says what
players should click. Sending the exe inside a zip avoids the blocks some mail and chat programs put on bare .exe files.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "eqvoice-client"
SIGNALLING = ROOT / "eqvoice-signalling"            # its hub goes inside the exe too: a test setup hosts its own server (launcher.LocalHub)
ADDON = ROOT / "eqvoice-addon" / "EqVoice"
ICON = CLIENT / "eqvoice_client" / "ui" / "eqvoice.ico"       # the program's icon (tools/make_app_icon.py makes it from tools/art/EqVc.png)
EXCLUDES = ["tkinter", "pytest", "numpy", "PIL", "pandas", "scipy", "matplotlib", "IPython", "setuptools", "pip", "wheel", "unittest",
            "pydoc", "doctest", "lib2to3", "distutils"]

VERSION_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a}, {b}, {c}, 0), prodvers=({a}, {b}, {c}, 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'EqVoice'),
      StringStruct('FileDescription', 'EqVoice - proximity voice chat for OctoWoW'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'EqVoice'),
      StringStruct('OriginalFilename', 'EqVoice.exe'),
      StringStruct('ProductName', 'EqVoice'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def client_version() -> str:
    sys.path.insert(0, str(CLIENT))
    from eqvoice_client import __version__
    return __version__


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(out: Path, hub_url: str = "", join_code: str = "", name: str = "EqVoice", clean: bool = True, entry: Path = None,
          extra_data: tuple = (), window_data: bool = True) -> Path:
    """``entry``: the script PyInstaller starts from (eqvoice_gui.py, the program; eqvoice_setup.py, the installer). ``extra_data``: (source, folder inside) pairs.
    ``window_data`` False leaves the window's files and the voice hub out (the installer has no window)."""
    work = ROOT / "build" / "pyi"
    stage = ROOT / "build" / "exe-staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    work.mkdir(parents=True, exist_ok=True)
    v = client_version()
    parts = (v.split(".") + ["0", "0", "0"])[:3]
    (stage / "version.txt").write_text(VERSION_TEMPLATE.format(a=parts[0], b=parts[1], c=parts[2], v=v), encoding="utf-8")
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--noconsole", "--name", name,
           "--distpath", str(out), "--workpath", str(work), "--specpath", str(work), "--paths", str(CLIENT),
           "--paths", str(SIGNALLING), "--collect-submodules", "eqvoice_signalling",
           "--version-file", str(stage / "version.txt"), "--collect-submodules", "eqvoice_client"]
    if ICON.exists():
        cmd += ["--icon", str(ICON)]
    else:
        print("WARNING: %s is missing: the exe will have no icon (python tools/make_app_icon.py makes it)" % ICON)
    if window_data:
        cmd += ["--add-data", "%s%seqvoice_client/ui" % (CLIENT / "eqvoice_client" / "ui", sep)]
    cmd += ["--add-data", "%s%saddon/EqVoice" % (ADDON, sep)]
    for src, inside in extra_data:
        cmd += ["--add-data", "%s%s%s" % (src, sep, inside)]
    if hub_url:
        (stage / "hub.txt").write_text((hub_url + " " + join_code).strip() + "\n", encoding="utf-8")
        cmd += ["--add-data", "%s%s." % (stage / "hub.txt", sep)]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    if clean:
        cmd.append("--clean")
    cmd.append(str(entry or (CLIENT / "eqvoice_gui.py")))
    env = dict(os.environ)
    tmp = os.environ.get("EQVOICE_TMP")
    if tmp:
        Path(tmp).mkdir(parents=True, exist_ok=True)
        env["TEMP"] = env["TMP"] = tmp
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stdout + r.stderr)[-4000:])
        raise SystemExit("PyInstaller failed (see above)")
    exe = Path(out) / (name + (".exe" if sys.platform == "win32" else ""))
    if not exe.exists():
        raise SystemExit("PyInstaller finished but %s is missing" % exe)
    print("built %s  %.1f MB  in %.0f s" % (exe, exe.stat().st_size / 1e6, time.time() - t0))
    print("sha256 %s" % sha256(exe))
    return exe


def main() -> int:
    ap = argparse.ArgumentParser(description="Build EqVoice.exe")
    ap.add_argument("--hub-url", default="", help="build the voice server address in (otherwise the exe reads hub.txt beside itself)")
    ap.add_argument("--join-code", default="")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--name", default="EqVoice")
    ap.add_argument("--no-clean", action="store_true", help="reuse PyInstaller's cache (faster rebuilds)")
    ap.add_argument("--smoke", action="store_true", help="start the exe afterwards and check it serves its window")
    a = ap.parse_args()
    exe = build(a.out, a.hub_url, a.join_code, a.name, clean=not a.no_clean)
    if a.smoke:
        sys.path.insert(0, str(ROOT / "tools"))
        from smoke_exe import smoke
        problems = smoke(exe)
        for p in problems:
            print("SMOKE FAIL:", p)
        if problems:
            return 1
        print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
