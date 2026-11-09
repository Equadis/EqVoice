// The keyboard filter: an audio worklet that keeps short noises (keyboard clacks, mouse clicks, a cup put down) off the microphone.
//
// How: a voice is a sound that lasts and holds its level. The gate listens to the band where speech lives (200 Hz to 3.5 kHz), and opens
// only when that band has been clearly above the room's noise floor, and not dying away, for `sustainMs` in a row. A keystroke is a burst
// and a thump that ring down within a few tens of milliseconds, so it never counts: and once the gate is open, only sound that holds its
// level keeps it open, so typing straight after talking does not hold it open either.
// The audio itself is delayed by the same `sustainMs` (a look-ahead), so when the gate does open for a word the gate is open from the
// very start of that word: nothing is clipped. The gate then stays open for `hangMs` after the voice stops (so word endings and
// short pauses are kept) and closes smoothly. While you are talking, a click inside the speech passes with it: nothing can tell them
// apart there, and it is masked.
//
// "Clearly above" means, in the automatic mode, `openDb` (9 dB) over the floor and never below `minDb` (-68 dB in that band: a quiet voice
// sits around -60). The room's noise floor is tracked all the time (it follows the quietest recent level up at half a decibel a second),
// so a fan or a hiss raises the bar by itself. In the manual mode the bar is `thresholdDb`, the player's own setting (3 dB lower once the
// gate is open, so a voice that sits right on the bar does not flutter). The sustain, look-ahead and hang rules are the same in both modes:
// the manual bar replaces only "how far above the room", so keystrokes are still kept out. The cost is `sustainMs` of delay.
//
// Loaded by the media page with audioWorklet.addModule('/static/gate-worklet.js'); the tests (tests/test_keyboard_filter.py) load the same file.

