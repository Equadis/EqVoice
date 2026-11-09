"""Bridge frame BF2: agreement with the Lua encoder, and decoding from pixels."""
import random

import pytest

from helpers import load_vectors, render, sample_state
from eqvoice_client.bridge import frame
from eqvoice_client.bridge.frame import (BridgeResult, BridgeState, FrameError, decode_bytes, decode_pixels,
                                           encode_state, make_state)

VECTORS = load_vectors()


def state_from_vector(v):
    s = v["state"]
    return BridgeState(
        counter=s["counter"], flags=s["flags"], login_id=s["loginId"], wall=s["wall"], ack=s["ack"],
        roster_size=s["rosterSize"], checked=s["checked"], loaded=s["loaded"], misses=s["misses"],
        interval_ms=s["intervalMs"], cost=s["cost"],
        character=bytes.fromhex(s["character_hex"]).decode("utf-8"), realm=bytes.fromhex(s["realm_hex"]).decode("utf-8"),
        results=tuple(BridgeResult(bytes.fromhex(r["name_hex"]).decode("utf-8", "replace"), float(r["dist"]), r["flags"])
                      for r in s["results"]))


def test_crc_check_value_matches_lua():
    assert frame.crc16(b"123456789") == 0x29B1 == VECTORS["crc16_check"]
    assert frame.crc16(b"") == 0xFFFF


def test_layout_constants_match_lua():
    assert VECTORS["version"] == frame.VERSION == 2
    assert VECTORS["cols"] == frame.COLS and VECTORS["rows"] == frame.ROWS
    assert tuple(VECTORS["anchor"]) == frame.ANCHOR
    assert VECTORS["optical_max"] == frame.OPTICAL_MAX


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["name"])
def test_python_encoder_matches_lua_encoder_byte_for_byte(v):
    assert encode_state(state_from_vector(v), v["max"]).hex().upper() == v["bytes_hex"]


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["name"])
def test_lua_bytes_decode_and_reencode_to_themselves(v):
    raw = bytes.fromhex(v["bytes_hex"])
    st = decode_bytes(raw)
    assert encode_state(st).hex().upper() == v["bytes_hex"]


@pytest.mark.parametrize("v", [v for v in VECTORS["vectors"] if v["cells"] is not None], ids=lambda v: v["name"])
def test_cells_match_lua(v):
    raw = bytes.fromhex(v["bytes_hex"])
    assert frame.bytes_to_cells(raw) == v["cells"]
    assert frame.cells_to_bytes(v["cells"]) == raw


def test_decoded_fields():
    by = {v["name"]: v for v in VECTORS["vectors"]}
    st = decode_bytes(bytes.fromhex(by["one_result"]["bytes_hex"]))
    assert (st.counter, st.login_id, st.ack, st.roster_size, st.checked, st.loaded, st.misses) == (1, 4000000000, 7, 12, 12, 1, 11)
    assert st.character == "Alice" and st.realm == "Turtle" and st.enabled and st.roster_fresh and st.lookups
    assert st.results == (BridgeResult("Bob", 18.5, 1),), "distances are carried in half yards"
    wraps = decode_bytes(bytes.fromhex(by["wraps_and_caps"]["bytes_hex"]))
    assert wraps.counter == 65535 and wraps.wall == 4294967295 and wraps.interval_ms == 65535
    assert [r.dist for r in wraps.results] == [127.0, 127.0, 0.0], "distance saturates at 127 yd"
    dirty = decode_bytes(bytes.fromhex(by["dirty_names"]["bytes_hex"]))
    assert dirty.character == "Alice" and [r.name for r in dirty.results] == ["ABCD", "Ok"]
    assert decode_bytes(bytes.fromhex(by["logout"]["bytes_hex"])).logout
    assert decode_bytes(bytes.fromhex(by["optical_truncated"]["bytes_hex"])).truncated
    assert not decode_bytes(bytes.fromhex(by["crowd_file_only"]["bytes_hex"])).truncated
    assert len(decode_bytes(bytes.fromhex(by["crowd_file_only"]["bytes_hex"])).results) == 40
    utf = decode_bytes(bytes.fromhex(by["utf8_names"]["bytes_hex"]))
    assert utf.character == "émile" and utf.results[0].name == "élise"


def test_decode_bytes_rejects_damage():
    raw = bytearray(encode_state(sample_state()))
    for i in range(len(raw)):
        bad = bytearray(raw)
        bad[i] ^= 0x01
        with pytest.raises(FrameError):
            decode_bytes(bytes(bad))
    for cut in (0, 4, 6, len(raw) - 1):
        with pytest.raises(FrameError):
            decode_bytes(bytes(raw[:cut]))


def test_optical_copy_is_cut_to_fit_and_keeps_the_nearest():
    results = [BridgeResult("P%03d" % i, i * 0.5) for i in range(1, 80)]
    st = make_state(results=results, character="Alice", realm="Testrealm")
    raw = encode_state(st, frame.OPTICAL_MAX)
    assert len(raw) <= 234
    got = decode_bytes(raw)
    assert got.truncated and 5 < len(got.results) < 79
    assert got.results[0].name == "P001"
    assert frame.bytes_to_cells(raw)[:8] == list(frame.ANCHOR)


# ------------------------------------------------------------------ pixels

