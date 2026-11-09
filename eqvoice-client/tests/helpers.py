"""Test helpers: render cell grids into fake screen captures."""
import json
import os
import random

from eqvoice_client.bridge import frame

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.path.join(HERE, "..", "..", "protocol", "bridge_vectors.json")


def load_vectors():
    with open(VECTORS, encoding="utf-8") as f:
        return json.load(f)


def render(cells, width=640, height=240, cell=6.0, x0=0, y0=0, noise_seed=None,
           gamma=1.0, drift=0, fmt="BGRA"):
    """A BGRA/RGBA image of ``cells`` laid out like the in-game frame.

    ``cell`` may be fractional (UI scale), ``gamma`` and ``drift`` bend the
    colours the way a real display chain might, ``noise_seed`` fills the rest of
    the image with a busy scene.
    """
    rnd = random.Random(noise_seed)
    if noise_seed is not None:
        buf = bytearray(rnd.getrandbits(8) for _ in range(width * height * 4))
    else:
        buf = bytearray(b"\x20\x30\x40\xff" * (width * height))
    ro, go, bo = {"BGRA": (2, 1, 0), "RGBA": (0, 1, 2)}[fmt]

    def px(v):
        return max(0, min(255, int(round(255 * ((v / 255.0) ** gamma)) + drift)))

    # black backing under the whole grid, like the addon's backdrop
    bx0, bx1 = int(round(x0)), int(round(x0 + frame.COLS * cell))
    by0, by1 = int(round(y0)), int(round(y0 + frame.ROWS * cell))
    for y in range(max(by0, 0), min(by1, height)):
        for x in range(max(bx0, 0), min(bx1, width)):
            o = (y * width + x) * 4
            buf[o + ro] = buf[o + go] = buf[o + bo] = 0
            buf[o + 3] = 255
    for i, v in enumerate(cells):
        c, r = i % frame.COLS, i // frame.COLS
        rgb = [px(x) for x in frame.cell_rgb(v)]
        xa, xb = int(round(x0 + c * cell)), int(round(x0 + (c + 1) * cell))
        ya, yb = int(round(y0 + r * cell)), int(round(y0 + (r + 1) * cell))
        for y in range(max(ya, 0), min(yb, height)):
            for x in range(max(xa, 0), min(xb, width)):
                o = (y * width + x) * 4
                buf[o + ro], buf[o + go], buf[o + bo], buf[o + 3] = rgb[0], rgb[1], rgb[2], 255
    return bytes(buf)


NAMES = ["Bobby", "Carol", "Dwight", "Edgar", "Fiona", "Gerald", "Hannah", "Isaac"]


def sample_state(counter=5, results=2, **kw):
    """A BF2 state with a few lookup results, small enough for the optical copy."""
    res = [frame.BridgeResult(NAMES[i % len(NAMES)], 10.0 + 5.5 * i, frame.R_PLAYER) for i in range(results)]
    base = dict(counter=counter, flags=frame.FLAG_ENABLED | frame.FLAG_OPTICAL | frame.FLAG_ROSTER | frame.FLAG_LOOKUPS,
                login_id=77, character="Alice", realm="Testrealm", results=res)
    base.update(kw)
    return frame.make_state(**base)
