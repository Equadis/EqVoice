"""Make EqVoice's program icon from Ollie's picture (tools/art/EqVc.png: a tentacle round a microphone).

    python tools/make_app_icon.py            writes eqvoice-client/eqvoice_client/ui/eqvoice.ico and eqvoice-icon.png
    python tools/make_app_icon.py --preview  also writes a PNG of it at the sizes Windows uses (D:\\Octowow\\.eco-work\\tmp\\app-icon-preview.png)

Where it shows: the program's own icon (EqVoice.exe in Explorer and on the taskbar), the window's icon on the taskbar (the page's favicon, which
the browser's app window uses for its button), and the little icon in the system tray. It is deliberately NOT used inside the game or on the
window's page: it is the face of the program, not of the voice.

The picture is 604 x 604 with the octopus in the middle and clear space round it. An icon is read at 16 pixels, so the clear space is cropped
away (a small margin stays) and the result is made square: the drawing then fills the icon instead of sitting small in it.

The .ico holds PNG-compressed images at every size Windows asks for (16 to 256), each resized from the one cropped picture. The files are
generated, small and checked in, so the build never needs Pillow; this script only exists to make them again.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "art" / "EqVc.png"
OUT = ROOT / "eqvoice-client" / "eqvoice_client" / "ui"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
MARGIN = 0.04                                                    # of the picture's height, left clear round the drawing


def square() -> Image.Image:
    """The picture cropped to what is drawn (plus a margin) and padded to a square, transparent round it."""
    im = Image.open(SOURCE).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box is None:
        raise SystemExit("%s is empty" % SOURCE)
    im = im.crop(box)
    w, h = im.size
    pad = int(round(max(w, h) * MARGIN))
    side = max(w, h) + 2 * pad
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return out


def sized(base: Image.Image, n: int) -> Image.Image:
    """`base` at n x n. The colour is resized as premultiplied so the clear pixels' (black) colour never darkens the edge."""
    premul = base.copy()
    px = premul.load()
    for y in range(premul.size[1]):
        for x in range(premul.size[0]):
            r, g, b, a = px[x, y]
            px[x, y] = (r * a // 255, g * a // 255, b * a // 255, a)
    small = premul.resize((n, n), Image.LANCZOS)
    sp = small.load()
    for y in range(n):
        for x in range(n):
            r, g, b, a = sp[x, y]
            if a:
                sp[x, y] = (min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a), a)
    return small


def make(out: Path = OUT) -> list:
    out.mkdir(parents=True, exist_ok=True)
    base = square()
    big = sized(base, max(SIZES))
    ico, png = out / "eqvoice.ico", out / "eqvoice-icon.png"
    big.save(ico, format="ICO", sizes=[(n, n) for n in SIZES])
    big.save(png, format="PNG", optimize=True)
    return [ico, png]


def preview(path: Path) -> None:
    base = square()
    sheet = Image.new("RGBA", (sum(n + 12 for n in (16, 24, 32, 48, 64, 128)) + 12, 140), (30, 30, 36, 255))
    x = 12
    for n in (16, 24, 32, 48, 64, 128):
        sheet.alpha_composite(sized(base, n), (x, 6))
        x += n + 12
    sheet.resize((sheet.size[0] * 3, sheet.size[1] * 3), Image.NEAREST).save(path)


def main(argv: list) -> int:
    for f in make():
        print("wrote", f, f.stat().st_size, "bytes")
    if "--preview" in argv:
        dest = Path(r"D:\Octowow\.eco-work\tmp\app-icon-preview.png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        preview(dest)
        print("preview", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
