"""The system tray icon: a real one, added to this desktop's notification area and taken off again (each is there for well under a second)."""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from eqvoice_client import tray

pytestmark = pytest.mark.skipif(not (sys.platform == "win32" and tray.available()), reason="needs a Windows taskbar with a notification area")

ICON = Path(tray.__file__).parent / "ui" / "eqvoice.ico"

if sys.platform == "win32":
    class NOTIFYICONIDENTIFIER(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("guidItem", tray.GUID)]

    tray.shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(wintypes.RECT)]
    tray.shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long


def is_in_the_tray(t) -> bool:
    """Does the shell know an icon for this window? (S_OK from Shell_NotifyIconGetRect; it asks the shell, not our own flag.)"""
    ident = NOTIFYICONIDENTIFIER(ctypes.sizeof(NOTIFYICONIDENTIFIER), t.hwnd, t.uid)
    rect = wintypes.RECT()
    return tray.shell32.Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(rect)) == 0


def until(pred, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class Calls:
    def __init__(self):
        self.open = self.quit = 0

    def tray(self, tip="EqVoice"):
        return tray.Tray(ICON, tip, self.on_open, self.on_quit)

    def on_open(self):
        self.open += 1

    def on_quit(self):
        self.quit += 1


def test_the_icon_goes_into_the_notification_area_and_comes_out_again():
    c = Calls()
    t = c.tray()
    assert t.start(), t.error
    try:
        assert t.shown and t.hwnd and is_in_the_tray(t), "the shell has an icon for it"
    finally:
        t.stop()
    assert not t.shown and t.hwnd is None


def test_a_double_click_and_the_menu_do_what_they_say():
    c = Calls()
    t = c.tray()
    assert t.start(), t.error
    try:
        user32 = tray.user32
        user32.PostMessageW(t.hwnd, tray.WM_TRAY, t.uid, tray.WM_LBUTTONDBLCLK)
        assert until(lambda: c.open == 1), "a double-click opens the window"
        user32.PostMessageW(t.hwnd, tray.WM_TRAY, t.uid, tray.WM_LBUTTONUP)
        assert until(lambda: c.open == 2), "so does a click"
        user32.PostMessageW(t.hwnd, tray.WM_COMMAND, tray.ID_OPEN, 0)
        assert until(lambda: c.open == 3), "so does 'Open EqVoice' on the menu"
        assert c.quit == 0, "and none of that quits"
        user32.PostMessageW(t.hwnd, tray.WM_COMMAND, tray.ID_QUIT, 0)
        assert until(lambda: c.quit == 1), "'Quit EqVoice' quits"
    finally:
        t.stop()


def test_a_handler_that_fails_does_not_take_the_icon_down():
    t = tray.Tray(ICON, "EqVoice", lambda: 1 / 0, lambda: None)
    assert t.start(), t.error
    try:
        tray.user32.PostMessageW(t.hwnd, tray.WM_COMMAND, tray.ID_OPEN, 0)
        time.sleep(0.3)
        assert is_in_the_tray(t), "still there after the handler raised"
    finally:
        t.stop()


def test_the_tooltip_is_kept_to_what_the_shell_holds_and_can_change():
    assert tray.Tray._clip("a   b\nc") == "a b c", "one line"
    long = "x" * 400
    assert len(tray.Tray._clip(long)) == tray.TIP_MAX and tray.Tray._clip(long).endswith("…")
    c = Calls()
    t = c.tray("EqVoice: starting")
    assert t.start(), t.error
    try:
        t.set_tooltip("EqVoice: Ready: Alice on Realm. Nobody in range.")
        t.set_tooltip(long)
        time.sleep(0.3)
        assert is_in_the_tray(t) and len(t.tip) == tray.TIP_MAX
    finally:
        t.stop()


def test_the_icon_comes_back_when_explorer_restarts():
    c = Calls()
    t = c.tray()
    assert t.start(), t.error
    try:
        tray.user32.PostMessageW(t.hwnd, t._created, 0, 0)          # what Windows broadcasts once a new taskbar exists
        time.sleep(0.4)
        assert t.shown and is_in_the_tray(t), "one icon, not none and not two"
    finally:
        t.stop()
    assert t.hwnd is None and not t.shown, "and it is gone when stopped"


def test_two_copies_of_the_program_each_have_their_own_icon():
    a, b = Calls().tray("one"), Calls().tray("two")
    assert a.start() and b.start()
    try:
        assert a.hwnd != b.hwnd and is_in_the_tray(a) and is_in_the_tray(b)
    finally:
        a.stop()
        b.stop()


def test_a_missing_icon_file_is_a_clean_no_not_a_crash(tmp_path):
    t = tray.Tray(tmp_path / "nope.ico", "EqVoice", lambda: None, lambda: None)
    assert t.start() is False and "icon" in t.error
    t.stop()


def test_show_window_finds_a_window_titled_exactly_eqvoice_and_no_other():
    def make(title):
        h = tray.user32.CreateWindowExW(0, "Static", title, 0x90000000, -3000, -3000, 60, 40, None, None, tray.kernel32.GetModuleHandleW(None), None)
        assert h
        return h

    assert tray.show_window("EqVoiceTestNobody") is False
    other = make("EqVoice notes")
    try:
        assert tray.show_window("EqVoice-test-title") is False, "a window whose title only starts the same is not it"
    finally:
        tray.user32.DestroyWindow(other)
    mine = make("EqVoice-test-title")
    try:
        assert tray.show_window("EqVoice-test-title") is True
    finally:
        tray.user32.DestroyWindow(mine)
    assert tray.show_window("EqVoice-test-title") is False


# ------------------------------------------------------------------ the launcher's use of it

class FakeApp:
    """What start_tray needs of the App, and what it asks of it."""

    def __init__(self, tray_setting=True, developer=False):
        from eqvoice_client.config import Config
        self.cfg = Config.load(Path(__import__("tempfile").mkdtemp(prefix="eqvoice-tray-")), {"ui": {"tray": tray_setting, "developer": developer}})
        self.quit_asked = 0
        self.reopened = 0
        self.reports = 0

    async def _save_report(self):
        self.reports += 1

    def request_quit(self):
        self.quit_asked += 1

    async def reopen_window(self):
        self.reopened += 1

    def status_summary(self):
        return {"text": "Ready: Alice on Realm. Nobody in range."}


def test_the_launcher_starts_the_tray_and_its_menu_reaches_the_app():
    import asyncio
    from eqvoice_client import launcher

    async def go():
        app = FakeApp(developer=True)
        icon = launcher.start_tray(app, asyncio.get_running_loop(), title="EqVoice-nobody-has-this-title")
        assert icon is not None and icon.shown
        try:
            tray.user32.PostMessageW(icon.hwnd, tray.WM_COMMAND, tray.ID_OPEN, 0)
            for _ in range(100):
                if app.reopened:
                    break
                await asyncio.sleep(0.02)
            assert app.reopened == 1, "a developer build: no window to bring forward, so the app opens it again"
            tray.user32.PostMessageW(icon.hwnd, tray.WM_COMMAND, tray.ID_QUIT, 0)
            for _ in range(100):
                if app.quit_asked:
                    break
                await asyncio.sleep(0.02)
            assert app.quit_asked == 1, "Quit EqVoice asks the app to quit"
            tip = asyncio.create_task(launcher.keep_tray_tip(app, icon))
            await asyncio.sleep(0.2)
            tip.cancel()
            assert icon.tip == "EqVoice: Ready: Alice on Realm. Nobody in range.", "the tooltip is the window's top line"
        finally:
            icon.stop()

    asyncio.run(go())


def test_the_player_can_switch_the_tray_icon_off():
    import asyncio
    from eqvoice_client import launcher

    async def go():
        assert launcher.start_tray(FakeApp(tray_setting=False), asyncio.get_running_loop()) is None

    asyncio.run(go())


# ------------------------------------------------------------------ keeping the voice engine's window out of sight

_classes = {}


def chrome_like(title, cls="Chrome_WidgetWin_1", visible=True):
    """A real top-level window of a class named like the browser's, which is how the tray module tells a browser window from any other."""
    if cls not in _classes:
        proc = tray.WNDPROC(lambda h, m, w, l: tray.user32.DefWindowProcW(h, m, w, l))
        wc = tray.WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = proc, tray.kernel32.GetModuleHandleW(None), cls
        tray.user32.RegisterClassW(ctypes.byref(wc))
        _classes[cls] = proc                                   # kept alive for as long as the class is
    h = tray.user32.CreateWindowExW(0, cls, title, 0x90000000 if visible else 0x80000000, -3000, -3000, 60, 40, None, None,
                                    tray.kernel32.GetModuleHandleW(None), None)
    assert h
    return h


def test_hide_windows_takes_the_engine_window_off_the_screen_and_leaves_every_other_one():
    engine = chrome_like("EqVoice engine")
    window = chrome_like("EqVoice")
    not_a_browser = tray.user32.CreateWindowExW(0, "Static", "EqVoice engine", 0x90000000, -3000, -3000, 60, 40, None, None,
                                                tray.kernel32.GetModuleHandleW(None), None)
    longer = chrome_like("EqVoice engine notes")
    try:
        assert tray.hide_windows("EqVoice engine") == (1, 1), "one browser window has that title, and it was showing"
        assert not tray.user32.IsWindowVisible(engine), "off the screen, and with it off the taskbar"
        assert tray.user32.IsWindowVisible(window), "the real window is not the engine"
        assert tray.user32.IsWindowVisible(not_a_browser), "a window of another kind with the same title is left alone"
        assert tray.user32.IsWindowVisible(longer), "and one whose title only starts the same"
        assert tray.hide_windows("EqVoice engine") == (1, 0), "already hidden: found, nothing to do"
        assert tray.find_windows("EqVoice engine") == [(engine, False)]
        assert tray.show_window("EqVoice") is True and tray.user32.IsWindowVisible(window)
        assert not tray.user32.IsWindowVisible(engine), "showing the window does not bring the engine's back"
    finally:
        for h in (engine, window, not_a_browser, longer):
            tray.user32.DestroyWindow(h)
    assert tray.hide_windows("EqVoice engine") == (0, 0), "no window, no problem"


class OwnThreadWindow:
    """A browser-like window made, and its messages handled, on a thread of its own, as a real browser's windows are: hiding a window from
    another thread is a request that thread has to answer."""

    def __init__(self, title):
        import threading
        self.hwnd = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(title,), daemon=True)
        self._thread.start()
        assert self._ready.wait(5) and self.hwnd

    def _run(self, title):
        def proc(h, m, w, l):
            if m == 0x0002:                                     # WM_DESTROY
                tray.user32.PostQuitMessage(0)
                return 0
            return tray.user32.DefWindowProcW(h, m, w, l)
        keep = tray.WNDPROC(proc)
        cls = "Chrome_WidgetWin_%d" % id(self)
        wc = tray.WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = keep, tray.kernel32.GetModuleHandleW(None), cls
        tray.user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = tray.user32.CreateWindowExW(0, cls, title, 0x90000000, -3000, -3000, 60, 40, None, None, tray.kernel32.GetModuleHandleW(None), None)
        self._ready.set()
        msg = wintypes.MSG()
        while tray.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            tray.user32.TranslateMessage(ctypes.byref(msg))
            tray.user32.DispatchMessageW(ctypes.byref(msg))

    def visible(self):
        return bool(tray.user32.IsWindowVisible(self.hwnd))

    def close(self):
        tray.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)       # WM_CLOSE
        self._thread.join(3)


