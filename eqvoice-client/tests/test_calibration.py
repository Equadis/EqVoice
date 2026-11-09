"""The microphone setup. The rules that turn three recordings into the noise gate's bar and the microphone's volume (pure, no browser),
then the app's half: the game's button, the page's progress, the result applied, saved and told to everyone."""
import json
import math
import random

import pytest

from eqvoice_client import calibration as cal

from scene import Scene, run, until
from test_ingame_panel import read_status, send


def readings(centre, spread, n, seed):
    r = random.Random(seed)
    return [centre + r.uniform(-spread, spread) for _ in range(n)]


def room(noise=-72.0, speech=-38.0, shout=-28.0, peak=-9.0, seed=1):
    """What the page would send for a person in a quiet room: 4 s of silence, 6 s of talking (with pauses), 4 s of shouting."""
    quiet = readings(noise, 2.0, 80, seed)
    normal = readings(noise, 2.0, 30, seed + 1) + readings(speech, 6.0, 90, seed + 2)
    loud = readings(shout, 5.0, 80, seed + 3)
    return quiet, normal, loud, peak


# --------------------------------------------------------------------------------------------- the rules

def test_a_quiet_room_and_an_ordinary_voice_get_a_bar_between_them_and_no_volume_change():
    r = cal.analyse(*room())
    assert r.ok and r.quality == "good" and r.gate_mode == "manual"
    floor = r.facts["floor_db"]
    assert floor == pytest.approx(-70.5, abs=2.0)
    assert r.facts["speech_low_db"] > r.gate_db + cal.CLOSEST_TO_SPEECH_DB - 1.0, "the bar is under the quiet end of speech"
    assert r.gate_db > floor + cal.CLOSEST_TO_NOISE_DB - 1.0, "and over the room"
    assert r.input_gain == 1.0, "a healthy microphone is left alone"
    assert r.gate_db % cal.GATE_STEP == 0 and cal.GATE_MIN <= r.gate_db <= cal.GATE_MAX
    assert "set to %d dB" % round(r.gate_db) in r.text


def test_a_shout_that_nearly_clips_turns_the_microphone_down_and_the_bar_follows():
    base = cal.analyse(*room(peak=-9.0))
    hot = cal.analyse(*room(peak=-0.5))
    assert hot.ok and hot.input_gain < 0.6, "a shout at -0.5 dBFS is brought back to about -6: roughly half the volume"
    assert 20 * math.log10(hot.input_gain) == pytest.approx(-5.5, abs=0.5)
    assert hot.gate_db < base.gate_db, "the gate comes after the volume change, so its bar moves down with it"
    assert hot.gate_db == pytest.approx(base.gate_db + 20 * math.log10(hot.input_gain), abs=2.5)
    assert "turned it down" in hot.text


def test_a_very_quiet_microphone_is_lifted_but_never_by_more_than_6_dB():
    r = cal.analyse(*room(noise=-90.0, speech=-62.0, shout=-50.0, peak=-30.0))
    assert r.ok and r.quality == "good"
    assert r.input_gain == pytest.approx(10 ** (6 / 20), abs=0.06), "lifted by the most it may be"
    assert "turned it up" in r.text


def test_a_voice_barely_above_the_room_leaves_the_gate_on_automatic_and_says_why():
    r = cal.analyse(*room(noise=-50.0, speech=-45.0, shout=-40.0))
    assert r.ok and r.quality == "noisy" and r.gate_mode == "auto"
    assert r.gate_db > r.facts["floor_db"], "a bar is still worked out, just over the noise"
    assert "automatic" in r.text and "quieter room" in r.text


def test_when_nobody_talks_nothing_changes():
    quiet = readings(-72.0, 2.0, 80, 1)
    r = cal.analyse(quiet, readings(-72.0, 2.0, 100, 2), readings(-72.0, 2.0, 80, 3), -50.0)
    assert not r.ok and r.quality == "none" and r.gate_db is None and r.input_gain is None
    assert "did not hear you talk" in r.text


