// The voice model: the numbers behind how far a voice carries, how loud it is up close, how a voice is told to be loud or normal,
// and how it is coloured. Pure functions and one small class: no page, no audio nodes, so a real browser can test it on its own
// (eqvoice-client/tests/test_voice_model.py) and the page (app.js) only has to wire it up.
//
// Distances are yards from the game; levels are dBFS of the decoded voice; gains are multipliers on the voice's own loudness.
(function (root) {
  'use strict';

  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  const normBitrate = (v) => { const n = Number(v); return isFinite(n) ? clamp(Math.round(n), 0, 64) : 24; };

  // ---- how loud a voice is at each distance
  //
  // `curve` = { boost, boost_r, boost_fade, full, full_loud, end, power }, distances in yards, volume as a multiple of the voice's own:
  //
  //     0 .. boost_fade     the boost: `boost` times (2 = doubled) at 0 yd, easing away SMOOTHLY (a cosine, in decibels) to nothing at
  //                         boost_fade (10 yd). There is no flat stretch and no step: every yard closer is a little louder.
  //                         (boost_r, 0 by default, would hold the full boost for that many yards before it starts to ease.)
  //     .. full             1: the voice's full volume (30 yd is still 100%)
  //     full .. end         falls to nothing at `end` (85 yd), as (1 - t) ^ power: about 12% at 70 yd, which is quiet
  //
  // A voice the speaker is raising keeps its full volume further out: `full` moves towards `full_loud` as loudness goes from 0 to 1 (a
  // shout is still at full volume at 45 yd, and louder than a normal voice at 70). The end of the curve does not move: it is how far calls are kept.
  const DEFAULT_CURVE = { boost: 2, boost_r: 0, boost_fade: 10, full: 30, full_loud: 45, end: 85, power: 1.6 };

  // The distance reaches the page a few times a second, and a person walks several yards in that time, so the page follows it rather than
  // jumping to it (`distTau` seconds), and the volume follows the curve a little more slowly still (`gainTau`): the volume, the boost most
  // of all, rises and falls as you walk and never in steps.
  const FOLLOW = { distTau: 0.3, gainTau: 0.08 };

  function flatFor(curve, loudness) {
    return curve.full + clamp(loudness, 0, 1) * Math.max(0, (curve.full_loud || curve.full) - curve.full);
  }

  function boostFor(dist, curve) {
    const boost = Math.max(1, curve.boost || 1);
    const hold = curve.boost_r || 0, fade = curve.boost_fade || 0;
    if (boost === 1 || dist >= fade) return 1;
    const t = dist <= hold ? 0 : (dist - hold) / (fade - hold);              // 0 right next to them .. 1 where the boost has gone
    return Math.pow(boost, 0.5 * (1 + Math.cos(Math.PI * t)));
  }

  function curveGain(dist, curve, loudness) {
    const boost = boostFor(dist, curve);
    const flat = flatFor(curve, loudness);
    if (dist <= flat) return boost;
    if (dist >= curve.end) return 0;
    return boost * Math.pow(1 - (dist - flat) / (curve.end - flat), curve.power || 1.6);
  }

  // ---- making different microphones equally loud
  //
  // People's microphones differ by 20 dB or more, and the browser's automatic level is off (it would flatten shouting). So each listener
  // measures how loud a speaker usually is (`Loudness.base`) and lifts a quiet one, or eases a hot one, towards the same target: an ordinary
  // voice is then loud and clear whoever it comes from. The lift is limited and slow (a couple of seconds), and waits until the speaker
  // has been heard for a few seconds.
  const LEVEL = { targetDb: -20, minDb: -6, maxDb: 12, tau: 2.0 };

  function levelingDb(base, o) {
    const c = Object.assign({}, LEVEL, o || {});
    return clamp(c.targetDb - base, c.minDb, c.maxDb);
  }

  // ---- how loud a speaker is being, compared with how they usually sound
  //
  // The receiver measures each voice as it arrives (the sender's microphone gain is theirs, so only the voice's own variation counts).
  // `base` tracks the level of the speaker's usual loud syllables (the 90th percentile of voiced levels, moving about 4 dB a second
  // up and half a dB a second down); how far the current level is above it, past a margin, becomes 0..1 (0 = talking, 1 = shouting).
  // It rises at once and drains over `holdS` so a raised voice is not chopped up between syllables. The first `warmTicks` voiced
  // 20 ms ticks only learn the speaker: nobody is "loud" before they have been heard.
  const LOUD = { floorDb: -52, smooth: 0.35, q: 0.9, eta: 0.1, warmTicks: 150, l0: 2, l1: 10, holdS: 1.5 };

  class Loudness {
    constructor(opts) {
      this.o = Object.assign({}, LOUD, opts || {});
      this.lv = null;       // smoothed level of the voice now (dBFS)
      this.base = null;     // the speaker's usual loud-syllable level (dBFS)
      this.voiced = 0;
      this.s = 0;           // 0..1
      this.level = -100;    // the last raw level given
    }

    // has this speaker been heard long enough for `base` to mean something?
    get ready() { return this.voiced >= this.o.warmTicks; }

    // db: level of the latest ~20 ms of the decoded voice; dt: seconds since the previous call
    update(db, dt) {
      const o = this.o;
      this.level = db;
      let target = 0;
      if (db > o.floorDb) {
        this.lv = this.lv === null ? db : this.lv + o.smooth * (db - this.lv);
        if (this.base === null) this.base = this.lv;
        else this.base += (this.lv > this.base ? o.q : -(1 - o.q)) * o.eta;
        this.voiced++;
        if (this.voiced >= o.warmTicks) target = clamp((this.lv - this.base - o.l0) / (o.l1 - o.l0), 0, 1);
      }
      this.s = Math.max(target, this.s - dt / o.holdS);
      return this.s;
    }
  }

  // ---- the little voice icon: nothing / ordinary speech / a raised voice, as a steady 0 / 1 / 2.
  //
  // Not the raw level (a breath, a key click or one noisy sample would flip it): the level is smoothed here (up in 40 ms, down in 120 ms: it really
  // falls in silence, which Loudness.lv does not, because that only moves while there is a voice), and the "how raised" measure Loudness keeps
  // (s, 0..1, which holds for a moment after a shout) goes through hold times and hysteresis.
  //
  //   0 -> 1   the smoothed level has been over floorDb for onS (120 ms: a click is shorter than that)
  //   1 -> 0   it has been under floorDb for holdS (450 ms: the gap between two words is not the end of speech)
  //   1 -> 2   the raised-voice measure has been at highS (0.55) or more for highOnS (150 ms)
  //   2 -> 1   it has been under lowS (0.25) for highHoldS (500 ms), a lower bar than the one that raised it (hysteresis), and loud has lasted
  //            at least minHighS (1 s): the measure dips while a long shout goes on (their baseline catches up), which must not flicker the icon
  //   2 -> 0   as 1 -> 0
  //
  // "Raised" is relative to this speaker's own usual speech (Loudness keeps their baseline), which is also what lengthens the range of a shout:
  // so the icon says what the volume curve is doing. A speaker is not "loud" until they have been heard for about three seconds (the baseline
  // has to exist first); until then ordinary speech shows as ordinary.
  const VA = { floorDb: -52, onS: 0.12, holdS: 0.45, highS: 0.55, lowS: 0.25, highOnS: 0.15, highHoldS: 0.5, minHighS: 1.0, attackTau: 0.04, releaseTau: 0.12 };

  class VoiceActivity {
    constructor(opts) {
      this.o = Object.assign({}, VA, opts || {});
      this.level = 0;
      this.lv = -100;       // the level, smoothed (dBFS)
      this.over = 0;        // seconds the level has been over the floor (only counted while it is not yet speech)
      this.under = 0;       // seconds it has been under the floor (only counted while speaking)
      this.raised = 0;      // seconds the raised-voice measure has been high (while speaking, not yet loud)
      this.calm = 0;        // seconds it has been low (while loud)
      this.loudFor = 0;     // seconds spent loud so far
    }

    // db: the level of the latest block of the voice (dBFS; -100 for nothing); s: the raised-voice measure, 0..1; dt: seconds since the last call
    update(db, s, dt) {
      const o = this.o;
      const x = Math.max(-100, Number.isFinite(db) ? db : -100);
      this.lv += (x - this.lv) * (1 - Math.exp(-dt / (x > this.lv ? o.attackTau : o.releaseTau)));
      const voiced = this.lv > o.floorDb;
      if (this.level === 0) {
        this.over = voiced ? this.over + dt : 0;
        if (this.over >= o.onS) { this.level = 1; this.over = 0; this.under = 0; this.raised = 0; this.calm = 0; }
      } else {
        this.under = voiced ? 0 : this.under + dt;
        if (this.under >= o.holdS) { this.level = 0; this.over = 0; this.under = 0; this.raised = 0; this.calm = 0; return 0; }
        if (this.level === 1) {
          this.raised = s >= o.highS ? this.raised + dt : 0;
          if (this.raised >= o.highOnS) { this.level = 2; this.raised = 0; this.calm = 0; this.loudFor = 0; }
        } else {
          this.loudFor += dt;
          this.calm = s < o.lowS ? this.calm + dt : 0;
          if (this.calm >= o.highHoldS && this.loudFor >= o.minHighS) { this.level = 1; this.raised = 0; this.calm = 0; }
        }
      }
      return this.level;
    }
  }

  // the level of a block of samples in dBFS
  function levelDb(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    return 20 * Math.log10(Math.sqrt(sum / Math.max(1, samples.length)) + 1e-9);
  }

  // ---- where a voice is heard from. `az` is the speaker's direction in degrees from straight ahead of YOU (the way your character faces):
  // +90 is directly to your right, -90 to your left, 180 behind; null when the game cannot say. `dist` is yards.
  //   mono        no direction at all.
  //   speakers    an equal-power pan, at most 80% to one side (loudspeakers already stand apart: a hard pan sounds like a dead channel),
  //               and a voice behind you a little quieter (two speakers in front cannot put a sound behind you: quieter is the cue left).
  //   headphones  a point on a circle round your head for the HRTF panner: left/right AND front/back, at full width.
  // A player standing on top of you has no meaningful direction (it flips as either of you shuffles): inside 1 yd a voice is centred, and the
  // direction comes in over the next 3 yd. The answer is { pan -1..1, gain, x, z } (x right, z back: Web Audio's listener looks along -z).
  const SPATIAL_MODES = ['mono', 'speakers', 'headphones'];
  const normSpatial = (v) => (SPATIAL_MODES.indexOf(v) >= 0 ? v : 'headphones');
  const SPATIAL = { nearFlat: 1, nearFull: 4, speakerWidth: 0.8, behindCut: 0.25, azTau: 0.12 };
  function spatialFor(mode, az, dist) {
    const centre = { pan: 0, gain: 1, x: 0, z: -1, width: 0 };
    if (mode === 'mono' || az === null || az === undefined || !isFinite(az)) return centre;
    const w = (dist === null || dist === undefined || !isFinite(dist)) ? 1 : clamp((dist - SPATIAL.nearFlat) / (SPATIAL.nearFull - SPATIAL.nearFlat), 0, 1);
    const r = az * Math.PI / 180, s = Math.sin(r), c = Math.cos(r);
    if (mode === 'speakers') {
      return { pan: SPATIAL.speakerWidth * s * w, gain: 1 - SPATIAL.behindCut * w * (1 - c) / 2, x: 0, z: -1, width: w };
    }
    return { pan: s * w, gain: 1, x: s * w, z: -(c * w + (1 - w)), width: w };
  }
  // the shortest way round from one direction to another, in degrees (-180..180]
  function azDelta(from, to) {
    let d = (to - from) % 360;
    if (d > 180) d -= 360;
    if (d <= -180) d += 360;
    return d;
  }

  const BOOST = { off: 1, low: 1.4, normal: 2, high: 3 };           // the boost option: how much louder a voice is right next to you
  const normBoost = (v) => (v === 'off' || v === 'low' || v === 'normal' || v === 'high' ? v : 'normal');

  root.VoiceModel = { normBitrate, DEFAULT_CURVE, FOLLOW, flatFor, boostFor, curveGain, LEVEL, levelingDb, BOOST, normBoost, LOUD, Loudness, VA, VoiceActivity, levelDb, clamp, SPATIAL_MODES, normSpatial, SPATIAL, spatialFor, azDelta };
})(typeof window !== 'undefined' ? window : globalThis);
