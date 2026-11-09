"""One-click setup for EqVoice.exe: find the game, put the addon in it, remember where, and say plainly what went wrong.

Standard library and ctypes only, so the same code runs inside the packaged exe. Nothing here needs the network.

    find_wow_dir(saved, ask)     the game folder: saved one, a running WoW.exe, the registry, the usual places, then ask
    install_addon(wow)           copy the addon into <game>\\Interface\\AddOns\\EqVoice (only files that differ; stale ones go)
    enable_addon(wow)            switch the addon on for every character (the game lists new addons as "disabled" for existing ones)
    read_hub()                   the voice server's address, from hub.txt beside the program or built into it
    make_report(cfg)             a zip of logs for whoever is helping (no passwords, no network addresses)
    pick_folder(), message(), ask_yes_no()     the few native dialogs setup needs (ctypes: no tkinter, no extra files)

The game folder is the one that holds WoW.exe. OctoWoW is "the game you already play": there is no registry entry that is
certain to exist for it, so several places are tried before the player is asked.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import __version__

log = logging.getLogger("eqvoice.installer")

IS_WINDOWS = sys.platform == "win32"
ADDON_NAME = "EqVoice"
LEGACY_ADDON_NAME = "OctoVoice"                  # what the addon was called before: it must not stay beside the new one (two addons, one /eqvc)
GAME_EXE = "WoW.exe"
NAME_HINTS = ("wow", "warcraft", "octo", "turtle", "vanilla", "classic")
SKIP_HINTS = ("backup", "_old", "-old", ".old", "bak", "temp", "tmp", "trash", "recycle")


class InstallError(Exception):
    """Setup could not finish. ``str(e)`` is written for the player; ``permission`` means "ask for administrator rights"."""

    def __init__(self, message: str, permission: bool = False):
        super().__init__(message)
        self.permission = permission


# --------------------------------------------------------------------------------------- where things are

def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Optional[Path]:
    """The folder PyInstaller unpacked the exe's built-in files into (None when running from source)."""
    return Path(sys._MEIPASS) if frozen() and getattr(sys, "_MEIPASS", None) else None        # type: ignore[attr-defined]


def app_dir() -> Path:
    """The folder the program sits in: hub.txt is looked up here."""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def addon_source() -> Optional[Path]:
    """The addon folder to copy from: inside the exe, or the repository, or a pack folder next to the client."""
    here = Path(__file__).resolve()
    cands = []
    if os.environ.get("EQVOICE_ADDON_SRC"):
        cands.append(Path(os.environ["EQVOICE_ADDON_SRC"]))
    if bundle_dir():
        cands.append(bundle_dir() / "addon" / ADDON_NAME)                                       # type: ignore[operator]
    cands += [here.parent.parent.parent / "eqvoice-addon" / ADDON_NAME,
              here.parent.parent.parent / "addon" / ADDON_NAME]
    for c in cands:
        if (c / (ADDON_NAME + ".toc")).exists():
            return c
    return None


def read_hub(folder: Optional[Path] = None) -> Optional[tuple]:
    """(voice server URL, join code) from ``hub.txt`` beside the program, else the one built into the exe; None when neither.

    hub.txt is one line, ``URL CODE``. The file beside the program wins so a new pack with a new address works without a rebuild.
    """
    spots = [(folder or app_dir()) / "hub.txt"]
    if bundle_dir():
        spots.append(bundle_dir() / "hub.txt")                                                  # type: ignore[operator]
    for p in spots:
        try:
            parts = p.read_text(encoding="utf-8-sig").split()
        except OSError:
            continue
        if parts and parts[0].startswith(("ws://", "wss://")):
            return parts[0], (parts[1] if len(parts) > 1 else "")
    return None


# -------------------------------------------------------------------------------------------- the game

def is_wow_dir(p) -> bool:
    try:
        return p is not None and (Path(p) / GAME_EXE).is_file()
    except OSError:
        return False


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    from . import resources as _res

    _k32 = _res._k32
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.GetLogicalDrives.restype = wintypes.DWORD
    _k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _k32.GetDriveTypeW.restype = wintypes.UINT