def test_a_silent_stretch_that_is_too_short_cannot_be_measured():
    r = cal.analyse([-72.0] * 5, [-40.0] * 100, [-30.0] * 80, -10.0)
    assert not r.ok and "too short" in r.text


def test_a_cough_in_the_quiet_stretch_does_not_set_the_floor():
    quiet = readings(-72.0, 2.0, 100, 1)
    quiet[40:44] = [-30.0] * 4                                   # four readings of a cough: 4% of them
    base = cal.analyse(*room())
    r = cal.analyse(quiet, *room()[1:])
    assert r.facts["floor_db"] < -60, "the loudest 5% is let go"
    assert r.gate_db == pytest.approx(base.gate_db, abs=4.0)


def test_nonsense_readings_are_ignored_not_trusted():
    quiet, normal, loud, peak = room()
    messy = [None, "x", float("nan"), float("inf"), -float("inf"), 5.0, -500.0] + normal
    r = cal.analyse(quiet + [None, "wat"], messy, loud, "loud")
    assert r.ok and r.quality == "good"
    assert cal.GATE_MIN <= r.gate_db <= cal.GATE_MAX


def test_the_bar_always_stays_inside_what_the_slider_can_show():
    for noise, speech in ((-95.0, -88.0), (-30.0, -2.0), (-60.0, -25.0)):
        r = cal.analyse(*room(noise=noise, speech=speech, shout=speech + 8, peak=-20.0))
        if r.ok:
            assert cal.GATE_MIN <= r.gate_db <= cal.GATE_MAX


def test_percentile_is_the_usual_one():
    assert cal.percentile([5.0], 90) == 5.0
    assert cal.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert cal.percentile([0.0, 10.0], 25) == 2.5
    assert cal.percentile([1.0, 2.0, 3.0], 0) == 1.0 and cal.percentile([1.0, 2.0, 3.0], 100) == 3.0


# --------------------------------------------------------------------------------------------- the app's half

def test_the_game_starts_the_setup_the_page_reports_and_the_result_is_applied_saved_and_told(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None and read_status(game).fields.get("cal") == "idle", 10, "status with the setup idle")
            assert read_status(game).fields["calq"] == "-" and read_status(game).fields["cald"] == "0"

            send(game, ("calibrate", "1"))
            await until(lambda: pa.named("calibrate"), 5, "the page is told to listen")
            await until(lambda: read_status(game).fields["cal"] == "quiet", 5, "the game is told the first step has begun")

            await pa.ws.send_json({"t": "cal", "phase": "normal", "left": 6.4})
            await until(lambda: read_status(game).fields["cal"] == "normal" and read_status(game).fields["calt"] == "7", 5, "the second step, seconds rounded up")

            quiet, normal, loud, peak = room()
            await pa.ws.send_json({"t": "cal", "phase": "done", "quiet": quiet, "normal": normal, "loud": loud, "peak": peak})
            await until(lambda: read_status(game).fields["cal"] == "done", 5, "the result")
            st = read_status(game).fields
            assert st["calq"] == "good" and st["cald"] == "1" and int(st["gdb"]) == int(round(a.cfg.audio.gate_db))
            assert a.cfg.audio.gate_mode == "manual" and a.cfg.audio.calibrated is True and a.cfg.audio.keyboard_filter is True
            await until(lambda: pa.named("gate") and pa.named("gate")[-1]["mode"] == "manual" and pa.named("gate")[-1]["db"] == a.cfg.audio.gate_db, 5,
                        "the page is given the bar")
            await until(lambda: pa.named("input_gain") and pa.named("input_gain")[-1]["value"] == a.cfg.audio.input_gain, 5, "and the volume")
            await until(lambda: pa.cal_results, 5, "the window is told what happened, in words")
            assert pa.cal_results[-1]["ok"] is True and pa.cal_results[-1]["quality"] == "good" and "noise gate is set" in pa.cal_results[-1]["text"]
            await until(lambda: json.loads((tmp_path / "Alice" / "config.json").read_text())["audio"]["calibrated"] is True, 10, "saved")

            send(game, ("calibrate", "0"))
            await until(lambda: read_status(game).fields["cal"] == "idle", 5, "the game can dismiss the result")
    run(go(), 90)