def _cells(state, max_bytes=frame.OPTICAL_MAX):
    return frame.bytes_to_cells(encode_state(state, max_bytes))


@pytest.mark.parametrize("cell", [4, 5, 6, 8, 12, 17])
def test_pixels_integer_cell_sizes(cell):
    st = sample_state(results=3)
    w, h = max(640, int(frame.COLS * cell) + 40), max(240, int(frame.ROWS * cell) + 40)
    img = render(_cells(st), w, h, cell=cell, x0=0, y0=0)
    r = decode_pixels(img, w, h)
    assert r.ok, r.error
    assert r.state == st
    assert abs(r.grid.cell - cell) < 0.5 and r.grid.x0 == 0 and r.grid.y0 == 0


@pytest.mark.parametrize("cell", [4.2, 5.6, 8.4, 11.25])
def test_pixels_fractional_scale(cell):
    """A UI scale that is not a whole number of pixels per unit."""
    st = sample_state(results=2)
    w, h = int(frame.COLS * cell) + 60, int(frame.ROWS * cell) + 60
    img = render(_cells(st), w, h, cell=cell, x0=0, y0=0)
    r = decode_pixels(img, w, h)
    assert r.ok, r.error
    assert r.state == st


@pytest.mark.parametrize("x0,y0", [(0, 0), (7, 3), (101, 57), (300, 40)])
def test_pixels_anywhere_in_the_image(x0, y0):
    st = sample_state(results=2)
    w, h = 700, 300
    img = render(_cells(st), w, h, cell=6, x0=x0, y0=y0)
    r = decode_pixels(img, w, h)
    assert r.ok, r.error
    assert (r.grid.x0, r.grid.y0) == (x0, y0)
    assert r.state == st


def test_a_full_optical_frame_decodes_from_pixels():
    results = [BridgeResult("P%03d" % i, i * 0.5) for i in range(1, 80)]
    st = make_state(results=results, character="Alice", realm="Testrealm")
    cells = _cells(st)
    assert cells[-1] != 0 or cells[-2] != 0, "the frame really uses the whole grid"
    img = render(cells, 700, 300, cell=6, x0=20, y0=10)
    r = decode_pixels(img, 700, 300)
    assert r.ok, r.error
    assert r.state == decode_bytes(encode_state(st, frame.OPTICAL_MAX))


def test_pixels_in_a_noisy_scene():
    st = sample_state(results=4)
    for seed in range(5):
        img = render(_cells(st), 700, 300, cell=6, x0=40, y0=20, noise_seed=seed)
        r = decode_pixels(img, 700, 300)
        assert r.ok, (seed, r.error)
        assert r.state == st


def test_pixels_survive_gamma_and_drift():
    st = sample_state(results=3)
    for gamma, drift in [(1.2, 0), (0.85, 0), (1.0, 12), (1.0, -12), (1.15, 8)]:
        img = render(_cells(st), 640, 240, cell=6, gamma=gamma, drift=drift)
        r = decode_pixels(img, 640, 240)
        assert r.ok, (gamma, drift, r.error)
        assert r.state == st


def test_rgba_order():
    st = sample_state()
    img = render(_cells(st), 640, 240, cell=6, fmt="RGBA")
    assert decode_pixels(img, 640, 240, fmt="RGBA").state == st
    assert not decode_pixels(img, 640, 240, fmt="BGRA").ok, "wrong channel order must not decode"


def test_blank_and_foreign_images_do_not_decode():
    assert decode_pixels(b"\x00\x00\x00\xff" * (640 * 240), 640, 240).error == "no_anchor"
    assert decode_pixels(b"\xff\xff\xff\xff" * (640 * 240), 640, 240).error == "no_anchor"
    rnd = random.Random(1)
    noise = bytes(rnd.getrandbits(8) for _ in range(320 * 120 * 4))
    assert not decode_pixels(noise, 320, 120).ok


def test_corrupt_cell_fails_the_crc_not_silently():
    st = sample_state(results=2)
    cells = _cells(st)
    cells[40] ^= 0x15
    img = render(cells, 640, 240, cell=6)
    r = decode_pixels(img, 640, 240)
    assert not r.ok and r.error == "crc"
    hdr = _cells(st)
    hdr[12] ^= 0x15                                  # inside the length field
    assert not decode_pixels(render(hdr, 640, 240, cell=6), 640, 240).ok


def test_hint_is_used_and_falls_back():
    st = sample_state()
    img = render(_cells(st), 640, 240, cell=6, x0=10, y0=10)
    first = decode_pixels(img, 640, 240)
    assert first.ok
    again = decode_pixels(img, 640, 240, hint=first.grid)
    assert again.ok and again.grid == first.grid
    moved = render(_cells(st), 640, 240, cell=6, x0=50, y0=30)
    r = decode_pixels(moved, 640, 240, hint=first.grid)
    assert r.ok and (r.grid.x0, r.grid.y0) == (50, 30), "a stale lock is dropped and the strip found again"


def test_truncated_image_is_out_of_bounds_not_a_crash():
    st = sample_state(results=4)
    img = render(_cells(st), 640, 240, cell=6)
    r = decode_pixels(img[: 640 * 4 * 12], 640, 12)
    assert not r.ok and r.error == "oob"
