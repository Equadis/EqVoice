"""EqVoice's icon in the Windows system tray (the notification area beside the clock), in plain ctypes: no extra package goes into the exe.

What it does, and nothing more:

* shows the program's icon (the tentacle round a microphone) with the status as its tooltip;
* a double-click calls ``on_open`` (the public build shows the status in a balloon; a developer build opens its window);
* the right-click menu is built each time it opens: a header (which build this is), the status in words, whatever ``items`` the caller gives
  (restart, the log folder, a support report), an "Open EqVoice" line only when the caller wants one, and "Exit EqVoice".
  There is no settings item here, on purpose: settings are in the game.

It lives on a thread of its own, because a tray icon needs a window and a message loop and neither belongs on the asyncio thread. The two
handlers are called on that thread: the caller hands them over to its own loop (`loop.call_soon_threadsafe`). If Explorer restarts, Windows
broadcasts "TaskbarCreated" and the icon is added again, so it does not vanish until the next launch. Where there is no taskbar to put it
on (a service session, a non-Windows machine) `start()` says False and the program simply runs without one.
"""
from __future__ import annotations

import ctypes
import itertools
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable, Optional

log = logging.getLogger("eqvoice.tray")

IS_WINDOWS = sys.platform == "win32"

WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0002, 0x0010, 0x0111
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1                  # what the shell sends us about the icon
WM_SET_TIP = WM_APP + 2               # what other threads send us to change the tooltip
WM_BALLOON = WM_APP + 3               # ... or to show a balloon
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_DISABLED = 0x0, 0x800, 0x1, 0x2
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN = 0x2, 0x20
SM_CXSMICON = 49
SW_HIDE, SW_RESTORE = 0, 9
ID_OPEN, ID_QUIT = 1, 2
ID_FIRST_ITEM = 10                    # the caller's own menu lines are numbered from here
TIP_MAX = 127                         # szTip is 128 characters, the last for the terminator

_classes = itertools.count(1)


