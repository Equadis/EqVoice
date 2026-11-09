"""The micro-bar button's art: the files the addon draws with exist, are the plain 32-bit TGA the 1.12 client reads, and are the
ones tools/make_micro_art.py makes."""
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "eqvoice-addon" / "EqVoice"
sys.path.insert(0, str(ROOT / "tools"))

SIZES = {"MicroUp.tga": (32, 64), "MicroDown.tga": (32, 64), "MicGlyph.tga": (32, 32), "MicSlash.tga": (32, 32), "Logo.tga": (64, 64), "RoundFill.tga": (16, 16), "IconClose.tga": (16, 16), "IconDown.tga": (16, 16), "IconLeft.tga": (16, 16), "IconRight.tga": (16, 16),
         "VoiceSpeaking.tga": (16, 16), "VoiceOpen.tga": (16, 16), "VoiceKnown.tga": (16, 16), "VoiceMuted.tga": (16, 16), "VoiceDeafened.tga": (16, 16)}


def header(path: Path):
    data = path.read_bytes()
    idlen, cmap, kind, _, _, _, _, _, w, h, bpp, desc = struct.unpack("<BBBHHBHHHHBB", data[:18])
    return data, idlen, cmap, kind, w, h, bpp, desc


@pytest.mark.parametrize("name", sorted(SIZES))
def test_each_texture_is_a_plain_32_bit_tga_with_power_of_two_sides(name):
    data, idlen, cmap, kind, w, h, bpp, desc = header(ADDON / name)
    assert (idlen, cmap, kind, bpp) == (0, 0, 2, 32), "uncompressed true-colour with alpha: what every 1.12 client reads"
    assert (w, h) == SIZES[name]
    assert w & (w - 1) == 0 and h & (h - 1) == 0, "the client wants power-of-two sides"
    assert len(data) == 18 + w * h * 4, "no compression, no footer surprises"
    assert desc & 0x0F == 8 and not desc & 0x20, "eight alpha bits, origin bottom-left"


def alpha_rows(path: Path):
    data, _, _, _, w, h, _, _ = header(path)
    rows = []
    for r in range(h):                                   # bottom-left origin: the first row in the file is the bottom of the picture
        base = 18 + r * w * 4
        rows.append([data[base + x * 4 + 3] for x in range(w)])
    return list(reversed(rows))                          # top row first


@pytest.mark.parametrize("name", ["MicroUp.tga", "MicroDown.tga"])
def test_the_frames_leave_the_top_of_the_picture_empty_like_the_real_micro_buttons(name):
    """The real art is 32x64 with the visible body in the lower 40 rows, so a 28x58 button lines up with the client's own."""
    rows = alpha_rows(ADDON / name)
    assert all(a == 0 for r in rows[:20] for a in r), "nothing drawn in the top rows"
    assert max(max(r) for r in rows[24:]) == 255 and rows[40][16] == 255, "the body is solid in the lower part"


def test_the_glyph_and_slash_have_something_drawn_and_something_clear():
    for name in sorted(SIZES):
        if name.startswith("Micro"):
            continue
        flat = [a for r in alpha_rows(ADDON / name) for a in r]
        assert 0 in flat and 255 in flat, name


def test_the_panel_and_the_name_icons_use_exactly_these_files():
    used = set()
    for lua in ("Panel.lua", "Icons.lua"):
        used |= set(re.findall(r'"(\w+\.tga)"', (ADDON / lua).read_text(encoding="utf-8")))
    assert used == set(SIZES), used


def test_the_files_are_the_ones_the_script_makes(tmp_path):
    pytest.importorskip("PIL")
    import make_micro_art
    made = make_micro_art.make(tmp_path)
    assert sorted(p.name for p in made) == sorted(SIZES)
    for p in made:
        assert p.read_bytes() == (ADDON / p.name).read_bytes(), "%s is out of date: python tools/make_micro_art.py" % p.name
