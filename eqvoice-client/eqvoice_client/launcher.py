"""What a double-click on EqVoice.exe does: set everything up, then run.

    1. one copy at a time
    2. the voice server's address (hub.txt beside the exe, or built into it)
    3. the game folder: found by itself (running game, registry, the usual places), otherwise the player is asked once
    4. the addon is copied into the game (asking for administrator rights only if Windows says it must)
    5. the icon goes into the system tray (no taskbar button, no window: the settings are in the game); the voice engine, a browser kept out of
       sight, runs only while a game is being played. Without a tray icon the window is the program, as before: closing it quits EqVoice
    6. the first time only: a balloon from the tray icon saying where EqVoice is and that the game will show a short setup

With arguments (--netcheck, --no-browser, the test flags) it is the ordinary command-line client, with the server address filled in
from hub.txt when none is given. Everything the player must read is a message box, because the exe has no console window.
Whatever goes wrong is written to <home>\\logs\\crash.txt and shown in words, not as a traceback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Optional

from . import __version__, installer, tray
from .app import UI_DIR, App
from .config import DEFAULTS, Config, default_home
from .logbuf import setup_logging

log = logging.getLogger("eqvoice.launcher")
TITLE = "EqVoice"
BUILD_LABEL = "EqVoice Test Build %s" % __version__          # what the logs, the tray menu and the installer call this build

WELCOME = ("EqVoice is set up.\n\n"
           "In the game:\n"
           "  1. Log in to your character (if you are already in, type /reload once).\n"
           "  2. A small microphone button appears beside the little menu buttons at the bottom of the screen. Click it, or type /eqvc, for the voice panel.\n"
           "  3. Walk up to someone else who uses EqVoice and talk. Your microphone is open: mute with the button's right-click.\n\n"
           "EqVoice lives in the system tray (by the clock; Windows may keep a new icon under the little ^ arrow: drag it out to keep it in view). "
           "Double-click that icon to open the EqVoice window again. Closing the window does not turn voice off; to quit, right-click the icon and choose Quit EqVoice.")


def switched_off_text(names: list) -> str:
    """The game is open and EqVoice is switched off for these characters: the one thing a player must do by hand."""
    shown = ", ".join(names[:6]) + (" and %d more" % (len(names) - 6) if len(names) > 6 else "")
    return ("EqVoice is switched OFF in the game for: %s.\n\n"
            "Until it is switched on, /eqvc does nothing for that character. To switch it on:\n"
            "  1. In the game press Esc, then Log Out, to get to the character screen.\n"
            "  2. Click AddOns (bottom left), tick EqVoice, click Okay, and log in again.\n\n"
            "Or close the game, close EqVoice, and double-click EqVoice again: it switches itself on for every character." % shown)


def fix_std_streams(home: Path) -> None:
    """A windowed exe has no console: sys.stdout and sys.stderr are None. Point them somewhere real so nothing that prints can crash."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        logs = Path(home) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        console = logs / "console.txt"
        try:
            if console.exists() and console.stat().st_size > 1_000_000:               # (it only ever grew: the last megabyte is kept)
                os.replace(console, logs / "console.old.txt")
        except OSError:
            pass
        sink = open(console, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        sink = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


MAX_COPIES = 4                                     # copies of EqVoice at once on one PC (one per game window)


class SingleInstance:
    """One named Windows mutex per running copy. A copy of EqVoice serves one game window; a second double-click (a second game window on
    the same PC) takes the next slot instead of being turned away, and ``already`` means every slot is taken. ``slot`` is 1 for the first copy."""

    MAX = MAX_COPIES

    def __init__(self, name: str = "Local\\EqVoice-single-instance"):
        name = os.environ.get("EQVOICE_MUTEX") or name          # tests and timing runs use their own, so the player's copy does not block them
        self.handle = None
        self.slot = 1
        self.already = False
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            self._k32 = k32
            self.slot = 0
            for n in range(1, self.MAX + 1):
                handle = k32.CreateMutexW(None, 0, name if n == 1 else "%s-%d" % (name, n))
                if handle and ctypes.get_last_error() != 183:           # 183 = ERROR_ALREADY_EXISTS: another copy has this slot
                    self.handle, self.slot = handle, n
                    break
                if handle:
                    k32.CloseHandle(handle)
            self.already = self.slot == 0

    def release(self) -> None:
        if self.handle and sys.platform == "win32":
            self._k32.CloseHandle(self.handle)
            self.handle = None


def with_hub(argv: list) -> list:
    """Command-line runs get the server address from hub.txt unless they name one."""
    if any(a == "--signalling" or a.startswith("--signalling=") for a in argv):
        return argv
    hub = installer.read_hub()
    if not hub:
        return argv
    out = list(argv) + ["--signalling", hub[0]]
    if hub[1] and not any(a == "--join-code" or a.startswith("--join-code=") for a in argv):
        out += ["--join-code", hub[1]]
    return out


def ensure_game(cfg: Config, admin_retry: bool = True) -> Optional[Path]:
    """Find the game and install the addon; shows its own messages. Returns the game folder, or None if the player gave up."""
    wow = installer.find_wow_dir(cfg.bridge.wow_dir, ask=_ask_for_game)
    if wow is None:
        return None
    try:
        res = installer.install_addon(wow)
    except installer.InstallError as e:
        if e.permission and admin_retry and not installer.is_admin():
            if installer.ask_yes_no(TITLE, "EqVoice needs your permission to add its addon to your game folder:\n\n  %s\n\n"
                                           "Windows protects that folder. Continue as administrator?" % wow):
                if installer.relaunch_as_admin():
                    raise SystemExit(0)
            return None
        installer.message(TITLE, str(e), "error")
        return None
    log.info(res.words())
    on = installer.enable_addon(wow)
    log.info(on.words())
    if on.still_off:
        installer.message(TITLE, switched_off_text(on.still_off), "warn")
    from .setup_app import mods_text, missing_mods
    missing = missing_mods(wow)
    if missing:
        installer.message(TITLE, mods_text(wow, missing), "warn")
    cfg.bridge.wow_dir = str(wow)
    return wow


def _ask_for_game() -> Optional[Path]:
    return installer.pick_folder("EqVoice could not find your game. Choose the folder that contains WoW.exe")


class LocalHub:
    """A test setup points hub.txt at this PC (ws://127.0.0.1:8787/ws). Nothing answers there unless somebody started a server, and a copy of
    EqVoice with no server just sits at "not connected": the first thing anyone testing met. So when the address is on this PC and nobody is
    listening, this copy hosts the server itself (the same hub the Cloudflare one is checked against), and a second copy finds it already there
    and connects. If the copy hosting it is closed, another takes over within a few seconds. A real, remote address is never touched."""

    LOCAL = ("127.0.0.1", "localhost", "::1")

    def __init__(self, url: str, join_code: str = ""):
        from urllib.parse import urlsplit
        u = urlsplit(url)
        self.host, self.port, self.join_code = u.hostname or "", u.port or 80, join_code
        self.runner = None
        self.error = ""

    @property
    def enabled(self) -> bool:
        return self.host in self.LOCAL

    def listening(self) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                return True
        except OSError:
            return False

    async def ensure(self) -> bool:
        """Start the hub if nothing is listening. True if this copy is hosting it."""
        if self.runner is not None:
            return True
        if not self.enabled or self.listening():
            return False
        runner = None
        try:
            from aiohttp import web
            from eqvoice_signalling.hub import Hub, Limits
            from eqvoice_signalling.server import DEFAULT_ICE, make_app
            hub = Hub(Limits(registers_per_ip_per_minute=120), join_code=self.join_code or None, ice_servers=DEFAULT_ICE)
            runner = web.AppRunner(make_app(hub), access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", self.port).start()
        except ImportError as e:
            self.error = "this copy has no voice server in it (%s)" % e
            return False
        except OSError as e:                                   # somebody else bound the port a moment ago: they are hosting it
            self.error = str(e)
            if runner is not None:
                try:
                    await runner.cleanup()
                except Exception:                                                                   # noqa: BLE001
                    pass
            return False
        self.runner = runner
        log.info("hosting the voice server for this PC on 127.0.0.1:%d (hub.txt points here and nothing else answered)", self.port)
        return True

    async def keep(self) -> None:
        while True:
            await asyncio.sleep(3.0)
            try:
                await self.ensure()
            except Exception:                                                                       # noqa: BLE001
                log.exception("the local voice server check failed")

    async def stop(self) -> None:
        if self.runner is not None:
            runner, self.runner = self.runner, None
            await runner.cleanup()


TRAY_TIP_EVERY_S = 3.0


def wait_for_pid(pid: int, seconds: float = 30.0) -> None:
    """Return when the process ``pid`` has ended (or after ``seconds``): a restarted copy waits for the one it replaces to let go of the mutex."""
    if sys.platform != "win32":
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.OpenProcess(0x00100000, False, int(pid))            # SYNCHRONIZE
    if not handle:
        return                                                       # already gone
    try:
        k32.WaitForSingleObject(handle, int(seconds * 1000))
    finally:
        k32.CloseHandle(handle)


def restart_program(app: App) -> None:
    """The tray's "Restart EqVoice": start a new copy that waits for this one to end, then end this one. Only the packaged program can do it
    (a source run is started by hand)."""
    if not installer.frozen():
        log.info("restart asked of a source run: nothing to start")
        return
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, "--wait-for-pid", str(os.getpid())], close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        log.exception("could not start a new copy")
        return
    log.info("restarting: a new copy waits for this one to end")
    app.request_quit()


def open_folder(path) -> None:
    try:
        os.startfile(str(path))                                      # type: ignore[attr-defined]  (Windows)
    except (OSError, AttributeError):
        log.exception("could not open %s", path)


def start_tray(app: App, loop, title: str = "EqVoice") -> Optional[tray.Tray]:
    """The icon in the system tray, or None where there is nowhere to put one (or the player has switched it off, `ui.tray`).

    The menu is small and has no settings in it: which build this is, what EqVoice is doing, Restart, the log folder, a support report, Exit.
    (The settings are in the game.) A developer build (`ui.developer`) also has "Open developer window" and opens it on a double-click."""
    if not cfg_wants_tray(app.cfg) or not tray.available():
        return None
    developer = bool(app.cfg.ui.get("developer"))
    holder: dict = {}

    def activate() -> None:                               # a double-click, on the tray's own thread
        if developer:
            if not tray.show_window(title):
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(app.reopen_window()))
        elif holder.get("tray") is not None:
            holder["tray"].notify(BUILD_LABEL, holder["tray"].status_text or "EqVoice is running.")

    def quit_program() -> None:
        loop.call_soon_threadsafe(app.request_quit)

    def report() -> None:
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(app._save_report()))

    items = [("Restart EqVoice", lambda: loop.call_soon_threadsafe(lambda: restart_program(app))),
             ("Open log folder", lambda: open_folder(Path(app.cfg.home) / "logs")),
             ("Save logs to send", report)]
    t = tray.Tray(UI_DIR / "eqvoice.ico", "EqVoice", activate, quit_program, items=items, header=BUILD_LABEL,
                  status=lambda: holder["tray"].status_text if holder.get("tray") else "",
                  open_label="Open developer window" if developer else None, quit_label="Exit EqVoice")
    holder["tray"] = t
    if not t.start():
        log.info("no tray icon: %s", t.error)
        return None
    return t


