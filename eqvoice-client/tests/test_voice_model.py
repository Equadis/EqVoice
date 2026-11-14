"""The voice model (ui/voicemodel.js) in a real browser: how loud a voice is at each distance, how a raised voice is recognised, levelling.

The page and the app compute the same distance curve, so the JavaScript is checked against ``peers.voice_gain`` over a grid, and the loudness
tracker is fed simulated speakers: someone talking, someone shouting, someone with a quiet microphone, someone who is always loud."""
import json

import pytest

import edgeharness
from eqvoice_client.peers import voice_gain

pytestmark = pytest.mark.skipif(not edgeharness.available(), reason="needs a Chromium-based browser")


def run(script):
    return edgeharness.run(edgeharness.page(script), timeout=60)


def test_the_distance_curve_is_the_same_in_the_page_and_the_app():
    grid = [(d / 2.0) for d in range(0, 200)]
    curves = [
        {"boost": 2.0, "boost_r": 0.0, "boost_fade": 10.0, "full": 30.0, "full_loud": 45.0, "end": 85.0, "power": 1.6},
        {"boost": 2.0, "boost_r": 3.0, "boost_fade": 8.0, "full": 30.0, "full_loud": 45.0, "end": 85.0, "power": 1.6},
        {"boost": 1.0, "boost_r": 3.0, "boost_fade": 8.0, "full": 10.0, "full_loud": 10.0, "end": 41.0, "power": 1.6},
        {"boost": 3.0, "boost_r": 4.0, "boost_fade": 12.0, "full": 20.0, "full_loud": 60.0, "end": 70.0, "power": 2.0},
    ]
    out = run("""
      const VM = window.VoiceModel, rows = [];
      for (const c of %s)
        for (const s of [0, 0.5, 1])
          for (const d of %s) rows.push([c, s, d, VM.curveGain(d, c, s)]);
      done(rows);
    """ % (json.dumps(curves), json.dumps(grid)))
    assert len(out) == len(curves) * 3 * len(grid)
    for c, s, d, js in out:
        assert js == pytest.approx(voice_gain(d, c, s), abs=1e-9), (c, s, d)


def test_the_default_curve_is_boosted_up_close_full_to_30_yd_quiet_at_70_and_gone_at_85():
    out = run("""
      const VM = window.VoiceModel, c = VM.DEFAULT_CURVE, g = (d, s) => VM.curveGain(d, c, s);
      let steps = 0, prev = g(0, 0), worst = 0;                                  // every 5 cm from 0 to 10 yd
      for (let i = 1; i <= 200; i++) { const v = g(i / 20, 0); worst = Math.max(worst, Math.abs(v - prev)); if (v > prev) steps++; prev = v; }
      done({ curve: c, boost: VM.BOOST, near: [0, 1, 2, 3, 5, 8].map((d) => g(d, 0)), worst, rises: steps,
             flat: [10, 15, 30].map((d) => g(d, 0)), follow: VM.FOLLOW, talk: [35, 41, 50, 60, 70, 80, 84.9, 85, 90].map((d) => g(d, 0)),
             shout: [30, 45, 55, 70, 80, 85].map((d) => g(d, 1)), half: [45, 60].map((d) => g(d, 0.5)),
             flatFor: [VM.flatFor(c, 0), VM.flatFor(c, 1), VM.flatFor(c, 0.5), VM.flatFor(c, 9), VM.flatFor(c, -2)] });
    """)
    assert out["curve"] == {"boost": 2, "boost_r": 0, "boost_fade": 10, "full": 30, "full_loud": 45, "end": 85, "power": 1.6}
    near = out["near"]
    assert near[0] == 2, "right next to each other the voice is boosted: twice its own loudness"
    assert near[0] > near[1] > near[2] > near[3] > near[4] > near[5] > 1 and near[4] == pytest.approx(2 ** 0.5, abs=0.001), \
        "and every yard further away is a little quieter: there is no plateau, 5 yd is +3 dB: %r" % near
    assert out["worst"] < 0.01 and out["rises"] == 0, "no step anywhere in the boost (biggest 5 cm change %.4f)" % out["worst"]
    assert out["flat"] == [1, 1, 1], "a voice is at its full volume from 10 to 30 yd"
    assert 0.1 <= out["follow"]["distTau"] <= 0.6 and 0.02 <= out["follow"]["gainTau"] <= 0.3, "the page follows the distance smoothly, not instantly"
    talk = dict(zip([35, 41, 50, 60, 70, 80, 84.9, 85, 90], out["talk"]))
    assert talk[35] > talk[41] > talk[50] > talk[60] > talk[70] > talk[80] > talk[84.9] > 0, "then it falls steadily"
    assert 0.08 < talk[70] < 0.18, "70 yd is quiet: %r" % talk[70]
    assert talk[85] == 0 and talk[90] == 0, "and gone at 85"
    shout = dict(zip([30, 45, 55, 70, 80, 85], out["shout"]))
    assert shout[30] == 1 and shout[45] == 1, "a shout is at full volume to 45 yd"
    assert shout[70] > talk[70] and shout[85] == 0, "and louder than talking at 70 yd, but over at the same 85"
    half = dict(zip([45, 60], out["half"]))
    assert 0.6 < half[45] < 1 and half[60] > 0, "half way between talking and shouting is half way"
    assert out["flatFor"] == [30, 45, 37.5, 45, 30], "the loudness 0..1 moves the end of the full-volume stretch, and is clamped"
    assert out["boost"] == {"off": 1, "low": 1.4, "normal": 2, "high": 3}


