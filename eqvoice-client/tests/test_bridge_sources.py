"""Monitor (fail-closed), file source, config and identity."""
import asyncio
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from helpers import sample_state
from eqvoice_client import identity
from eqvoice_client.bridge import frame
from eqvoice_client.bridge.filesource import FileSource, candidate_paths, parse_line
from eqvoice_client.bridge.frame import FrameError, encode_state, make_state
from eqvoice_client.bridge.source import BridgeMonitor
from eqvoice_client.config import Config
from eqvoice_signalling.hub import b64u, registration_message, unb64u


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def st(counter, results=1, **kw):
    return sample_state(counter=counter, results=results, **kw)


# ---------------------------------------------------------------- monitor

def test_monitor_live_stale_lost():
    c = Clock()
    m = BridgeMonitor(stale_s=4, hold_s=8, clock=c)
    assert m.view().status == "none" and not m.view().usable
    assert m.push(st(1))
    v = m.view()
    assert v.status == "live" and v.usable and [r.name for r in v.results] == ["Bobby"]
    assert v.identity == ("Testrealm", "Alice", 77)
    c.t += 5
    v = m.view()
    assert v.status == "stale" and v.usable and len(v.results) == 1, "an alt-tab keeps the conversation going briefly"
    c.t += 4
    v = m.view()
    assert v.status == "lost" and not v.usable and v.results == (), "fail closed: nobody is audible once the bridge is dead"
    assert "no frames" in v.reason
    assert v.identity == ("Testrealm", "Alice", 77), "identity survives, only readings are dropped"


def test_monitor_same_counter_is_not_progress():
    c = Clock()
    m = BridgeMonitor(clock=c)
    assert m.push(st(7))
    c.t += 3
    assert not m.push(st(7)), "the optical source decodes the same frame repeatedly"
    c.t += 3
    assert m.view().status == "stale", "a frozen counter means a frozen game"
    assert m.push(st(8))
    assert m.view().status == "live"


def test_monitor_a_new_login_with_the_same_counter_is_progress():
    m = BridgeMonitor(clock=Clock())
    assert m.push(st(1, login_id=5))
    assert m.push(st(1, login_id=6)), "counter restarts from 1 in a new game session"


@pytest.mark.parametrize("flags,reason", [
    (frame.FLAG_FILE | frame.FLAG_ROSTER | frame.FLAG_LOOKUPS, "switched off"),
    (frame.FLAG_FILE | frame.FLAG_ENABLED | frame.FLAG_ROSTER, "look players up"),
    (frame.FLAG_FILE | frame.FLAG_ENABLED | frame.FLAG_ROSTER | frame.FLAG_LOOKUPS | frame.FLAG_LOGOUT, "logging out"),
    (frame.FLAG_FILE | frame.FLAG_ENABLED | frame.FLAG_ROSTER | frame.FLAG_LOOKUPS | frame.FLAG_ID_MISMATCH, "another character"),
])
def test_monitor_refuses_to_use_readings_the_game_flags(flags, reason):
    m = BridgeMonitor(clock=Clock())
    m.push(st(1, flags=flags))
    v = m.view()
    assert v.status == "live" and not v.usable and v.results == () and reason in v.reason


def test_monitor_logout_withdraws_the_identity():
    m = BridgeMonitor(clock=Clock())
    m.push(st(1, flags=frame.FLAG_FILE | frame.FLAG_ENABLED | frame.FLAG_LOGOUT))
    assert m.view().identity is None


def test_monitor_measures_the_frame_interval():
    c = Clock()
    m = BridgeMonitor(clock=c)
    for i in range(1, 12):
        m.push(st(i))
        c.t += 0.5
    assert m.view().interval_s == pytest.approx(0.5, abs=0.01)


# ------------------------------------------------------------------- file

def line(state):
    return "OVB2 " + encode_state(state).hex().upper() + "\n"


def test_parse_line():
    s = st(3, results=3)
    assert parse_line(line(s)) == s
    for bad in ["", "OVB1 00", "OVB2 zz", "OVB2 " + encode_state(s).hex()[:-2], "OVB2 " + encode_state(s).hex()[:-2] + "00"]:
        with pytest.raises(FrameError):
            parse_line(bad)


def test_candidate_paths(tmp_path):
    assert candidate_paths("") == []
    p = candidate_paths(str(tmp_path))
    assert [x.parent.name for x in p] == ["CustomData", "Imports"]
    assert candidate_paths(str(tmp_path), str(tmp_path / "x.txt")) == [tmp_path / "x.txt"]


def test_file_source_reads_newest_valid_and_survives_torn_writes(tmp_path):
    (tmp_path / "CustomData").mkdir()
    (tmp_path / "Imports").mkdir()
    fs = FileSource(candidate_paths(str(tmp_path)))
    assert fs.poll_once() is None and fs.status["searching"]
    a = tmp_path / "CustomData" / "eqvoice.txt"
    a.write_text(line(st(3)), encoding="utf-8")
    got = fs.poll_once()
    assert got.counter == 3 and fs.status["path"] == str(a) and not fs.status["searching"]
    a.write_text("OVB2 garbage", encoding="utf-8")        # torn / partial write
    assert fs.poll_once() is None
    assert fs.status["fail"] == 1 and "eqvoice.txt" in fs.status["last_error"]
    a.write_text(line(st(4))[:-20], encoding="utf-8")      # cut in half
    assert fs.poll_once() is None
    a.write_text(line(st(5)), encoding="utf-8")
    assert fs.poll_once().counter == 5