def test_the_launcher_keeps_the_engine_window_hidden_the_moment_it_appears_and_again_after_a_restart():
    import asyncio
    from eqvoice_client import launcher

    async def go():
        task = asyncio.create_task(launcher.keep_engine_hidden(None))
        try:
            await asyncio.sleep(0.2)                            # nothing to hide yet: it just looks
            first = OwnThreadWindow(launcher.ENGINE_TITLE)
            for _ in range(60):
                if not first.visible():
                    break
                await asyncio.sleep(0.02)
            assert not first.visible(), "hidden within a moment of appearing"
            first.close()
            second = OwnThreadWindow(launcher.ENGINE_TITLE)      # the engine was restarted: a new window
            for _ in range(80):
                if not second.visible():
                    break
                await asyncio.sleep(0.05)
            assert not second.visible(), "and the next one too"
            second.close()
        finally:
            task.cancel()

    asyncio.run(go())


# ------------------------------------------------------------------ the public build's menu

def menu_lines(icon) -> list:
    """The right-click menu, line by line: "-" for a separator, text in brackets for a line that cannot be chosen."""
    u = tray.user32
    u.GetMenuItemCount.argtypes = [wintypes.HMENU]
    u.GetMenuState.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    u.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
    menu = icon._build_menu()
    try:
        out = []
        for i in range(u.GetMenuItemCount(menu)):
            state = u.GetMenuState(menu, i, 0x400)                       # MF_BYPOSITION
            if state & 0x800:
                out.append("-")
                continue
            buf = ctypes.create_unicode_buffer(256)
            u.GetMenuStringW(menu, i, buf, 256, 0x400)
            out.append("(%s)" % buf.value if state & 0x3 else buf.value)
        return out
    finally:
        u.DestroyMenu(menu)