def cfg_wants_tray(cfg: Config) -> bool:
    return bool(cfg.ui.get("tray", True))


def cfg_wants_tray_only(cfg: Config) -> bool:
    """Run from the tray without a taskbar button (needs the tray icon; `ui.tray_only` false keeps the window and its button as before)."""
    return bool(cfg.ui.get("tray_only", True))


ENGINE_TITLE = "EqVoice engine"            # the title the page gives itself when it is only the voice engine (ui/app.js)


async def keep_engine_hidden(app: App) -> None:
    """The voice engine's window is never meant to be seen. Take it off the screen the moment it exists (it is looked for twenty times a second
    until it does), and again whenever it comes back, as it does when the engine is restarted. It keeps running: hiding is not closing."""
    loop = asyncio.get_running_loop()
    while True:
        found, hidden = await loop.run_in_executor(None, tray.hide_windows, ENGINE_TITLE)
        if hidden:
            log.info("the voice engine's window is out of sight (no taskbar button)")
        launching = app is None or app.clock() - app._engine_started_at < 20.0       # only then is a window about to appear
        await asyncio.sleep(0.05 if (launching and not found) else 1.0)


async def keep_tray_tip(app: App, t: "tray.Tray") -> None:
    """The tray tooltip says what the window's top line says."""
    while True:
        try:
            text = str(app.status_summary().get("text") or "")
            t.status_text = text                           # (the tray's menu and balloon read it from the tray's own thread)
            t.set_tooltip("EqVoice: " + text)
        except Exception:                                  # noqa: BLE001
            log.debug("tray tooltip", exc_info=True)
        await asyncio.sleep(TRAY_TIP_EVERY_S)


