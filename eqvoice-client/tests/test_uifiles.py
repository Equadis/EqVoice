"""The in-game panel's two files: status (app -> addon) and commands (addon -> app), and their agreement with the addon's Lua."""
import os
import shutil
import subprocess
import time

import pytest

from eqvoice_client.bridge.frame import crc16
from eqvoice_client.bridge.uifiles import (ControlReader, StatusWriter, UiFileError, build_control, build_status, control_path,
                                             file_suffix, parse_control, parse_status, status_path)

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.abspath(os.path.join(HERE, "..", "..", "eqvoice-addon"))
LUAJIT = shutil.which("luajit")

FIELDS = {"level": "ok", "mode": "open", "mute": 0, "deaf": 0, "tx": 1, "talk": 0, "vol": 80, "near": 2, "conn": 1, "key": "F9",
          "game": 1, "hub": 1, "mic": "ok", "app": "0.2.0"}
PEERS = [{"name": "Alice", "dist": 12.5, "connected": True, "speaking": True, "path": "direct", "state": "s"},
         {"name": "Bobby", "dist": None, "connected": False, "speaking": False, "path": "-"},
         {"name": "Carol", "dist": 40.0, "connected": True, "speaking": False, "path": "relay", "state": "d", "muted": True}]
MUTED = ["bobby", "carol"]


def status_text(**kw):
    args = dict(seq=7, ts=1700000000, character="Me", fields=FIELDS, headline="Ready: Me on Realm. 2 players in range, 1 connected.",
                more=[], peers=PEERS, muted=MUTED, devices=None, blocked=())
    args.update(kw)
    return build_status(args["seq"], args["ts"], args["character"], args["fields"], args["headline"], args["more"], args["peers"], args["muted"],
                        args["devices"], args["blocked"])


# ------------------------------------------------------------------- status

def test_status_round_trip():
    got = parse_status(status_text(more=["No voice window is connected."]))
    assert (got.seq, got.ts, got.character) == (7, 1700000000, "Me")
    assert got.fields["mode"] == "open" and got.fields["vol"] == "80" and got.fields["key"] == "F9"
    assert got.headline.startswith("Ready: Me on Realm") and got.more == ["No voice window is connected."]
    assert got.peers[0] == {"name": "Alice", "dist": 12.5, "connected": True, "speaking": True, "path": "direct", "state": "s", "muted": False, "blocked": False, "vol": 100}
    assert got.peers[1]["dist"] is None and got.peers[1]["connected"] is False
    assert got.peers[1]["state"] == "-" and got.peers[2]["state"] == "d" and got.peers[2]["muted"] is True
    assert got.muted == ["bobby", "carol"]


def test_a_player_row_from_an_older_app_has_no_state_and_is_still_read():
    text = "OVS1 7 1700000000 %04X Me\n" % crc16(b"S level=ok\nM hi\nP Alice 5.0 1 0 direct\n") + "S level=ok\nM hi\nP Alice 5.0 1 0 direct\n"
    got = parse_status(text)
    assert got.peers == [{"name": "Alice", "dist": 5.0, "connected": True, "speaking": False, "path": "direct", "state": "-", "muted": False, "blocked": False, "vol": 100}]


def test_a_state_letter_the_addon_does_not_know_becomes_unknown_and_the_mute_list_is_split_over_lines():
    got = parse_status(status_text(peers=[{"name": "Zed", "dist": 1.0, "connected": True, "speaking": False, "path": "direct", "state": "Q"}],
                                   muted=["n%02d" % i for i in range(20)]))
    assert got.peers[0]["state"] == "-"
    assert got.muted == ["n%02d" % i for i in range(20)]
    assert status_text(muted=["n%02d" % i for i in range(20)]).count("\nX ") == 3, "eight names to a line"


def test_status_header_crc_covers_exactly_the_body():
    head, body = status_text().split("\n", 1)
    assert head.split(" ")[3] == "%04X" % crc16(body.encode())


