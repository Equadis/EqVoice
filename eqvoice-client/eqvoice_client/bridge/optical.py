"""Optical bridge: read the addon's colour grid off the screen.

Windows only. Finds the WoW window, copies a small block of pixels from one
corner of its client area with GDI BitBlt, and decodes it. This is passive:
the process never touches WoW's memory, never injects, never sends input.

Limits, all reported in ``status`` so the UI can say what is wrong:
  * WoW must be visible. Minimised, covered at that corner, or exclusive
    fullscreen that GDI cannot see all look like "no frames".
  * The in-game frame must not be hidden (Alt+Z hides the whole UI).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Callable, Optional

from .frame import BridgeState, Grid, decode_pixels
from .source import BridgeSource

log = logging.getLogger("eqvoice.bridge.optical")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    SRCCOPY = 0x00CC0020
    CAPTUREBLT = 0x40000000
    DIB_RGB_COLORS = 0
    BI_RGB = 0

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsIconic.argtypes = [wt.HWND]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    user32.IsWindow.argtypes = [wt.HWND]
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC,
                             ctypes.c_int, ctypes.c_int, wt.DWORD]
    gdi32.BitBlt.restype = wt.BOOL
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFOHEADER), wt.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]


def make_dpi_aware() -> None:
    """Coordinates must be physical pixels, or the region would be scaled wrongly."""
    if not IS_WINDOWS:
        return
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))      # per-monitor v2
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def grab(x: int, y: int, w: int, h: int) -> bytes:
    """BGRA pixels of a screen rectangle (top-down)."""
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY | CAPTUREBLT):
            raise OSError("BitBlt failed (%d)" % ctypes.get_last_error())
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount, bi.biCompression = w, -h, 1, 32, BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS) == 0:
            raise OSError("GetDIBits failed")
        return buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, hdc)


def find_window(classes, title_part: str) -> Optional[int]:
    """First visible top-level window whose class is in ``classes`` or whose title contains ``title_part``."""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cbuf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cbuf, 256)
        tbuf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, tbuf, 256)
        if cbuf.value in classes or (title_part and title_part.lower() in tbuf.value.lower()
                                      and "eqvoice" not in tbuf.value.lower()):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


def client_rect(hwnd) -> tuple:
    """(x, y, w, h) of the window's client area in screen pixels."""
    rc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top


def region_for(corner: str, client: tuple, size: tuple) -> tuple:
    cx, cy, cw, ch = client
    rw, rh = min(size[0], cw), min(size[1], ch)
    corner = corner.upper()
    x = cx + (cw - rw if "RIGHT" in corner else 0)
    y = cy + (ch - rh if "BOTTOM" in corner else 0)
    return x, y, rw, rh


class OpticalSource(BridgeSource):
    kind = "optical"

    def __init__(self, cfg_bridge):
        super().__init__()
        self.cfg = cfg_bridge
        self.interval = 1.0 / max(float(cfg_bridge.poll_hz), 1.0)
        self.grid: Optional[Grid] = None
        self.hwnd: Optional[int] = None
        self.fail_streak = 0
        self.status = {"kind": "optical", "window": "none", "client": None, "region": None, "locked": False,
                       "cell_px": None, "ok": 0, "fail": 0, "last_error": "", "grab_ms": 0.0}

    def capture_once(self) -> Optional[BridgeState]:
        if not IS_WINDOWS:
            self.status["last_error"] = "optical bridge needs Windows"
            return None
        s = self.status
        sr = self.cfg.screen_region
        if sr:
            region = tuple(int(v) for v in sr)
            s["window"] = "fixed screen region"
        else:
            if self.hwnd is None or not user32.IsWindow(self.hwnd):
                self.hwnd = find_window(self.cfg.window_classes, self.cfg.window_title)
            if self.hwnd is None:
                s.update(window="not found", locked=False, last_error="WoW window not found")
                self.grid = None
                return None
            if user32.IsIconic(self.hwnd):
                s.update(window="minimised", locked=False, last_error="WoW is minimised")
                self.grid = None
                return None
            client = client_rect(self.hwnd)
            if client[2] < 100 or client[3] < 100:
                s["last_error"] = "WoW client area is tiny"
                return None
            region = region_for(self.cfg.corner, client, tuple(self.cfg.region))
            s.update(window="found", client=list(client))
        s["region"] = list(region)
        import time as _t
        t0 = _t.perf_counter()
        try:
            buf = grab(*region)
        except OSError as e:
            s["last_error"] = str(e)
            s["fail"] += 1
            return None
        s["grab_ms"] = round((_t.perf_counter() - t0) * 1000, 1)
        r = decode_pixels(buf, region[2], region[3], "BGRA", hint=self.grid)
        if r.ok:
            self.grid = r.grid
            self.fail_streak = 0
            s.update(locked=True, cell_px=round(r.grid.cell, 2), last_error="")
            s["ok"] += 1
            return r.state
        self.fail_streak += 1
        s["fail"] += 1
        s["last_error"] = r.error or "?"
        if self.fail_streak >= 3:
            self.grid = None
            s["locked"] = False
        return None

    async def run(self, emit: Callable[[BridgeState], None]) -> None:
        make_dpi_aware()
        loop = asyncio.get_running_loop()
        while True:
            state = await loop.run_in_executor(None, self.capture_once)
            if state is not None:
                emit(state)
            await asyncio.sleep(self.interval)