def running_wow_dirs() -> list:
    """Folders of every running WoW.exe (the surest answer: the game is right there)."""
    out: list = []
    if not IS_WINDOWS:
        return out
    snap = _k32.CreateToolhelp32Snapshot(_res.TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _res.INVALID_HANDLE:
        return out
    try:
        e = _res.PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(_res.PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == GAME_EXE.lower():
                h = _k32.OpenProcess(_res.PROCESS_QUERY_LIMITED_INFORMATION, False, e.th32ProcessID)
                if h:
                    try:
                        buf = ctypes.create_unicode_buffer(1024)
                        size = wintypes.DWORD(1024)
                        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                            d = Path(buf.value).parent
                            if d not in out:
                                out.append(d)
                    finally:
                        _k32.CloseHandle(h)
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        _k32.CloseHandle(snap)
    return out


def registry_wow_dirs() -> list:
    """Install folders the registry knows for anything called World of Warcraft or OctoWoW."""
    out: list = []
    if not IS_WINDOWS:
        return out
    import winreg
    spots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft", "InstallPath"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\World of Warcraft", "InstallPath"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Blizzard Entertainment\World of Warcraft", "InstallPath")]
    for root, sub, value in spots:
        try:
            with winreg.OpenKey(root, sub) as k:
                out.append(Path(winreg.QueryValueEx(k, value)[0]))
        except OSError:
            pass
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, uninstall), (winreg.HKEY_LOCAL_MACHINE, uninstall.replace("SOFTWARE", r"SOFTWARE\WOW6432Node")),
                      (winreg.HKEY_CURRENT_USER, uninstall)):
        try:
            with winreg.OpenKey(root, sub) as parent:
                for i in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        with winreg.OpenKey(parent, winreg.EnumKey(parent, i)) as k:
                            name = str(winreg.QueryValueEx(k, "DisplayName")[0]).lower()
                            if any(h in name for h in ("warcraft", "octowow", "turtle wow")):
                                out.append(Path(winreg.QueryValueEx(k, "InstallLocation")[0]))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def fixed_drives() -> list:
    if not IS_WINDOWS:
        return []
    mask = _k32.GetLogicalDrives()
    return [Path("%s:\\" % chr(65 + i)) for i in range(26) if mask & (1 << i) and _k32.GetDriveTypeW("%s:\\" % chr(65 + i)) == 3]


def common_wow_dirs(drives: Optional[Iterable] = None, budget_s: float = 6.0) -> list:
    """Folders that hold WoW.exe in the usual places: a drive's top level, Games, Program Files, and any folder there whose name
    sounds like the game. Never searches whole drives; gives up after ``budget_s``."""
    end = time.monotonic() + budget_s
    out: list = []

    def add(p: Path) -> None:
        # updater backups and old copies hold a WoW.exe too, and they are never the one to install into
        if is_wow_dir(p) and p not in out and not any(b in p.name.lower() for b in SKIP_HINTS):
            out.append(p)

    for root in (list(drives) if drives is not None else fixed_drives()):
        for base in (root, root / "Games", root / "Program Files (x86)", root / "Program Files", root / "Blizzard"):
            if time.monotonic() > end:
                return out
            add(base)
            try:
                kids = [c for c in base.iterdir() if c.is_dir() and any(h in c.name.lower() for h in NAME_HINTS)]
            except OSError:
                continue
            for c in kids:
                add(c)
                try:
                    for g in c.iterdir():                       # one level deeper: D:\Games\OctoWoW\Client
                        if g.is_dir() and any(h in g.name.lower() for h in NAME_HINTS + ("client", "game", "bin")):
                            add(g)
                except OSError:
                    pass
    return sorted(out, key=lambda p: len(str(p)))