def test_values_and_text_are_made_safe_for_the_game():
    text = status_text(fields=dict(FIELDS, key="Mouse 4", mic="no=way|x"), headline="two\nlines | and a pipe", more=["a\r\nb"])
    got = parse_status(text)
    assert got.fields["key"] == "Mouse_4", "spaces cannot split a field"
    assert "=" not in got.fields["mic"] and "|" not in got.fields["mic"]
    assert "\n" not in got.headline and "|" not in got.headline, "'|' is the game's escape character"
    assert got.more == ["a b"]


def test_status_limits():
    peers = [{"name": "P%02d" % i, "dist": float(i), "connected": True, "speaking": False, "path": "direct"} for i in range(60)]
    got = parse_status(status_text(peers=peers, more=["x"] * 9, headline="h" * 400, muted=["m%02d" % i for i in range(90)]))
    assert len(got.peers) == 40 and len(got.more) == 4 and len(got.headline) == 160 and len(got.muted) == 60


@pytest.mark.parametrize("mutate,reason", [
    (lambda t: t.replace("OVS1", "OVS2", 1), "magic"),
    (lambda t: t.replace("Ready", "Rbady"), "crc"),
    (lambda t: t.split("\n")[0], "noheader"),
    (lambda t: "OVS1 x 5 0000 Me\n", "numbers"),
    (lambda t: "", "noheader"),
])
def test_status_damage_is_refused(mutate, reason):
    with pytest.raises(UiFileError) as e:
        parse_status(mutate(status_text()))
    assert str(e.value) == reason


def test_file_names_follow_the_roster_file():
    assert file_suffix("eqvoice-in.txt") == ".txt" and file_suffix("eqvoice-in-Alice.txt") == "-Alice.txt"
    assert status_path("C:/g/CustomData/eqvoice-in-Alice.txt").name == "eqvoice-st-Alice.txt"
    assert control_path("C:/g/CustomData/eqvoice-in.txt").name == "eqvoice-ctl.txt"


def test_status_writer_writes_changes_promptly_and_heartbeats(tmp_path):
    clock = {"t": 1000.0}
    w = StatusWriter(tmp_path / "eqvoice-st.txt", refresh_s=2.0, min_interval_s=0.4, clock=lambda: clock["t"])
    assert w.write("Me", FIELDS, "Ready", [], PEERS), "the first write always happens"
    s1 = parse_status((tmp_path / "eqvoice-st.txt").read_text()).seq
    assert not w.write("Me", FIELDS, "Ready", [], PEERS), "nothing changed, nothing written"
    clock["t"] += 0.1
    assert not w.write("Me", dict(FIELDS, mute=1), "Ready", [], PEERS), "a change inside the minimum interval waits"
    clock["t"] += 0.4
    assert w.write("Me", dict(FIELDS, mute=1), "Ready", [], PEERS), "and goes out once it has passed"
    assert parse_status((tmp_path / "eqvoice-st.txt").read_text()).fields["mute"] == "1"
    clock["t"] += 2.1
    assert w.write("Me", dict(FIELDS, mute=1), "Ready", [], PEERS), "the same status is rewritten as a heartbeat"
    s3 = parse_status((tmp_path / "eqvoice-st.txt").read_text()).seq
    assert s3 != s1 and w.writes == 3, "the sequence number moves on every write"
    w.clear()
    assert not (tmp_path / "eqvoice-st.txt").exists()


def test_a_write_error_is_counted_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    w = StatusWriter(blocker / "sub" / "eqvoice-st.txt")
    assert w.write("Me", FIELDS, "Ready") is False and w.errors == 1 and w.last_error


# ------------------------------------------------------------------ control

def ids(n, base=None):
    base = int((base if base is not None else time.time()) * 100)
    return [base + i for i in range(n)]


