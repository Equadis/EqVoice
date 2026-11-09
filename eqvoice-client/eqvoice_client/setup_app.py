"""The one-click installer, and the uninstaller.

``EqVoice-Test-Setup-<version>.exe`` is a program that carries EqVoice.exe and the addon inside it and does, with one question and one message at the end:

  1. finds the OctoWoW folder (a running game, the registry, the usual places; asks only if none of them has it)
  2. checks that Edge or Chrome is there (the voice engine is a browser kept out of sight; Windows 10 and 11 have Edge) and that the game starts
     with the two mods EqVoice cannot work without (nampower.dll and ClassicAPI.dll)
  3. stops a running EqVoice (an upgrade), copies the program into %LOCALAPPDATA%\\Programs\\EqVoice, the addon into the game, and switches the
     addon on for every character
  4. registers it: Settings > Apps (so it can be uninstalled), a Start menu folder, and start with Windows (so that "start WoW" is all anyone does)
  5. starts it, and says in one message where it lives (the system tray) and what to expect in the game

Nothing is asked about paths or settings. Settings live in the game. An upgrade over an earlier test build keeps the player's settings and logs.

``EqVoice.exe --uninstall`` (what Settings > Apps runs) takes it all away again: the program, the addon, the bridge files in the game's
CustomData, the registry entries and shortcuts; it asks whether to keep the settings and logs.

The functions take every path and registry location as an argument so that the tests can run the whole thing in a scratch folder and a scratch registry
key without touching the real ones. The setup program itself has no switches a player needs; ``EQVOICE_SETUP_*`` environment variables do the same for
the exe's own test (silent: no message boxes; a given game folder; no launch).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import __version__, browser, installer

log = logging.getLogger("eqvoice.setup")

TITLE = "EqVoice Test Build"
BUILD_LABEL = "EqVoice Test Build %s" % __version__
APP_KEY = "EqVoice"
EXE_NAME = "EqVoice.exe"
MANIFEST = "install.json"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
RUN_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Run"
MODS = ("nampower.dll", "ClassicAPI.dll")                  # without these the game gives EqVoice neither files nor name lookups


class SetupError(Exception):
    pass


# ------------------------------------------------------------------------------------------------- where things go

def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "EqVoice"


def default_data_dir() -> Path:
    from .config import default_home
    return Path(default_home())


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "EqVoice"


def missing_mods(wow: Path) -> List[str]:
    """The mods the game must start with that are missing from its folder, or are there but not listed in dlls.txt (so never loaded)."""
    wow = Path(wow)
    listed = ""
    try:
        listed = (wow / "dlls.txt").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        pass
    out = []
    for dll in MODS:
        if not (wow / dll).exists():
            out.append("%s (not in the game folder)" % dll)
        elif listed and dll.lower() not in listed:
            out.append("%s (in the folder, but dlls.txt does not load it)" % dll)
    return out


def mods_text(wow: Path, missing: List[str]) -> str:
    return ("Your game folder does not look ready for EqVoice:\n\n  %s\n\n  %s\n\n"
            "EqVoice needs the OctoWoW client to start with these (the OctoWoW launcher does that). Without them nobody near you can be found. "
            "Setup will go on; start the game the way you normally play OctoWoW." % (wow, "\n  ".join(missing)))


# ------------------------------------------------------------------------------------------------- the manifest

@dataclass
class Manifest:
    version: str
    install_dir: str
    wow_dir: str = ""
    addon_dir: str = ""
    start_menu: str = ""
    data_dir: str = ""
    time: float = 0.0

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Optional["Manifest"]:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        except (OSError, ValueError, TypeError, KeyError):
            return None


# ------------------------------------------------------------------------------------------------- windows pieces

def _winreg():
    import winreg
    return winreg


def write_registration(manifest: Manifest, uninstall_root: str = UNINSTALL_ROOT, run_root: str = RUN_ROOT, autostart: bool = True) -> None:
    """Settings > Apps entry, and start with Windows (both current user: no administrator needed)."""
    w = _winreg()
    exe = str(Path(manifest.install_dir) / EXE_NAME)
    with w.CreateKeyEx(w.HKEY_CURRENT_USER, uninstall_root + "\\" + APP_KEY, 0, w.KEY_WRITE) as k:
        for name, value in (("DisplayName", "EqVoice (Test Build)"), ("DisplayVersion", manifest.version), ("Publisher", "EqVoice"),
                            ("InstallLocation", manifest.install_dir), ("DisplayIcon", exe),
                            ("UninstallString", '"%s" --uninstall' % exe), ("QuietUninstallString", '"%s" --uninstall --quiet' % exe)):
            w.SetValueEx(k, name, 0, w.REG_SZ, value)
        w.SetValueEx(k, "NoModify", 0, w.REG_DWORD, 1)
        w.SetValueEx(k, "NoRepair", 0, w.REG_DWORD, 1)
        try:
            size_kb = int(Path(exe).stat().st_size / 1024)
        except OSError:
            size_kb = 0
        w.SetValueEx(k, "EstimatedSize", 0, w.REG_DWORD, size_kb)
    if autostart:
        with w.CreateKeyEx(w.HKEY_CURRENT_USER, run_root, 0, w.KEY_WRITE) as k:
            w.SetValueEx(k, APP_KEY, 0, w.REG_SZ, '"%s"' % exe)


def remove_registration(uninstall_root: str = UNINSTALL_ROOT, run_root: str = RUN_ROOT) -> None:
    w = _winreg()
    try:
        w.DeleteKey(w.HKEY_CURRENT_USER, uninstall_root + "\\" + APP_KEY)
    except OSError:
        pass
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, run_root, 0, w.KEY_SET_VALUE) as k:
            w.DeleteValue(k, APP_KEY)
    except OSError:
        pass


def registered_manifest_dir(uninstall_root: str = UNINSTALL_ROOT) -> Optional[Path]:
    """Where an earlier install says it lives, or None."""
    w = _winreg()
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, uninstall_root + "\\" + APP_KEY) as k:
            return Path(w.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def make_shortcut(link: Path, target: Path, args: str = "", description: str = "") -> bool:
    """A .lnk through Windows' own scripting object (no extra package)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    esc = lambda s: str(s).replace("'", "''")                                           # noqa: E731
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath = '%s'; $s.Arguments = '%s'; "
              "$s.WorkingDirectory = '%s'; $s.Description = '%s'; $s.IconLocation = '%s,0'; $s.Save()"
              % (esc(link), esc(target), esc(args), esc(Path(target).parent), esc(description), esc(target)))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            log.warning("shortcut %s failed: %s", link.name, (r.stderr or b"").decode("utf-8", "replace")[:300])
        return r.returncode == 0 and link.exists()
    except (OSError, subprocess.TimeoutExpired):
        return False


