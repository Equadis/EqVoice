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
  //
  // The NATURAL curve (the default; `points` set) replaces the plain one above: a voice carries the way a real one does, getting steadily
  // and smoothly louder as you walk up to it, with no flat stretch and no step. It is a smooth curve through these (yards, decibels)
  // for an ordinary voice: 100% to 5 yd, 50% at 20 yd, 15% at 40 yd, 4% at 60 yd, and nothing at the end (`end`, 85 yd). A raised voice
  // is heard as if the speaker were `yell` times as far away (0.65: at 70 yd a shout is 10%). The last 10 yd before `end` ease to nothing.
  const NATURAL_POINTS = [[0, 0], [5, 0], [20, -6.0206], [40, -16.4782], [60, -28], [85, -60]];
  const DEFAULT_CURVE = { boost: 1, boost_r: 0, boost_fade: 10, full: 30, full_loud: 45, end: 85, power: 1.6, points: NATURAL_POINTS, yell: 0.65, whisper: 2, quiet: 1.6 };

  // a smooth curve through the points that never overshoots them (a monotone cubic, Fritsch-Carlson), flat outside the first and last
  function throughPoints(pts, x) {
    const n = pts.length;
    if (x <= pts[0][0]) return pts[0][1];
    if (x >= pts[n - 1][0]) return pts[n - 1][1];
    const h = [], d = [];
    for (let i = 0; i < n - 1; i++) { h[i] = pts[i + 1][0] - pts[i][0]; d[i] = (pts[i + 1][1] - pts[i][1]) / h[i]; }
    const m = new Array(n);
    m[0] = d[0]; m[n - 1] = d[n - 2];
    for (let i = 1; i < n - 1; i++) {
      if (d[i - 1] * d[i] <= 0) m[i] = 0;
      else { const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1]; m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]); }
    }
    let k = 0;
    while (k < n - 2 && x >= pts[k + 1][0]) k++;
    const t = (x - pts[k][0]) / h[k], t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * pts[k][1] + (t3 - 2 * t2 + t) * h[k] * m[k] + (-2 * t3 + 3 * t2) * pts[k + 1][1] + (t3 - t2) * h[k] * m[k + 1];
  }
  const yellScale = (curve, loudness) => 1 - clamp(loudness, 0, 1) * (1 - (curve.yell || 1));

  // The distance reaches the page a few times a second, and a person walks several yards in that time, so the page follows it rather than
  // jumping to it (`distTau` seconds), and the volume follows the curve a little more slowly still (`gainTau`): the volume, the boost most
  // of all, rises and falls as you walk and never in steps.
  const FOLLOW = { distTau: 0.3, gainTau: 0.08 };

  function flatFor(curve, loudness) {
    if (Array.isArray(curve.points)) return curve.points[1][0] / yellScale(curve, loudness);       // the natural curve: full volume this far out
    return curve.full + clamp(loudness, 0, 1) * Math.max(0, (curve.full_loud || curve.full) - curve.full);
  }

  function boostFor(dist, curve) {
    const boost = Math.max(1, curve.boost || 1);
    const hold = curve.boost_r || 0, fade = curve.boost_fade || 0;
    if (boost === 1 || dist >= fade) return 1;
    const t = dist <= hold ? 0 : (dist - hold) / (fade - hold);              // 0 right next to them .. 1 where the boost has gone
    return Math.pow(boost, 0.5 * (1 + Math.cos(Math.PI * t)));
  }

  // `whisper`: the speaker holds the whisper key: they are heard as if twice as far away (the range is halved, as in Simple Voice Chat)
  // `quiet` (0..1): the speaker is speaking quietly: heard as if up to `curve.quiet` (1.6) times as far away, so a quiet voice carries less far
  function curveGain(dist0, curve, loudness, whisper, quiet) {
    const qm = 1 + clamp(quiet || 0, 0, 1) * ((curve.quiet || 1) - 1);
    const dist = dist0 * (whisper ? (curve.whisper || 2) : 1) * qm;
    const boost = boostFor(dist, curve);
    if (Array.isArray(curve.points)) {
      if (dist >= curve.end) return 0;
      const db = throughPoints(curve.points, dist * yellScale(curve, loudness));
      const left = clamp((curve.end - dist) / 10, 0, 1);                                // the last 10 yd ease out
      return boost * Math.pow(10, db / 20) * left * left * (3 - 2 * left);
    }
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
  const LOUD = { floorDb: -52, smooth: 0.35, q: 0.9, eta: 0.1, warmTicks: 150, l0: 2, l1: 10, holdS: 1.5, quiet0: 7, quiet1: 15, quietHoldS: 0.5, refVoiced: 1500 };

  class Loudness {
    constructor(opts) {
      this.o = Object.assign({}, LOUD, opts || {});
      this.lv = null;       // smoothed level of the voice now (dBFS)
      this.base = null;     // the speaker's usual loud-syllable level (dBFS)
      this.ref = null;      // their average level over the last half minute of speech (dBFS): what "quiet" is measured against
      this.voiced = 0;
      this.s = 0;           // 0..1: how raised the voice is
      this.q = 0;           // 0..1: how far below their usual level (a quiet voice: it carries less far)
      this.level = -100;    // the last raw level given
    }

    // has this speaker been heard long enough for `base` to mean something?
    get ready() { return this.voiced >= this.o.warmTicks; }

    // db: level of the latest ~20 ms of the decoded voice; dt: seconds since the previous call
    update(db, dt) {
      const o = this.o;
      this.level = db;
      let target = 0, quiet = 0;
      if (db > o.floorDb) {
        this.lv = this.lv === null ? db : this.lv + o.smooth * (db - this.lv);
        if (this.base === null) this.base = this.lv;
        else this.base += (this.lv > this.base ? o.q : -(1 - o.q)) * o.eta;
        this.voiced++;
        // the average follows the voice slowly (a mean of the first seconds, then 30 s of speech): a shout does not move it far, so ordinary speech
        // after a shout is not mistaken for a quiet voice, which the quicker, upward-biased `base` would do
        this.ref = this.ref === null ? this.lv : this.ref + (this.lv - this.ref) * Math.max(1 / this.voiced, 1 / o.refVoiced);
        if (this.voiced >= o.warmTicks) {
          target = clamp((this.lv - this.base - o.l0) / (o.l1 - o.l0), 0, 1);
          quiet = clamp((this.ref - this.lv - o.quiet0) / (o.quiet1 - o.quiet0), 0, 1);       // 7 dB under their average starts it, 15 dB is as quiet as it counts
        }
      }
      this.s = Math.max(target, this.s - dt / o.holdS);
      this.q = Math.max(quiet, this.q - dt / o.quietHoldS);
      return this.s;
    }
  }

  // ---- the little voice icon: nothing / quiet speech / ordinary speech / a raised voice, as a steady 0 / 1 / 2 / 3.
  //
  // Not the raw level (a breath, a key click or one noisy sample would flip it): the level is smoothed here (up in 40 ms, down in 120 ms: it really
  // falls in silence, which Loudness.lv does not, because that only moves while there is a voice), and the two measures Loudness keeps (s: how
  // raised, q: how quiet, both 0..1 and both relative to this speaker's own usual speech) go through hold times and hysteresis.
  //
  //   0 -> speech   the smoothed level has been over floorDb for onS (120 ms: a click is shorter than that)
  //   speech -> 0   it has been under floorDb for holdS (450 ms: the gap between two words is not the end of speech)
  //   normal -> 3   the raised-voice measure has been at highS (0.55) or more for highOnS (150 ms)
  //   3 -> normal   it has been under lowS (0.25) for highHoldS (500 ms), a lower bar than the one that raised it (hysteresis), and loud has lasted
  //                 at least minHighS (1 s): the measure dips while a long shout goes on (their baseline catches up), which must not flicker the icon
  //   normal -> 1   the quiet measure has been at quietHighS (0.55) or more for quietOnS (200 ms); 1 -> normal when it has been under quietLowS
  //                 (0.25) for quietHoldS (400 ms)
  //
  // "Raised" and "quiet" are relative to this speaker's own usual speech (Loudness keeps their baseline), which is also what lengthens the range of a
  // shout and shortens the range of a quiet voice: so the icon says what the volume curve is doing. A speaker is not "loud" or "quiet" until they have
  // been heard for about three seconds (the baseline has to exist first); until then ordinary speech shows as ordinary.
  const VA = { floorDb: -52, onS: 0.12, holdS: 0.45, highS: 0.55, lowS: 0.25, highOnS: 0.15, highHoldS: 0.5, minHighS: 1.0, attackTau: 0.04, releaseTau: 0.12,
               quietHighS: 0.55, quietLowS: 0.25, quietOnS: 0.2, quietHoldS: 0.4 };

  class VoiceActivity {
    constructor(opts) {
      this.o = Object.assign({}, VA, opts || {});
      this.level = 0;       // 0 nothing, 1 speaking (inside: quiet or ordinary), 2 a raised voice
      this.quiet = false;   // ... and, while speaking, whether it is a quiet voice
      this.lv = -100;       // the level, smoothed (dBFS)
      this.over = 0;        // seconds the level has been over the floor (only counted while it is not yet speech)
      this.under = 0;       // seconds it has been under the floor (only counted while speaking)
      this.raised = 0;      // seconds the raised-voice measure has been high (while speaking, not yet loud)
      this.calm = 0;        // seconds it has been low (while loud)
      this.loudFor = 0;     // seconds spent loud so far
      this.qOn = 0;         // seconds the quiet measure has been high (ordinary) or low (quiet)
    }

    // db: the level of the latest block of the voice (dBFS; -100 for nothing); s: the raised-voice measure, 0..1; dt: seconds since the last call;
    // q: the quiet-voice measure, 0..1. Returns 0 nothing, 1 quiet, 2 ordinary, 3 raised.
    update(db, s, dt, q) {
      const o = this.o;
      q = q || 0;
      const x = Math.max(-100, Number.isFinite(db) ? db : -100);
      this.lv += (x - this.lv) * (1 - Math.exp(-dt / (x > this.lv ? o.attackTau : o.releaseTau)));
      const voiced = this.lv > o.floorDb;
      if (this.level === 0) {
        this.over = voiced ? this.over + dt : 0;
        if (this.over >= o.onS) { this.level = 1; this.over = 0; this.under = 0; this.raised = 0; this.calm = 0; this.quiet = false; this.qOn = 0; }
      } else {
        this.under = voiced ? 0 : this.under + dt;
        if (this.under >= o.holdS) { this.level = 0; this.over = 0; this.under = 0; this.raised = 0; this.calm = 0; this.quiet = false; this.qOn = 0; return 0; }
        if (this.level === 1) {
          this.raised = s >= o.highS ? this.raised + dt : 0;
          if (this.raised >= o.highOnS) { this.level = 2; this.raised = 0; this.calm = 0; this.loudFor = 0; this.quiet = false; this.qOn = 0; }
          else if (!this.quiet) {
            this.qOn = q >= o.quietHighS ? this.qOn + dt : 0;
            if (this.qOn >= o.quietOnS) { this.quiet = true; this.qOn = 0; }
          } else {
            this.qOn = q < o.quietLowS ? this.qOn + dt : 0;
            if (this.qOn >= o.quietHoldS) { this.quiet = false; this.qOn = 0; }
          }
        } else {
          this.loudFor += dt;
          this.calm = s < o.lowS ? this.calm + dt : 0;
          if (this.calm >= o.highHoldS && this.loudFor >= o.minHighS) { this.level = 1; this.raised = 0; this.calm = 0; this.quiet = false; this.qOn = 0; }
        }
      }
      return this.level === 0 ? 0 : this.level === 2 ? 3 : (this.quiet ? 1 : 2);
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

  root.VoiceModel = { normBitrate, DEFAULT_CURVE, NATURAL_POINTS, FOLLOW, flatFor, boostFor, curveGain, LEVEL, levelingDb, BOOST, normBoost, LOUD, Loudness, VA, VoiceActivity, levelDb, clamp, SPATIAL_MODES, normSpatial, SPATIAL, spatialFor, azDelta };
})(typeof window !== 'undefined' ? window : globalThis);