def test_control_round_trip():
    a, b = ids(2)
    text = build_control(3, 1700000000, "Me", [(a, "mode", "ptt"), (b, "volume", "75")])
    got = parse_control(text)
    assert (got.seq, got.wall, got.character) == (3, 1700000000, "Me")
    assert got.commands == [(a, "mode", "ptt"), (b, "volume", "75")]
    head, body = text.split("\n", 1)
    assert head.split(" ")[3] == "%04X" % crc16(body.encode())


def test_control_keeps_the_newest_eight():
    cmds = [(i, "test", "1") for i in ids(12)]
    assert parse_control(build_control(1, 1, "Me", cmds)).commands == cmds[-8:]


@pytest.mark.parametrize("mutate,reason", [
    (lambda t: t.replace("OVC1", "OVC9", 1), "magic"),
    (lambda t: t.replace("mute 1", "mute 0"), "crc"),
    (lambda t: t.split("\n")[0], "noheader"),
])
def test_control_damage_is_refused(mutate, reason):
    with pytest.raises(UiFileError) as e:
        parse_control(mutate(build_control(1, 1, "Me", [(1, "mute", "1")])))
    assert str(e.value) == reason


def test_control_reader_hands_each_command_out_once(tmp_path):
    p = tmp_path / "eqvoice-ctl.txt"
    r = ControlReader(p, since=time.time())
    assert r.poll() == [], "no file yet"
    a, b, c = ids(3, time.time() + 1)
    p.write_text(build_control(1, 1, "Me", [(a, "mode", "ptt")]))
    assert r.poll() == [(a, "mode", "ptt")]
    assert r.poll() == [], "the same file is not read twice"
    p.write_text(build_control(2, 1, "Me", [(a, "mode", "ptt"), (b, "mute", "1"), (c, "volume", "40")]))
    assert r.poll() == [(b, "mute", "1"), (c, "volume", "40")], "only what is new"
    p.write_text(build_control(3, 1, "Me", [(b, "mute", "1"), (c, "volume", "40")]))
    assert r.poll() == [], "an older id never replays"
    assert r.applied == 3


def test_a_file_left_from_an_earlier_session_is_ignored(tmp_path):
    p = tmp_path / "eqvoice-ctl.txt"
    old = ids(1, time.time() - 3600)[0]
    p.write_text(build_control(1, 1, "Me", [(old, "mute", "1")]))
    r = ControlReader(p, since=time.time())
    assert r.poll() == [], "an hour-old command is not replayed when the app starts"
    new = ids(1, time.time() + 1)[0]
    p.write_text(build_control(2, 1, "Me", [(old, "mute", "1"), (new, "mode", "open")]))
    assert r.poll() == [(new, "mode", "open")]


def test_a_torn_file_is_read_again_not_skipped(tmp_path):
    p = tmp_path / "eqvoice-ctl.txt"
    r = ControlReader(p, since=time.time())
    cid = ids(1, time.time() + 1)[0]
    good = build_control(1, 1, "Me", [(cid, "mute", "1")])
    p.write_text(good[:-4])                           # the game was half way through writing it
    assert r.poll() == [] and r.errors == 1
    p.write_text(good)
    assert r.poll() == [(cid, "mute", "1")], "the finished file is picked up"


# ----------------------------------------------------- the addon's own Lua

def run_lua(script, *args):
    r = subprocess.run([LUAJIT, os.path.join(ADDON, "tests", script), *args], capture_output=True, encoding="utf-8", errors="replace",
                       cwd=os.path.join(ADDON, "tests"))
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout.splitlines()