async def run_app(cfg: Config, welcome: bool) -> int:
    local = LocalHub(cfg.signalling_url, cfg.join_code)
    keeper = None
    if local.enabled:
        await local.ensure()
        keeper = asyncio.create_task(local.keep())
    # The EQVOICE_* switches below are for the exe's own tests (tools/smoke_exe.py): no window pops up on the machine running them, the
    # microphone is the browser's fake one (it beeps; the keyboard filter would rightly drop that), and nothing comes out of the speakers.
    fake = bool(os.environ.get("EQVOICE_FAKE_MEDIA"))
    if fake:
        cfg.audio.keyboard_filter = False
    app = App(cfg, launch_browser=not os.environ.get("EQVOICE_NO_BROWSER"), headless=bool(os.environ.get("EQVOICE_HEADLESS")),
              fake_media=fake, mute_audio=bool(os.environ.get("EQVOICE_MUTE_AUDIO")), quit_on_window_close=True)
    icon, icon_task, hide_task = None, None, None
    if app.launch_browser and not app.headless:             # a real run, not one of the exe's own tests (they must not leave icons behind)
        icon = start_tray(app, asyncio.get_running_loop())  # first: with an icon to find it by, the window can be kept out of the taskbar
    # Tray-only needs the icon: without one (no notification area, or `ui.tray` off) the window stays where it is, because a window nobody can
    # find again is worse than a button on the taskbar.
    app.tray_only = icon is not None and cfg_wants_tray_only(cfg)
    developer = bool(cfg.ui.get("developer"))
    app.engine_on_demand = app.tray_only and not developer and bool(cfg.ui.get("engine_on_demand", True))
    if icon is not None:
        app.notify_user = icon.notify
    log.info("%s starting%s", BUILD_LABEL, " (developer build)" if developer else "")
    url = await app.start()
    log.info("EqVoice window: %s%s", url.split("?")[0], " (tray only: the voice engine is kept out of sight)" if app.tray_only else "")
    if icon is not None:
        icon_task = asyncio.create_task(keep_tray_tip(app, icon))
    if app.tray_only:
        hide_task = asyncio.create_task(keep_engine_hidden(app))
    if welcome:
        if app.tray_only and not developer:
            icon.notify(BUILD_LABEL, "EqVoice is running here, in the system tray. Start the game: a short setup appears in it.")
        else:
            if app.tray_only:
                await app.open_ui_window()                  # a developer build: the window as well
            await asyncio.get_running_loop().run_in_executor(None, installer.message, TITLE, WELCOME, "info")
    try:
        await app.wait_closed()
    finally:
        if hide_task is not None:
            hide_task.cancel()
        if icon_task is not None:
            icon_task.cancel()
        if icon is not None:
            icon.stop()
        await app.stop()
        if keeper is not None:
            keeper.cancel()
        await local.stop()
    return 0