def test_levelling_lifts_a_quiet_voice_and_eases_a_hot_one_towards_the_same_level_within_limits():
    out = run("""
      const VM = window.VoiceModel, L = VM.levelingDb;
      done({ target: VM.LEVEL, at: L(-20), quiet: L(-28), veryQuiet: L(-60), hot: L(-16), veryHot: L(-5), custom: L(-30, { targetDb: -24, maxDb: 3 }) });
    """)
    assert out["target"]["targetDb"] == -20
    assert out["at"] == 0, "a voice already at the target is left alone"
    assert out["quiet"] == 8, "a quiet microphone is lifted ..."
    assert out["veryQuiet"] == 12, "... by at most 12 dB, so that a microphone that is nearly off is not turned into a hiss"
    assert out["hot"] == -4, "a hot one is eased down"
    assert out["veryHot"] == -6, "... by at most 6 dB"
    assert out["custom"] == 3, "and the limits can be changed"


SPEAKER_JS = """
  // a deterministic pseudo-random number between 0 and 1
  let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) %% 2147483648) / 2147483648;
  // the level of a person talking: syllables at about 4 a second, each a few dB above or below their usual, with a pause now and then
  function talk(L, ticks, extra) {
    const out = [];
    for (let i = 0; i < ticks; i++) {
      const phase = (i %% 12) / 12;                                          // 12 ticks of 20 ms = 240 ms per syllable
      const syl = Math.sin(Math.PI * phase);                                 // swell and fall
      const pause = (Math.floor(i / 12) %% 9 === 8) ? -60 : 0;               // a pause roughly every ninth syllable
      out.push(L - 14 + 14 * syl + (rnd() - 0.5) * 4 + (extra || 0) + pause);
    }
    return out;
  }
  function track(levels, mic) {
    const m = new VoiceModel.Loudness(), s = [];
    for (const db of levels) s.push(m.update(db, 0.02));
    return { s, model: m };
  }
"""


def test_talking_is_not_loud_a_shout_is_and_it_settles_afterwards():
    out = run(SPEAKER_JS % () + """
      const base = talk(-18, 1500);                                          // 30 s of ordinary talking, then ...
      const shout = talk(-18, 100, 14);                                      // ... 2 s of shouting (14 dB louder), then ...
      const after = talk(-18, 400);                                          // ... 8 s back to normal
      const r = track(base.concat(shout, after));
      const s = r.s, at = (a, b) => s.slice(a, b);
      done({ warm: Math.max(...at(0, 150)), talk: Math.max(...at(300, 1500)), shoutMax: Math.max(...at(1500, 1600)),
             shoutRise: s[1500 + 15], afterOne: s[1600 + 25], afterLater: Math.max(...at(1600 + 120, 2000)), base: r.model.base });
    """)
    assert out["warm"] == 0, "nobody is loud before they have been heard for a few seconds"
    assert out["talk"] < 0.15, "ordinary talking, syllable by syllable, never counts as raised: %r" % out["talk"]
    assert out["shoutMax"] > 0.9, "a shout reaches the full range: %r" % out["shoutMax"]
    assert out["shoutRise"] > 0.5, "and within 0.3 s: %r" % out["shoutRise"]
    assert out["afterOne"] > 0.1, "it fades over a second or so rather than chopping the voice up between syllables"
    assert out["afterLater"] < 0.1, "and is back to normal soon after the shout ends: %r" % out["afterLater"]


