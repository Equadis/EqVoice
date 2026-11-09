"""The optical bridge against a real window: draw the cell grid in a Tk window, capture it off the screen
with the same GDI code that reads WoW, and decode it. Windows only; skipped without an interactive desktop."""
import sys
import time

import pytest

from helpers import sample_state
from eqvoice_client.bridge import frame, optical
from eqvoice_client.bridge.frame import encode_state
from eqvoice_client.config import Config

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows screen capture")

TITLE = "Fake World of Warcraft capture test"


def draw_grid(canvas, cells, x0, y0, cell):
    canvas.create_rectangle(x0, y0, x0 + frame.COLS * cell, y0 + frame.ROWS * cell, fill="#000000", outline="")
    for i, v in enumerate(cells):
        c, r = i % frame.COLS, i // frame.COLS
        rr, gg, bb = frame.cell_rgb(v)
        canvas.create_rectangle(x0 + c * cell, y0 + r * cell, x0 + (c + 1) * cell, y0 + (r + 1) * cell,
                                fill="#%02x%02x%02x" % (rr, gg, bb), outline="")


def make_window(tk, cells, corner, cell=6, w=900, h=500):
    root = tk.Tk()
    root.title(TITLE)
    root.geometry("%dx%d+140+140" % (w, h))
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=w, height=h, bg="#203040", highlightthickness=0, bd=0)
    canvas.pack()
    x0 = w - frame.COLS * cell if "RIGHT" in corner else 0
    y0 = h - frame.ROWS * cell if "BOTTOM" in corner else 0
    draw_grid(canvas, cells, x0, y0, cell)
    for _ in range(30):
        root.update()
        time.sleep(0.03)
    return root


@pytest.mark.parametrize("corner", ["TOPLEFT", "TOPRIGHT", "BOTTOMLEFT", "BOTTOMRIGHT"])
def test_capture_decodes_a_real_window(corner):
    tk = pytest.importorskip("tkinter")
    optical.make_dpi_aware()
    st = sample_state(counter=42, results=3)
    root = make_window(tk, frame.bytes_to_cells(encode_state(st)), corner)
    try:
        cfg = Config.load(__import__("tempfile").mkdtemp(dir=None), {"bridge": {
            "window_classes": [], "window_title": "Fake World of Warcraft", "corner": corner}})
        src = optical.OpticalSource(cfg.bridge)
        got = src.capture_once()
        if got is None and src.status["last_error"] in ("no_anchor", "anchor") and src.status["window"] == "found":
            # the window was found but the pixels do not show it: no interactive desktop (locked screen, service)
            region = src.status["region"]
            buf = optical.grab(*region)
            if not any(buf[:4000]):
                pytest.skip("screen capture returned black: no interactive desktop here")
        assert got == st, src.status
        assert src.status["locked"] and abs(src.status["cell_px"] - 6) < 0.6
        # a second capture reuses the lock
        again = src.capture_once()
        assert again == st
    finally:
        root.destroy()


def test_window_not_found_and_minimised_are_reported():
    tk = pytest.importorskip("tkinter")
    optical.make_dpi_aware()
    cfg = Config.load(__import__("tempfile").mkdtemp(), {"bridge": {"window_classes": [], "window_title": "no such window title zzz"}})
    src = optical.OpticalSource(cfg.bridge)
    assert src.capture_once() is None and src.status["window"] == "not found"
    root = make_window(tk, frame.bytes_to_cells(encode_state(sample_state())), "TOPLEFT")
    try:
        cfg2 = Config.load(__import__("tempfile").mkdtemp(), {"bridge": {"window_classes": [], "window_title": "Fake World of Warcraft"}})
        s2 = optical.OpticalSource(cfg2.bridge)
        root.iconify()
        for _ in range(20):
            root.update()
            time.sleep(0.03)
        assert s2.capture_once() is None
        assert s2.status["window"] in ("minimised", "not found")
    finally:
        root.destroy()


def test_region_for_corners():
    client = (100, 50, 1000, 600)
    assert optical.region_for("TOPLEFT", client, (768, 288)) == (100, 50, 768, 288)
    assert optical.region_for("TOPRIGHT", client, (768, 288)) == (332, 50, 768, 288)
    assert optical.region_for("BOTTOMLEFT", client, (768, 288)) == (100, 362, 768, 288)
    assert optical.region_for("BOTTOMRIGHT", client, (768, 288)) == (332, 362, 768, 288)
    assert optical.region_for("TOPLEFT", (0, 0, 300, 200), (768, 288)) == (0, 0, 300, 200), "clamped to a small window"