def slot_home(base: Path, slot: int) -> Path:
    """The first copy keeps the usual data folder; the others each get their own (settings, identity and logs must not be shared)."""
    return Path(base) if slot <= 1 else Path(base) / ("copy-%d" % slot)


def double_click(home: Optional[Path] = None) -> int:
    base = Path(home or default_home())
    inst = SingleInstance()
    try:
        if inst.already:
            base.mkdir(parents=True, exist_ok=True)
            fix_std_streams(base)
            installer.message(TITLE, "EqVoice is already running %d times on this PC, which is as many as there is room for.\n"
                                     "Look for the windows in the taskbar." % MAX_COPIES)
            return 0
        home = slot_home(base, inst.slot)
        home.mkdir(parents=True, exist_ok=True)
        fix_std_streams(home)
        setup_logging(home, "INFO", console=False)
        return _double_click(home, inst)
    finally:
        inst.release()


def _another_copy_is_welcome(wow: Path) -> bool:
    """A second double-click. If there is a logged-in game no other copy serves, this copy is for it: no question. Otherwise it is most
    likely an accident, and the player is asked."""
    from .bridge.claims import CLAIMS
    from .bridge.filesource import ClaimingFileSource
    src = ClaimingFileSource(str(wow), claims=CLAIMS)
    names = src.fresh_characters()
    if any(not CLAIMS.taken_by_other(n) for n in names):
        return True
    shown = (" (serving %s)" % ", ".join(names)) if names else ""
    return installer.ask_yes_no(TITLE, "EqVoice is already running on this PC%s.\n\nEach copy of EqVoice serves one game window. "
                                       "Open another copy, for a second game window?" % shown)