def find_wow_dir(saved: str = "", ask: Optional[Callable[[], Optional[Path]]] = None, say: Callable[[str], None] = lambda s: None) -> Optional[Path]:
    """The folder with WoW.exe, or None. Order: the one saved last time, a running game, the registry, the usual places, then ``ask()``."""
    if saved and is_wow_dir(Path(saved)):
        return Path(saved)
    for label, finder in (("running game", running_wow_dirs), ("registry", registry_wow_dirs), ("usual places", common_wow_dirs)):
        for p in finder():
            if is_wow_dir(p):
                say("found your game (%s): %s" % (label, p))
                log.info("game folder found via %s: %s", label, p)
                return Path(p)
    while ask is not None:
        p = ask()
        if p is None:
            return None
        if is_wow_dir(p):
            return Path(p)
        say("There is no %s in %s." % (GAME_EXE, p))
    return None


def classicapi_present(wow: Path) -> bool:
    return (Path(wow) / "ClassicAPI.dll").exists()


# ------------------------------------------------------------------------------------------- the addon

@dataclass
class InstallResult:
    folder: Path
    files: int = 0
    changed: int = 0
    removed: int = 0
    legacy: int = 0                               # files of the addon's former name taken away (its folder, its bridge files)

    def words(self) -> str:
        tail = ", the old %s addon removed" % LEGACY_ADDON_NAME if self.legacy else ""
        if not self.changed and not self.removed:
            return "addon already up to date in %s%s" % (self.folder, tail)
        return "addon installed in %s (%d file(s) updated%s%s)" % (self.folder, self.changed, ", %d old one(s) removed" % self.removed if self.removed else "", tail)


def install_addon(wow: Path, src: Optional[Path] = None) -> InstallResult:
    """Copy the addon into the game. Only files that differ are written, so a second run (or a game that is open) is harmless.
    Files from an older version that are no longer part of the addon are removed from EqVoice's own folder."""
    src = Path(src) if src else addon_source()
    if src is None or not (src / (ADDON_NAME + ".toc")).exists():
        raise InstallError("The addon files are missing from this copy of EqVoice. Download it again.")
    dst = Path(wow) / "Interface" / "AddOns" / ADDON_NAME
    res = InstallResult(dst)
    try:
        dst.mkdir(parents=True, exist_ok=True)
        wanted = set()
        for f in sorted(src.iterdir()):
            if not f.is_file():
                continue
            wanted.add(f.name)
            res.files += 1
            data = f.read_bytes()
            target = dst / f.name
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
                res.changed += 1
        for old in dst.iterdir():
            if old.is_file() and old.suffix.lower() in (".lua", ".toc") and old.name not in wanted:
                old.unlink()
                res.removed += 1
    except PermissionError as e:
        raise InstallError("Windows would not let EqVoice write into %s (%s)." % (dst, e), permission=True) from e
    except OSError as e:
        raise InstallError("Could not install the addon into %s: %s" % (dst, e)) from e
    res.legacy = remove_legacy(wow)
    return res


def remove_legacy(wow: Path) -> int:
    """Take away the addon under its former name, and the bridge files it left in CustomData. Only what is certainly ours: the folder
    must hold its own .toc, and the files must be named the way it named them. Never raises: a leftover is harmless, a crash is not."""
    gone = 0
    try:
        old = Path(wow) / "Interface" / "AddOns" / LEGACY_ADDON_NAME
        if (old / (LEGACY_ADDON_NAME + ".toc")).is_file():
            import shutil
            shutil.rmtree(old)
            gone += 1
        data = Path(wow) / "CustomData"
        if data.is_dir():
            for f in data.glob(LEGACY_ADDON_NAME.lower() + "*.txt"):
                if f.is_file():
                    f.unlink()
                    gone += 1
    except OSError as e:
        log.warning("could not remove the old %s files: %s", LEGACY_ADDON_NAME, e)
    return gone


# ---------------------------------------------------------------- switching the addon on for each character
#
# The game keeps one list per character, WTF\Account\<account>\<realm>\<character>\AddOns.txt, with a line "Name: enabled" or
# "Name: disabled" for each addon. A character that existed before EqVoice was installed gets "EqVoice: disabled": the addon
# is on disk but never loads, and /eqvc is simply an unknown command. The game rewrites these files when the player leaves
# a character, so they are only touched while the game is closed.

ADDON_LINE = re.compile(r"^\s*" + re.escape(ADDON_NAME) + r"\s*:\s*(\S+)\s*$", re.IGNORECASE)