@pytest.mark.skipif(LUAJIT is None, reason="luajit is not installed")
def test_the_addon_reads_what_python_writes(tmp_path):
    f = tmp_path / "st.txt"
    f.write_text(status_text(fields=dict(FIELDS, key="Mouse 4"), more=["one", "two"]), encoding="utf-8", newline="\n")
    out = run_lua("status_parity.lua", str(f))
    assert out[0] == "OK 7 1700000000 Me"
    assert "S key=Mouse_4" in out and "S vol=80" in out and "S mode=open" in out
    assert "M Ready: Me on Realm. 2 players in range, 1 connected." in out
    assert [ln for ln in out if ln.startswith("N ")] == ["N one", "N two"]
    assert [ln for ln in out if ln.startswith("P ")] == ["P Alice 12.5 1 1 direct s 0", "P Bobby - 0 0 - - 0", "P Carol 40.0 1 0 relay d 1"]
    assert "X bobby carol" in out, "the mute list"
    f.write_text(status_text().replace("Ready", "Rbady"), encoding="utf-8", newline="\n")
    assert run_lua("status_parity.lua", str(f))[0] == "ERR crc", "Lua refuses a torn file for the same reason Python does"


def test_a_loud_voice_is_a_letter_both_ends_know_and_so_is_how_you_sound(tmp_path):
    row = lambda who, st: {"name": who, "dist": 3.0, "connected": True, "speaking": True, "path": "direct", "state": st}      # noqa: E731
    text = status_text(fields=dict(FIELDS, vl="2"), peers=[row("Ann", "o"), row("Bea", "s"), row("Cat", "l")])
    got = parse_status(text)
    assert [p["state"] for p in got.peers] == ["o", "s", "l"] and got.fields["vl"] == "2"
    if LUAJIT is not None:
        f = tmp_path / "st.txt"
        f.write_text(text, encoding="utf-8", newline="\n")
        out = run_lua("status_parity.lua", str(f))
        assert [ln for ln in out if ln.startswith("P ")] == ["P Ann 3.0 1 1 direct o 0", "P Bea 3.0 1 1 direct s 0", "P Cat 3.0 1 1 direct l 0"], out
        assert "S vl=2" in out, "the game reads how you sound"


def test_the_lists_of_microphones_and_speakers_go_to_the_game_as_lines_with_safe_names(tmp_path):
    devices = {"in": ["Default - Microphone (2- AT2020USB+) (0909:001c)", "Headset | mic\nwith  odd   spacing", "X" * 100],
               "out": ["Speakers (Realtek(R) Audio)"] + ["Output %d" % i for i in range(2, 12)]}
    text = status_text(devices=devices)
    got = parse_status(text)
    assert got.devices["in"][0] == "Default - Microphone (2- AT2020USB+) (0909:001c)"
    assert got.devices["in"][1] == "Headset / mic with odd spacing", "one line, the game's chat escape gone, spaces tidied: %r" % got.devices["in"][1]
    assert len(got.devices["in"][2]) <= 60 and got.devices["in"][2].endswith("\u2026"), "a long name is cut"
    assert len(got.devices["out"]) == 8, "eight of each are listed"
    assert parse_status(status_text()).devices == {"in": [], "out": []}, "an older file has none"
    if LUAJIT is not None:
        f = tmp_path / "st.txt"
        f.write_text(text, encoding="utf-8", newline="\n")
        out = run_lua("status_parity.lua", str(f))
        assert "D in 1 Default - Microphone (2- AT2020USB+) (0909:001c)" in out, "the game reads a name with spaces and brackets whole"
        assert "D in 2 Headset / mic with odd spacing" in out
        assert [ln for ln in out if ln.startswith("D out ")][-1] == "D out 8 Output 8" and len([ln for ln in out if ln.startswith("D out ")]) == 8


@pytest.mark.skipif(LUAJIT is None, reason="luajit is not installed")
def test_python_reads_what_the_addon_writes(tmp_path):
    out = tmp_path / "ctl.txt"
    assert run_lua("control_parity.lua", str(out), "Alice", "mode", "ptt", "mute", "1", "volume", "75", "test", "1") == ["OK"]
    got = parse_control(out.read_text(encoding="utf-8"))
    assert got.character == "Alice"
    assert [(c, a) for _, c, a in got.commands] == [("mode", "ptt"), ("mute", "1"), ("volume", "75"), ("test", "1")]
    cids = [i for i, _, _ in got.commands]
    assert cids == sorted(set(cids)) and cids[0] >= 1700000000 * 100, "ids increase and start from the clock"


