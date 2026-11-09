"""The roster file (client -> addon): format, atomic writer, and agreement with the addon's own Lua parser."""
import os
import shutil
import subprocess

import pytest

from eqvoice_client.bridge.frame import crc16
from eqvoice_client.bridge.inbox import (InboxError, InboxWriter, MAX_NAMES, body_crc, build_text, parse_text)

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.abspath(os.path.join(HERE, "..", "..", "eqvoice-addon"))
LUAJIT = shutil.which("luajit")


def test_build_and_parse_round_trip():
    text = build_text(7, 1700000000, "Alice", ["Bobby", "Carol"], {"radius": 40, "interval": 0.5, "checks": 80}, "hello")
    assert text.startswith("OVI1 7 1700000000 ") and "\nC radius=40 interval=0.5 checks=80\nR Bobby,Carol\nT hello\n" in text
    got = parse_text(text)
    assert (got.seq, got.ts, got.character, got.names, got.test) == (7, 1700000000, "Alice", ["Bobby", "Carol"], "hello")
    assert got.config == {"radius": 40.0, "interval": 0.5, "checks": 80.0}


def test_header_crc_covers_exactly_the_body():
    text = build_text(1, 5, "Alice", ["Bobby"])
    head, body = text.split("\n", 1)
    assert head.split(" ")[3] == "%04X" % crc16(body.encode()) == "%04X" % body_crc(body)


def test_long_rosters_are_split_over_lines():
    names = ["Name%03d" % i for i in range(60)]
    text = build_text(1, 1, "Alice", names)
    assert text.count("\nR ") == 3, "25 names per line keeps every line short"
    assert parse_text(text).names == names


@pytest.mark.parametrize("mutate,reason", [
    (lambda t: t.replace("OVI1", "OVI2", 1), "magic"),
    (lambda t: t.replace("Bobby", "Bobbz"), "crc"),
    (lambda t: t.replace("R Bobby", "R Bobby,Evil"), "crc"),
    (lambda t: t.split("\n")[0], "noheader"),
    (lambda t: "OVI1 x 5 0000 Alice\n", "numbers"),
    (lambda t: "OVI1 1 5 Alice\n", "magic"),
    (lambda t: "", "noheader"),
])
def test_parse_rejects_damage(mutate, reason):
    text = build_text(1, 5, "Alice", ["Bobby"])
    with pytest.raises(InboxError) as e:
        parse_text(mutate(text))
    assert str(e.value) == reason


def test_crlf_does_not_matter():
    text = build_text(1, 5, "Alice", ["Bobby", "Carol"])
    assert parse_text(text.replace("\n", "\r\n")).names == ["Bobby", "Carol"]


def test_bad_names_are_dropped_not_fatal():
    body_names = ["Bobby", "B", "x" * 30, "with space", "tab\tname", "Bobby", "Carol"]
    body = "R " + ",".join(body_names) + "\n"
    text = "OVI1 1 5 %04X Alice\n%s" % (crc16(body.encode()), body)
    assert parse_text(text).names == ["Bobby", "Carol"], "too short, too long, whitespace and duplicates are dropped"


def test_the_name_limit_holds():
    names = ["N%04d" % i for i in range(MAX_NAMES + 50)]
    assert len(parse_text(build_text(1, 1, "Alice", names)).names) == MAX_NAMES


# ------------------------------------------------------------------ the writer

def test_writer_replaces_atomically_and_leaves_no_temp_file(tmp_path):
    p = tmp_path / "CustomData" / "eqvoice-in.txt"
    now = [100.0]
    w = InboxWriter(p, refresh_s=4, clock=lambda: now[0])
    assert w.write("Alice", ["Bobby"], {"checks": 80}) is True
    assert parse_text(p.read_text()).names == ["Bobby"]
    assert [x.name for x in p.parent.iterdir()] == ["eqvoice-in.txt"], "the temp file is renamed away"