def test_the_public_menu_says_which_build_and_what_it_is_doing_and_has_no_settings():
    import asyncio
    from eqvoice_client import launcher

    async def go():
        icon = launcher.start_tray(FakeApp(), asyncio.get_running_loop())
        assert icon is not None
        try:
            icon.status_text = "Ready: Alice on Realm. 2 players in range, 1 connected."
            lines = menu_lines(icon)
            assert lines == ["(%s)" % launcher.BUILD_LABEL, "(Ready: Alice on Realm. 2 players in range, 1 connected.)", "-",
                             "Restart EqVoice", "Open log folder", "Export logs", "-", "Exit EqVoice"], lines
            assert not any("setting" in x.lower() or "option" in x.lower() or "open eqvoice" in x.lower() for x in lines), "settings are in the game"
            icon.status_text = ""
            assert menu_lines(icon)[1] == "-", "no status yet: no empty line"
        finally:
            icon.stop()
    asyncio.run(go())


def test_a_developer_build_also_has_its_window():
    import asyncio
    from eqvoice_client import launcher

    async def go():
        icon = launcher.start_tray(FakeApp(developer=True), asyncio.get_running_loop())
        try:
            lines = menu_lines(icon)
            assert "Open developer window" in lines and lines[-1] == "Exit EqVoice", lines
        finally:
            icon.stop()
    asyncio.run(go())