def test_a_player_row_carries_whether_they_are_blocked_and_their_volume_and_the_block_list_and_defaults_are_read():
    peers = [{"name": "Alice", "dist": 5.0, "connected": True, "speaking": False, "path": "direct", "state": "o", "muted": False, "blocked": True, "vol": 1.5},
             {"name": "Bob", "dist": 9.0, "connected": True, "speaking": False, "path": "direct", "state": "o", "vol": 7.0}]
    got = parse_status(status_text(peers=peers, blocked=["alice", "zed"], devices={"in": ["AT2020"], "out": ["Realtek"], "default": {"in": "AT2020", "out": "Realtek (R) Audio"}}))
    assert got.peers[0]["blocked"] is True and got.peers[0]["vol"] == 150
    assert got.peers[1]["blocked"] is False and got.peers[1]["vol"] == 200, "a volume is kept between 0 and 200%"
    assert got.blocked == ["alice", "zed"]
    assert got.devices == {"in": ["AT2020"], "out": ["Realtek"]} and got.defaults == {"in": "AT2020", "out": "Realtek (R) Audio"}


@pytest.mark.parametrize("label,plain", [
    ("Default - Microphone (2- AT2020USB+) (0909:001c)", "AT2020USB+"),
    ("Microphone (Steam Streaming Microphone)", "Steam Streaming Microphone"),
    ("Microphone (NexiGo N60 FHD Webcam Audio) (1d6c:0103)", "NexiGo N60 FHD Webcam Audio"),
    ("Speakers (Realtek(R) Audio)", "Realtek(R) Audio"),
    ("Communications - Line (2- USB AUDIO  CODEC) (08bb:29c2)", "USB AUDIO CODEC"),
    ("CU34G2XP (4- NVIDIA High Definition Audio)", "CU34G2XP (NVIDIA High Definition Audio)"),
    ("Realtek Digital Output (Realtek(R) Audio)", "Realtek Digital Output (Realtek(R) Audio)"),
])
def test_a_device_is_shown_by_its_own_name_without_the_kind_wrapped_round_it(label, plain):
    from eqvoice_client.bridge.uifiles import clean_device_label
    assert clean_device_label(label) == plain


def test_the_systems_default_and_communications_entries_are_not_devices_of_their_own_and_the_default_names_what_it_is():
    from eqvoice_client.bridge.uifiles import default_device_name, game_devices
    devs = [{"id": "default", "label": "Default - Microphone (2- AT2020USB+) (0909:001c)"},
            {"id": "communications", "label": "Communications - Microphone (2- AT2020USB+) (0909:001c)"},
            {"id": "a", "label": "Microphone (2- AT2020USB+) (0909:001c)"},
            {"id": "b", "label": "Microphone (Steam Streaming Microphone)"}]
    assert game_devices(devs) == [("a", "AT2020USB+"), ("b", "Steam Streaming Microphone")]
    assert default_device_name(devs) == "AT2020USB+"
    assert game_devices([{"id": "x", "label": "Mic"}, {"id": "y", "label": "Mic"}]) == [("x", "Mic"), ("y", "Mic (2)")], "two of one name are told apart"


def test_the_direction_file_is_read_as_degrees_from_straight_ahead_and_damage_is_left_out():
    from eqvoice_client.bridge.uifiles import parse_direction
    got = parse_direction("OVD1 3\nAlice 90\nBob -170\nCarol 190\nbad x\nDave 9999\n")
    assert got["alice"] == 90 and got["bob"] == -170 and abs(got["carol"] - (-170)) < 1e-6, "an angle is brought into -180..180"
    assert "bad" not in got and "dave" not in got
    assert parse_direction("garbage") == {} and parse_direction("") == {}
