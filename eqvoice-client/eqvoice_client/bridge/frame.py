"""Bridge frame BF2: the addon's state as bytes, as a file line, as a grid of colour cells, and back.

The Lua side is eqvoice-addon/EqVoice/Frame.lua; protocol/bridge_vectors.json, generated from that
file, pins the two together (tests/test_frame.py). The layout is documented there.

    byte 0      version (2)
    bytes 1-2   counter (u16), +1 per publish
    bytes 3-4   payload length (u16)
    payload     flags, login id, wall clock, roster ack, roster size, scan metrics, identity, results
    last 2      CRC-16/CCITT-FALSE of everything before

Pixel reading (the optical copy, 234 bytes at most) in three steps, all tolerant of scaling and colour drift:
  1. find_anchor   locate the 8-cell colour strip (the corners of the RGB cube), measure the cell size
  2. sample        read each cell centre, snap every channel to 0/85/170/255
  3. decode_bytes  check the CRC and parse
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

VERSION = 2
HEADER = 5
COLS = 32
ROWS = 10
NCELLS = COLS * ROWS
ANCHOR = (63, 0, 48, 12, 3, 60, 15, 51)           # W K R G B Y C M
LEVELS = (0, 85, 170, 255)
OPTICAL_MAX = 234
MAX_RESULTS = 200
NAME_MAX = 24
REALM_MAX = 32

FLAG_ENABLED = 1
FLAG_FILE = 2
FLAG_OPTICAL = 4
FLAG_ROSTER = 8
FLAG_LOGOUT = 16
FLAG_LOOKUPS = 32
FLAG_ID_MISMATCH = 64
FLAG_TRUNCATED = 128

R_PLAYER = 1
R_ENEMY = 2


class FrameError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------- state

@dataclass(frozen=True)
class BridgeResult:
    name: str
    dist: float
    flags: int = R_PLAYER


@dataclass(frozen=True)
class BridgeState:
    counter: int
    flags: int
    login_id: int
    wall: int
    ack: int
    roster_size: int
    checked: int
    loaded: int
    misses: int
    interval_ms: int
    cost: int
    character: str
    realm: str
    results: tuple = ()

    @property
    def enabled(self) -> bool:
        return bool(self.flags & FLAG_ENABLED)

    @property
    def roster_fresh(self) -> bool:
        return bool(self.flags & FLAG_ROSTER)

    @property
    def logout(self) -> bool:
        return bool(self.flags & FLAG_LOGOUT)

    @property
    def lookups(self) -> bool:
        return bool(self.flags & FLAG_LOOKUPS)

    @property
    def id_mismatch(self) -> bool:
        return bool(self.flags & FLAG_ID_MISMATCH)

    @property
    def truncated(self) -> bool:
        return bool(self.flags & FLAG_TRUNCATED)

    @property
    def identity(self) -> tuple:
        return (self.realm, self.character, self.login_id)


def make_state(**kw) -> BridgeState:
    """A BridgeState with sensible defaults, for tests and the simulator."""
    base = dict(counter=0, flags=FLAG_ENABLED | FLAG_FILE | FLAG_ROSTER | FLAG_LOOKUPS, login_id=1, wall=0, ack=0,
                roster_size=0, checked=0, loaded=0, misses=0, interval_ms=500, cost=0, character="Player",
                realm="Realm", results=())
    base.update(kw)
    base["results"] = tuple(base["results"])
    return BridgeState(**base)


# ------------------------------------------------------------------- bytes

def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE. crc16(b"123456789") == 0x29B1."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def clean_name(name: str) -> bytes:
    """Same rule as the Lua encoder: no control characters, spaces or commas; at most 24 bytes."""
    out = bytearray()
    for b in name.encode("utf-8", "replace"):
        if b >= 33 and b != 127 and b != 44:
            if len(out) + 1 > NAME_MAX:
                break
            out.append(b)
    return bytes(out)


def _lstr(data: bytes, limit: int) -> bytes:
    data = data[:limit]
    return bytes([len(data)]) + data


def encode_state(state: BridgeState, max_bytes: Optional[int] = None) -> bytes:
    """The bytes the Lua encoder produces for the same state (the simulator and the tests use this)."""
    flags = state.flags & 255
    ident = _lstr(clean_name(state.character), NAME_MAX) + _lstr(state.realm.encode("utf-8", "replace"), REALM_MAX)
    base = HEADER + 20 + len(ident) + 1 + 2
    entries, size, cut = [], 0, False
    for r in state.results[:MAX_RESULTS]:
        name = clean_name(r.name)
        if not name:
            continue
        e = _lstr(name, NAME_MAX) + bytes([min(254, int(r.dist * 2 + 0.5)), r.flags & 255])
        if max_bytes is not None and base + size + len(e) > max_bytes:
            cut = True
            break
        entries.append(e)
        size += len(e)
    if len(entries) < len(state.results) and not cut:
        cut = True
    if cut and not flags & FLAG_TRUNCATED:
        flags += FLAG_TRUNCATED
    payload = (bytes([flags]) + (state.login_id & 0xFFFFFFFF).to_bytes(4, "big") + (state.wall & 0xFFFFFFFF).to_bytes(4, "big")
               + (state.ack & 0xFFFF).to_bytes(2, "big") + (state.roster_size & 0xFFFF).to_bytes(2, "big")
               + bytes([min(state.checked, 255), min(state.loaded, 255), min(state.misses, 255)])
               + (state.interval_ms & 0xFFFF).to_bytes(2, "big") + (state.cost & 0xFFFF).to_bytes(2, "big")
               + ident + bytes([len(entries)]) + b"".join(entries))
    head = bytes([VERSION]) + (state.counter & 0xFFFF).to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload
    frame = head + crc16(head).to_bytes(2, "big")
    if max_bytes is not None and len(frame) > max_bytes:
        raise FrameError("toolong")
    return frame


def decode_bytes(data: bytes) -> BridgeState:
    if len(data) < HEADER + 2:
        raise FrameError("short")
    if data[0] != VERSION:
        raise FrameError("version")
    n = int.from_bytes(data[3:5], "big")
    if len(data) < n + HEADER + 2:
        raise FrameError("short")
    head = data[: n + HEADER]
    if crc16(head) != int.from_bytes(data[n + HEADER: n + HEADER + 2], "big"):
        raise FrameError("crc")
    p = data[HEADER: n + HEADER]
    if len(p) < 22:
        raise FrameError("payload")
    pos = 20
    cl = p[pos]
    character = p[pos + 1: pos + 1 + cl].decode("utf-8", "replace")
    pos += 1 + cl
    rl = p[pos]
    realm = p[pos + 1: pos + 1 + rl].decode("utf-8", "replace")
    pos += 1 + rl
    count = p[pos]
    pos += 1
    results = []
    for _ in range(count):
        if pos >= len(p):
            raise FrameError("result")
        nl = p[pos]
        if pos + nl + 3 > len(p):
            raise FrameError("result")
        results.append(BridgeResult(p[pos + 1: pos + 1 + nl].decode("utf-8", "replace"), p[pos + nl + 1] / 2.0, p[pos + nl + 2]))
        pos += nl + 3
    return BridgeState(
        counter=int.from_bytes(data[1:3], "big"), flags=p[0], login_id=int.from_bytes(p[1:5], "big"),
        wall=int.from_bytes(p[5:9], "big"), ack=int.from_bytes(p[9:11], "big"),
        roster_size=int.from_bytes(p[11:13], "big"), checked=p[13], loaded=p[14], misses=p[15],
        interval_ms=int.from_bytes(p[16:18], "big"), cost=int.from_bytes(p[18:20], "big"),
        character=character, realm=realm, results=tuple(results))


# ------------------------------------------------------------------- cells

def bytes_to_cells(data: bytes) -> list:
    cells = [0] * NCELLS
    cells[:8] = ANCHOR
    acc = nbits = 0
    idx = 8
    for b in data:
        acc = (acc << 8) | b
        nbits += 8
        while nbits >= 6:
            nbits -= 6
            if idx >= NCELLS:
                raise FrameError("toolong")
            cells[idx] = (acc >> nbits) & 63
            idx += 1
        acc &= (1 << nbits) - 1
    if nbits:
        if idx >= NCELLS:
            raise FrameError("toolong")
        cells[idx] = (acc << (6 - nbits)) & 63
    return cells


def cells_to_bytes(cells: Sequence[int], count: Optional[int] = None) -> bytes:
    """Bytes from cells 8.. . With ``count`` None, reads the length field to know when to stop."""
    out = bytearray()
    acc = nbits = 0
    need = count
    for idx in range(8, len(cells)):
        acc = (acc << 6) | (cells[idx] & 63)
        nbits += 6
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 255)
            if need is None and len(out) == HEADER:
                need = int.from_bytes(out[3:5], "big") + HEADER + 2
            if need is not None and len(out) >= need:
                return bytes(out)
        acc &= (1 << nbits) - 1
    raise FrameError("short")


def cell_rgb(v: int) -> tuple:
    return LEVELS[(v >> 4) & 3], LEVELS[(v >> 2) & 3], LEVELS[v & 3]


def snap(v: int) -> int:
    """Nearest of 0/85/170/255, as a level 0..3."""
    return 0 if v < 43 else 1 if v < 128 else 2 if v < 213 else 3


# ------------------------------------------------------------------ pixels

_THRESH = bytes(1 if i >= 128 else 0 for i in range(256))
_ANCHOR_RE = re.compile(rb"(\x07+)(\x00+)(\x04+)(\x02+)(\x01+)(\x06+)(\x03+)(\x05+)")
_FORMATS = {"BGRA": (2, 1, 0), "RGBA": (0, 1, 2), "BGRX": (2, 1, 0)}
_HEADER_CELLS = 7          # 42 bits: enough for the 5 header bytes


@dataclass(frozen=True)
class Grid:
    x0: int
    y0: int
    cell: float

    @property
    def width(self) -> float:
        return self.cell * COLS

    @property
    def height(self) -> float:
        return self.cell * ROWS


@dataclass
class DecodeResult:
    state: Optional[BridgeState] = None
    grid: Optional[Grid] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.state is not None


def find_anchor(buf: bytes, width: int, height: int, fmt: str = "BGRA",
                stride: Optional[int] = None, row_step: int = 2) -> Optional[Grid]:
    """Locate the anchor strip anywhere in the image. None when absent."""
    ro, go, bo = _FORMATS[fmt]
    stride = stride or width * 4
    for y in range(0, height, row_step):
        seg = buf[y * stride: y * stride + width * 4]
        if len(seg) < width * 4:
            break
        r = seg[ro::4].translate(_THRESH)
        g = seg[go::4].translate(_THRESH)
        b = seg[bo::4].translate(_THRESH)
        cls = bytes([(a << 2) | (c << 1) | d for a, c, d in zip(r, g, b)])
        for m in _ANCHOR_RE.finditer(cls):
            lens = [m.end(i) - m.start(i) for i in range(1, 9)]
            lo, hi = min(lens), max(lens)
            if lo < 3 or hi > lo * 1.5 + 1:
                continue
            x0 = m.start(1)
            cell = (m.end(8) - x0) / 8.0
            col = min(width - 1, int(x0 + cell / 2))
            top = y
            while top > 0:
                o = (top - 1) * stride + col * 4
                if (buf[o + ro] >= 128) and (buf[o + go] >= 128) and (buf[o + bo] >= 128):
                    top -= 1
                else:
                    break
            if y - top > cell * 1.5:
                continue
            return Grid(x0, top, cell)
    return None


def _sample(buf: bytes, width: int, height: int, grid: Grid, idxs, ro, go, bo, stride) -> list:
    out = []
    for i in idxs:
        c, r = i % COLS, i // COLS
        px = int(grid.x0 + (c + 0.5) * grid.cell)
        py = int(grid.y0 + (r + 0.5) * grid.cell)
        if px < 0 or py < 0 or px >= width or py >= height:
            raise FrameError("oob")
        o = py * stride + px * 4
        out.append((snap(buf[o + ro]) << 4) | (snap(buf[o + go]) << 2) | snap(buf[o + bo]))
    return out


def decode_at(buf: bytes, width: int, height: int, grid: Grid, fmt: str = "BGRA",
              stride: Optional[int] = None) -> DecodeResult:
    ro, go, bo = _FORMATS[fmt]
    stride = stride or width * 4
    try:
        anchor = _sample(buf, width, height, grid, range(8), ro, go, bo, stride)
        if tuple(anchor) != ANCHOR:
            return DecodeResult(grid=grid, error="anchor")
        head = _sample(buf, width, height, grid, range(8, 8 + _HEADER_CELLS), ro, go, bo, stride)
        hb = cells_to_bytes([0] * 8 + head, HEADER)
        if hb[0] != VERSION:
            return DecodeResult(grid=grid, error="version")
        total = HEADER + int.from_bytes(hb[3:5], "big") + 2
        ncells = -(-total * 8 // 6)
        if 8 + ncells > NCELLS:
            return DecodeResult(grid=grid, error="len")
        data = _sample(buf, width, height, grid, range(8, 8 + ncells), ro, go, bo, stride)
        raw = cells_to_bytes([0] * 8 + data, total)
        return DecodeResult(state=decode_bytes(raw), grid=grid)
    except FrameError as e:
        return DecodeResult(grid=grid, error=e.code)


def decode_pixels(buf: bytes, width: int, height: int, fmt: str = "BGRA",
                  stride: Optional[int] = None, hint: Optional[Grid] = None) -> DecodeResult:
    """Decode one captured image. ``hint`` is tried first (the previous lock)."""
    if hint is not None:
        r = decode_at(buf, width, height, hint, fmt, stride)
        if r.ok:
            return r
    grid = find_anchor(buf, width, height, fmt, stride)
    if grid is None:
        return DecodeResult(error="no_anchor")
    return decode_at(buf, width, height, grid, fmt, stride)
