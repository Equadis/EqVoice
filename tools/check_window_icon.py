"""Check by eye what icon the EqVoice window gets on the taskbar: a real Edge app window is opened (off screen) on a page that names the EqVoice
icon as its favicon, and the icon Windows holds for that window is read back and saved as a PNG.

    python tools/check_window_icon.py            with the favicon link: the octopus (what the window's page does)
    python tools/check_window_icon.py none       without it: Edge's own grey default (what the window did before)

It is a tool and not a test because a real window, even off screen, puts a button on the taskbar for a few seconds. The suite checks the
parts instead: the link is in index.html, the file is served, the .ico and .png are what tools/make_app_icon.py makes.
"""
import ctypes
import http.server
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eqvoice-client"))
from PIL import Image
from eqvoice_client import browser

UI = ROOT / "eqvoice-client" / "eqvoice_client" / "ui"
FAVICON = not (len(sys.argv) > 1 and sys.argv[1] == "none")
PAGE = ("<!doctype html><title>EqVoiceProbe</title>" + ('<link rel="icon" type="image/png" href="/static/eqvoice-icon.png">' if FAVICON else "")
        + "<body>probe</body>").encode()


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/static/"):
            body, ctype = (UI / self.path[len("/static/"):]).read_bytes(), "image/png"
        elif self.path.startswith("/favicon.ico"):
            self.send_response(404); self.end_headers(); return
        else:
            body, ctype = PAGE, "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
profile = Path(r"D:\Octowow\.eco-work\tmp\icon-probe-profile")
proc = subprocess.Popen([str(browser.find_browser()), "--app=http://127.0.0.1:%d/" % srv.server_address[1], "--user-data-dir=%s" % profile, "--no-first-run",
                         "--no-default-browser-check", "--window-position=-2000,-2000", "--window-size=300,200"])
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_void_p


def find():
    found = []
    enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n and user32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            if buf.value.startswith("EqVoiceProbe"):
                found.append(h)
        return True
    user32.EnumWindows(enum(each), 0)
    return found[0] if found else None


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


def pixels(hicon, size=32):
    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bih = BIH(ctypes.sizeof(BIH), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
    gdi32.SelectObject(mem, bmp)
    ctypes.memset(bits, 0, size * size * 4)
    user32.DrawIconEx(mem, 0, 0, hicon, size, size, 0, None, 3)           # DI_NORMAL
    raw = ctypes.string_at(bits, size * size * 4)
    im = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(mem); user32.ReleaseDC(None, hdc)
    return im


try:
    hwnd = None
    for _ in range(80):
        hwnd = find()
        if hwnd:
            break
        time.sleep(0.25)
    print("window found:", bool(hwnd))
    time.sleep(3.0)                                                   # the favicon arrives after the page
    res = {}
    for name, which in (("small", 0), ("big", 1)):
        h = user32.SendMessageW(hwnd, 0x7F, which, 0)             # WM_GETICON
        res[name] = h
    res["class"] = user32.GetClassLongPtrW(hwnd, -14)
    print({k: bool(v) for k, v in res.items()})
    h = res["big"] or res["small"] or res["class"]
    if h:
        im = pixels(h)
        px = [im.getpixel((x, y)) for y in range(im.size[1]) for x in range(im.size[0])]
        solid = [p for p in px if p[3] > 200]
        mag = sum(1 for r, g, b, a in solid if r > 150 and b > 120 and g < 110)
        print("solid", len(solid), "magenta", mag, "mean colour", tuple(sum(c[i] for c in solid) // max(1, len(solid)) for i in range(3)))
        im.resize((128, 128), Image.NEAREST).save(r"D:\Octowow\.eco-work\tmp\window-icon-%s.png" % ("favicon" if FAVICON else "none"))
finally:
    proc.terminate()
    srv.shutdown()
    time.sleep(1)
    subprocess.run(["taskkill", "/F", "/T", "/FI", "WINDOWTITLE eq EqVoiceProbe*"], capture_output=True)
