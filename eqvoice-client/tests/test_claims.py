"""Two game windows on one PC, two copies of EqVoice, and never the same character twice."""
import os
import time

import pytest

from eqvoice_client.bridge.claims import Claims
from eqvoice_client.bridge.filesource import ClaimingFileSource, is_bridge_file

FRAME = "OVB2 0200BE00262F4930\nOVT 1 1792965316 something\n"


def prefix():
    return "Local\\EqVoice-test-%d-%d-" % (os.getpid(), id(object()))


def game(tmp_path, *names, age=0.0, plain=False):
    data = tmp_path / "CustomData"
    data.mkdir(exist_ok=True)
    for n in names:
        p = data / ("eqvoice-%s.txt" % n)
        p.write_text(FRAME, encoding="utf-8")
        os.utime(p, (time.time() - age, time.time() - age))
    if plain:
        p = data / "eqvoice.txt"
        p.write_text(FRAME, encoding="utf-8")
        os.utime(p, (time.time() - age, time.time() - age))
    return data


def test_a_claim_is_held_until_it_is_given_back_and_by_one_copy_only():
    pre = prefix()
    a, b = Claims(pre), Claims(pre)
    assert a.try_claim("Nue") and a.try_claim("nue"), "claiming again is fine; names are not case sensitive"
    assert not b.try_claim("Nue") and b.taken_by_other("Nue") and not a.taken_by_other("Nue")
    assert b.try_claim("Equavoice"), "another character is free"
    a.release("Nue")
    assert b.try_claim("Nue") and not b.taken_by_other("Nue")
    a.release_all()
    b.release_all()
    assert Claims(pre).try_claim("Nue") and Claims(pre).try_claim("Equavoice"), "released claims are free again"


def test_two_copies_each_take_a_different_logged_in_character(tmp_path):
    game(tmp_path, "Nue", "Equavoice")
    pre = prefix()
    one, two = Claims(pre), Claims(pre)
    s1, s2 = ClaimingFileSource(str(tmp_path), claims=one), ClaimingFileSource(str(tmp_path), claims=two)
    assert s1.poll_once() is not None or s1.claimed, "the first copy takes a character"
    assert s2.poll_once() is not None or s2.claimed
    assert {s1.claimed, s2.claimed} == {"Equavoice", "Nue"}, "...and the second takes the other: %r %r" % (s1.claimed, s2.claimed)
    assert s1.character == s1.claimed and s2.character == s2.claimed
    assert s1.inbox_path.name == "eqvoice-in-%s.txt" % s1.claimed, "each copy writes the roster file for its own character"
    s3 = ClaimingFileSource(str(tmp_path), claims=Claims(pre))
    assert s3.poll_once() is None and s3.claimed == "", "a third copy has nothing left to take"
    assert sorted(s3.status["taken_by_others"]) == ["Equavoice", "Nue"], "and knows why"
    one.release_all()
    two.release_all()


def test_a_game_that_is_not_writing_is_not_a_game(tmp_path):
    game(tmp_path, "Old", age=300)                                  # a character from earlier today: the file is five minutes old
    game(tmp_path, "Nue")
    src = ClaimingFileSource(str(tmp_path), claims=Claims(prefix()))
    assert src.fresh_characters() == ["Nue"]
    src.poll_once()
    assert src.claimed == "Nue"
    src.claims.release_all()


def test_the_apps_own_files_are_not_characters(tmp_path):
    data = game(tmp_path, "Nue")
    for name, body in (("eqvoice-in.txt", "OVI1 5 1 ABCD Me\n"), ("eqvoice-st.txt", "OVS1 5 1 ABCD Me\n"), ("eqvoice-ctl.txt", "OVC1 5 1 ABCD Me\n")):
        (data / name).write_text(body, encoding="utf-8")
    (data / "eqvoice-in-Nue.txt").write_text("OVI1 5 1 ABCD Nue\n", encoding="utf-8")
    src = ClaimingFileSource(str(tmp_path), claims=Claims(prefix()))
    assert src.fresh_characters() == ["Nue"]
    assert is_bridge_file(data / "eqvoice-Nue.txt") and not is_bridge_file(data / "eqvoice-in.txt")


def test_a_claimed_character_is_let_go_when_its_game_goes_quiet_and_another_can_be_taken(tmp_path):
    data = game(tmp_path, "Nue")
    pre = prefix()
    src = ClaimingFileSource(str(tmp_path), claims=Claims(pre))
    src.RELEASE_S = 5
    src.poll_once()
    assert src.claimed == "Nue"
    old = time.time() - 60
    os.utime(data / "eqvoice-Nue.txt", (old, old))                  # the game closed a minute ago
    game(tmp_path, "Equavoice")                                      # and someone else logged in
    src.poll_once()
    assert src.claimed == "", "Nue was let go"
    assert Claims(pre).try_claim("Nue"), "so another copy could now take Nue"
    src.poll_once()
    assert src.claimed == "Equavoice", "and this copy takes the one that is playing"
    src.claims.release_all()


def test_a_character_without_a_per_character_file_falls_back_to_the_shared_file(tmp_path):
    """Names with accents or digits get no per-character file from the addon: the shared eqvoice.txt is all there is."""
    game(tmp_path, plain=True)
    src = ClaimingFileSource(str(tmp_path), claims=Claims(prefix()))
    src.PLAIN_AFTER_S = 0.0
    src.poll_once()
    assert src.claimed == "" or src.claimed == "*"
    src.poll_once()
    assert src.claimed == "*" and src.character == "" and src.paths[0].name == "eqvoice.txt"
    src.claims.release_all()
    quiet = ClaimingFileSource(str(tmp_path / "nowhere"), claims=Claims(prefix()))
    assert quiet.poll_once() is None and quiet.claimed == "", "no game folder, no claim, no crash"


def test_the_exe_flow_asks_for_claiming_and_the_command_line_does_not(tmp_path):
    from eqvoice_client.app import make_source
    from eqvoice_client.bridge.filesource import FileSource
    from eqvoice_client.config import Config
    cfg = Config.load(tmp_path)
    cfg.bridge.wow_dir = str(tmp_path)
    cfg.bridge.source = "file"
    assert type(make_source(cfg)) is FileSource, "by default a copy reads the shared file, as before"
    cfg.bridge.claim = True
    assert isinstance(make_source(cfg), ClaimingFileSource)
    cfg.bridge.character = "Nue"
    assert type(make_source(cfg)) is FileSource, "a copy told which character it is for does not claim"