class VoiceGate extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = Object.assign({ sustainMs: 50, hangMs: 220, openDb: 9, minDb: -68, fallDb: 3, enabled: true, mode: 'auto', thresholdDb: -48 },
                            (options && options.processorOptions) || {});
    this.o = o;
    // look-ahead: the sustain time plus a little for the block the decision is taken at the end of, so the gate is open before the first sound
    this.la = Math.max(1, Math.round(sampleRate * (o.sustainMs + 8) / 1000));
    this.N = 1 << Math.ceil(Math.log2(this.la + 256));
    this.ring = new Float32Array(this.N);
    this.w = 0;
    this.blockS = 128 / sampleRate;
    this.sustainBlocks = Math.max(1, Math.round(o.sustainMs / 1000 / this.blockS));
    this.hangBlocks = Math.max(1, Math.round(o.hangMs / 1000 / this.blockS));
    this.refreshBlocks = Math.max(1, Math.round(0.015 / this.blockS));      // an open gate is held open only by 15 ms or more of steady voice
    this.run = 0;                    // consecutive blocks that were voice (clearly above the floor and not dying away)
    this.since = 1e9;                // blocks since the last one
    this.pw = new Float64Array(8);   // the power of the last blocks, and its 4-block average (about one pitch period), for the "dying away" test
    this.sm = new Float64Array(8);
    this.bi = 0;
    this.open = false;
    this.floorDb = null;             // the room's noise level in that band (dB): not known until the first real block
    this.warmBlocks = Math.round(3 / this.blockS);
    this.g = 0;                      // the gain being applied (0 shut .. 1 open)
    this.ka = 1 - Math.exp(-1 / (0.003 * sampleRate));
    this.kr = 1 - Math.exp(-1 / (0.040 * sampleRate));
    // the analysis band: a 200 Hz high-pass then a 3.5 kHz low-pass (biquads, RBJ cookbook)
    this.hp = this.biquad('hp', 200);
    this.lp = this.biquad('lp', 3500);
    this.blocks = 0;
    this.openBlocks = 0;
    this.level = -100;               // the band's level now (dB), smoothed for the meter
    this.thr = o.minDb;              // the bar in force at the last block (dB)
    this.port.onmessage = (e) => {
      const d = e.data || {};
      if (typeof d.enabled === 'boolean') this.o.enabled = d.enabled;
      if (d.mode === 'auto' || d.mode === 'manual') this.o.mode = d.mode;
      if (typeof d.thresholdDb === 'number' && isFinite(d.thresholdDb)) this.o.thresholdDb = Math.max(-100, Math.min(0, d.thresholdDb));
    };
  }

  biquad(kind, f) {
    const w0 = 2 * Math.PI * f / sampleRate, cos = Math.cos(w0), alpha = Math.sin(w0) / (2 * 0.7071);
    let b0, b1, b2;
    if (kind === 'hp') { b0 = (1 + cos) / 2; b1 = -(1 + cos); b2 = (1 + cos) / 2; }
    else { b0 = (1 - cos) / 2; b1 = 1 - cos; b2 = (1 - cos) / 2; }
    const a0 = 1 + alpha;
    return { b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: (-2 * cos) / a0, a2: (1 - alpha) / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
  }

  step(f, x) {
    const y = f.b0 * x + f.b1 * f.x1 + f.b2 * f.x2 - f.a1 * f.y1 - f.a2 * f.y2;
    f.x2 = f.x1; f.x1 = x; f.y2 = f.y1; f.y1 = y;
    return y;
  }

  process(inputs, outputs) {
    const out = outputs[0];
    if (!out || !out.length) return true;
    const inp = inputs[0] && inputs[0][0];
    const n = out[0].length;
    if (!this.o.enabled) {                                   // switched off: straight through (no delay, nothing changes)
      for (let c = 0; c < out.length; c++) { if (inp) out[c].set(inp); else out[c].fill(0); }
      return true;
    }
    let e = 0;
    for (let i = 0; i < n; i++) {
      const x = inp ? inp[i] : 0;
      this.ring[this.w] = x;
      const y = this.ring[(this.w - this.la + this.N) % this.N];
      this.w = (this.w + 1) % this.N;
      const b = this.step(this.lp, this.step(this.hp, x));
      e += b * b;
      // the decision for the whole block is taken at its end (below); the gain follows it sample by sample
      const target = this.open ? 1 : 0;
      this.g += (target - this.g) * (target > this.g ? this.ka : this.kr);
      const v = y * this.g;
      for (let c = 0; c < out.length; c++) out[c][i] = v;
    }
    const i0 = this.bi;
    this.pw[i0 & 7] = e / Math.max(1, n) + 1e-12;
    const smooth = (this.pw[i0 & 7] + this.pw[(i0 - 1) & 7] + this.pw[(i0 - 2) & 7] + this.pw[(i0 - 3) & 7]) / 4;
    this.sm[i0 & 7] = smooth;
    const before = this.sm[(i0 - 4) & 7];
    this.bi++;
    const db = 10 * Math.log10(smooth);
    // The floor follows the quietest recent level: down at once, up slowly (half a decibel a second, so a long held note is not mistaken
    // for the room), but in the first three seconds up fast, because the first level seen is as likely to be a click as the room.
    // Digital silence (the stream's very first blocks) says nothing about the room and is ignored.
    if (db > -100) {
      if (this.floorDb === null || db < this.floorDb) this.floorDb = db;
      else this.floorDb += (this.blocks < this.warmBlocks ? 20 : 0.5) * this.blockS;
      if (this.floorDb > -35) this.floorDb = -35;
    }
    const manual = this.o.mode === 'manual';
    this.thr = manual ? this.o.thresholdDb - (this.open ? 3 : 0) : Math.max(this.o.minDb, (this.floorDb === null ? 0 : this.floorDb) + this.o.openDb);
    this.level += (Math.max(db, -100) - this.level) * (db > this.level ? 0.5 : 0.15);
    const loud = (manual || this.floorDb !== null) && db > this.thr;
    const dying = this.bi > 8 && smooth < before * Math.pow(10, -this.o.fallDb / 10);        // down by `fallDb` or more in 4 blocks (11 ms)
    const voice = loud && !dying;
    if (voice) this.run++; else this.run = 0;
    if (!this.open) {
      if (this.run >= this.sustainBlocks) { this.open = true; this.since = 0; }
    } else {
      // the rising edge of a click is 'voice' for a block or two: it must not hold the gate open, only a voice that lasts does
      if (this.run >= this.refreshBlocks) this.since = 0; else this.since++;
      if (this.since > this.hangBlocks) this.open = false;
    }
    this.blocks++;
    if (this.open) this.openBlocks++;
    if (this.blocks % Math.round(0.05 / this.blockS) === 0) {               // a heartbeat twenty times a second: the page watches that it is alive, and the game's meter moves with it
      this.port.postMessage({ hb: this.blocks, open: this.openBlocks / Math.max(1, this.blocks), floor: this.floorDb === null ? null : Math.round(this.floorDb * 10) / 10, isOpen: this.open,
                              level: Math.round(this.level * 10) / 10, thr: Math.round(this.thr * 10) / 10 });
    }
    return true;
  }
}

registerProcessor('voice-gate', VoiceGate);