def test_it_is_how_loud_for_this_speaker_not_how_loud_in_absolute_terms():
    out = run(SPEAKER_JS % () + """
      const quiet = track(talk(-38, 1500).concat(talk(-38, 100, 14)));         // a quiet microphone, then a shout from it
      const loud = track(talk(-12, 1500).concat(talk(-12, 100, 14)));          // a hot microphone, then a shout
      const raised = track(talk(-12, 1500).concat(talk(-12, 100, 8)));         // the same microphone, a merely raised voice
      const always = track(talk(-12, 2500));                                   // a hot microphone and nothing else
      const whisperer = track(talk(-30, 1500));                                // quiet and steady
      done({ quietTalk: Math.max(...quiet.s.slice(300, 1500)), quietShout: Math.max(...quiet.s.slice(1500)),
             loudTalk: Math.max(...loud.s.slice(300, 1500)), loudShout: Math.max(...loud.s.slice(1500)),
             always: Math.max(...always.s.slice(300)), whisper: Math.max(...whisperer.s.slice(300)), raised: Math.max(...raised.s.slice(1500)) });
    """)
    assert out["quietTalk"] < 0.15 and out["quietShout"] > 0.9, "a quiet microphone shouting is still a shout: %r" % out
    assert out["loudTalk"] < 0.15 and out["loudShout"] > 0.9, "and so is a loud one: %r" % out
    assert out["always"] < 0.15 and out["whisper"] < 0.15, "a hot or a quiet microphone does not stretch anyone's range by itself: %r" % out
    assert 0.4 < out["raised"] < 0.95, "a merely raised voice reaches part of the way: %r" % out["raised"]


def test_silence_and_noise_floor_do_not_count_and_the_range_drains():
    out = run(SPEAKER_JS % () + """
      const m = new VoiceModel.Loudness(), s = [];
      for (const db of talk(-20, 800)) m.update(db, 0.02);
      for (const db of talk(-20, 60, 16)) s.push(m.update(db, 0.02));         // a shout ...
      const peak = Math.max(...s);
      const drain = [];
      for (let i = 0; i < 100; i++) drain.push(m.update(-80, 0.02));          // ... then silence for 2 s
      done({ peak, d1: drain[10], d2: drain[60], d3: drain[99], voiced: m.voiced, level: m.level });
    """)
    assert out["peak"] > 0.9
    assert out["d1"] > out["d2"] > out["d3"] and out["d3"] == 0 or out["d3"] < 0.05, "it drains to nothing over about a second and a half: %r" % out


# ---- the little icon: nothing / speaking / loud (VoiceActivity), steady in front of a simulated speaker

ICON_JS = SPEAKER_JS % () + """
  // what the page does for every voice, every 20 ms: Loudness, then VoiceActivity on its smoothed level and its raised-voice measure
  function icon(levels) {
    const m = new VoiceModel.Loudness(), va = new VoiceModel.VoiceActivity(), out = [];
    for (const db of levels) { m.update(db, 0.02); out.push(va.update(db, m.s, 0.02)); }
    return out;
  }
  const silence = (ticks) => new Array(ticks).fill(-80);
  const changes = (a, from, to) => { let n = 0; for (let i = (from || 1); i < (to || a.length); i++) if (a[i] !== a[i - 1]) n++; return n; };
"""