def _double_click(home: Path, inst: "SingleInstance") -> int:
    cfg = Config.load(home)
    hub = installer.read_hub()
    if hub:
        cfg.signalling_url, cfg.join_code = hub
    elif cfg.signalling_url == DEFAULTS["signalling_url"]:
        installer.message(TITLE, "This copy of EqVoice does not know which voice server to use.\n"
                                 "Ask whoever sent it to you for a new copy.", "error")
        return 1
    if inst.slot > 1 and not cfg.bridge.wow_dir:         # a later copy: the first one has already found the game, do not search again
        try:
            cfg.bridge.wow_dir = Config.load(home.parent).bridge.wow_dir
        except Exception:                                                                           # noqa: BLE001
            pass
    wow = ensure_game(cfg)
    if wow is None:
        installer.message(TITLE, "EqVoice cannot work without your game folder, so it is closing.\nRun it again whenever you are ready.", "warn")
        return 1
    if inst.slot > 1 and not _another_copy_is_welcome(wow):
        return 0
    cfg.ui.port = 0                                  # any free port: a busy one must never stop a double-click from working
    welcome = inst.slot == 1 and not (cfg.get("ux") or {}).get("welcomed")
    cfg["ux"] = {"welcomed": True}
    try:
        cfg.save()
    except OSError as e:
        log.warning("could not save the settings: %s", e)
    cfg.bridge.claim = True                          # (not saved) serve a game no other copy has: two windows, two copies, never the same character
    return asyncio.run(run_app(cfg, welcome))


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    home = default_home()
    if "--home" in argv:
        i = argv.index("--home")
        if i + 1 < len(argv):
            home = Path(argv[i + 1])
    fix_std_streams(home)
    if "--uninstall" in argv:                          # Settings > Apps
        from . import setup_app
        setup_logging(home, "INFO", console=False)
        return setup_app.run_uninstall(argv)
    if "--wait-for-pid" in argv:                       # a restart: the copy this one replaces is still ending
        i = argv.index("--wait-for-pid")
        try:
            pid = int(argv[i + 1])
        except (IndexError, ValueError):
            del argv[i:]
        else:
            del argv[i:i + 2]
            wait_for_pid(pid)
    try:
        if argv:
            from .__main__ import main as cli
            return cli(with_hub(argv))
        return double_click(home)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 0
    except BaseException:                                                                           # noqa: BLE001
        text = traceback.format_exc()
        try:
            (home / "logs").mkdir(parents=True, exist_ok=True)
            (home / "logs" / "crash.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
        installer.message(TITLE, "EqVoice hit a problem and has to close.\n\n%s\n\nDetails were saved in\n%s" % (
            text.strip().splitlines()[-1], home / "logs" / "crash.txt"), "error")
        return 2


if __name__ == "__main__":
    sys.exit(main())