def addon_lists(wow: Path) -> list:
    """Every character's AddOns.txt under this game folder."""
    try:
        return sorted(Path(wow).glob("WTF/Account/*/*/*/AddOns.txt"))
    except OSError:
        return []


def character_label(addons_txt: Path) -> str:
    p = Path(addons_txt)
    return "%s (%s)" % (p.parent.name, p.parent.parent.name)


def addon_off_for(wow: Path) -> list:
    """Names ("Nue (N'Zoth)") of the characters whose list says EqVoice is disabled: the addon will not load for them."""
    out = []
    for p in addon_lists(wow):
        try:
            text = p.read_bytes().decode("latin-1")
        except OSError:
            continue
        for line in text.splitlines():
            m = ADDON_LINE.match(line)
            if m and m.group(1).lower() != "enabled":
                out.append(character_label(p))
                break
    return out


def game_is_open(wow: Path) -> bool:
    try:
        here = os.path.normcase(os.path.abspath(str(wow)))
        return any(os.path.normcase(os.path.abspath(str(d))) == here for d in running_wow_dirs())
    except Exception:                                                                               # noqa: BLE001
        return False


@dataclass
class EnableResult:
    turned_on: list                      # characters switched on just now
    still_off: list                      # characters left off: the game is open (it would undo the change) or the file is locked

    def words(self) -> str:
        if not self.turned_on and not self.still_off:
            return "addon already on for every character"
        return "addon switched on for %d character(s)%s" % (len(self.turned_on), (", still off for %s" % ", ".join(self.still_off)) if self.still_off else "")


def enable_addon(wow: Path) -> EnableResult:
    """Make every character's list say "EqVoice: enabled". Only that one line is touched; a character with a list but no EqVoice
    line gets one. While the game is open nothing is written: the characters that need it are returned in ``still_off`` instead."""
    res = EnableResult([], [])
    is_open = game_is_open(wow)
    for p in addon_lists(wow):
        label = character_label(p)
        tmp = p.with_name(p.name + ".eqvoice-tmp")
        try:
            text = p.read_bytes().decode("latin-1")                      # latin-1 keeps every byte of the file exactly as it was
            eol = "\r\n" if "\r\n" in text else ("\n" if "\n" in text else "\r\n")
            lines = text.splitlines(keepends=True)
            found, dirty = False, False
            for i, line in enumerate(lines):
                m = ADDON_LINE.match(line.rstrip("\r\n"))
                if not m:
                    continue
                found = True
                if m.group(1).lower() != "enabled":
                    lines[i] = ADDON_NAME + ": enabled" + (line[len(line.rstrip("\r\n")):] or eol)
                    dirty = True
            if not found:
                if is_open:
                    continue                                              # unlisted: the game's own default applies, nothing to warn about
                if lines and not lines[-1].endswith(("\n", "\r")):
                    lines[-1] += eol
                lines.append(ADDON_NAME + ": enabled" + eol)
                dirty = True
            if not dirty:
                continue
            if is_open:
                res.still_off.append(label)
                continue
            tmp.write_bytes("".join(lines).encode("latin-1"))
            os.replace(tmp, p)
            res.turned_on.append(label)
        except OSError as e:
            log.warning("could not switch the addon on in %s: %s", p, e)
            res.still_off.append(label)
            try:
                tmp.unlink()
            except OSError:
                pass
    return res


# ----------------------------------------------------------------------------------- administrator rights

def is_admin() -> bool:
    try:
        return bool(IS_WINDOWS and ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                                                               # noqa: BLE001
        return False


def relaunch_as_admin(args: Optional[list] = None) -> bool:
    """Start this program again with administrator rights (Windows shows its own permission prompt). True if it was started."""
    if not IS_WINDOWS or not frozen():
        return False
    params = subprocess.list2cmdline(list(args or []))
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1) > 32


# --------------------------------------------------------------------------------------- native dialogs

MB = {"info": 0x40, "warn": 0x30, "error": 0x10}


def message(title: str, text: str, kind: str = "info") -> None:
    """A plain message box. Without Windows (tests, other systems) it is written to the log instead."""
    if not IS_WINDOWS:
        log.info("%s: %s", title, text)
        return
    ctypes.windll.user32.MessageBoxW(0, text, title, MB.get(kind, 0x40))