def test_writer_skips_unchanged_content_but_keeps_the_sequence_moving(tmp_path):
    p = tmp_path / "in.txt"
    now = [100.0]
    w = InboxWriter(p, refresh_s=4, clock=lambda: now[0])
    assert w.write("Alice", ["Bobby", "Carol"])
    first = parse_text(p.read_text()).seq
    now[0] += 1
    assert w.write("Alice", ["Carol", "Bobby"]) is False, "same names in another order: nothing to say"
    now[0] += 4
    assert w.write("Alice", ["Bobby", "Carol"]) is True, "but the game treats silence as 'client gone', so refresh"
    second = parse_text(p.read_text()).seq
    assert second == first + 1
    assert w.write("Alice", ["Bobby"]) is True, "a changed roster is written at once"
    assert w.writes == 3


def test_writer_sequence_wraps_to_nonzero(tmp_path):
    w = InboxWriter(tmp_path / "in.txt", clock=lambda: 0.0)
    w.seq = 65535
    w.write("Alice", ["Bobby"], force=True)
    assert parse_text((tmp_path / "in.txt").read_text()).seq == 1


def test_writer_clear_removes_the_file(tmp_path):
    p = tmp_path / "in.txt"
    w = InboxWriter(p)
    w.write("Alice", ["Bobby"])
    w.clear()
    assert not p.exists()
    w.clear()                                       # clearing twice is fine
    assert w.write("Alice", ["Bobby"]) is True, "and a cleared writer writes again"


def test_writer_survives_a_directory_it_cannot_write(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    w = InboxWriter(blocker / "sub" / "in.txt")
    assert w.write("Alice", ["Bobby"]) is False
    assert w.errors == 1 and w.last_error


def test_character_binding_is_in_the_header(tmp_path):
    p = tmp_path / "in.txt"
    InboxWriter(p).write("Mage", ["Bobby"])
    assert parse_text(p.read_text()).character == "Mage"


# ------------------------------------------------------- agreement with the Lua parser

def lua_parse(path):
    out = subprocess.run([LUAJIT, os.path.join(ADDON, "tests", "inbox_parity.lua"), str(path)], capture_output=True, encoding="utf-8", errors="replace",
                         timeout=60, cwd=ADDON)
    assert out.returncode == 0, out.stderr
    res = {"names": [], "cfg": {}, "test": None, "ok": False}
    for line in out.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "ERR":
            return {"error": rest}
        if kind == "OK":
            seq, ts, character = rest.split(" ", 2)
            res.update(ok=True, seq=int(seq), ts=int(ts), character=character)
        elif kind == "CFG":
            k, _, v = rest.partition("=")
            res["cfg"][k] = float(v)
        elif kind == "NAMES":
            res["names"] = [n for n in rest.split(",") if n]
        elif kind == "TEST":
            res["test"] = rest
    return res


@pytest.mark.skipif(LUAJIT is None, reason="luajit is not installed")
@pytest.mark.parametrize("names,config,test", [
    (["Bobby", "Carol"], {"radius": 40, "interval": 0.5, "checks": 80, "report": 60}, None),
    ([], {}, None),
    (["Zoë", "Émile", "Bobby"], {"interval": 0.25}, "a message with spaces"),
    (["N%03d" % i for i in range(120)], {"checks": 80}, None),
    (["Bobby", "B", "x" * 30, "Carol", "Bobby"], {}, None),
], ids=["basic", "empty", "utf8", "large", "dirty"])
def test_lua_parser_reads_what_the_python_writer_wrote(tmp_path, names, config, test):
    p = tmp_path / "eqvoice-in.txt"
    p.write_bytes(build_text(4242, 1700000123, "Alice", names, config, test).encode("utf-8"))
    py = parse_text(p.read_text(encoding="utf-8"))
    lua = lua_parse(p)
    assert lua["ok"], lua
    assert (lua["seq"], lua["ts"], lua["character"]) == (py.seq, py.ts, py.character)
    assert lua["names"] == py.names
    assert lua["cfg"] == pytest.approx(py.config)
    assert lua["test"] == py.test


@pytest.mark.skipif(LUAJIT is None, reason="luajit is not installed")
@pytest.mark.parametrize("raw", [
    b"", b"garbage", b"OVI1 1 2 0000 Alice\n", b"OVI1 1 2 FFFF Alice\nR Bobby\n",
])
def test_lua_and_python_reject_the_same_files(tmp_path, raw):
    p = tmp_path / "eqvoice-in.txt"
    p.write_bytes(raw)
    with pytest.raises(InboxError):
        parse_text(raw.decode("utf-8", "replace"))
    assert "error" in lua_parse(p)