def test_the_icon_stays_idle_for_silence_and_for_a_key_click_or_a_cough():
    out = run(ICON_JS + """
      const quiet = icon(silence(250));
      const clicks = silence(100);                                            // five key clicks, 150 ms apart, each 40 ms of -20 dB
      for (let k = 0; k < 5; k++) for (let i = 0; i < 2; i++) clicks[10 + k * 8 + i] = -20;
      const click = icon(clicks);
      const hum = icon(new Array(500).fill(-58));                            // a steady hiss under the floor
      done({ quiet: Math.max(...quiet), click: Math.max(...click), hum: Math.max(...hum) });
    """)
    assert out == {"quiet": 0, "click": 0, "hum": 0}, "nothing is not speech: %r" % out


def test_ordinary_talking_is_speaking_all_the_way_through_with_no_flicker_and_never_loud():
    out = run(ICON_JS + """
      const lv = icon(talk(-18, 1500));                                       // 30 s of talking: syllables, and a pause every ninth one
      done({ first: lv.findIndex((v) => v >= 1), after: Math.min(...lv.slice(30)), top: Math.max(...lv), changes: changes(lv) });
    """)
    assert 5 <= out["first"] <= 12, "speaking shows within about 0.12 s of the first voice: %r ticks" % out["first"]
    assert out["after"] == 1, "and never drops to idle again while they talk (the pauses between words are shorter than the hold)"
    assert out["top"] == 1, "ordinary talking is vol_mid, never vol_high"
    assert out["changes"] == 1, "one change in 30 s: up"


def test_a_shout_is_loud_and_it_does_not_flicker_between_loud_and_speaking():
    out = run(ICON_JS + """
      const lv = icon(talk(-18, 1500).concat(talk(-18, 125, 14), talk(-18, 500)));     // 30 s talking, 2.5 s shouting, 10 s talking
      const shoutAt = 1500, end = 1625;
      const loudAt = lv.findIndex((v, i) => i >= shoutAt && v === 2);
      done({ before: Math.max(...lv.slice(60, shoutAt)), loudAfterTicks: loudAt - shoutAt,
             during: Math.min(...lv.slice(loudAt, end)), changesDuring: changes(lv, shoutAt, end),
             backAt: lv.findIndex((v, i) => i >= end && v === 1) - end, tail: Math.max(...lv.slice(end + 150)),
             total: changes(lv) });
    """)
    assert out["before"] == 1, "speaking up to the shout"
    assert 0 <= out["loudAfterTicks"] <= 40, "loud within 0.8 s of the shout starting: %r ticks" % out["loudAfterTicks"]
    assert out["during"] == 2, "and stays loud for as long as they shout"
    assert out["changesDuring"] <= 1, "no flicker inside the shout: %r changes" % out["changesDuring"]
    assert 10 <= out["backAt"] <= 120, "it comes back to speaking a second or two after: %r ticks" % out["backAt"]
    assert out["tail"] == 1, "and stays there"
    assert out["total"] == 3, "up, loud, back: three changes in 45 s, no more: %r" % out["total"]


def test_when_they_stop_talking_the_icon_goes_idle_after_the_hold_and_not_before():
    out = run(ICON_JS + """
      const lv = icon(talk(-18, 500).concat(silence(100)));
      const last = 500;                                                        // the first tick of silence
      const dropAt = lv.findIndex((v, i) => i >= last && v === 0);
      done({ dropAfter: dropAt - last, held: lv.slice(last, last + 15) });
    """)
    assert 20 <= out["dropAfter"] <= 40, "idle about 0.45 s after the voice ends, not at once and not much later: %r ticks (20 ms each)" % out["dropAfter"]
    assert set(out["held"]) == {1}, "speaking is held through the first 0.3 s of quiet"


def test_a_loud_start_is_only_ordinary_speech_until_the_speaker_has_been_heard_for_a_few_seconds():
    out = run(ICON_JS + """
      const lv = icon(talk(-18, 100, 14));                                     // shouting from the very first word, for 2 s
      done({ top: Math.max(...lv) });
    """)
    assert out["top"] == 1, "raised is relative to their own usual voice, which has to be heard first: %r" % out["top"]


