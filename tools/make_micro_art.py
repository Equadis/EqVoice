"""Draw the art for the EqVoice micro-bar button and write it into the addon folder as 32-bit TGA files.

    python tools/make_micro_art.py            writes eqvoice-addon/EqVoice/Micro{Up,Down}.tga, MicGlyph.tga, MicSlash.tga and Voice{Speaking,Open,Known,Muted,Deafened}.tga
    python tools/make_micro_art.py --preview  also writes a PNG of them on a dark background (D:\\Octowow\\.eco-work\\tmp\\micro-preview.png)

The 1.12 client wants uncompressed TGA with power-of-two sides. The frame is 32x64 like the client's own micro-button art: the
visible body is the lower 40 pixels and the top 18 are empty, so a 28x58 button lines up with the real ones. The glyph and the
slash are white (the slash has a black edge): the addon tints them with SetVertexColor to show the state.

The five Voice*.tga files are the little icons beside players' names (addon Icons.lua). Their pictures are Ollie's: 24 x 24 PNGs in
tools/art/voice-icons/, made 16 x 16 here (the 1.12 client wants power-of-two sides, and 16 is the size they are drawn at, so 1:1).

The files are generated, small and checked in, so the game never needs Pillow; this script only exists to make them again.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eqvoice-addon" / "EqVoice"
SS = 8                                                   # supersampling: drawn 8x too big, then averaged down

ICONS = ROOT / "tools" / "art" / "voice-icons"
VOICE = {"VoiceSpeaking.tga": "speaking", "VoiceOpen.tga": "open", "VoiceKnown.tga": "known", "VoiceMuted.tga": "muted", "VoiceDeafened.tga": "deafened"}
# vol_high = speaking.png, vol_mid = open.png, vol_low = known.png (see addon Icons.lua)
NAMES = ("MicroUp.tga", "MicroDown.tga", "MicGlyph.tga", "MicSlash.tga", "Logo.tga", "RoundFill.tga", "IconClose.tga", "IconDown.tga") + tuple(sorted(VOICE))


def write_tga(img: Image.Image, path: Path) -> None:
    """Uncompressed 32-bit TGA, origin bottom-left: the plainest form every 1.12 client reads."""
    img = img.convert("RGBA")
    w, h = img.size
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 8)        # 8 = eight alpha bits, bottom-left origin
    rows = []
    px = img.load()
    for y in range(h - 1, -1, -1):
        rows.append(b"".join(struct.pack("BBBB", px[x, y][2], px[x, y][1], px[x, y][0], px[x, y][3]) for x in range(w)))
    path.write_bytes(header + b"".join(rows))


def read_tga(path: Path) -> Image.Image:
    data = path.read_bytes()
    idlen, cmap, kind, _, _, _, _, _, w, h, bpp, desc = struct.unpack("<BBBHHBHHHHBB", data[:18])
    assert kind == 2 and bpp == 32 and cmap == 0 and idlen == 0, "not the plain 32-bit TGA this script writes"
    img = Image.new("RGBA", (w, h))
    px, base = img.load(), 18
    top_left = bool(desc & 0x20)
    for row in range(h):
        y = row if top_left else h - 1 - row
        for x in range(w):
            b, g, r, a = data[base:base + 4]
            px[x, y] = (r, g, b, a)
            base += 4
    return img


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def frame(down: bool) -> Image.Image:
    W, H = 32, 64
    im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 2, 23, 30, 62                                   # the visible body: 28 x 39
    if down:
        x0, y0, x1, y1 = x0 + 0, y0 + 1, x1 - 0, y1 + 0
    r = 5 * SS

    def box(inset, fill):
        d.rounded_rectangle(((x0 + inset) * SS, (y0 + inset) * SS, (x1 - inset) * SS - 1, (y1 - inset) * SS - 1), radius=max(1, r - inset * SS), fill=fill)

    box(0, (0, 0, 0, 235))                                           # dark outline
    top, bottom = ((18, 15, 12), (10, 8, 7)) if down else ((52, 45, 36), (24, 20, 16))
    edge_hi, edge_lo = ((120, 92, 38), (66, 48, 20)) if down else ((196, 156, 70), (104, 76, 30))
    # the bronze rim: a vertical gradient laid down one row at a time, clipped to the rounded rectangle by a mask
    rim = Image.new("RGBA", im.size, (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    for yy in range(y0 * SS, y1 * SS):
        t = (yy - y0 * SS) / float((y1 - y0) * SS)
        rd.line(((x0 * SS, yy), (x1 * SS, yy)), fill=lerp(edge_hi, edge_lo, t) + (255,))
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0 * SS + SS, y0 * SS + SS, x1 * SS - SS - 1, y1 * SS - SS - 1), radius=r - SS, fill=255)
    im.paste(rim, (0, 0), mask)
    # the face, inside the rim
    face = Image.new("RGBA", im.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(face)
    for yy in range(y0 * SS, y1 * SS):
        t = (yy - y0 * SS) / float((y1 - y0) * SS)
        fd.line(((x0 * SS, yy), (x1 * SS, yy)), fill=lerp(top, bottom, t) + (255,))
    fmask = Image.new("L", im.size, 0)
    ImageDraw.Draw(fmask).rounded_rectangle((x0 * SS + 3 * SS, y0 * SS + 3 * SS, x1 * SS - 3 * SS - 1, y1 * SS - 3 * SS - 1), radius=max(1, r - 3 * SS), fill=255)
    im.paste(face, (0, 0), fmask)
    # a one-pixel bevel inside the face: light on top when up, on the bottom when pressed
    bev = (255, 240, 200, 70)
    if down:
        d.line(((x0 + 4) * SS, (y1 - 4) * SS, (x1 - 4) * SS, (y1 - 4) * SS), fill=bev, width=SS // 2)
    else:
        d.line(((x0 + 4) * SS, (y0 + 4) * SS, (x1 - 4) * SS, (y0 + 4) * SS), fill=bev, width=SS // 2)
    return im.resize((W, H), Image.LANCZOS)


def glyph() -> Image.Image:
    """A microphone, white on clear, 32x32."""
    S = 32
    im = Image.new("RGBA", (S * SS, S * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    white = (255, 255, 255, 255)
    # head: a capsule
    d.rounded_rectangle((11 * SS, 3 * SS, 21 * SS, 18 * SS), radius=5 * SS, fill=white)
    # grille lines cut into the head
    for y in (7, 10, 13):
        d.line((12 * SS, y * SS, 20 * SS, y * SS), fill=(0, 0, 0, 0), width=SS // 2)
    # the cradle: a U under the head
    d.arc((7 * SS, 8 * SS, 25 * SS, 24 * SS), start=0, end=180, fill=white, width=int(1.8 * SS))
    # stem and foot
    d.rectangle((15 * SS, 23 * SS, 17 * SS - 1, 27 * SS), fill=white)
    d.rounded_rectangle((10 * SS, 27 * SS, 22 * SS - 1, 29 * SS), radius=SS, fill=white)
    return im.resize((S, S), Image.LANCZOS)


def voice_icon(name: str) -> Image.Image:
    """One of the five pictures, 24 x 24 grey on clear, as a 16 x 16 texture. Only the alpha is resized, so the grey stays one clean colour
    (resizing the colours of the clear pixels too would put a dark fringe round the edge)."""
    src = Image.open(ICONS / (name + ".png")).convert("RGBA")
    px = src.load()
    solid = [px[x, y][:3] for x in range(src.size[0]) for y in range(src.size[1]) if px[x, y][3] > 200]
    colour = tuple(sum(c[i] for c in solid) // max(1, len(solid)) for i in range(3)) if solid else (227, 227, 227)
    out = Image.new("RGBA", (16, 16), colour + (0,))
    out.putalpha(src.getchannel("A").resize((16, 16), Image.LANCZOS))
    return out


def slash() -> Image.Image:
    """A diagonal bar with a dark edge: white core (tinted by the addon), black outline (stays black)."""
    S = 32
    im = Image.new("RGBA", (S * SS, S * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.line((4 * SS, 4 * SS, 28 * SS, 28 * SS), fill=(0, 0, 0, 255), width=int(5.5 * SS))
    d.line((4 * SS, 4 * SS, 28 * SS, 28 * SS), fill=(255, 255, 255, 255), width=int(3 * SS))
    return im.resize((S, S), Image.LANCZOS)


def icon_close() -> Image.Image:
    """A heavy cross with rounded ends (the window's close button), white, 16 x 16."""
    big = Image.new("L", (16 * SS, 16 * SS), 0)
    d = ImageDraw.Draw(big)
    a, b, wd = 3.5 * SS, 12.5 * SS, int(2.6 * SS)
    d.line([a, a, b, b], fill=255, width=wd)
    d.line([a, b, b, a], fill=255, width=wd)
    for p in ((a, a), (b, b), (a, b), (b, a)):
        d.ellipse([p[0] - wd / 2, p[1] - wd / 2, p[0] + wd / 2, p[1] + wd / 2], fill=255)
    img = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
    img.putalpha(big.resize((16, 16), Image.LANCZOS))
    return img


def icon_down() -> Image.Image:
    """A solid triangle pointing down (the arrow of a choice box), white, 16 x 16."""
    big = Image.new("L", (16 * SS, 16 * SS), 0)
    ImageDraw.Draw(big).polygon([(3 * SS, 5 * SS), (13 * SS, 5 * SS), (8 * SS, 12 * SS)], fill=255)
    img = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
    img.putalpha(big.resize((16, 16), Image.LANCZOS))
    return img


def round_fill() -> Image.Image:
    """16 x 16, white, with corners rounded at a radius of 8 (a pill at that size): the addon cuts it in nine to draw any rounded rectangle, tinted
    with SetVertexColor. The 1.12 client cannot round a backdrop, so every panel, button and bar is made of these pieces."""
    big = Image.new("L", (16 * SS, 16 * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, 16 * SS - 1, 16 * SS - 1], radius=8 * SS, fill=255)
    a = big.resize((16, 16), Image.LANCZOS)
    img = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
    img.putalpha(a)
    return img


def make(out: Path = OUT) -> list:
    out.mkdir(parents=True, exist_ok=True)
    arts = {"MicroUp.tga": frame(False), "MicroDown.tga": frame(True), "MicGlyph.tga": glyph(), "MicSlash.tga": slash()}
    arts.update({tga: voice_icon(png) for tga, png in VOICE.items()})
    arts["RoundFill.tga"] = round_fill()
    arts["IconClose.tga"], arts["IconDown.tga"] = icon_close(), icon_down()
    arts["Logo.tga"] = Image.open(ICONS.parent / "eqvoice-logo.png").convert("RGBA").resize((64, 64), Image.LANCZOS)      # the panel's header: EqUpdater's icon
    for name, img in arts.items():
        write_tga(img, out / name)
    return [out / n for n in NAMES]


def preview(path: Path, out: Path = OUT) -> None:
    """The four files read back from disk, on the dark colour of the action bar, at 6x, with the glyph tinted each way."""
    up, down = read_tga(out / "MicroUp.tga"), read_tga(out / "MicroDown.tga")
    g, s = read_tga(out / "MicGlyph.tga"), read_tga(out / "MicSlash.tga")

    def tint(img, rgb):
        px = img.copy()
        p = px.load()
        for y in range(px.size[1]):
            for x in range(px.size[0]):
                r, gg, b, a = p[x, y]
                p[x, y] = (r * rgb[0] // 255, gg * rgb[1] // 255, b * rgb[2] // 255, a)
        return px

    states = [("ok", (80, 255, 80), False, False), ("talk", (128, 255, 255), False, False), ("warn", (255, 210, 0), False, False),
              ("muted", (255, 80, 80), True, False), ("noapp", (166, 166, 166), False, False), ("pressed", (80, 255, 80), False, True)]
    W = 40 * len(states) + 8
    sheet = Image.new("RGBA", (W, 76), (14, 14, 18, 255))
    for i, (_, rgb, slashed, pressed) in enumerate(states):
        ox = 4 + i * 40
        sheet.alpha_composite(down if pressed else up, (ox + 2, 6))
        off = 1 if pressed else 0
        sheet.alpha_composite(tint(g, rgb).resize((22, 22), Image.LANCZOS), (ox + 2 + 5, 6 + 33 + off))
        if slashed:
            sheet.alpha_composite(tint(s, (255, 70, 70)).resize((22, 22), Image.LANCZOS), (ox + 2 + 5, 6 + 33))
    sheet.resize((sheet.size[0] * 5, sheet.size[1] * 5), Image.NEAREST).save(path)


def main(argv: list) -> int:
    files = make()
    for f in files:
        print("wrote", f, f.stat().st_size, "bytes")
    if "--preview" in argv:
        dest = Path(r"D:\Octowow\.eco-work\tmp\micro-preview.png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        preview(dest)
        print("preview", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