def test_the_menu_lines_reach_what_they_name_and_a_double_click_says_the_status(monkeypatch):
    import asyncio
    from eqvoice_client import launcher
    seen = []
    monkeypatch.setattr(launcher, "open_folder", lambda p: seen.append(("folder", Path(p).name)))
    monkeypatch.setattr(launcher, "restart_program", lambda app: seen.append(("restart", app)))

    async def go():
        app = FakeApp()
        icon = launcher.start_tray(app, asyncio.get_running_loop())
        try:
            async def pick(item, wait):
                tray.user32.PostMessageW(icon.hwnd, tray.WM_COMMAND, tray.ID_FIRST_ITEM + item, 0)
                for _ in range(100):
                    if wait():
                        return True
                    await asyncio.sleep(0.02)
                return wait()
            assert await pick(0, lambda: ("restart", app) in seen), "Restart EqVoice"
            assert await pick(1, lambda: ("folder", "logs") in seen), "Open log folder opens <home>\\logs"
            assert await pick(2, lambda: app.reports == 1), "Export logs"
            icon.status_text = "Ready: Alice."
            tray.user32.PostMessageW(icon.hwnd, tray.WM_TRAY, 1, tray.WM_LBUTTONDBLCLK)
            for _ in range(100):
                if icon._balloon:
                    break
                await asyncio.sleep(0.02)
            assert icon._balloon == (launcher.BUILD_LABEL, "Ready: Alice."), "a double-click answers with the status, in a balloon (there is no window)"
            assert app.reopened == 0, "and opens nothing"
            await asyncio.sleep(0.2)
            assert icon.shown, "the icon is still there after the balloon"
        finally:
            icon.stop()
    asyncio.run(go())


def test_wait_for_pid_returns_when_that_process_ends_and_at_once_when_it_is_already_gone():
    import subprocess
    from eqvoice_client import launcher
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    began = time.time()
    launcher.wait_for_pid(p.pid, 20)
    took = time.time() - began
    assert 0.8 < took < 10 and p.poll() is not None, "it waited for the process: %.1f s" % took
    began = time.time()
    launcher.wait_for_pid(p.pid, 20)
    assert time.time() - began < 1.0, "an ended process is not waited for"


def test_a_restarted_copy_waits_for_the_old_one_and_then_starts_as_usual(monkeypatch, tmp_path):
    from eqvoice_client import launcher
    calls = []
    monkeypatch.setattr(launcher, "wait_for_pid", lambda pid, seconds=30.0: calls.append(("wait", pid)))
    monkeypatch.setattr(launcher, "double_click", lambda home: calls.append(("start", Path(home).name)) or 0)
    monkeypatch.setattr(launcher, "fix_std_streams", lambda home: None)
    monkeypatch.setattr(launcher, "default_home", lambda: tmp_path / "EqVoice")
    assert launcher.main(["--wait-for-pid", "4321"]) == 0
    assert calls == [("wait", 4321), ("start", "EqVoice")], calls
    calls.clear()
    assert launcher.main(["--wait-for-pid", "nonsense"]) == 0
    assert calls == [("start", "EqVoice")], "a damaged number is dropped, not waited for"