if IS_WINDOWS:
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                    ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    user32, shell32, kernel32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def show_window(title: str = "EqVoice") -> bool:
    """Bring the program's own window forward (and out of the taskbar if it was minimised). True if there was one.

    The window is the browser's app window and its title is the page's title, "EqVoice"; only a visible top-level window with exactly that
    title counts, so another program that merely has EqVoice in its title is left alone."""
    if not IS_WINDOWS:
        return False
    found = []
    enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n == len(title):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    found.append(hwnd)
        return True

    user32.EnumWindows(enum(each), 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    return bool(found)


def find_windows(title: str) -> list:
    """(handle, visible) of every top-level browser window whose title is exactly ``title``, shown or not."""
    if not IS_WINDOWS:
        return []
    found = []
    enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n == len(title):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                if cls.value.startswith("Chrome_WidgetWin"):
                    found.append((hwnd, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(enum(each), 0)
    return found


def hide_windows(title: str) -> tuple:
    """Take the browser window(s) with this exact title off the screen, and with them their button on the taskbar (a hidden window has none).
    Returns (how many there are, how many were showing and are hidden now). The page keeps running: hiding is not closing."""
    found = find_windows(title)
    hidden = 0
    for hwnd, visible in found:
        if visible:
            user32.ShowWindow(hwnd, SW_HIDE)
            hidden += 1
    return len(found), hidden


def available() -> bool:
    """Is there a taskbar with a notification area to put an icon on?"""
    return IS_WINDOWS and bool(user32.FindWindowW("Shell_TrayWnd", None))


class Tray:
    def __init__(self, icon_path, tooltip: str, on_open: Callable[[], None], on_quit: Callable[[], None], *, items=(), header: str = "",
                 status: Optional[Callable[[], str]] = None, open_label: Optional[str] = "Open EqVoice", quit_label: str = "Quit EqVoice"):
        self.icon_path, self.tip = str(icon_path), self._clip(tooltip)
        self.on_open, self.on_quit = on_open, on_quit
        self.items = [(str(label), fn) for label, fn in items]       # (menu text, function to call) in the order shown
        self.header, self.status, self.open_label, self.quit_label = header, status, open_label, quit_label
        self._balloon = None
        self.status_text = ""                # what EqVoice is doing, in words: the menu's second line and a double-click's balloon
        self.hwnd = None
        self.uid = 1
        self.shown = False
        self.error = ""
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._icon = None
        self._proc = None                 # the window procedure, kept alive for as long as the window is
        self._class = "EqVoiceTray%d" % next(_classes)
        self._created = 0                 # the "TaskbarCreated" message number
        self._lock = threading.Lock()

    @staticmethod
    def _clip(text: str) -> str:
        text = " ".join(str(text).split())
        return text if len(text) <= TIP_MAX else text[:TIP_MAX - 1] + "…"

    # ------------------------------------------------------------------ from outside

    def start(self, timeout: float = 5.0) -> bool:
        if not IS_WINDOWS:
            self.error = "not Windows"
            return False
        self._thread = threading.Thread(target=self._run, name="eqvoice-tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.error = "the tray thread did not start"
        return self.shown

    def stop(self) -> None:
        h = self.hwnd
        if h and self._thread is not None and self._thread.is_alive():
            user32.PostMessageW(h, WM_CLOSE, 0, 0)
            self._thread.join(3.0)
        self.shown = False

    def notify(self, title: str, text: str) -> None:
        """A balloon from the icon (a few seconds, then gone): how a tray program answers a double-click when it has no window."""
        if IS_WINDOWS and self.hwnd and self.shown:
            with self._lock:
                self._balloon = (" ".join(str(title).split())[:63], " ".join(str(text).split())[:255])
            user32.PostMessageW(self.hwnd, WM_BALLOON, 0, 0)

    def set_tooltip(self, text: str) -> None:
        text = self._clip(text)
        with self._lock:
            if text == self.tip:
                return
            self.tip = text
        h = self.hwnd
        if h and self.shown:
            user32.PostMessageW(h, WM_SET_TIP, 0, 0)

    # ------------------------------------------------------------------ the tray thread

    def _data(self) -> "NOTIFYICONDATAW":
        d = NOTIFYICONDATAW()
        d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        d.hWnd, d.uID = self.hwnd, self.uid
        d.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        d.uCallbackMessage = WM_TRAY
        d.hIcon = self._icon
        d.szTip = self.tip
        return d

    def _add(self) -> bool:
        d = self._data()
        # An icon that is already there (the shell announced a new taskbar while ours was still on the old one) cannot be added again: it is
        # updated instead, which leaves exactly one.
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(d))) or bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d)))
        self.shown = ok
        if not ok:
            self.error = "the shell refused the icon"
        return ok

    def _remove(self) -> None:
        if self.hwnd:                                       # always asked, shown or not: a delete of nothing is harmless, a leaked icon is not
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
        self.shown = False

    def _build_menu(self):
        """The right-click menu as the shell will show it (a handle; the caller destroys it)."""
        menu = user32.CreatePopupMenu()
        if self.header:
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED | MF_DISABLED, 0, self.header)
        if self.status is not None:
            try:
                said = self._clip(str(self.status()))[:80]
            except Exception:                                # noqa: BLE001
                log.debug("tray status", exc_info=True)
                said = ""
            if said:
                user32.AppendMenuW(menu, MF_STRING | MF_GRAYED | MF_DISABLED, 0, said)
        if self.header or self.status is not None:
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        for i, (label, _fn) in enumerate(self.items):
            user32.AppendMenuW(menu, MF_STRING, ID_FIRST_ITEM + i, label)
        if self.open_label:
            user32.AppendMenuW(menu, MF_STRING, ID_OPEN, self.open_label)
        if self.items or self.open_label:
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, self.quit_label)
        return menu

    def _menu(self) -> None:
        menu = self._build_menu()
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)           # without this the menu does not close when you click elsewhere
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)

    def _call(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:                                # noqa: BLE001
            log.exception("a tray handler failed")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                    self._call(self.on_open)
                elif lparam == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_COMMAND:
                which = wparam & 0xFFFF
                if which == ID_OPEN:
                    self._call(self.on_open)
                elif which == ID_QUIT:
                    self._call(self.on_quit)
                elif ID_FIRST_ITEM <= which < ID_FIRST_ITEM + len(self.items):
                    self._call(self.items[which - ID_FIRST_ITEM][1])
                return 0
            if msg == WM_BALLOON:
                with self._lock:
                    title, text = self._balloon or ("", "")
                d = self._data()
                d.uFlags |= NIF_INFO
                d.szInfoTitle, d.szInfo, d.dwInfoFlags = title, text, NIIF_INFO
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d))
                return 0
            if msg == WM_SET_TIP:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data()))
                return 0
            if self._created and msg == self._created:   # Explorer restarted: its notification area is empty again
                self._add()
                return 0
            if msg == WM_CLOSE:
                self._remove()
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:                                # noqa: BLE001
            log.exception("tray window procedure failed")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        inst = kernel32.GetModuleHandleW(None)
        try:
            self._proc = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, inst, self._class
            if not user32.RegisterClassW(ctypes.byref(wc)):
                self.error = "could not register the tray window class"
                return
            size = user32.GetSystemMetrics(SM_CXSMICON) or 16
            self._icon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if not self._icon:
                self.error = "could not load the icon %s" % self.icon_path
                return
            self._created = user32.RegisterWindowMessageW("TaskbarCreated")
            self.hwnd = user32.CreateWindowExW(0, self._class, "EqVoice tray", 0, 0, 0, 0, 0, None, None, inst, None)
            if not self.hwnd:
                self.error = "could not create the tray window"
                return
            self._add()
        finally:
            self._ready.set()
        if not self.shown:
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
            self._cleanup(inst)
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._cleanup(inst)

    def _cleanup(self, inst) -> None:
        if self._icon:
            user32.DestroyIcon(self._icon)
            self._icon = None
        user32.UnregisterClassW(self._class, inst)
        self.hwnd = None