def test_file_source_knows_where_the_roster_file_goes(tmp_path):
    (tmp_path / "CustomData").mkdir()
    (tmp_path / "Imports").mkdir()
    fs = FileSource(candidate_paths(str(tmp_path)))
    assert fs.inbox_path == tmp_path / "CustomData" / "eqvoice-in.txt", "before the addon writes: the CustomData default"
    (tmp_path / "Imports" / "eqvoice.txt").write_text(line(st(1)), encoding="utf-8")
    fs.poll_once()
    assert fs.inbox_path == tmp_path / "Imports" / "eqvoice-in.txt", "beside whichever file the addon is actually producing"


def test_file_source_run_emits(tmp_path):
    p = tmp_path / "eqvoice.txt"
    p.write_text(line(st(9)), encoding="utf-8")

    async def go():
        fs = FileSource([p], poll_hz=50)
        got = []
        t = asyncio.create_task(fs.run(got.append))
        await asyncio.sleep(0.2)
        t.cancel()
        return got
    got = asyncio.run(go())
    assert got and got[0].counter == 9


# ---------------------------------------------------------- config/identity

def test_config_defaults_merge_save_and_redact(tmp_path):
    cfg = Config.load(tmp_path)
    assert cfg.bridge.corner == "TOPLEFT" and cfg.ui.port == 17420 and cfg.audio.mode == "open", "open mic is the default"
    assert cfg.proximity.say_radius == 41.0 and cfg.proximity.full_radius == 30.0 and cfg.roster.max_checks == "auto"
    assert cfg.proximity.peak_radius == 85.0 and cfg.audio.boost == "normal" and cfg.proximity.report_radius == 95.0 and cfg.roster.report == 100.0
    assert cfg.proximity.loud_full_radius == 45.0 and cfg.proximity.curve_power == 1.6 and cfg.audio.auto_level is True
    cfg.audio.mode = "ptt"
    cfg.join_code = "hunter2"
    cfg.save()
    again = Config.load(tmp_path)
    assert again.audio.mode == "ptt" and again.bridge.poll_hz == 10, "unspecified keys keep their defaults"
    assert again.redacted()["join_code"] == "***"
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    broken = Config.load(tmp_path)
    assert broken.audio.mode == "open" and (tmp_path / "config.json.bad").exists(), "a corrupt file is kept, not fatal"
    over = Config.load(tmp_path, {"bridge": {"source": "sim"}})
    assert over.bridge.source == "sim" and over.bridge.corner == "TOPLEFT"


def test_identity_per_game_login_and_signature(tmp_path):
    t = [1000.0]
    a = identity.load_or_create(tmp_path, "Testrealm", "Alice", 11, now=lambda: t[0])
    assert len(unb64u(a.sid)) == 16, "22 base64url characters = 128 random bits"
    b = identity.load_or_create(tmp_path, "Testrealm", "Alice", 11, now=lambda: t[0] + 60)
    assert (a.sid, a.public_b64) == (b.sid, b.public_b64), "the same game login: a restart keeps its id and key"
    for other in [("Testrealm", "Bobby", 11), ("Testrealm", "Alice", 12), ("Otherrealm", "Alice", 11)]:
        c = identity.load_or_create(tmp_path, *other, now=lambda: t[0] + 61)
        assert c.sid != a.sid and c.public_b64 != a.public_b64, "a different character, login or realm gets a new identity: %s" % (other,)
    d = identity.load_or_create(tmp_path, "Otherrealm", "Alice", 11, now=lambda: t[0] + 61 + 3600)
    assert d.sid != c.sid, "old identities are not kept"
    # the signature verifies the way the hub verifies it
    nonce = b"0123456789abcdef"
    pub, sig = d.registration(b64u(nonce))
    Ed25519PublicKey.from_public_bytes(unb64u(pub)).verify(unb64u(sig), registration_message(nonce, d.sid, "Otherrealm", "Alice"))


def test_identity_token_persists_and_logout_forgets(tmp_path):
    a = identity.load_or_create(tmp_path, "Testrealm", "Alice", 11)
    a.token = "tok" * 8
    identity.save(tmp_path, a)
    assert identity.load_or_create(tmp_path, "Testrealm", "Alice", 11).token == "tok" * 8, "resume token survives a restart"
    identity.forget(tmp_path)
    assert identity.peek(tmp_path) is None
    assert identity.load_or_create(tmp_path, "Testrealm", "Alice", 11).sid != a.sid, "after a clean logout nothing is reused"
    identity.forget(tmp_path)
    identity.forget(tmp_path)                                  # forgetting twice is fine