def test_a_setup_where_nobody_talked_changes_nothing_and_says_so(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), cfg={"audio": {"gate_mode": "auto", "gate_db": -48.0}})
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            a._on_ui({"cmd": "calibrate"})
            await until(lambda: pa.named("calibrate"), 5, "started from the window")
            quiet = readings(-72.0, 2.0, 80, 1)
            await pa.ws.send_json({"t": "cal", "phase": "done", "quiet": quiet, "normal": readings(-72.0, 2.0, 100, 2),
                                   "loud": readings(-72.0, 2.0, 80, 3), "peak": -60.0})
            await until(lambda: pa.cal_results, 5, "told")
            assert pa.cal_results[-1]["ok"] is False and pa.cal_results[-1]["quality"] == "none" and "did not hear you talk" in pa.cal_results[-1]["text"]
            assert a.cfg.audio.gate_mode == "auto" and a.cfg.audio.gate_db == -48.0 and a.cfg.audio.calibrated is False and a.cfg.audio.input_gain == 1.0
            assert a.cal["phase"] == "idle" and a.cal["quality"] == "none"
    run(go(), 60)


def test_cancelling_and_a_microphone_that_is_not_ready_both_end_the_setup(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            send(game, ("calibrate", "1"))
            await until(lambda: a.cal["phase"] == "quiet", 5, "started")
            send(game, ("calibrate", "1"))                       # a second press while it is running does not start another
            send(game, ("calibrate", "0"))
            await until(lambda: pa.named("calibrate_cancel"), 5, "the page is told to stop")
            await until(lambda: a.cal["phase"] == "idle", 5, "and the app is idle again")
            assert len(pa.named("calibrate")) == 1, "one run, not two"

            a._on_ui({"cmd": "calibrate"})
            await pa.ws.send_json({"t": "cal", "phase": "failed", "error": "the microphone is not ready"})
            await until(lambda: pa.cal_results and pa.cal_results[-1]["ok"] is False, 5, "the failure is shown")
            assert "not ready" in pa.cal_results[-1]["text"] and a.cal["phase"] == "idle"
            assert a.cfg.audio.calibrated is False
    run(go(), 60)


def test_the_gate_comes_on_when_the_setup_runs_with_it_off(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), cfg={"audio": {"keyboard_filter": False}})
            pa = sc.pages["Alice"]
            a._on_ui({"cmd": "calibrate"})
            quiet, normal, loud, peak = room()
            await pa.ws.send_json({"t": "cal", "phase": "done", "quiet": quiet, "normal": normal, "loud": loud, "peak": peak})
            await until(lambda: a.cfg.audio.keyboard_filter is True, 5, "the gate was switched on, with its new bar")
            await until(lambda: pa.named("keyboard") and pa.named("keyboard")[-1]["value"] is True, 5, "and the page was told")
    run(go(), 60)


def test_the_input_volume_goes_through_the_window_command_too(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa = sc.pages["Alice"]
            await until(lambda: pa.named("config"), 5, "config")
            a._on_ui({"cmd": "input_gain", "value": 0.4})
            assert a.cfg.audio.input_gain == 0.4
            await until(lambda: pa.named("input_gain") and pa.named("input_gain")[-1]["value"] == 0.4, 5, "the page is told")
            a._on_ui({"cmd": "input_gain", "value": 9})
            assert a.cfg.audio.input_gain == 2.0, "capped at 200%"
            a._on_ui({"cmd": "input_gain", "value": -1})
            assert a.cfg.audio.input_gain == 0.0, "and floored at 0"
            assert a.build_state()["voice"]["input_gain"] == 0.0 and a.build_state()["voice"]["calibrated"] is False
    run(go(), 60)
