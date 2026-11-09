"""NOTE (0.3.0): written for the 0.2 program, which started the engine at launch and opened a window from the tray. The public build starts the engine only
while a character is in game and has no window or Open item, so steps 2-5 no longer apply as written; the unit tests in tests/test_tray_only.py cover the
new behaviour, and tools/smoke_exe.py still checks the exe boots. Rewrite this against a simulated game before relying on it.

The real EqVoice.exe, once, for what only a real run shows: it lives in the system tray, not on the taskbar, and its window comes and goes.

    python tools/smoke_tray.py [dist/EqVoice.exe]

It starts the exe in a sandbox (its own data folder, a pretend game folder, a spare single-instance name and port, a fake microphone, muted)
WITHOUT the switches the other smoke tests use to keep it invisible, so a real icon goes into the tray and, for a few seconds, a real window opens
on the screen of whoever runs it. Then it reads back, from Windows itself and from the app's own diagnostics:

  * the icon resource inside the exe (ExtractIconEx), drawn and checked for the tentacle's magenta;
  * the tray icon: the exe's tray window, and whether the shell knows an icon for it (Shell_NotifyIconGetRect);
  * the voice engine's window exists, is NOT visible (so it has no taskbar button), and no EqVoice window is visible at all;
  * the engine is the page the app talks to (the microphone is open and its 20 ms timer really runs 50 times a second although the window is hidden);
  * a double-click on the tray icon (sent as the shell sends it) opens the real window, which holds the tentacle icon;
  * closing that window ends nothing: the program, the tray icon and the engine all go on, and the engine's timer too;
  * the tray's Open opens a window again, and its Quit ends the program and every browser process of its profile.

It is a tool and not part of the suite because it puts a window on the screen of whoever runs it (twice, for about ten seconds in all).
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client"))

user32, gdi32, shell32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shell32
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON, ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]


def magenta_in(hicon, size: int = 32) -> tuple:
    """(pixels drawn, of them magenta) for an icon handle, drawn onto a clear 32-bit bitmap."""
    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bih = BIH(ctypes.sizeof(BIH), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
    gdi32.SelectObject(mem, bmp)
    ctypes.memset(bits, 0, size * size * 4)
    user32.DrawIconEx(mem, 0, 0, hicon, size, size, 0, None, 3)
    raw = ctypes.string_at(bits, size * size * 4)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, hdc)
    drawn = magenta = 0
    for i in range(0, len(raw), 4):
        b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if a > 200:
            drawn += 1
            if r > 150 and b > 120 and g < 110:
                magenta += 1
    return drawn, magenta


def windows_of(pid: int) -> list:
    """(handle, class, title) of the visible-or-not top-level windows of one process."""
    out = []
    enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(h, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(h, cls, 256)
            n = user32.GetWindowTextLengthW(h)
            title = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, title, n + 1)
            out.append((h, cls.value, title.value))
        return True
    user32.EnumWindows(enum(each), 0)
    return out


def descendants(pid: int) -> list:
    """pid and every process below it (the browser is a child of the exe)."""
    out = subprocess.run(["wmic", "process", "get", "ProcessId,ParentProcessId", "/format:csv"], capture_output=True, text=True).stdout
    kids: dict = {}
    for line in out.splitlines():
        parts = line.strip().split(",")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[2]))
    found, todo = [], [pid]
    while todo:
        p = todo.pop()
        found.append(p)
        todo += kids.get(p, [])
    return found


WM_CLOSE, WM_COMMAND, WM_APP = 0x0010, 0x0111, 0x8000
WM_LBUTTONDBLCLK = 0x0203
ID_QUIT = 2
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def find_port(home: Path) -> int:
    """The port the exe chose (it takes any free one): its log says so on the line that names the window."""
    import re
    for log in sorted((home / "logs").glob("eqvoice.log*")):
        m = re.search(r"EqVoice window: http://127\.0\.0\.1:(\d+)/", log.read_text(encoding="utf-8", errors="replace"))
        if m:
            return int(m.group(1))
    return 0


def diag(port: int, token: str) -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/diag?token=%s" % (port, token), timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:                                                  # noqa: BLE001
        return {}


def tree_windows(pid: int, title: str) -> list:
    """(handle, visible) of every browser window with exactly this title anywhere in the exe's process tree."""
    out = []
    for p in descendants(pid):
        for h, cls, t in windows_of(p):
            if t == title and cls.startswith("Chrome_WidgetWin"):
                out.append((h, bool(user32.IsWindowVisible(h))))
    return out