def test_the_two_bars_are_different_so_a_hovering_measure_does_not_flip_the_icon():
    out = run("""
      const VM = window.VoiceModel, va = new VM.VoiceActivity();
      for (let i = 0; i < 20; i++) va.update(-30, 0, 0.02);                    // speaking
      const speaking = va.level;
      for (let i = 0; i < 400; i++) va.update(-30, 0.45, 0.02);                // the raised-voice measure sits between the two bars (0.25 and 0.55)
      const hoverUp = va.level;
      for (let i = 0; i < 20; i++) va.update(-30, 0.9, 0.02);                  // really raised
      const loud = va.level;
      for (let i = 0; i < 400; i++) va.update(-30, 0.45, 0.02);                // hovering again, now coming from above
      const hoverDown = va.level;
      for (let i = 0; i < 60; i++) va.update(-30, 0.1, 0.02);                  // really calm
      done({ speaking, hoverUp, loud, hoverDown, calm: va.level });
    """)
    assert out == {"speaking": 1, "hoverUp": 1, "loud": 2, "hoverDown": 2, "calm": 1}, out


def test_a_chatty_minute_changes_the_icon_only_a_handful_of_times():
    out = run(ICON_JS + """
      let ticks = [], seedShift = 0;
      for (let k = 0; k < 12; k++) {                                          // twelve utterances of 2 to 5 s, a second of quiet between, one of them shouted
        ticks = ticks.concat(talk(-18 + (k % 3) - 1, 100 + (k * 37) % 150, k === 7 ? 14 : 0), silence(50));
      }
      const lv = icon(ticks);
      done({ seconds: ticks.length * 0.02, changes: changes(lv), top: Math.max(...lv) });
    """)
    assert out["seconds"] > 50
    assert out["changes"] <= 2 * 12 + 4, "an utterance is up and down, a shout adds two: %r changes in %r s" % (out["changes"], out["seconds"])


def test_where_a_voice_comes_from_depends_on_the_mode_and_the_direction_and_never_on_the_distance_curve():
    out = run("""
      const VM = window.VoiceModel, rows = {};
      for (const mode of VM.SPATIAL_MODES)
        for (const az of [0, 90, -90, 180, null]) rows[mode + ':' + az] = VM.spatialFor(mode, az, 20);
      rows.near = VM.spatialFor('headphones', 90, 0.5); rows.mid = VM.spatialFor('headphones', 90, 2.5);
      rows.delta = [VM.azDelta(170, -170), VM.azDelta(-170, 170), VM.azDelta(10, 20)];
      rows.norm = [VM.normSpatial('speakers'), VM.normSpatial('x'), VM.normSpatial(undefined)];
      done(rows);""")
    for az in ("0", "90", "-90", "180", "null"):
        m = out["mono:" + az]
        assert m["pan"] == 0 and m["gain"] == 1 and m["x"] == 0, "mono has no sides whatever the direction (%s)" % az
    assert out["speakers:90"]["pan"] > 0.7 and out["speakers:-90"]["pan"] < -0.7 and out["speakers:0"]["pan"] == 0, "right is right, left is left, ahead is the middle"
    assert abs(out["speakers:90"]["pan"]) <= 0.8 + 1e-9, "speakers never pan hard to one side"
    assert out["speakers:180"]["gain"] < out["speakers:0"]["gain"] == 1, "behind you is a little quieter on speakers (they cannot place it behind you)"
    assert out["headphones:90"]["x"] == 1 and out["headphones:-90"]["x"] == -1 and out["headphones:0"]["z"] == -1, "headphones: right, left and ahead"
    assert out["headphones:180"]["z"] == 1 and out["headphones:180"]["gain"] == 1, "headphones put a voice behind you, at full volume"
    assert out["headphones:null"]["x"] == 0, "no direction known: the middle"
    assert out["near"]["x"] == 0, "a player standing on you is centred (the direction flips as either of you shuffles)"
    assert 0 < out["mid"]["x"] < 1, "and the direction comes in over the next few yards"
    assert out["delta"][0] == 20 and out["delta"][1] == -20 and out["delta"][2] == 10, "directions turn the short way round"
    assert out["norm"] == ["speakers", "headphones", "headphones"]
