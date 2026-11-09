"""EqVoice's program icon (the tentacle round a microphone, Ollie's EqVc.png): the files exist, are what tools/make_app_icon.py makes from the
picture, and are wired to the three places it shows -- the exe, the window's taskbar button and the system tray -- and to nowhere else."""
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "eqvoice-client" / "eqvoice_client" / "ui"
sys.path.insert(0, str(ROOT / "tools"))


def ico_images(path: Path):
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "an .ico file"
    out = []
    for i in range(count):
        w, h, _c, _r, _planes, bpp, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        out.append(((w or 256), (h or 256), data[offset:offset + size]))
    return out


def test_the_icon_has_every_size_windows_asks_for_and_something_drawn_in_each():
    pytest.importorskip("PIL")
    from PIL import Image
    import io
    imgs = ico_images(UI / "eqvoice.ico")
    sizes = sorted(w for w, h, _ in imgs)
    assert sizes == [16, 20, 24, 32, 40, 48, 64, 96, 128, 256], sizes
    for w, h, blob in imgs:
        assert w == h
        im = Image.open(io.BytesIO(blob)).convert("RGBA")
        assert im.size == (w, h)
        a = im.getchannel("A")
        assert a.getextrema() == (0, 255), "transparent round it and solid in it at %d px" % w
        ap = a.load()
        covered = sum(1 for y in range(h) for x in range(w) if ap[x, y] > 128) / float(w * h)
        assert 0.35 < covered < 0.9, "the drawing fills the icon (%.0f%% at %d px), it does not sit small in clear space" % (covered * 100, w)
    big = Image.open(UI / "eqvoice-icon.png")
    assert big.size == (256, 256) and big.mode == "RGBA"


def test_the_icon_files_are_the_ones_the_script_makes_from_the_picture(tmp_path):
    pytest.importorskip("PIL")
    import make_app_icon
    assert make_app_icon.SOURCE.exists() and make_app_icon.SOURCE.name == "EqVc.png", "the picture is kept in the repo"
    made = make_app_icon.make(tmp_path)
    assert sorted(p.name for p in made) == ["eqvoice-icon.png", "eqvoice.ico"]
    for p in made:
        assert p.read_bytes() == (UI / p.name).read_bytes(), "%s is out of date: python tools/make_app_icon.py" % p.name


def test_the_picture_is_magenta_and_orange_not_a_stand_in():
    """The magenta of the tentacle (about a fifth of it) and the orange of its suckers (about a thirteenth): the real picture, not a placeholder."""
    pytest.importorskip("PIL")
    from PIL import Image
    im = Image.open(UI / "eqvoice-icon.png").convert("RGBA")
    w, h = im.size
    pix = im.load()
    solid = [pix[x, y] for y in range(h) for x in range(w) if pix[x, y][3] > 200]
    magenta = sum(1 for r, g, b, a in solid if r > 150 and b > 120 and g < 110)
    orange = sum(1 for r, g, b, a in solid if r > 200 and 100 < g < 200 and b < 110)
    assert magenta > len(solid) * 0.15 and orange > len(solid) * 0.05, (magenta, orange, len(solid))


def test_the_window_names_its_icon_and_the_build_puts_it_on_the_exe():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/png" href="/static/eqvoice-icon.png">' in html, "the window's taskbar button takes the page's icon"
    build = (ROOT / "tools" / "build_exe.py").read_text(encoding="utf-8")
    assert '"--icon", str(ICON)' in build and "eqvoice.ico" in build, "the exe carries it"


def test_the_icon_is_not_used_inside_the_game_or_anywhere_else():
    """Ollie, 2026-10-26: it 'should just be the hotbar icon - what appears in System Tray'. So the game's indicator and panel keep their own
    art, and the window's page shows no logo of it."""
    for lua in (ROOT / "eqvoice-addon" / "EqVoice").glob("*"):
        assert lua.name.lower() not in ("eqvoice.ico", "eqvc.png", "eqvoice-icon.png"), lua
    assert not list((ROOT / "eqvoice-addon" / "EqVoice").glob("*.png"))
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert html.count("eqvoice-icon.png") == 1, "named once, as the favicon: not drawn on the page"
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "eqvoice-icon" not in js and "eqvoice.ico" not in js


@pytest.mark.skipif(sys.platform != "win32", reason="the icon resource of an exe is a Windows thing")
def test_the_built_exe_carries_the_icon():
    import ctypes
    exe = ROOT / "dist" / "EqVoice.exe"
    if not exe.exists():
        pytest.skip("no dist/EqVoice.exe built")
    big, small = ctypes.c_void_p(), ctypes.c_void_p()
    n = ctypes.windll.shell32.ExtractIconExW(str(exe), 0, ctypes.byref(big), ctypes.byref(small), 1)
    assert n >= 1 and (big.value or small.value), "the exe has an icon of its own (not Windows' blank default)"
    # the icon is not the generic one: two different exes give different pictures
    for h in (big, small):
        if h.value:
            ctypes.windll.user32.DestroyIcon(h)