def wait(pred, seconds, step=0.4):
    end = time.time() + seconds
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def main(argv: list) -> int:
    exe = Path(argv[0]) if argv else ROOT / "dist" / "EqVoice.exe"
    problems = []
    root = Path(tempfile.mkdtemp(prefix="eqvoice-tray-smoke-"))
    prog, home, game = root / "prog", root / "home", root / "Game"
    for d in (prog, home / "logs", game / "CustomData"):
        d.mkdir(parents=True)
    (game / "WoW.exe").write_bytes(b"MZ")
    (game / "ClassicAPI.dll").write_bytes(b"MZ")
    shutil.copy2(exe, prog / exe.name)
    (prog / "hub.txt").write_text("ws://127.0.0.1:%d/ws smoketray\n" % 18999, encoding="utf-8")
    token = "smoketraytoken-0123456789"
    (home / "config.json").write_text(json.dumps({"bridge": {"wow_dir": str(game)}, "ux": {"welcomed": True}, "ui": {"token": token}}), encoding="utf-8")
    env = dict(os.environ, EQVOICE_HOME=str(home), EQVOICE_FAKE_MEDIA="1", EQVOICE_MUTE_AUDIO="1", EQVOICE_MUTEX=r"Local\EqVoice-tray-smoke-%d" % os.getpid())

    # 1. the icon inside the exe, with nothing running
    big, small = ctypes.c_void_p(), ctypes.c_void_p()
    n = shell32.ExtractIconExW(str(prog / exe.name), 0, ctypes.byref(big), ctypes.byref(small), 1)
    if n < 1 or not (big.value or small.value):
        problems.append("the exe has no icon of its own")
    else:
        drawn, mag = magenta_in(big.value or small.value)
        print("exe icon resource: %d pixels drawn, %d magenta" % (drawn, mag))
        if mag < 20:
            problems.append("the exe's own icon is not the tentacle (%d magenta of %d)" % (mag, drawn))
        user32.DestroyIcon(big.value or small.value)

    proc = subprocess.Popen([str(prog / exe.name)], cwd=str(prog), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    profile = home / "browser-profile"
    try:
        # 2. started: a tray icon, a voice engine, and nothing on the screen
        def tray_window():
            for p in descendants(proc.pid):
                for h, cls, title in windows_of(p):
                    if cls.startswith("EqVoiceTray"):
                        return h
            return None
        tray_hwnd = wait(tray_window, 40)
        engine = wait(lambda: tree_windows(proc.pid, "EqVoice engine"), 40)
        print("tray window: %s, voice engine window: %s" % (bool(tray_hwnd), bool(engine)))
        if not tray_hwnd:
            problems.append("the exe made no tray window")
        else:
            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            class IDENT(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("guid", GUID)]
            shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(IDENT), ctypes.POINTER(wintypes.RECT)]
            shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
            ident, rect = IDENT(ctypes.sizeof(IDENT), tray_hwnd, 1), wintypes.RECT()
            known = shell32.Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(rect)) == 0
            print("the shell has a tray icon for it:", known)
            if not known:
                problems.append("the shell has no tray icon for the exe's tray window")
        if not engine:
            problems.append("no voice engine window (titled 'EqVoice engine') was ever made")
        time.sleep(3.0)                                              # it is hidden within a moment of appearing; give it that
        engine = tree_windows(proc.pid, "EqVoice engine")
        shown = [h for h, v in engine if v]
        print("voice engine window(s): %d, showing: %d" % (len(engine), len(shown)))
        if shown:
            problems.append("the voice engine's window is on the screen (and so on the taskbar)")
        if any(v for _, v in tree_windows(proc.pid, "EqVoice")):
            problems.append("a real EqVoice window is on the screen although nobody asked for one")

        # 3. the engine is the page the app talks to, and a hidden page is not a slowed-down page
        port = wait(lambda: find_port(home), 30)
        print("the app's port (from its log):", port)
        if not port:
            problems.append("the exe's log never named the window's address")

        def healthy():
            d = diag(port, token)
            pages = d.get("pages") or []
            m = d.get("mic") or {}
            return d if any(p.get("media") and p.get("role") == "engine" for p in pages) and m.get("state") == "ok" and m.get("hz") else None
        d = wait(healthy, 30)
        if not d:
            problems.append("the voice engine never became the app's media page with a working microphone: %r" % (diag(port, token).get("pages"),))
            hz = 0
        else:
            time.sleep(3.0)
            hz = (diag(port, token).get("mic") or {}).get("hz") or 0
            print("the hidden engine page's 20 ms timer runs %s times a second (50 when nothing slows it down)" % hz)
            if hz < 40:
                problems.append("the hidden page is slowed down: its 20 ms timer runs %s times a second, not 50" % hz)

        # 4. the tray's double-click opens the real window
        if tray_hwnd:
            user32.PostMessageW(tray_hwnd, WM_APP + 1, 1, WM_LBUTTONDBLCLK)
            win = wait(lambda: [h for h, v in tree_windows(proc.pid, "EqVoice") if v], 25)
            print("window after a double-click on the tray icon:", bool(win))
            if not win:
                problems.append("a double-click on the tray icon opened no window")
            else:
                window_hwnd = win[0]
                roles = sorted(p.get("role") for p in diag(port, token).get("pages") or [])
                print("pages now:", roles)
                if roles != ["engine", "ui"]:
                    problems.append("with the window open there should be an engine and a window page, not %r" % (roles,))
                time.sleep(3.0)                                      # the page's icon arrives a moment after the page
                h = user32.SendMessageW(window_hwnd, 0x7F, 1, 0) or user32.SendMessageW(window_hwnd, 0x7F, 0, 0)
                if not h:
                    problems.append("the window holds no icon")
                else:
                    drawn, mag = magenta_in(h)
                    print("window icon: %d pixels drawn, %d magenta" % (drawn, mag))
                    if mag < 20:
                        problems.append("the window's taskbar icon is not the tentacle (%d magenta of %d)" % (mag, drawn))

                # 5. closing the window ends nothing
                user32.PostMessageW(window_hwnd, WM_CLOSE, 0, 0)
                time.sleep(5.0)
                alive = proc.poll() is None
                roles = sorted(p.get("role") for p in diag(port, token).get("pages") or [])
                m = diag(port, token).get("mic") or {}
                print("after closing the window: program running %s, pages %s, microphone %s, timer %s/s, tray window %s"
                      % (alive, roles, m.get("state"), m.get("hz"), bool(tray_window())))
                if not alive:
                    problems.append("closing the window ended the program")
                if roles != ["engine"]:
                    problems.append("after closing the window only the engine should be left, not %r" % (roles,))
                if m.get("state") != "ok" or (m.get("hz") or 0) < 40:
                    problems.append("the voice engine did not keep running after the window was closed: %r" % (m,))
                if not tray_window():
                    problems.append("the tray icon went when the window closed")
                if any(v for _, v in tree_windows(proc.pid, "EqVoice")) or any(v for _, v in tree_windows(proc.pid, "EqVoice engine")):
                    problems.append("a window is on the screen after the window was closed")

                # 6. open again, then quit from the tray's menu
                user32.PostMessageW(tray_hwnd, WM_APP + 1, 1, WM_LBUTTONDBLCLK)
                again = wait(lambda: [h for h, v in tree_windows(proc.pid, "EqVoice") if v], 25)
                print("window after a second double-click:", bool(again))
                if not again:
                    problems.append("the tray could not open the window a second time")
                user32.PostMessageW(tray_hwnd, WM_COMMAND, ID_QUIT, 0)
                gone = wait(lambda: proc.poll() is not None, 25)
                print("the program ended after Quit:", bool(gone))
                if not gone:
                    problems.append("Quit on the tray's menu did not end the program")
                left = wait(lambda: not browser_pids(profile), 20) if gone else False
                print("browser processes of its profile left:", 0 if left else len(browser_pids(profile)))
                if gone and not left:
                    problems.append("browser processes of the exe's profile outlived the program")
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        for pid in browser_pids(profile):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        time.sleep(1.0)
        if os.environ.get("EQVOICE_SMOKE_KEEP"):
            print("kept:", root)
        else:
            shutil.rmtree(root, ignore_errors=True)
    for p in problems:
        print("PROBLEM:", p)
    print("exe tray-only smoke test:", "FAILED" if problems else "passed")
    return 1 if problems else 0


def browser_pids(profile: Path) -> list:
    from eqvoice_client import browser
    return browser.profile_pids(profile)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