# ------------------------------------------------------------------------------------------------- processes

def stop_running(install_dir: Optional[Path] = None, wait_s: float = 8.0, keep_pid: Optional[int] = None) -> int:
    """End every running EqVoice.exe (and its hidden browser): an upgrade or an uninstall cannot replace a program that is running.
    Returns how many were running. With ``install_dir`` only the programs started from that folder are touched."""
    if sys.platform != "win32":
        return 0
    script = "Get-CimInstance Win32_Process -Filter \"Name='%s'\" | ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }" % EXE_NAME
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    pids = []
    for line in out.splitlines():
        pid, _, path = line.strip().partition("|")
        if not pid.isdigit() or int(pid) in (os.getpid(), os.getppid(), keep_pid):          # (a one-file exe is two processes: this one and the one that started it)
            continue
        if install_dir is not None and path and Path(path).resolve().parent != Path(install_dir).resolve():
            continue
        pids.append(int(pid))
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    end = time.time() + wait_s
    while pids and time.time() < end:
        alive = subprocess.run(["tasklist", "/FI", "PID eq %d" % pids[0], "/NH"], capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if str(pids[0]) not in alive:
            break
        time.sleep(0.3)
    return len(pids)


def _copy_with_retry(src: Path, dst: Path, seconds: float = 8.0) -> None:
    end = time.time() + seconds
    while True:
        try:
            tmp = dst.with_name(dst.name + ".new")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
            return
        except PermissionError:
            if time.time() > end:
                raise
            time.sleep(0.4)


# ------------------------------------------------------------------------------------------------- install

@dataclass
class InstallReport:
    manifest: Manifest
    upgraded: bool = False
    addon: str = ""
    enabled: str = ""
    warnings: List[str] = field(default_factory=list)
    shortcuts: bool = False


def install(payload: Path, install_dir: Path, wow: Path, *, addon_src: Optional[Path] = None, hub_txt: Optional[Path] = None,
            uninstall_root: str = UNINSTALL_ROOT, run_root: str = RUN_ROOT, menu_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
            autostart: bool = True, registry: bool = True, stop: Callable[[Path], int] = lambda d: stop_running(d)) -> InstallReport:
    """Put EqVoice in place. ``payload`` is the EqVoice.exe to install. Raises SetupError (with words a player can read) if it cannot."""
    install_dir, wow = Path(install_dir), Path(wow)
    if not Path(payload).exists():
        raise SetupError("The setup file is damaged: EqVoice.exe is missing from it. Download it again.")
    previous = Manifest.load(install_dir / MANIFEST)
    rep = InstallReport(Manifest(__version__, str(install_dir), wow_dir=str(wow), data_dir=str(data_dir or default_data_dir()),
                                 start_menu=str(menu_dir) if menu_dir else "", time=time.time()), upgraded=previous is not None or (install_dir / EXE_NAME).exists())
    stop(install_dir)                                                                          # (the addon can be copied into an open game: /reload loads it)
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        _copy_with_retry(Path(payload), install_dir / EXE_NAME)
        if hub_txt is not None and Path(hub_txt).exists():
            shutil.copyfile(hub_txt, install_dir / "hub.txt")
        (install_dir / "VERSION").write_text("%s\n" % BUILD_LABEL, encoding="utf-8")
    except OSError as e:
        raise SetupError("EqVoice could not be copied to %s (%s). Close EqVoice if it is running, and try again." % (install_dir, e)) from e
    try:
        res = installer.install_addon(wow, addon_src)
    except installer.InstallError as e:
        raise SetupError(str(e)) from e
    rep.addon = res.words()
    rep.manifest.addon_dir = str(res.folder)
    rep.enabled = installer.enable_addon(wow).words()
    missing = missing_mods(wow)
    if missing:
        rep.warnings.append(mods_text(wow, missing))
    if not browser.find_browser(None):
        rep.warnings.append("EqVoice needs Microsoft Edge or Google Chrome to carry the voice, and neither was found. Windows 10 and 11 come with Edge: "
                            "run Windows Update, or install Edge, and EqVoice will start working by itself.")
    # the game's folder is the one thing the program would otherwise have to ask for again
    try:
        cfgdir = Path(data_dir or default_data_dir())
        cfgdir.mkdir(parents=True, exist_ok=True)
        cfgfile = cfgdir / "config.json"
        cfg = json.loads(cfgfile.read_text(encoding="utf-8")) if cfgfile.exists() else {}
        cfg.setdefault("bridge", {})["wow_dir"] = str(wow)
        cfgfile.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError, TypeError) as e:
        log.warning("could not save the game folder in the settings: %s", e)
    rep.manifest.save(install_dir / MANIFEST)
    if registry:
        write_registration(rep.manifest, uninstall_root, run_root, autostart)
    if menu_dir is not None:
        exe = install_dir / EXE_NAME
        ok1 = make_shortcut(Path(menu_dir) / "EqVoice.lnk", exe, "", "EqVoice voice chat (runs in the system tray)")
        ok2 = make_shortcut(Path(menu_dir) / "Uninstall EqVoice.lnk", exe, "--uninstall", "Remove EqVoice")
        rep.shortcuts = ok1 and ok2
    return rep


