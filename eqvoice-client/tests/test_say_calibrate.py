"""tools/say_calibrate.py: it must fit the /say cutoff from both sides' logs, from one side's, and from the addon's real words."""
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pipeline_report as pr  # noqa: E402
import say_calibrate as sc  # noqa: E402

LUAJIT = shutil.which("luajit")
T = [0.0]


def stamp(dt=0.0):
    T[0] += dt
    s = T[0]
    return "2026-10-17 03:%02d:%02d,%03d" % (int(s) // 60 % 60, int(s) % 60, int((s % 1) * 1000))


def line(who, text):
    return "%s INFO  eqvoice.trace: TRACE %s %s" % (stamp(), who, text)


def log(label, lines):
    return sc.Log(label, pr.parse("\n".join(lines)))


def walk(n=70):
    """Distances for n messages: out from 5 to 85 yd and back again."""
    out = [5 + 80.0 * i / (n // 2) for i in range(n // 2)]
    return out + out[::-1]


def session(name_a, name_b, dists, rng, cutoff=40.0, flip=0.0, garble=False, numbers=True, start_seq=1):
    """(sender lines, receiver lines) for one calibration: A speaks every 2 s while B is `dists[i]` yards away."""
    a = [line("desktop", "identity character=%s realm=R login=1" % name_a), line("addon", "saycal start with %s, one /say every 2.0 s" % name_b)]
    b = [line("desktop", "identity character=%s realm=R login=1" % name_b), line("addon", "saycal start with %s, one /say every 2.0 s" % name_a)]
    last_heard = None
    for i, d in enumerate(dists):
        seq = start_seq + i
        T[0] += 2.0
        a.append(line("addon", "saycal sent %d to %s at %.1f yd" % (seq, name_b, d)))
        heard = (d <= cutoff) and not (rng.random() < flip)        # `flip` = lines lost inside the range (never heard from outside it)
        if heard:
            T[0] += 0.3
            if garble:
                b.append(line("addon", "saycal heard ? from %s at %.1f yd (text not recognised, language Orcish)" % (name_a, d)))
            else:
                b.append(line("addon", "saycal heard %d from %s at %.1f yd" % (seq, name_a, d)))
                if last_heard is not None and numbers:
                    for k in range(last_heard + 1, seq):
                        b.append(line("addon", "saycal skipped %d from %s at %.1f yd" % (k, name_a, dists[k - start_seq])))
                last_heard = seq
    a.append(line("addon", "saycal stop: sent %d, heard 0, skipped 0" % len(dists)))
    T[0] += 0.4
    b.append(line("addon", "saycal stop: sent 0, heard 0, skipped 0"))
    return a, b


def test_both_sides_fit_the_cutoff_even_with_noise():
    rng = random.Random(7)
    a, b = session("Alice", "Bobby", walk(), rng, cutoff=40.0, flip=0.03)
    a2, b2 = session("Alice", "Bobby", walk(), rng, cutoff=40.0, flip=0.03, start_seq=1)       # a second pass, numbers restart at 1
    logs = [log("Alice", a + a2), log("Bobby", b + b2)]
    samples = sc.samples_from(logs)
    assert len(samples) == 140
    f = sc.fit(samples)
    assert 38.0 <= f["range"] <= 42.0, f
    assert f["errors"] <= 0.08 * f["n"], "the lost lines are counted as dropouts"
    out = sc.render(samples, f)
    assert "/say carried to about 4" in out or "/say carried to about 39" in out or "/say carried to about 38" in out, out
    rec = sc.recommendation(f["range"])
    assert rec["say_radius"] == round(f["range"]) and "full_radius" not in rec, "how far a voice is at full volume is not tied to /say"


def test_two_passes_with_restarting_numbers_are_not_mixed_up():
    rng = random.Random(1)
    a1, b1 = session("Alice", "Bobby", [10, 20, 30, 50, 60], rng, cutoff=40.0)
    a2, b2 = session("Alice", "Bobby", [12, 24, 36, 48, 70], rng, cutoff=40.0)
    samples = sc.samples_from([log("Alice", a1 + a2), log("Bobby", b1 + b2)])
    assert len(samples) == 10
    assert all(s["heard"] == (s["dist"] <= 40) for s in samples)


def test_garbled_text_from_the_other_faction_is_matched_by_time():
    rng = random.Random(3)
    a, b = session("Alice", "Bobby", walk(), rng, cutoff=35.0, garble=True)
    samples = sc.samples_from([log("Alice", a), log("Bobby", b)])
    assert samples and all(s["how"] != "receiver only" for s in samples)
    f = sc.fit(samples)
    assert 33.0 <= f["range"] <= 37.0, f
    assert any("matched by time" in s["how"] for s in samples)


def test_a_receiver_alone_still_gives_an_estimate_from_its_own_heard_and_skipped_lines():
    rng = random.Random(5)
    a, b = session("Alice", "Bobby", walk(), rng, cutoff=40.0)
    samples = sc.samples_from([log("Bobby", b)])                         # Alice's log is missing
    assert samples and all(s["how"].startswith("receiver only") for s in samples)
    f = sc.fit(samples)
    assert 36.0 <= f["range"] <= 44.0, f


def test_warnings_when_the_data_cannot_show_the_edge():
    rng = random.Random(2)
    a, b = session("Alice", "Bobby", [5, 10, 15, 20, 25], rng, cutoff=40.0)
    s = sc.samples_from([log("Alice", a), log("Bobby", b)])
    out = sc.render(s, sc.fit(s))
    assert "no line was missed beyond that" in out and "lower bound" in out
    a, b = session("Alice", "Bobby", [50, 60, 70], rng, cutoff=40.0)
    s = sc.samples_from([log("Alice", a), log("Bobby", b)])
    assert "not heard at all" in sc.render(s, sc.fit(s))
    assert "nothing to fit" in sc.render([], None)


def test_a_wide_radius_warns_about_the_hubs_link_radius():
    samples = [{"dist": d, "heard": d <= 60, "how": "x"} for d in range(5, 90, 2)]
    out = sc.render(samples, sc.fit(samples))
    assert "raise it to at least 63" in out and "say_radius 60" in out


def test_the_default_config_can_be_updated(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text('    "proximity": {\n        "say_radius": 40.0,       # yards\n        "full_radius": 10.0,      # yards\n        "other": 1.0,\n    },\n')
    msg = sc.apply_default(28.4, cfg)
    text = cfg.read_text()
    assert '"say_radius": 28.0,' in text and '"full_radius": 10.0,' in text and '"other": 1.0' in text and "wrote" in msg, "full_radius is left alone"
    assert "already" in sc.apply_default(28.2, cfg), "idempotent"


@pytest.mark.skipif(LUAJIT is None, reason="luajit is not installed")
def test_the_real_addons_words_are_understood():
    out = subprocess.run([LUAJIT, str(ROOT / "eqvoice-addon" / "tests" / "say_sample.lua")], capture_output=True, encoding="utf-8",
                         errors="replace", cwd=str(ROOT / "eqvoice-addon"), timeout=60)
    assert out.returncode == 0, out.stderr
    lines = [l[len("TRACE "):] for l in out.stdout.splitlines() if l.startswith("TRACE ")]
    assert any(l.startswith("saycal start with Bobby") for l in lines) and any(l.startswith("saycal sent 1 to Bobby at 20.0 yd") for l in lines)
    text = "\n".join(line("desktop", "identity character=Me realm=R login=1") if i == 0 else line("addon", l) for i, l in enumerate(["x"] + lines))
    me = sc.Log("Me", pr.parse(text))
    s = me.sessions[0]
    assert s["partner"] == "Bobby" and len(s["sent"]) >= 4
    assert [h["seq"] for h in s["heard"]] == [1, 2, 4, None], s["heard"]
    assert [(k["seq"], k["who"]) for k in s["skipped"]] == [(3, "Bobby")], s["skipped"]
    assert s["heard"][0]["dist"] == 22.0 and s["heard"][2]["dist"] == 42.0 and s["skipped"][0]["dist"] is not None


def test_a_character_that_cannot_speak_can_still_be_the_listener():
    """Dgawdawdad has no /say permission: it only listens (`/ev say listen`), Testststsets speaks. The two logs join as usual."""
    rng = random.Random(11)
    a, b = session("Testststsets", "Dgawdawdad", walk(), rng, cutoff=40.0)
    b[1] = line("addon", "saycal start with Testststsets, listening only (this character does not speak)")
    sender, listener = log("Testststsets", a), log("Dgawdawdad", b)
    assert listener.sessions[0]["partner"] == "Testststsets" and not listener.sessions[0]["sent"]
    samples = sc.samples_from([sender, listener])
    assert len(samples) == len(walk()) and all(s["how"] == "matched by number" for s in samples)
    f = sc.fit(samples)
    assert 39.0 <= f["range"] <= 41.0, f
    # a muted SENDER would be wrong data (every line it "said" unheard at every distance): the analyzer notices and leaves it out
    muted_a, silent_b = session("Dgawdawdad", "Testststsets", walk(), rng, cutoff=-1.0)
    notes = []
    assert sc.samples_from([log("Dgawdawdad", muted_a), log("Testststsets", silent_b)], notes) == []
    assert notes and "cannot /say here" in notes[0] and "/ev say listen" in notes[0], notes


SAVED = """EqVoiceDB = {
	["saycal"] = {
		["at"] = 1792246516,
		["heard"] = "%s",
		["interval"] = 2,
		["sent"] = "%s",
		["partner"] = "%s",
		["skipped"] = "%s",
	},
}
"""


def test_the_saved_variables_of_a_real_run_give_the_range_and_say_where_it_is_uncertain(tmp_path):
    """A real calibration (Testststsets spoke while walking out to 128 yd and back; Dgawdawdad listened), as the addon saved it."""
    sent = ("1:27.0,2:28.5,3:28.7,4:28.7,5:17.8,6:22.3,7:36.2,8:50.3,9:62.5,10:74.3,11:86.8,12:99.9,13:113.6,14:127.7,15:125.9,16:111.9,17:97.7,"
            "18:83.6,19:69.6,20:55.6,21:41.2,22:27.0,23:12.7,24:3.2,25:3.2")
    heard = "1:27.0,2:28.3,3:28.7,4:28.7,5:17.9,6:21.7,7:36.0,22:27.4,23:13.2,24:3.2,25:3.2"
    skipped = "8:50.2,9:62.2,10:74.3,11:86.9,12:99.9,13:113.6,14:127.7,15:125.9,16:111.8,17:97.6,18:83.5,19:69.5,20:55.4,21:41.0"
    for acct, text in (("EQUADIS", SAVED % ("", sent, "Dgawdawdad", "")), ("EQUADIS2", SAVED % (heard, "", "Testststsets", skipped))):
        d = tmp_path / "WTF" / "Account" / acct / "SavedVariables"
        d.mkdir(parents=True)
        (d / "EqVoice.lua").write_text(text)
    logs = sc.logs_from_savedvars(tmp_path)
    assert {l.character for l in logs} == {"Testststsets", "Dgawdawdad"}, "who is who is worked out from the partners"
    notes = []
    samples = sc.samples_from(logs, notes)
    assert len(samples) == 25 and sum(s["heard"] for s in samples) == 11 and not notes
    f = sc.fit(samples)
    assert f["errors"] == 0 and 38.0 <= f["range"] <= 39.5 and f["gap"] == (36.2, 41.2), f
    out = sc.render(samples, f, notes)
    assert "somewhere between 36.2 and 41.2 yd" in out and "40 yd is inside that range" in out and "stand still at about 38, 40 and 42 yd" in out


def test_a_muted_speaker_and_a_late_listener_are_left_out_of_the_fit_with_a_note():
    """Both found in real data: the character that cannot /say (all its lines 'unheard' even at 15 yd), and the first lines said before the listener pressed start."""
    spoken = sc.SavedLog("A", {"character": "Testststsets", "partner": "Dgawdawdad", "sent": "1:15.4,2:15.4,3:15.4,4:15.7,5:24.9,6:34.1,7:42.1,8:51.2", "heard": ""})
    other = sc.SavedLog("B", {"character": "Dgawdawdad", "partner": "Testststsets", "sent": "1:15.4,2:21.1,3:30.3,4:38.2,5:47.3,6:56.4",
                              "heard": "3:15.4,4:20.7,5:30.0,6:37.8"})
    notes = []
    samples = sc.samples_from([spoken, other], notes)
    text = " | ".join(notes)
    assert "Dgawdawdad's 6 lines never arrived" in text and "cannot /say here" in text, text
    assert "Testststsets's first 2 line(s) (numbers 1,2)" in text, text
    assert sorted((s["dist"], s["heard"]) for s in samples) == [(15.4, True), (15.7, True), (24.9, True), (34.1, True), (42.1, False), (51.2, False)]


# --- the second and third real passes (2026-10-17): the first ran at 2 s per line, the third at 1 s per line

def saved_pair(tmp_path, sent, heard, skipped, at, tag):
    for acct, text in ((tag + "A", SAVED % ("", sent, "Dgawdawdad", "")), (tag + "B", SAVED % (heard, "", "Testststsets", skipped))):
        d = tmp_path / tag / "WTF" / "Account" / acct / "SavedVariables"
        d.mkdir(parents=True)
        (d / "EqVoice.lua").write_text(text.replace("1792246516", str(at)))
    return sc.logs_from_savedvars(tmp_path / tag)


THIRD_SENT = ("1:56.1,2:56.2,3:59.0,25:58.3,26:51.1,27:44.0,28:36.8,29:31.0,30:29.1,31:29.1,32:32.6,33:37.2,34:37.2,35:37.2,36:31.4,37:30.5,38:32.9,"
              "39:35.1,40:34.5,41:36.2,42:40.8,43:45.5,44:50.1,45:54.7,46:59.3")
THIRD_HEARD = "28:37.1,29:31.0,30:29.1,31:29.1,40:34.5,41:35.8,42:40.4"
THIRD_SKIPPED = "32:32.8,33:37.2,34:37.2,35:37.2,36:31.4,37:30.5,38:33.1,39:35.1"


def test_a_burst_of_lost_lines_inside_the_range_does_not_move_the_estimate(tmp_path):
    """The 1-line-a-second pass lost eight lines in a row at 30 to 37 yd (a server chat flood limit is the likely cause), then was heard again out to 40.8 yd.
    A fit that treated every miss as evidence put the range at 30 yd. Hits are the evidence."""
    logs = saved_pair(tmp_path, THIRD_SENT, THIRD_HEARD, THIRD_SKIPPED, 1792247000, "third")
    notes = []
    samples = sc.samples_from(logs, notes)
    f = sc.fit(samples)
    assert f["farthest_heard"] == 40.8 and f["nearest_missed"] == 44.0, f
    assert len(f["dropouts"]) == 8 and min(f["dropouts"]) == 30.5 and max(f["dropouts"]) == 37.2, f["dropouts"]
    assert f["gap"] == (40.8, 44.0)
    out = sc.render(samples, f, notes)
    assert "8 line(s) were NOT heard although a line was heard from farther away (they were at 30.5 to 37.2 yd)" in out
    assert "chat flood limit" in out and "every 1 s is not" in out


def test_passes_are_kept_and_fitted_together_which_narrows_the_edge(tmp_path):
    second_sent = ("1:27.0,2:28.5,3:28.7,4:28.7,5:17.8,6:22.3,7:36.2,8:50.3,9:62.5,10:74.3,11:86.8,12:99.9,13:113.6,14:127.7,15:125.9,16:111.9,17:97.7,"
                   "18:83.6,19:69.6,20:55.6,21:41.2,22:27.0,23:12.7,24:3.2,25:3.2")
    second_heard = "1:27.0,2:28.3,3:28.7,4:28.7,5:17.9,6:21.7,7:36.0,22:27.4,23:13.2,24:3.2,25:3.2"
    second_skipped = "8:50.2,9:62.2,10:74.3,11:86.9,12:99.9,13:113.6,14:127.7,15:125.9,16:111.8,17:97.6,18:83.5,19:69.5,20:55.4,21:41.0"
    hist = tmp_path / "history.json"
    one = saved_pair(tmp_path, second_sent, second_heard, second_skipped, 1792246516, "second")
    sc.history_add(hist, sc.run_key(one), sc.samples_from(one, []))
    f1 = sc.fit([s for v in sc.history_add(hist, sc.run_key(one), sc.samples_from(one, [])).values() for s in v])
    assert f1["gap"] == (36.2, 41.2), "the same pass analysed twice counts once, and says what it said before"
    three = saved_pair(tmp_path, THIRD_SENT, THIRD_HEARD, THIRD_SKIPPED, 1792247000, "third")
    h = sc.history_add(hist, sc.run_key(three), sc.samples_from(three, []))
    assert len(h) == 2
    f = sc.fit([s for v in h.values() for s in v])
    assert f["gap"] == (40.8, 41.2) and 40.9 <= f["range"] <= 41.1, f
    out = sc.render([s for v in h.values() for s in v], f, [])
    assert "Pinned down to 40.8 to 41.2 yd (a width of 0.4 yd)" in out and "/say carried to about 41.0 yards" in out