def ask_yes_no(title: str, text: str) -> bool:
    if not IS_WINDOWS:
        return False
    return ctypes.windll.user32.MessageBoxW(0, text, title, 0x4 | MB["warn"]) == 6


def pick_folder(title: str = "Choose the folder that contains WoW.exe") -> Optional[Path]:
    """Windows' own folder chooser. None if the player cancels."""
    if not IS_WINDOWS:
        return None

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p), ("pszDisplayName", ctypes.c_void_p),
                    ("lpszTitle", wintypes.LPCWSTR), ("ulFlags", wintypes.UINT), ("lpfn", ctypes.c_void_p),
                    ("lParam", ctypes.c_void_p), ("iImage", ctypes.c_int)]

    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.OleInitialize(None)
    name = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFOW(None, None, ctypes.addressof(name), title, 0x1 | 0x10 | 0x40, None, None, 0)    # folders only, edit box, new style
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return None
    try:
        path = ctypes.create_unicode_buffer(1024)
        return Path(path.value) if shell32.SHGetPathFromIDListW(pidl, path) else None
    finally:
        ole32.CoTaskMemFree(pidl)


# --------------------------------------------------------------------------------------------- reports

def system_text(cfg, wow: Optional[Path]) -> str:
    host = str(cfg.signalling_url).split("//", 1)[-1].split("/", 1)[0]
    return "\n".join([
        "time: " + time.strftime("%Y-%m-%d %H:%M:%S"), "windows: " + platform.platform(), "python: " + sys.version.split()[0],
        "eqvoice: " + __version__, "packaged exe: %s" % frozen(), "voice server host: " + host,
        "game folder found: %s" % bool(wow), "ClassicAPI.dll: %s" % bool(wow and classicapi_present(wow)),
        "addon installed: %s" % bool(wow and (Path(wow) / "Interface" / "AddOns" / ADDON_NAME / (ADDON_NAME + ".toc")).exists()),
        "microphone mode: %s" % cfg.audio.mode, ""])


REPORT_FILE_CAP = 1_500_000                                  # a file longer than this goes into the report as its newest 1.5 MB (Discord takes small files)


def _add_capped(z, f: Path, arcname: str) -> None:
    try:
        size = f.stat().st_size
        if size <= REPORT_FILE_CAP:
            z.write(f, arcname)
            return
        with open(f, "rb") as fh:
            fh.seek(size - REPORT_FILE_CAP)
            z.writestr(arcname, b"[... the start of this file is left out: " + str(size - REPORT_FILE_CAP).encode() + b" bytes ...]\n" + fh.read())
    except OSError:
        pass


def make_report(cfg, dest_dir: Optional[Path] = None) -> Path:
    """Zip what helps someone else work out what is wrong: the logs, the settings with the server code blanked out, the addon's own
    notes. It holds character names and the kind of network you have (private/public), nothing else."""
    import json
    wow = Path(cfg.bridge.wow_dir) if cfg.bridge.wow_dir else None
    if dest_dir is None:
        desk = Path.home() / "Desktop"
        dest_dir = desk if desk.exists() else Path(cfg.home)
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    out = Path(dest_dir) / ("EqVoice-logs-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", system_text(cfg, wow))
        z.writestr("config.json", json.dumps(cfg.redacted(), indent=2, sort_keys=True))
        logs = Path(cfg.home) / "logs"
        for f in sorted(logs.glob("*")) if logs.exists() else []:
            if f.is_file():
                _add_capped(z, f, "logs/" + f.name)
        if wow and (wow / "WTF" / "Account").exists():
            for f in (wow / "WTF" / "Account").glob("*/SavedVariables/EqVoice.lua"):
                _add_capped(z, f, "addon/" + f.parent.parent.name + "-EqVoice.lua")
        if wow and (wow / "CustomData").exists():                               # what the addon and the app last said to each other
            for f in sorted((wow / "CustomData").glob("eqvoice*.txt")):
                _add_capped(z, f, "game/" + f.name)
    return out