def final_text(rep: InstallReport) -> str:
    head = "EqVoice Test Build %s is %s." % (rep.manifest.version, "updated" if rep.upgraded else "installed")
    body = ("\n\nIt runs in the system tray (the little icons by the clock; Windows may keep a new one under the ^ arrow: drag it out to keep it in view) "
            "and starts with Windows. There is nothing to open.\n\nNow start WoW. A short setup appears in the game the first time: your microphone, "
            "your speakers and the key you press to talk. Afterwards, click the microphone beside your name, or type /eqvoice, to change anything.")
    warn = "".join("\n\n" + w for w in rep.warnings)
    tail = "\n\nTo remove it: Settings > Apps > EqVoice."
    return head + body + warn + tail


# ------------------------------------------------------------------------------------------------- uninstall

def uninstall(install_dir: Path, *, keep_data: bool = True, uninstall_root: str = UNINSTALL_ROOT, run_root: str = RUN_ROOT,
              menu_dir: Optional[Path] = None, data_dir: Optional[Path] = None, stop: Callable[[Path], int] = lambda d: stop_running(d),
              delete_install_dir: bool = True) -> List[str]:
    """Take EqVoice away. Returns what was done, as lines. The install folder is removed last and, if it holds the running uninstaller, after
    this program has ended (``delete_install_dir`` False leaves that to the caller)."""
    install_dir = Path(install_dir)
    done: List[str] = []
    m = Manifest.load(install_dir / MANIFEST)
    stop(install_dir)
    remove_registration(uninstall_root, run_root)
    done.append("registry entries removed")
    menu = Path(menu_dir) if menu_dir is not None else (Path(m.start_menu) if m and m.start_menu else None)
    if menu is not None and menu.exists():
        shutil.rmtree(menu, ignore_errors=True)
        done.append("Start menu folder removed")
    if m and m.addon_dir and Path(m.addon_dir).exists():
        shutil.rmtree(m.addon_dir, ignore_errors=True)
        done.append("addon removed from the game: %s" % m.addon_dir)
    if m and m.wow_dir:
        custom = Path(m.wow_dir) / "CustomData"
        n = 0
        if custom.is_dir():
            for f in custom.glob("eqvoice*.txt"):
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
        if n:
            done.append("%d bridge file(s) removed from the game's CustomData" % n)
    data = Path(data_dir) if data_dir is not None else (Path(m.data_dir) if m and m.data_dir else default_data_dir())
    if not keep_data and data.exists():
        shutil.rmtree(data, ignore_errors=True)
        done.append("settings and logs removed")
    if delete_install_dir and install_dir.exists():
        shutil.rmtree(install_dir, ignore_errors=True)
        if install_dir.exists():
            _delete_later(install_dir)
            done.append("program folder will be removed in a moment")
        else:
            done.append("program removed")
    return done


def _delete_later(path: Path) -> None:
    """A running program cannot delete its own folder: a small command waits for it to end and does it."""
    cmd = 'for /l %%i in (1,1,20) do (ping -n 2 127.0.0.1 >nul & rmdir /s /q "%s" 2>nul & if not exist "%s" exit)' % (path, path)
    try:
        subprocess.Popen("cmd /d /c " + cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), cwd=os.environ.get("TEMP") or None,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        log.exception("could not schedule the removal of %s", path)


# ------------------------------------------------------------------------------------------------- the programs

def _silent() -> bool:
    return bool(os.environ.get("EQVOICE_SETUP_SILENT"))


def _say(text: str, kind: str = "info") -> None:
    if _silent():
        log.info("%s", text)
    else:
        installer.message(TITLE, text, kind)


def bundled(name: str) -> Optional[Path]:
    """A file that setup carries inside itself (PyInstaller's folder), or beside the script in a source run."""
    base = installer.bundle_dir()
    if base is not None and (Path(base) / name).exists():
        return Path(base) / name
    here = Path(__file__).resolve().parent.parent.parent / "dist" / name
    return here if here.exists() else None


def run_setup(argv: Optional[list] = None) -> int:
    """What a double-click on EqVoice-Test-Setup does."""
    argv = list(argv or [])
    root = Path(os.environ.get("EQVOICE_SETUP_ROOT") or default_install_dir())
    payload = bundled("payload/EqVoice.exe") or bundled("EqVoice.exe")
    hub = bundled("payload/hub.txt") or (bundled("hub.txt"))
    if payload is None:
        _say("This setup file is damaged (EqVoice.exe is missing from it). Please download it again.", "error")
        return 1
    existing = (root / EXE_NAME).exists()
    if not _silent() and not installer.ask_yes_no(TITLE, "%s\n\n%s EqVoice, proximity voice chat for OctoWoW?\n\nIt adds a small addon to your game and a program that "
                                                          "runs in the system tray. It needs nothing else from you." % (BUILD_LABEL, "Update" if existing else "Install")):
        return 0
    wow_env = os.environ.get("EQVOICE_SETUP_WOW")
    saved = ""
    try:
        saved = json.loads((default_data_dir() / "config.json").read_text(encoding="utf-8")).get("bridge", {}).get("wow_dir", "")
    except (OSError, ValueError, AttributeError):
        pass
    if wow_env:
        wow = Path(wow_env) if installer.is_wow_dir(Path(wow_env)) else None
    else:
        wow = installer.find_wow_dir(saved, ask=lambda: installer.pick_folder("Setup could not find your game. Choose the folder that contains WoW.exe"))
    if wow is None:
        _say("EqVoice needs your game folder (the one with WoW.exe), so nothing was installed. Run setup again whenever you are ready.", "warn")
        return 1
    registry = not os.environ.get("EQVOICE_SETUP_NO_REGISTRY")
    reg_base = os.environ.get("EQVOICE_SETUP_REGBASE")                               # tests: a scratch key instead of the real ones
    try:
        rep = install(payload, root, wow, hub_txt=hub, addon_src=bundled("addon/EqVoice"), registry=registry,
                      uninstall_root=(reg_base + r"\Uninstall") if reg_base else UNINSTALL_ROOT, run_root=(reg_base + r"\Run") if reg_base else RUN_ROOT,
                      menu_dir=None if os.environ.get("EQVOICE_SETUP_NO_SHORTCUTS") else start_menu_dir(),
                      data_dir=Path(os.environ["EQVOICE_SETUP_DATA"]) if os.environ.get("EQVOICE_SETUP_DATA") else None)
    except installer.InstallError as e:
        _say(str(e), "error")
        return 1
    except SetupError as e:
        if "permission" in str(e).lower() and not installer.is_admin() and not _silent():
            if installer.ask_yes_no(TITLE, "%s\n\nContinue as administrator?" % e) and installer.relaunch_as_admin():
                return 0
        _say(str(e), "error")
        return 1
    log.info("%s", final_text(rep))
    if not os.environ.get("EQVOICE_SETUP_NO_LAUNCH"):
        try:
            subprocess.Popen([str(root / EXE_NAME)], cwd=str(root), close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except OSError:
            log.exception("could not start the installed program")
    _say(final_text(rep))
    return 0


def run_uninstall(argv: Optional[list] = None) -> int:
    """What Settings > Apps (``EqVoice.exe --uninstall``) runs."""
    argv = list(argv or [])
    quiet = "--quiet" in argv or _silent()
    me = Path(sys.executable).resolve().parent if installer.frozen() else None
    root = Path(os.environ.get("EQVOICE_SETUP_ROOT") or me or default_install_dir())
    reg_base = os.environ.get("EQVOICE_SETUP_REGBASE")
    if not quiet and not installer.ask_yes_no(TITLE, "Remove %s from this PC?\n\nThe addon is taken out of your game and the program stops." % BUILD_LABEL):
        return 0
    keep = True
    if not quiet:
        keep = installer.ask_yes_no(TITLE, "Keep your EqVoice settings and logs?\n\nChoose No to delete them too.")
    done = uninstall(root, keep_data=keep, uninstall_root=(reg_base + r"\Uninstall") if reg_base else UNINSTALL_ROOT,
                     run_root=(reg_base + r"\Run") if reg_base else RUN_ROOT,
                     data_dir=Path(os.environ["EQVOICE_SETUP_DATA"]) if os.environ.get("EQVOICE_SETUP_DATA") else None)
    log.info("uninstalled: %s", "; ".join(done))
    if not quiet:
        installer.message(TITLE, "EqVoice has been removed.", "info")
    return 0
