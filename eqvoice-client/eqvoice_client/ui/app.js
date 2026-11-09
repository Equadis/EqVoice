'use strict';
/*
 * EqVoice page: the WebRTC media engine plus the control surface.
 *
 * All decisions (who to connect to, when to fade, when to close) are made by the
 * Python client. This page only does what the browser alone can do: capture the
 * microphone, hold RTCPeerConnections, play remote audio, and report back what
 * happened. Commands arrive as {t:'cmd', cmd:{...}}; events leave as {t:'peer'|'mic'|'devices'|'ui'}.
 */

const params = new URLSearchParams(location.search);
const TOKEN = params.get('token') || '';
// The packaged EqVoice keeps its voice engine in a window nobody sees (so there is no taskbar button) and opens the real window from the tray
// on request: role=engine is the first, role=ui the second. With no role one page does both, as it always did.
const ROLE = params.get('role') || '';
if (ROLE === 'engine') document.title = 'EqVoice engine';
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- transport

let ws = null;
let wsOpen = false;
let S = {};                      // last state pushed by the client

function send(obj) {
  if (wsOpen) ws.send(JSON.stringify(obj));
}

let retryMs = 1000;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws/ui?token=${encodeURIComponent(TOKEN)}`);
  ws.onopen = () => {
    wsOpen = true;
    retryMs = 1000;
    send({ t: 'hello', role: ROLE, ua: navigator.userAgent, sink: 'setSinkId' in HTMLMediaElement.prototype, webdriver: !!navigator.webdriver });
  };
  ws.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch (_) { return; }
    if (m.t === 'state') { S = m; if (ROLE !== 'engine') render(); }
    else if (m.t === 'cmd') media.enqueue(m.cmd);
    else if (m.t === 'log') appendLog(m.line);
    else if (m.t === 'report') showReport(m);
    else if (m.t === 'cal_result') calResult(m);
    else if (m.t === 'cal_progress' && ROLE !== 'engine') calRender(m);
  };
  ws.onclose = () => {
    wsOpen = false;
    media.closeAll();
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10000);            // a page whose client is gone (or has another token) backs off
  };
}

// -------------------------------------------------------------------- opus

function tuneOpus(sdp) {
  const m = sdp.match(/a=rtpmap:(\d+) opus\/48000\/2/i);
  if (!m) return sdp;
  const pt = m[1];
  // maxaveragebitrate / maxplaybackrate are what THIS side is willing to receive: the other end's encoder honours them, so the cap
  // and a 16 kHz (wideband) ceiling apply both ways. A telephone-style voice has nothing above 4 kHz to spend bits on.
  const want = { minptime: '10', useinbandfec: '1', usedtx: '1', stereo: '0', 'sprop-stereo': '0',
                 maxaveragebitrate: String(media.sdpBitrate()), maxplaybackrate: '16000' };
  const re = new RegExp('a=fmtp:' + pt + ' ([^\\r\\n]*)');
  const merge = (params) => {
    const kv = new Map();
    params.split(';').map((s) => s.trim()).filter(Boolean).forEach((s) => {
      const i = s.indexOf('=');
      kv.set(i < 0 ? s : s.slice(0, i), i < 0 ? '' : s.slice(i + 1));
    });
    Object.keys(want).forEach((k) => kv.set(k, want[k]));
    return Array.from(kv).map(([k, v]) => (v === '' ? k : k + '=' + v)).join(';');
  };
  if (re.test(sdp)) return sdp.replace(re, (all, p) => 'a=fmtp:' + pt + ' ' + merge(p));
  return sdp.replace(new RegExp('(a=rtpmap:' + pt + ' opus/48000/2\\r?\\n)', 'i'), (all) => all + 'a=fmtp:' + pt + ' ' + merge('') + '\r\n');
}

// ------------------------------------------------------------------- media

// A coarse, privacy-preserving kind for an address (never the address itself reaches the log).
function ipClass(a) {
  if (!a) return 'unknown';
  if (/\.local$/i.test(a)) return 'mdns';
  const m = a.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (m) {
    const x = +m[1], y = +m[2];
    if (x === 127) return 'loopback';
    if (x === 169 && y === 254) return 'link-local';
    if (x === 100 && y >= 64 && y <= 127) return 'cgnat';
    if (x === 10 || (x === 172 && y >= 16 && y <= 31) || (x === 192 && y === 168)) return 'private';
    return 'public';
  }
  if (/^fe80/i.test(a)) return 'link-local';
  if (/^f[cd]/i.test(a)) return 'ula';
  return a.indexOf(':') >= 0 ? 'public6' : 'unknown';
}

function toneWav(freqs, secs) {
  const rate = 22050, per = Math.floor(rate * secs), n = per * freqs.length;
  const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const j = i % per, env = Math.min(1, j / 600, (per - j) / 600);
    v.setInt16(44 + i * 2, Math.round(Math.sin(2 * Math.PI * freqs[Math.floor(i / per)] * j / rate) * 0.5 * env * 32767), true);
  }
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

// The numbers (how far a voice carries, how loud it is up close, how a raised voice is recognised) are in voicemodel.js, loaded
// first, so that a real browser can test them on their own.
const VM = window.VoiceModel;
const { normBitrate } = VM;

class Media {
  constructor() {
    this.peers = new Map();
    this.queue = Promise.resolve();
    this.micStream = null;
    this.micTrack = null;           // what is sent: the microphone, or the microphone through the keyboard filter
    this.devTrack = null;           // the microphone itself
    this.meterTrack = null;
    this.analyser = null;
    this.gate = null;               // the keyboard filter, while it runs
    this.gateLoaded = false;
    this.ctx = null;
    this.txEnabled = false;
    this.deafened = false;
    this.outputId = '';
    this.inputId = '';
    this.relayOnly = false;
    this.processing = true;
    this.micState = { state: 'starting', label: '', level: 0 };
    this.selfLoud = new VM.Loudness({ warmTicks: 30, smooth: 0.5 });      // (read every 100 ms, not every 20)
    this.selfVa = new VM.VoiceActivity();
    this.selfLevel = 0;
    this.volumes = {};
    this.master = 1;
    this.inputGain = 1;             // how loud the microphone is before the keyboard filter: 0 .. 2 (1 = as it comes)
    this.sendChain = null;          // microphone -> volume -> (keyboard filter) -> the track that is sent; null while the raw microphone is sent
    this.cal = null;                // the microphone setup, while it runs
    this.bitrate = 24;              // kbps cap on the voice you send and ask for (0 = the browser's own default, about 32)
    this.curve = Object.assign({}, VM.DEFAULT_CURVE);     // how loud voices are at each distance, boost included (the app sends the real one)
    this.selfMute = false;          // told to us by the app: what the people we are connected to should know about us
    this.selfDeaf = false;
    this.gateMode = 'auto';         // the noise gate: 'auto' follows the room's noise, 'manual' uses gateDb
    this.gateDb = -48;
    this.autoLevel = true;          // lift quiet microphones and ease hot ones towards the same level
    this.keyboard = true;           // keep keyboard clacks and other short noises off the microphone
    this.autoGain = false;          // the browser's automatic microphone level: off, so that a louder voice is heard as louder
    this.fxBroken = '';             // set when the chain went silent or could not be built: everything then plays plain
    this.fxBuf = new Float32Array(512);
    this.lvBuf = new Float32Array(1024);
    this.lastMicSent = '';
    this.tickN = 0;                 // fadeTick runs 50 times a second when nothing slows the page down
    this.tickHz = 0;
    setInterval(() => { this.tickHz = this.tickN; this.tickN = 0; }, 1000);
    setInterval(() => this.fadeTick(), 20);
    setInterval(() => this.fxTick(), 500);
    setInterval(() => this.statsTick(), 500);
    setInterval(() => this.meterTick(), 100);
    setInterval(() => this.sendMic(false), 50);                      // the gate's level moves twenty times a second: the game's one bar follows it
    if (navigator.mediaDevices) navigator.mediaDevices.addEventListener('devicechange', () => this.sendDevices());
  }

  enqueue(cmd) {
    this.queue = this.queue.then(() => this.handle(cmd)).catch((e) => {
      console.error('cmd failed', cmd.cmd, e);
      if (cmd.sid) send({ t: 'peer', sid: cmd.sid, ep: cmd.ep, type: 'error', msg: String(e && e.message || e) });
    });
  }

  async handle(c) {
    switch (c.cmd) {
      case 'config':
        this.relayOnly = !!c.relay_only;
        this.processing = c.processing !== false;
        this.outputId = c.output || '';
        this.volumes = c.volumes || {};
        this.master = c.master == null ? 1 : Math.max(0, Math.min(1, Number(c.master)));
        if (c.input_gain != null && isFinite(Number(c.input_gain))) this.inputGain = Math.max(0, Math.min(2, Number(c.input_gain)));
        this.bitrate = normBitrate(c.bitrate);
        if (c.curve) this.curve = Object.assign({}, VM.DEFAULT_CURVE, c.curve);
        this.autoLevel = c.auto_level !== false;
        this.gateMode = c.gate_mode === 'manual' ? 'manual' : 'auto';
        if (isFinite(Number(c.gate_db))) this.gateDb = Number(c.gate_db);
        const kb = c.keyboard !== false;
        this.keyboard = kb;
        const kbChanged = this.chainWanted() !== !!this.sendChain;       // the keyboard filter or the volume now needs (or no longer needs) the chain
        if (this.sendChain) this.sendChain.inGain.gain.value = this.inputGain;
        this.autoGain = !!c.auto_gain;
        this.applyFxAll();
        this.applyBitrateAll();
        this.applySinkAll();
        if (c.input !== this.inputId || !this.micTrack || kbChanged) { this.inputId = c.input || ''; await this.initMic(); }
        break;
      case 'fail_chain': this.fxFailed('requested by a test'); break;       // the end-to-end test of the fallback
      case 'keyboard': this.keyboard = !!c.value; if (this.micTrack) await this.initMic(); break;
      case 'input': this.inputId = c.id || ''; await this.initMic(); break;
      case 'output': this.outputId = c.id || ''; this.applySinkAll(); break;
      case 'tx': this.txEnabled = !!c.enabled; if (this.micTrack) this.micTrack.enabled = this.txEnabled; break;
      case 'deafen': this.deafened = !!c.value; this.peers.forEach((p) => { p.audio.muted = this.deafened; }); break;
      case 'volume': this.volumes[c.sid] = c.value; { const p = this.peers.get(c.sid); if (p) p.userVol = c.value; } break;
      case 'master': this.master = Math.max(0, Math.min(1, Number(c.value))); break;
      case 'input_gain': await this.setInputGain(c.value); break;
      case 'calibrate': await this.startCalibration(c.plan); break;
      case 'calibrate_cancel': this.stopCalibration('cancelled'); break;
      case 'bitrate': this.bitrate = normBitrate(c.value); this.applyBitrateAll(); break;
      case 'curve': if (c.value) this.curve = Object.assign({}, VM.DEFAULT_CURVE, c.value); break;       // the boost option changed
      case 'level': this.autoLevel = !!c.value; break;
      case 'cue': this.cue(c.kind); break;
      case 'self_state': this.selfMute = !!c.mute; this.selfDeaf = !!c.deaf; this.peers.forEach((p) => this.sendState(p)); break;
      case 'gate':                                                          // the noise gate's mode and bar changed: the filter takes it live
        this.gateMode = c.mode === 'manual' ? 'manual' : 'auto';
        if (isFinite(Number(c.db))) this.gateDb = Number(c.db);
        if (this.gate) this.gate.node.port.postMessage({ mode: this.gateMode, thresholdDb: this.gateDb });
        break;
      case 'peer_mute': { const p = this.peers.get(c.sid); if (p && p.ep === c.ep) p.mute = !!c.value; } break;
      case 'open': await this.open(c); break;
      case 'remote': await this.remote(c); break;
      case 'candidate': await this.candidate(c); break;
      case 'gain': this.setGain(c); break;
      case 'tone': await this.tone(); break;
      case 'close': this.close(c.sid, c.ep); break;
      default: console.warn('unknown cmd', c);
    }
  }

  // ---- microphone

  async initMic() {
    this.releaseMic();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.setMic('unsupported', '');
      return;
    }
    try {
      const audio = { echoCancellation: this.processing, noiseSuppression: this.processing, autoGainControl: this.processing && this.autoGain, channelCount: 1 };
      if (this.inputId) audio.deviceId = { exact: this.inputId };
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio });
      } catch (e) {
        if (this.inputId && e.name === 'OverconstrainedError') {   // saved device is unplugged
          delete audio.deviceId;
          stream = await navigator.mediaDevices.getUserMedia({ audio });
        } else { throw e; }
      }
      this.devTrack = stream.getAudioTracks()[0];             // the microphone itself; micTrack is what is sent (the same track, or the filtered one)
      this.devTrack.addEventListener('ended', () => this.setMic('ended', ''));
      // A clone that stays enabled feeds the level meter while push-to-talk is up.
      this.meterTrack = this.devTrack.clone();
      this.meterTrack.enabled = true;
      this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
      const src = this.ctx.createMediaStreamSource(new MediaStream([this.meterTrack]));
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      src.connect(this.analyser);
      this.micTrack = await this.buildSendTrack(this.devTrack);
      this.micStream = new MediaStream([this.micTrack]);
      this.micTrack.enabled = this.txEnabled;
      this.peers.forEach((p) => { if (p.sender && p.send) p.sender.replaceTrack(this.micTrack).catch(() => {}); });
      this.setMic('ok', this.devTrack.label || 'default');
    } catch (e) {
      const name = e && e.name;
      this.setMic(name === 'NotAllowedError' ? 'denied' : name === 'NotFoundError' ? 'no_device' : 'error', String(e && e.message || e));
    }
    await this.sendDevices();
  }

  // ---- the microphone setup: three short recordings (silence, a normal voice, a raised voice) measured in the band the noise gate
  // listens to (200 Hz to 3.5 kHz, in dB: the gate's own measure), on the microphone as it comes, before the volume. The app turns the
  // readings into the gate's bar and the volume (calibration.py).

  async startCalibration(plan) {
    if (this.cal) return;
    const fail = (why) => { send({ t: 'cal', phase: 'failed', error: why }); calRender({ phase: 'failed', error: why }); };
    if (!this.meterTrack) { fail('the microphone is not ready'); return; }
    const ctx = this.ensureCtx();
    if (!ctx) { fail('there is no audio in this browser'); return; }
    if (ctx.state !== 'running') await Promise.race([ctx.resume(), new Promise((r) => setTimeout(r, 1500))]);
    if (ctx.state !== 'running') { fail('the audio is not running'); return; }
    const src = ctx.createMediaStreamSource(new MediaStream([this.meterTrack]));
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 200; hp.Q.value = 0.7071;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 3500; lp.Q.value = 0.7071;
    const band = ctx.createAnalyser(); band.fftSize = 1024;
    const raw = ctx.createAnalyser(); raw.fftSize = 2048;
    src.connect(hp); hp.connect(lp); lp.connect(band); src.connect(raw);
    const steps = Array.isArray(plan) && plan.length === 3 ? plan.map(Number) : [5, 7, 5];
    this.cal = { src, hp, lp, band, raw, steps, names: ['quiet', 'normal', 'loud'], settle: [1.0, 1.0, 0.8], i: 0, t0: performance.now(),
                 data: { quiet: [], normal: [], loud: [] }, peak: -100, lastSend: '', bufB: new Float32Array(1024), bufR: new Float32Array(2048) };
    this.cal.timer = setInterval(() => this.calTick(), 50);
    this.calTick();
  }

  calTick() {
    const c = this.cal;
    if (!c) return;
    const el = (performance.now() - c.t0) / 1000;
    if (el >= c.steps[c.i]) {
      c.i++;
      c.t0 = performance.now();
      if (c.i >= c.steps.length) { this.finishCalibration(); return; }
      return;
    }
    c.band.getFloatTimeDomainData(c.bufB);
    let s = 0;
    for (let k = 0; k < c.bufB.length; k++) s += c.bufB[k] * c.bufB[k];
    const db = Math.max(-100, 10 * Math.log10(s / c.bufB.length + 1e-12));
    c.raw.getFloatTimeDomainData(c.bufR);
    let pk = 0;
    for (let k = 0; k < c.bufR.length; k++) { const a = Math.abs(c.bufR[k]); if (a > pk) pk = a; }
    const pkDb = 20 * Math.log10(pk + 1e-9);
    const name = c.names[c.i];
    if (el >= c.settle[c.i]) {
      c.data[name].push(Math.round(db * 10) / 10);
      if (name === 'loud' && pkDb > c.peak) c.peak = pkDb;
    }
    const left = c.steps[c.i] - el;
    calRender({ phase: name, step: c.i + 1, left, level: db });
    const key = name + ':' + Math.ceil(left);
    if (key !== c.lastSend) { c.lastSend = key; send({ t: 'cal', phase: name, left }); }
    if (performance.now() - (c.lastLive || 0) >= 100) { c.lastLive = performance.now(); send({ t: 'cal', phase: name, left, level: Math.round(db * 10) / 10, live: true }); }
  }

  calStop() {
    const c = this.cal;
    if (!c) return null;
    clearInterval(c.timer);
    try { c.src.disconnect(); c.hp.disconnect(); c.lp.disconnect(); } catch (_) {}
    this.cal = null;
    return c;
  }

  finishCalibration() {
    const c = this.calStop();
    if (!c) return;
    calRender({ phase: 'working' });
    send({ t: 'cal', phase: 'done', quiet: c.data.quiet, normal: c.data.normal, loud: c.data.loud, peak: Math.round(c.peak * 10) / 10 });
  }

  stopCalibration(why) {
    if (!this.calStop()) return;
    send({ t: 'cal', phase: why });
    calRender({ phase: 'idle' });
  }

  releaseMic() {
    [this.micTrack, this.devTrack, this.meterTrack].forEach((t) => { if (t) t.stop(); });
    this.dropGate();
    this.calStop();
    this.micTrack = this.devTrack = this.meterTrack = this.analyser = null;
    this.micStream = null;
  }

  // ---- what is sent: the microphone through its volume and, when the keyboard filter is on, through a gate that opens only for a
  // sustained voice (gate-worklet.js). With the volume at 100% and the filter off the raw microphone is sent as it is.
  // Any trouble with the chain and the raw microphone is sent instead: being heard comes before being clean.

  chainWanted() { return this.keyboard || Math.abs(this.inputGain - 1) > 0.001; }

  async buildSendTrack(dev) {
    if (!this.chainWanted()) return dev;
    try {
      const ctx = this.ensureCtx();
      if (!ctx) throw new Error('no audio in this browser');
      if (this.keyboard && !ctx.audioWorklet) throw new Error('no audio worklet in this browser');
      if (ctx.state !== 'running') await Promise.race([ctx.resume(), new Promise((r) => setTimeout(r, 1500))]);
      if (ctx.state !== 'running') throw new Error('the audio context is not running');
      if (this.keyboard && !this.gateLoaded) { await ctx.audioWorklet.addModule('/static/gate-worklet.js'); this.gateLoaded = true; }
      const src = ctx.createMediaStreamSource(new MediaStream([dev]));
      const inGain = ctx.createGain();
      inGain.gain.value = this.inputGain;
      const dest = ctx.createMediaStreamDestination();
      src.connect(inGain);
      let node = null;
      if (this.keyboard) {
        node = new AudioWorkletNode(ctx, 'voice-gate', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
                                                         processorOptions: { mode: this.gateMode, thresholdDb: this.gateDb } });
        inGain.connect(node); node.connect(dest);
        this.gate = { src, node, dest, beat: performance.now(), isOpen: false, floor: null, level: null, thr: null };
        node.port.onmessage = (e) => {
          if (this.gate && this.gate.node === node && e.data && e.data.hb) {
            Object.assign(this.gate, { beat: performance.now(), isOpen: !!e.data.isOpen, floor: e.data.floor, level: e.data.level, thr: e.data.thr });
          }
        };
        node.onprocessorerror = () => this.gateFailed('the filter stopped');
      } else {
        inGain.connect(dest);
      }
      this.sendChain = { src, inGain, node, dest };
      return dest.stream.getAudioTracks()[0];
    } catch (e) {
      this.gate = null;
      this.sendChain = null;
      send({ t: 'gate', ok: false, error: String(e && e.message || e).slice(0, 160) });
      return dev;
    }
  }

  async setInputGain(v) {
    const g = Number(v);
    if (!isFinite(g)) return;
    this.inputGain = Math.max(0, Math.min(2, g));
    if (this.sendChain) { this.sendChain.inGain.gain.value = this.inputGain; return; }
    if (this.chainWanted() && this.micTrack) await this.initMic();        // the volume left 100% with nothing to carry it: build the chain
  }

  dropGate() {
    const c = this.sendChain;
    if (c) {
      try { c.src.disconnect(); c.inGain.disconnect(); if (c.node) { c.node.disconnect(); c.node.port.onmessage = null; } } catch (_) {}
    }
    this.sendChain = null;
    this.gate = null;
  }

  gateFailed(msg) {
    if (!this.gate) return;
    console.warn('keyboard filter off:', msg);
    this.dropGate();
    if (!this.devTrack) return;
    this.micTrack = this.devTrack;
    this.micStream = new MediaStream([this.micTrack]);
    this.micTrack.enabled = this.txEnabled;
    this.peers.forEach((p) => { if (p.sender) p.sender.replaceTrack(p.send ? this.micTrack : null).catch(() => {}); });
    send({ t: 'gate', ok: false, error: String(msg).slice(0, 160) });
  }

  setMic(state, label) {
    this.micState = { state, label, level: 0 };
    this.sendMic(true);
  }

  sendMic(force) {
    const m = { t: 'mic', state: this.micState.state, label: this.micState.label, level: Math.round(this.micState.level * 100) / 100, tx: this.txEnabled, pcs: this.peers.size, gate: !!this.gate, hz: Math.round(this.tickHz / 10) * 10, vl: this.selfLevel,
               gopen: this.gate ? this.gate.isOpen : null, gfloor: this.gate ? this.gate.floor : null,
               glevel: this.gate ? this.gate.level : null, gthr: this.gate ? this.gate.thr : null, ldb: this.micDb };
    const key = JSON.stringify(m);
    if (force || key !== this.lastMicSent) { this.lastMicSent = key; send(m); }
  }

  meterTick() {
    if (this.gate && performance.now() - this.gate.beat > 3000) this.gateFailed('the filter stopped answering');
    if (!this.analyser) return;
    const buf = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    const lvl = Math.min(1, rms * 4);
    this.micState.level = this.micState.level * 0.6 + lvl * 0.4;
    const dbMic = 20 * Math.log10(rms + 1e-9);
    this.micDb = Math.round(Math.max(-100, dbMic));                          // the broadband level, for the meter when no gate is running
    this.selfLoud.update(dbMic, 0.1);
    this.selfLevel = this.selfVa.update(dbMic, this.selfLoud.s, 0.1);       // my own icon: nothing / speaking / loud
    this.sendMic(false);
  }

  async sendDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      const pick = (kind) => list.filter((d) => d.kind === kind).map((d, i) => ({ id: d.deviceId, label: d.label || (kind === 'audioinput' ? 'Microphone ' : 'Speakers ') + (i + 1) }));
      send({ t: 'devices', inputs: pick('audioinput'), outputs: pick('audiooutput') });
    } catch (e) { console.warn('enumerateDevices', e); }
  }

  // ---- peers

  async open(c) {
    this.close(c.sid, undefined);
    const pc = new RTCPeerConnection({
      iceServers: c.ice || [],
      iceTransportPolicy: this.relayOnly ? 'relay' : 'all',
      bundlePolicy: 'max-bundle',
    });
    const audio = new Audio();
    audio.autoplay = true;
    audio.volume = 0;
    audio.muted = this.deafened;
    // Chromium only feeds a remote stream into Web Audio while the stream is also attached to a media element: a silent one does it
    const raw = new Audio();
    raw.autoplay = true;
    raw.muted = true;
    const t0 = performance.now();
    const since = () => Math.round(performance.now() - t0);
    const p = { sid: c.sid, ep: c.ep, pc, audio, raw, gain: 0, target: 0, step: 1, t0, userVol: this.volumes[c.sid] == null ? 1 : this.volumes[c.sid],
                remoteSet: false, pending: [], sender: null, send: false, prev: { t: 0, rx: 0, tx: 0 },
                stream: null, fx: null, route: 'none', outPeak: 0, silent: 0, lastRx: 0, maxbr: null, chainEnergy: 0, chainPeak: 0, lvMaxDb: -120,
                loud: new VM.Loudness(), va: new VM.VoiceActivity(), vlevel: 0, loudT: performance.now(), dist: null, distS: null, gate: true, slew: 0.1, value: 0, refDb: null, levDb: 0, mute: !!c.mute };
    this.peers.set(c.sid, p);
    // A small data channel beside the audio, the same on both ends (negotiated, id 1), carries what each side is doing: muted, deafened.
    try {
      p.dc = pc.createDataChannel('eqv', { negotiated: true, id: 1 });
      p.dc.onopen = () => this.sendState(p);
      p.dc.onmessage = (e) => {
        let m = null;
        try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m && m.t === 'state') send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'vstate', mute: !!m.mute, deaf: !!m.deaf });
      };
    } catch (e) { p.dc = null; console.warn('data channel', e); }
    if (this.micTrack) {
      p.sender = pc.addTrack(this.micTrack, this.micStream);
      await p.sender.replaceTrack(null);          // nothing is sent to a peer until the client says they are in range
    } else {
      pc.addTransceiver('audio', { direction: 'recvonly' });
    }
    pc.ontrack = (e) => {
      p.stream = e.streams && e.streams[0] ? e.streams[0] : new MediaStream([e.track]);
      this.applyFx(p);
      audio.play().catch(() => {});
      this.applySink(audio);
    };
    pc.onicecandidate = (e) => {
      if (e.candidate) send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'candidate', candidate: e.candidate.toJSON() });
    };
    const report = () => {
      send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'state', ice: pc.iceConnectionState, conn: pc.connectionState,
             gather: pc.iceGatheringState, ms: since() });
      if (pc.connectionState === 'connected') this.applyBitrate(p);
    };
    pc.oniceconnectionstatechange = report;
    pc.onconnectionstatechange = report;
    pc.onicegatheringstatechange = () => send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'gathering', state: pc.iceGatheringState, ms: since() });
    pc.onicecandidateerror = (e) => send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'ice_error', code: e.errorCode, ms: since(),
                                          text: String(e.errorText || '').slice(0, 120), url: String(e.url || '').slice(0, 100) });
    if (c.initiator) {
      const offer = await pc.createOffer({ offerToReceiveAudio: true });
      offer.sdp = tuneOpus(offer.sdp);
      await pc.setLocalDescription(offer);
      send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'description', sdp_type: 'offer', sdp: pc.localDescription.sdp });
    }
  }

  async remote(c) {
    const p = this.peers.get(c.sid);
    if (!p || p.ep !== c.ep) return;
    await p.pc.setRemoteDescription({ type: c.type, sdp: c.sdp });
    p.remoteSet = true;
    for (const cand of p.pending.splice(0)) await p.pc.addIceCandidate(cand).catch((e) => console.warn('ice', e));
    if (c.type === 'offer') {
      const ans = await p.pc.createAnswer();
      ans.sdp = tuneOpus(ans.sdp);
      await p.pc.setLocalDescription(ans);
      send({ t: 'peer', sid: c.sid, ep: c.ep, type: 'description', sdp_type: 'answer', sdp: p.pc.localDescription.sdp });
    }
  }

  async candidate(c) {
    const p = this.peers.get(c.sid);
    if (!p || p.ep !== c.ep) return;
    if (!p.remoteSet) p.pending.push(c.candidate);
    else await p.pc.addIceCandidate(c.candidate).catch((e) => console.warn('ice', e));
  }

  sendState(p) {
    if (p.dc && p.dc.readyState === 'open') {
      try { p.dc.send(JSON.stringify({ t: 'state', mute: this.selfMute, deaf: this.selfDeaf })); } catch (_) { /* a closing channel */ }
    }
  }

  setSend(p, on) {
    if (p.send === on) return;
    p.send = on;
    if (p.sender) p.sender.replaceTrack(on ? this.micTrack : null).catch(() => {});
  }

  setGain(c) {
    const p = this.peers.get(c.sid);
    if (!p || p.ep !== c.ep) return;
    if (c.send !== undefined) this.setSend(p, !!c.send);     // out of range: stop transmitting to them too
    // The app says where they are (dist, null = no reading right now: keep the last), whether they may be heard at all (gate: false
    // while leaving) and, as a fallback and a check, the volume of an ordinary voice there (value). The page works the real volume out
    // itself from the distance and how loud the speaker is being, so a raised voice carries further.
    if (c.dist !== undefined && c.dist !== null) p.dist = c.dist;
    p.gate = c.gate !== false;
    p.value = c.value;
    p.target = c.value;
    const ms = Math.max(1, c.ms || 1);
    p.step = Math.abs(p.target - p.gain) / (ms / 20) || 1;
    p.slew = Math.max(0.002, this.curve.boost / (ms / 20));
  }

  close(sid, ep) {
    const p = this.peers.get(sid);
    if (!p || (ep !== undefined && p.ep !== ep)) return;
    this.peers.delete(sid);
    try { p.pc.ontrack = p.pc.onicecandidate = p.pc.oniceconnectionstatechange = p.pc.onconnectionstatechange = null; if (p.dc) p.dc.onopen = p.dc.onmessage = null; p.pc.close(); } catch (_) {}
    p.audio.pause();
    p.audio.srcObject = null;
    p.raw.pause();
    p.raw.srcObject = null;
    this.dropFx(p);
  }

  closeAll() {
    Array.from(this.peers.keys()).forEach((sid) => this.close(sid, undefined));
  }

  fadeTick() {
    this.tickN++;
    const now = performance.now();
    this.peers.forEach((p) => {
      if (p.fx) {                                           // how loud the speaker is being, from the voice as it arrives
        p.fx.meter.getFloatTimeDomainData(this.lvBuf);
        const db = VM.levelDb(this.lvBuf), dt = Math.min(0.1, (now - p.loudT) / 1000), rms = Math.pow(10, db / 20);
        p.loud.update(db, dt);
        const vl = p.va.update(db, p.loud.s, dt);                   // the icon beside their name: nothing / speaking / loud
        if (vl !== p.vlevel) { p.vlevel = vl; send({ t: 'peer', sid: p.sid, ep: p.ep, type: 'vlevel', level: vl }); }
        // the browser's own energy and level figures stand still for a voice that goes through Web Audio, so the chain keeps them itself
        p.chainEnergy += rms * rms * dt;
        if (rms > p.chainPeak) p.chainPeak = rms;
        if (db > p.lvMaxDb) p.lvMaxDb = db;
        p.fx.an.getFloatTimeDomainData(this.fxBuf);         // and the loudest sample leaving the chain since the last report
        for (let i = 0; i < this.fxBuf.length; i++) { const a = Math.abs(this.fxBuf[i]); if (a > p.outPeak) p.outPeak = a; }
      }
      const dtl = Math.min(0.1, (now - p.loudT) / 1000);
      p.loudT = now;
      let target = p.target, step = p.step, smooth = false;
      if (p.dist !== null && p.dist !== undefined) {         // the page's own curve: a raised voice stays louder further out
        // the distance arrives a few times a second: follow it, so the volume (the boost most of all) changes continuously as you walk
        p.distS = p.distS === null ? p.dist : p.distS + (p.dist - p.distS) * (1 - Math.exp(-dtl / VM.FOLLOW.distTau));
        target = p.gate ? VM.curveGain(p.distS, this.curve, p.loud.s) : 0;
        step = p.slew;
        smooth = p.gate;                                     // leaving still ramps down in a straight line, so it ends exactly at silence
      }
      if (p.gain !== target) {
        const d = target - p.gain;
        if (smooth) p.gain = Math.abs(d) < 1e-4 ? target : p.gain + d * (1 - Math.exp(-dtl / VM.FOLLOW.gainTau));
        else p.gain = Math.abs(d) <= step ? target : p.gain + Math.sign(d) * step;
      }
      // Levelling: this speaker's usual loudness, as heard here, against the common target. The reference only follows their usual
      // level while they are not raising their voice, so that shouting is still heard as louder.
      if (this.autoLevel && p.loud.ready && p.loud.s < 0.15) p.refDb = p.loud.base;
      const wantDb = this.autoLevel && p.refDb !== null ? VM.levelingDb(p.refDb) : 0;
      p.levDb += (wantDb - p.levDb) * (1 - Math.exp(-dtl / VM.LEVEL.tau));
      const total = p.mute ? 0 : p.gain * Math.pow(10, p.levDb / 20) * p.userVol * this.master;       // a muted player (the mute list) is never played
      p.audio.volume = Math.max(0, Math.min(1, total));      // the element carries up to full volume ...
      if (p.fx) p.fx.boost.gain.value = Math.max(1, Math.min(16, total));     // ... the boost everything above it (close up, a quiet microphone)
    });
  }

  // ---- how a voice is played: the chain every voice goes through (meter, boost, limiter) and the bitrate cap

  sdpBitrate() { return (this.bitrate > 0 ? this.bitrate : 32) * 1000; }

  // 'direct' (the stream straight to the speakers) only while the chain cannot run or has failed; otherwise 'chain'
  routeWanted() { return this.fxBroken ? 'direct' : 'chain'; }

  chainOn(p) { return p.route === 'chain'; }

  ensureCtx() {
    try {
      this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
      return this.ctx;
    } catch (e) { return null; }
  }

  // remote stream -> meter (how loud the speaker is being)
  //               -> boost -> limiter -> a stream the output element plays
  // The element keeps the speaker choice, the volume (up to full) and deafen exactly as before; the boost carries the volume above
  // full (a voice doubled up close) and the limiter keeps that from clipping.
  buildFx(p, ctx) {
    const src = ctx.createMediaStreamSource(p.stream);
    const meter = ctx.createAnalyser(); meter.fftSize = 1024;
    const boost = ctx.createGain();
    const lim = ctx.createDynamicsCompressor();
    lim.threshold.value = -10; lim.knee.value = 6; lim.ratio.value = 12; lim.attack.value = 0.003; lim.release.value = 0.12;
    const dest = ctx.createMediaStreamDestination();
    const an = ctx.createAnalyser(); an.fftSize = 512;
    src.connect(meter); src.connect(boost); boost.connect(lim);
    lim.connect(dest); lim.connect(an);
    p.fx = { src, meter, boost, lim, dest, an };
  }

  routeDirect(p) {
    if (!p.stream) return;
    if (p.route !== 'direct') {
      p.audio.srcObject = p.stream;
      p.audio.play().catch(() => {});
      p.raw.pause();
      p.raw.srcObject = null;
      p.route = 'direct';
    }
    this.dropFx(p);
  }

  routeProcessed(p, name) {
    if (!this.chainOn(p)) {
      p.raw.srcObject = p.stream;
      p.raw.play().catch(() => {});
      p.audio.srcObject = p.fx.dest.stream;
      p.audio.play().catch(() => {});
    }
    p.route = name;
    p.silent = 0;
  }

  dropFx(p) {
    if (!p.fx) return;
    try { p.fx.src.disconnect(); p.fx.boost.disconnect(); } catch (_) {}
    p.fx = null;
  }

  applyFx(p) {
    if (!p.stream) return;
    const want = this.routeWanted();
    const ctx = want === 'direct' ? null : this.ensureCtx();
    if (want === 'direct' || !ctx || ctx.state !== 'running') {
      this.routeDirect(p);                       // plain until the context runs: fxTick tries again
      return;
    }
    try {
      if (!p.fx) this.buildFx(p, ctx);
      this.routeProcessed(p, want);
    } catch (e) { this.fxFailed('could not build the audio chain: ' + String(e && e.message || e)); }
  }

  applyFxAll() { this.peers.forEach((p) => this.applyFx(p)); }

  // Anything wrong with the chain must never cost anyone their voices: every peer goes back to the plain path and the app is told.
  fxFailed(msg) {
    if (this.fxBroken) return;
    this.fxBroken = msg;
    console.warn('audio chain switched off:', msg);
    this.peers.forEach((p) => this.routeDirect(p));
    send({ t: 'fx', ok: false, error: String(msg).slice(0, 160) });
  }

  fxTick() {
    const want = this.routeWanted();
    const running = !!this.ctx && this.ctx.state === 'running';
    if (want !== 'direct' && this.ctx && this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
    this.peers.forEach((p) => {
      if (!p.stream) return;
      if (want !== 'direct' && running && p.route !== want) this.applyFx(p);
      else if (this.chainOn(p) && !running) this.routeDirect(p);
    });
  }

  // Is a voice arriving that the chain is not delivering? Bytes over the wire say a voice is arriving (speech is about 3000 a second;
  // the pauses between are a few dozen); the meter at the chain's input says whether Web Audio is getting it, and the one at its
  // output whether it comes out. Five half-seconds in a row of "arriving but not heard" and everything goes back to the plain path.
  fxWatchdog(p, rx) {
    const got = rx - p.lastRx;
    p.lastRx = rx;
    if (!this.chainOn(p)) { p.silent = 0; return; }
    const arriving = got > 400;
    const notIn = p.lvMaxDb < -75;
    const notOut = p.lvMaxDb > -60 && p.outPeak < 0.001;
    if (arriving && (notIn || notOut)) p.silent++; else p.silent = 0;
    if (p.silent >= 5) this.fxFailed('the audio chain produced silence while a voice was arriving');
  }

  async applyBitrate(p) {
    if (!p.sender) return;
    try {
      const prm = p.sender.getParameters();
      if (!prm.encodings || !prm.encodings.length) prm.encodings = [{}];
      if (this.bitrate > 0) prm.encodings[0].maxBitrate = this.bitrate * 1000;
      else delete prm.encodings[0].maxBitrate;
      await p.sender.setParameters(prm);
      p.maxbr = this.bitrate;
    } catch (e) { console.warn('maxBitrate', e); }
  }

  applyBitrateAll() { this.peers.forEach((p) => { if (p.pc.connectionState === 'connected') this.applyBitrate(p); }); }

  applySink(audio) {
    if (audio.setSinkId) audio.setSinkId(this.outputId || '').catch((e) => console.warn('setSinkId', e));
  }

  applySinkAll() { this.peers.forEach((p) => this.applySink(p.audio)); }

  // Two rising notes through the same output the voices use: proves the speakers, apart from any peer.
  async tone() {
    const a = new Audio(toneWav([660, 880], 0.35));
    a.volume = 0.6;
    let err = '';
    try {
      if (a.setSinkId) await a.setSinkId(this.outputId || '');
      await a.play();
    } catch (e) { err = String(e && e.message || e); }
    a.onended = () => URL.revokeObjectURL(a.src);
    send({ t: 'tone', ok: !err, error: err, sink: a.sinkId || '' });
  }

  // A short blip when you mute, unmute, deafen or undeafen (falling = off, rising = on), through the same speakers as the voices.
  cue(kind) {
    const notes = { mute: [620, 420], unmute: [420, 620], deafen: [520, 390, 280], undeafen: [280, 390, 520] }[kind];
    if (!notes) return;
    const a = new Audio(toneWav(notes, 0.07));
    a.volume = 0.35;
    a.onended = () => URL.revokeObjectURL(a.src);
    (async () => {
      try { if (a.setSinkId) await a.setSinkId(this.outputId || ''); await a.play(); } catch (_) { /* a cue is a courtesy */ }
    })();
  }

  async statsTick() {
    for (const p of Array.from(this.peers.values())) {
      let rep;
      try { rep = await p.pc.getStats(); } catch (_) { continue; }
      let level = 0, energy = 0, rx = 0, tx = 0, lost = 0, jitter = 0, rtt = null, pair = '', pr = 0, rlost = null, avail = null;
      let pairinfo = null, sel = null;
      const byId = {};
      rep.forEach((r) => { byId[r.id] = r; });
      rep.forEach((r) => { if (r.type === 'transport' && r.selectedCandidatePairId) sel = byId[r.selectedCandidatePairId] || sel; });
      rep.forEach((r) => {
        if (r.type === 'inbound-rtp' && (r.kind === 'audio' || r.mediaType === 'audio')) {
          level = r.audioLevel || 0; energy = r.totalAudioEnergy || 0; rx = r.bytesReceived || 0;
          lost = r.packetsLost || 0; jitter = r.jitter || 0; pr = r.packetsReceived || 0;
        } else if (r.type === 'outbound-rtp' && (r.kind === 'audio' || r.mediaType === 'audio')) {
          tx = r.bytesSent || 0;
        } else if (r.type === 'remote-inbound-rtp' && (r.kind === 'audio' || r.mediaType === 'audio')) {
          rlost = r.fractionLost == null ? null : r.fractionLost;           // how much of OUR audio the other side lost
        } else if (!sel && r.type === 'candidate-pair' && (r.nominated || r.selected) && r.state === 'succeeded') {
          sel = r;
        }
      });
      if (sel) {
        rtt = sel.currentRoundTripTime == null ? null : Math.round(sel.currentRoundTripTime * 1000);
        avail = sel.availableOutgoingBitrate == null ? null : Math.round(sel.availableOutgoingBitrate / 1000);
        const l = byId[sel.localCandidateId], rm = byId[sel.remoteCandidateId];
        if (l && rm) {
          pair = l.candidateType + '>' + rm.candidateType;
          pairinfo = { lt: l.candidateType, rt: rm.candidateType, proto: l.protocol || '', rp: l.relayProtocol || '',
                       lnet: ipClass(l.address || l.ip), rnet: ipClass(rm.address || rm.ip), net: l.networkType || '' };
        }
      }
      if (this.chainOn(p)) { energy = p.chainEnergy; level = p.chainPeak; }
      this.fxWatchdog(p, rx);
      send({ t: 'peer', sid: p.sid, ep: p.ep, type: 'stats', level, energy, rx, tx, lost, jitter, rtt, pair, pairinfo, pr, rlost, avail, gain: Math.round(p.gain * 100) / 100, sending: p.send,
             playing: !p.audio.paused && !p.audio.ended && p.audio.readyState >= 2, muted: p.audio.muted,
             vol: Math.round(p.audio.volume * 100) / 100, sink: p.audio.sinkId || '',
             fx: p.route, out: Math.round(p.outPeak * 10000) / 10000, maxbr: p.maxbr,
             loud: Math.round(p.loud.s * 100) / 100, lvl: p.loud.lv === null ? null : Math.round(p.loud.lv * 10) / 10,
             flat: Math.round(VM.flatFor(this.curve, p.loud.s) * 10) / 10, lev: Math.round(p.levDb * 10) / 10,
             boost: p.fx ? Math.round(p.fx.boost.gain.value * 100) / 100 : 1 });
      p.outPeak = 0; p.chainPeak = 0; p.lvMaxDb = -120;
    }
  }
}

const media = new Media();

// ------------------------------------------------------------------ render

function text(node, s) { if (node.textContent !== s) node.textContent = s; }

function pill(id, cls, label) {
  const n = $(id);
  text(n, label);
  n.className = 'pill ' + cls;
}

// The voice icon after a player's name: the same five pictures as the little icons in the game (the addon's Icons.lua).
const VOICE_ICONS = {
  s: { file: 'voice-open.png', word: 'speaking' },                                  // vol_mid
  l: { file: 'voice-speaking.png', word: 'speaking loudly' },                       // vol_high
  o: { file: 'voice-known.png', word: 'using EqVoice, not talking' },       // vol_low
  m: { file: 'voice-muted.png', word: 'muted (or push to talk and the key is not held)' },
  d: { file: 'voice-deafened.png', word: 'deafened' },
  '-': { file: 'voice-known.png', word: 'has EqVoice', dim: 0.45 },
};
function voiceIcon(state) {
  const d = VOICE_ICONS[state] || VOICE_ICONS['-'];
  const img = document.createElement('img');
  img.src = '/static/' + d.file;
  img.width = 18; img.height = 18;
  img.className = 'vico';
  if (d.dim) img.style.opacity = String(d.dim);
  img.alt = d.word;
  img.title = d.word;
  return img;
}

function renderMuted(names) {
  const sig = JSON.stringify(names);
  const box = $('muted-list');
  $('muted-empty').hidden = names.length > 0;
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = '';
  names.forEach((n) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = n;
    const x = document.createElement('button');
    x.type = 'button'; x.textContent = '×'; x.title = 'Hear ' + n + ' again';
    x.onclick = () => send({ t: 'ui', cmd: 'mute_player', name: n, value: false });
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

function fmtAge(s) { return s < 10 ? s.toFixed(1) + 's' : Math.round(s) + 's'; }

function fmtMs(v) { return v ? Math.round(v) + ' ms' : '-'; }

function render() {
  const sg = S.signalling || {}, br = S.bridge || {}, mic = S.mic || {}, v = S.voice || {}, me = S.identity, m = S.metrics || {};
  pill('pill-char', me ? 'ok' : 'warn', me ? me.character + ' - ' + me.realm : 'no character yet');
  pill('pill-sig', sg.state === 'registered' ? 'ok' : sg.state === 'backoff' ? 'bad' : 'warn',
       'signalling: ' + (sg.state || '…') + (sg.state === 'registered' && sg.rtt_ms != null ? ' ' + sg.rtt_ms + 'ms' : ''));
  const bcls = br.status === 'live' ? 'ok' : br.status === 'stale' ? 'warn' : 'bad';
  pill('pill-bridge', bcls, 'bridge: ' + (br.status || 'none') + (br.status && br.status !== 'none' ? ' (' + (br.source_kind || '?') + ', ' + fmtAge(br.age || 0) + ')' : ''));
  pill('pill-mic', mic.state === 'ok' ? 'ok' : mic.state === 'starting' ? 'warn' : 'bad', 'mic: ' + (mic.state || '…'));
  text($('self-sid'), me ? '(session ' + me.sid + ')' : '(waiting for the game)');

  $('mode-ptt').checked = v.mode === 'ptt';
  $('mode-open').checked = v.mode === 'open';
  text($('btn-key'), v.ptt_label || '…');
  if (document.activeElement !== $('master-vol')) $('master-vol').value = Math.round((v.master == null ? 1 : v.master) * 100);
  if (document.activeElement !== $('inp-vol')) $('inp-vol').value = Math.round((v.input_gain == null ? 1 : v.input_gain) * 100);
  text($('inp-vol-n'), Math.round((v.input_gain == null ? 1 : v.input_gain) * 100) + '%');
  text($('cal-note'), v.calibrated ? 'done: the noise gate and volume were set from your voice' : 'not done yet');
  if (document.activeElement !== $('sel-boost')) $('sel-boost').value = v.boost || 'normal';
  if (document.activeElement !== $('chk-level')) $('chk-level').checked = v.auto_level !== false;
  const gateMode = v.keyboard === false ? 'off' : (v.gate_mode || 'auto');
  if (document.activeElement !== $('sel-gate')) $('sel-gate').value = gateMode;
  if (document.activeElement !== $('rng-gate')) $('rng-gate').value = Math.round(v.gate_db == null ? -48 : v.gate_db);
  $('gate-row').hidden = gateMode === 'off';
  $('rng-gate').hidden = gateMode !== 'manual';
  const glevel = mic.glevel, gthr = mic.gthr;
  const gpos = (db) => Math.max(0, Math.min(100, ((db + 90) / 70) * 100));          // -90 dB .. -20 dB across the bar
  $('gate-fill').style.width = (glevel == null ? 0 : gpos(glevel)) + '%';
  $('gate-fill').classList.toggle('open', !!mic.gopen);
  $('gate-thr').style.left = (gthr == null ? 0 : gpos(gthr)) + '%';
  $('gate-thr').style.display = gthr == null ? 'none' : '';
  text($('gate-db'), gateMode === 'manual' ? Math.round(v.gate_db == null ? -48 : v.gate_db) + ' dB' : gthr == null ? '' : 'opens at ' + Math.round(gthr) + ' dB');
  text($('gate-note'), gateMode === 'off' ? 'everything your microphone hears is sent' : mic.gate === false && mic.state === 'ok' ? 'not running: the raw microphone is sent' : '');
  text($('lbl-key'), v.mode === 'ptt' ? 'Push-to-talk key' : 'Mute key');
  if (document.activeElement !== $('sel-br')) {
    const kb = String(v.bitrate == null ? 24 : v.bitrate);
    if (!Array.from($('sel-br').options).some((o) => o.value === kb)) $('sel-br').add(new Option(kb + ' kbps', kb));      // a value set by hand in config.json
    $('sel-br').value = kb;
  }
  text($('key-hint'), v.ptt_supported ? (v.ptt_down ? 'key down' : '') : 'global key unavailable here - use the button');
  $('btn-mute').classList.toggle('on', !!v.muted);
  $('btn-mute').classList.add('bad');
  $('btn-deafen').classList.toggle('on', !!v.deafened);
  $('btn-deafen').classList.add('bad');
  text($('btn-mute'), v.muted ? 'Muted' : 'Mute');
  text($('btn-deafen'), v.deafened ? 'Deafened' : 'Deafen');
  $('meter-fill').style.width = Math.round((mic.level || 0) * 100) + '%';
  const tx = $('tx-ind');
  text(tx, v.tx ? 'transmitting' : 'idle');
  tx.className = 'tx' + (v.tx ? ' live' : '');

  fillSelect($('sel-in'), (S.devices || {}).inputs || [], (S.audio || {}).input);
  fillSelect($('sel-out'), (S.devices || {}).outputs || [], (S.audio || {}).output);

  const tbody = $('peers');
  const peers = S.peers || [];
  $('peers-empty').hidden = peers.length > 0;
  const keep = new Set(peers.map((p) => p.sid));
  Array.from(tbody.children).forEach((tr) => { if (!keep.has(tr.dataset.sid)) tr.remove(); });
  peers.forEach((p) => {
    let tr = tbody.querySelector('tr[data-sid="' + p.sid + '"]');
    if (!tr) {
      tr = document.createElement('tr');
      tr.dataset.sid = p.sid;
      for (let i = 0; i < 6; i++) tr.appendChild(document.createElement('td'));
      const vol = document.createElement('input');
      vol.type = 'range'; vol.min = 0; vol.max = 150; vol.value = 100;
      vol.oninput = () => send({ t: 'ui', cmd: 'peer_volume', sid: p.sid, value: vol.value / 100 });
      tr.children[5].appendChild(vol);
      const pm = document.createElement('button');
      pm.type = 'button'; pm.className = 'pm';
      pm.onclick = () => send({ t: 'ui', cmd: 'mute_player', name: tr.dataset.name, value: pm.dataset.on !== '1' });
      tr.children[5].appendChild(pm);
      tbody.appendChild(tr);
    }
    const c = tr.children;
    tr.dataset.name = p.name || '';
    const pmb = c[5].querySelector('button.pm');
    pmb.dataset.on = p.muted_by_me ? '1' : '0';
    pmb.classList.toggle('on', !!p.muted_by_me);
    text(pmb, p.muted_by_me ? 'Muted' : 'Mute');
    pmb.title = p.muted_by_me ? 'Click to hear ' + (p.name || 'them') + ' again' : 'Click to stop hearing ' + (p.name || 'them');
    let nm = c[0].querySelector('.pname');
    if (!nm) { nm = document.createElement('span'); nm.className = 'pname'; c[0].appendChild(nm); }
    text(nm, p.name || '?');
    if (c[0].dataset.vst !== (p.vstate || '-')) {                  // the icon is drawn again only when what it shows changes
      c[0].dataset.vst = p.vstate || '-';
      Array.from(c[0].querySelectorAll('.vico')).forEach((n) => n.remove());
    }
    if (!c[0].querySelector('.vico')) c[0].appendChild(voiceIcon(p.vstate || '-'));          // after the name
    text(c[1], p.dist == null ? '-' : p.dist.toFixed(1) + ' yd');
    c[2].innerHTML = '';
    const chip = document.createElement('span');
    chip.className = 'chip ' + p.state;
    chip.textContent = p.state.replace(/_/g, ' ').toLowerCase();
    c[2].appendChild(chip);
    if (p.state === 'DISCONNECTED' && p.retry_in > 0) c[2].appendChild(document.createTextNode(' retry ' + Math.ceil(p.retry_in) + 's'));
    if (p.capped) c[2].appendChild(document.createTextNode(' (full)'));
    const st = p.stats || {};
    text(c[3], (p.linked ? 'mutual' : 'not confirmed') + (p.path ? ' / ' + (p.path === 'relayed' ? 'relayed' : 'direct') : (p.ice ? ' / ' + p.ice : '')) +
         (st.pair ? ' ' + st.pair : '') + (st.rtt != null ? ' ' + st.rtt + 'ms' : ''));
    text(c[4], p.state === 'CONNECTED' ? Math.round((p.gain || 0) * 100) + '%' + (p.level > 0.01 ? ' speaking' : '') +
         (st.playing === false ? ' (not playing)' : '') + (st.muted ? ' (deafened)' : '') : (p.err || ''));
    c[4].title = p.err || '';
    let dot = c[0].querySelector('.dot');
    if (!dot) { dot = document.createElement('span'); dot.className = 'dot'; c[0].insertBefore(dot, c[0].firstChild); }
    dot.classList.toggle('live', p.state === 'CONNECTED' && p.level > 0.01);
  });
  renderMuted(v.muted_players || []);

  const stat = S.status || { level: 'warn', text: 'Starting...', more: [] };
  $('status-bar').className = stat.level;
  text($('status-bar'), stat.text);
  const more = $('status-more');
  more.hidden = !(stat.more || []).length;
  text(more, (stat.more || []).join('  '));
  const warn = $('engine-warn');
  warn.hidden = true;                                   // folded into the status bar above
  text($('using-mic'), mic.label ? mic.label : (mic.state === 'ok' ? 'default microphone' : '-'));
  const sp = S.speaker_test || {};
  text($('speaker-note'), sp.ok === undefined ? '' : sp.ok ? 'played two rising notes' + (sp.sink ? ' on ' + sp.sink : ' on the system default') : 'could not play: ' + (sp.error || '?'));

  const note = $('bridge-note');
  const gate = S.gate || {};
  const hint = br.hint || (gate.open === false && me ? gate.reason : '');
  note.hidden = !hint;
  text(note, hint || '');

  text($('m-roster'), String(m.roster == null ? '-' : m.roster));
  text($('m-checked'), m.checked == null ? '-' : m.checked + ' checked, ' + m.loaded + ' seen, ' + m.missed + ' not loaded');
  text($('m-scan'), fmtMs(m.scan_ms) + ' per scan, every ' + fmtMs(m.addon_interval_ms));
  text($('m-bridge'), fmtMs(m.bridge_interval_ms) + ' between frames' + (m.roster_acked ? ', roster acknowledged' : ''));
  text($('m-nearby'), (m.nearby == null ? '-' : m.nearby) + ' within ' + ((S.proximity || {}).say_radius || 40) + ' yd, ' + (m.connected || 0) + ' connected');
  text($('m-gate'), gate.open ? 'open' : 'closed' + (gate.reason ? ': ' + gate.reason : ''));
  const mesh = m.mesh || {}, res = m.resources || {};
  text($('m-mesh'), mesh.connected == null ? '-' : mesh.connected + ' connected of ' + mesh.active + ' active, audio in ' + mesh.in_kbps + ' / out ' + mesh.out_kbps +
       ' kbit/s' + (mesh.setup_ms_avg != null ? ', set-up ' + mesh.setup_ms_avg + ' ms avg' : ''));
  const rc = res.client || {}, re = res.engine || {};
  text($('m-res'), res.client ? 'client ' + (rc.cpu_pct == null ? '-' : rc.cpu_pct + '%') + ' / ' + rc.private_mb + ' MB; voice engine ' +
       (re.procs ? (re.cpu_pct == null ? '-' : re.cpu_pct + '%') + ' / ' + re.private_mb + ' MB' : 'not ours') : '-');
  const tn = m.test_names;
  $('t-test').hidden = $('m-test').hidden = tn == null;
  if (tn != null) text($('m-test'), Object.keys(tn).length ? Object.entries(tn).map(([n, d]) => n + ' ' + d + ' yd').join(', ') : 'none seen by the game yet');

  if (S.sim) renderSim();
  $('s-sim').hidden = !S.sim;
}

function fillSelect(sel, items, current) {
  const sig = JSON.stringify(items) + '|' + current;
  if (sel.dataset.sig === sig) return;
  sel.dataset.sig = sig;
  sel.innerHTML = '';
  const def = document.createElement('option');
  def.value = ''; def.textContent = 'System default';
  sel.appendChild(def);
  items.forEach((d) => {
    if (d.id === 'default' || d.id === 'communications') return;
    const o = document.createElement('option');
    o.value = d.id; o.textContent = d.label;
    sel.appendChild(o);
  });
  sel.value = current || '';
}

function renderSim() {
  const sim = S.sim_state || {};
  $('sim-freeze').checked = !!sim.frozen;
  $('sim-lookups').checked = sim.lookups !== false;
  const box = $('sim-peers');
  const sig = JSON.stringify([sim.chars, sim.views, sim.character]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = '';
  Object.keys(sim.chars || {}).forEach((name) => {
    if (name === sim.character) return;
    const row = document.createElement('div');
    row.className = 'row small';
    const seen = (sim.views || {})[sim.character + '>' + name];
    const input = document.createElement('input');
    input.type = 'number'; input.min = 0; input.max = 120; input.step = 1; input.size = 4;
    input.value = seen == null ? '' : seen;
    input.placeholder = 'yards';
    const set = document.createElement('button');
    set.type = 'button'; set.textContent = 'I see them at';
    set.onclick = () => send({ t: 'ui', cmd: 'sim_see', target: name, yards: input.value === '' ? null : Number(input.value) });
    const rm = document.createElement('button');
    rm.type = 'button'; rm.textContent = 'remove';
    rm.onclick = () => send({ t: 'ui', cmd: 'sim_remove', name });
    row.appendChild(document.createTextNode(name + '  '));
    row.appendChild(set);
    row.appendChild(input);
    row.appendChild(document.createTextNode(' yd '));
    row.appendChild(rm);
    box.appendChild(row);
  });
}

const logEl = () => $('log');
function appendLog(line) {
  const el = logEl();
  el.textContent += line + '\n';
  if (el.textContent.length > 40000) el.textContent = el.textContent.slice(-30000);
  el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------- controls

$('sel-in').onchange = (e) => send({ t: 'ui', cmd: 'set_input', id: e.target.value });
$('sel-out').onchange = (e) => send({ t: 'ui', cmd: 'set_output', id: e.target.value });
$('mode-ptt').onchange = () => send({ t: 'ui', cmd: 'set_mode', mode: 'ptt' });
$('mode-open').onchange = () => send({ t: 'ui', cmd: 'set_mode', mode: 'open' });
$('master-vol').oninput = (e) => send({ t: 'ui', cmd: 'master_volume', value: e.target.value / 100 });
$('sel-boost').onchange = (e) => send({ t: 'ui', cmd: 'boost', value: e.target.value });
$('chk-level').onchange = (e) => send({ t: 'ui', cmd: 'auto_level', value: e.target.checked });
$('sel-gate').onchange = (e) => send({ t: 'ui', cmd: 'gate', value: e.target.value });
$('rng-gate').oninput = (e) => send({ t: 'ui', cmd: 'gate_db', value: Number(e.target.value) });
const addMute = () => {
  const n = $('inp-mute').value.trim();
  if (n) send({ t: 'ui', cmd: 'mute_player', name: n, value: true });
  $('inp-mute').value = '';
};
$('btn-mute-add').onclick = addMute;
$('inp-mute').onkeydown = (e) => { if (e.key === 'Enter') addMute(); };
$('inp-vol').oninput = (e) => send({ t: 'ui', cmd: 'input_gain', value: Number(e.target.value) / 100 });
$('btn-cal').onclick = () => calRender({ phase: 'intro' });
$('cal-go').onclick = () => { send({ t: 'ui', cmd: 'calibrate' }); calRender({ phase: 'quiet', left: 0, level: -100 }); };
$('cal-cancel').onclick = () => { send({ t: 'ui', cmd: 'calibrate_cancel' }); calRender({ phase: 'idle' }); };
$('cal-done').onclick = () => calRender({ phase: 'idle' });
$('sel-br').onchange = (e) => send({ t: 'ui', cmd: 'max_bitrate', value: Number(e.target.value) });
$('btn-mute').onclick = () => send({ t: 'ui', cmd: 'mute', value: !(S.voice || {}).muted });
$('btn-deafen').onclick = () => send({ t: 'ui', cmd: 'deafen', value: !(S.voice || {}).deafened });
$('btn-speakers').onclick = () => send({ t: 'ui', cmd: 'test_speakers' });

const talk = $('btn-talk');
const talkDown = (e) => { e.preventDefault(); send({ t: 'ui', cmd: 'ptt_virtual', down: true }); };
const talkUp = () => send({ t: 'ui', cmd: 'ptt_virtual', down: false });
talk.addEventListener('mousedown', talkDown);
talk.addEventListener('touchstart', talkDown, { passive: false });
window.addEventListener('mouseup', talkUp);
window.addEventListener('touchend', talkUp);
window.addEventListener('blur', talkUp);

let capturing = false;
$('btn-key').onclick = () => {
  capturing = true;
  text($('btn-key'), 'press a key or mouse button…');
};
window.addEventListener('keydown', (e) => {
  if (!capturing) return;
  e.preventDefault();
  if (e.code === 'Escape') { capturing = false; render(); return; }
  capturing = false;
  send({ t: 'ui', cmd: 'ptt_key', code: e.code, key: e.key });
}, true);
window.addEventListener('mousedown', (e) => {
  if (!capturing || e.target === $('btn-key')) return;
  e.preventDefault();
  capturing = false;
  send({ t: 'ui', cmd: 'ptt_key', button: e.button });
}, true);

$('diag-copy').onclick = async () => {
  try {
    const r = await fetch('/api/diag?token=' + encodeURIComponent(TOKEN));
    const t = JSON.stringify(await r.json(), null, 2);
    await navigator.clipboard.writeText(t);
    appendLog('diagnostics copied to the clipboard (' + t.length + ' bytes)');
  } catch (e) { appendLog('could not copy diagnostics: ' + e); }
};
$('diag-clear').onclick = () => { logEl().textContent = ''; };
$('diag-quit').onclick = () => send({ t: 'ui', cmd: 'quit' });
function showReport(m) { const el = $('report-out'); el.hidden = false; el.textContent = m.text || ''; }

// ---- the microphone setup card: what to do now, how long is left, and how loud the microphone is hearing you
const CAL_STEPS = [
  { title: 'Stay silent', text: 'Do not talk or type. Let your room be as quiet as it normally is.' },
  { title: 'Talk normally', text: 'Say anything, the way you would talk in the game: a few sentences, and keep going until the time is up.' },
  { title: 'Now shout', text: 'Shout the way you would at someone a long way off. Do not worry about the neighbours.' },
];
function calRender(info) {
  const card = $('cal-card');
  const p = info.phase;
  const idx = p === 'quiet' ? 0 : p === 'normal' ? 1 : p === 'loud' ? 2 : -1;
  card.hidden = p === 'idle';
  if (p === 'idle') return;
  $('cal-live').hidden = idx < 0;
  $('cal-go').hidden = p !== 'intro';
  $('cal-cancel').hidden = !(p === 'intro' || idx >= 0 || p === 'working');
  $('cal-done').hidden = !(p === 'result' || p === 'failed');
  if (p === 'intro') {
    text($('cal-step'), 'Microphone setup');
    text($('cal-title'), 'Three short steps');
    text($('cal-text'), 'I will ask you to stay silent, talk normally and then shout, about 17 seconds in all. From that I set how sensitive the noise gate is and, if your microphone needs it, its volume. Use the microphone you play with.');
  } else if (idx >= 0) {
    const sec = Math.max(0, Math.ceil(info.left || 0));
    text($('cal-step'), 'Step ' + (idx + 1) + ' of 3');
    text($('cal-title'), CAL_STEPS[idx].title);
    text($('cal-text'), CAL_STEPS[idx].text);
    text($('cal-left'), sec ? sec + ' s' : '');
    const pos = Math.max(0, Math.min(100, (((info.level == null ? -100 : info.level) + 90) / 70) * 100));        // -90 dB .. -20 dB across the bar
    $('cal-fill').style.width = pos + '%';
  } else if (p === 'working') {
    text($('cal-step'), 'Almost done');
    text($('cal-title'), 'Working it out');
    text($('cal-text'), '');
  } else if (p === 'failed') {
    text($('cal-step'), 'It did not work');
    text($('cal-title'), 'The microphone could not be measured');
    text($('cal-text'), info.error || '');
  } else if (p === 'result') {
    text($('cal-step'), info.quality === 'good' ? 'Done' : 'Finished');
    text($('cal-title'), info.quality === 'good' ? 'Your microphone is set up' : info.quality === 'noisy' ? 'Your room is noisy' : 'Nothing was changed');
    text($('cal-text'), info.text || '');
  }
}
function calResult(m) { calRender({ phase: m.ok === false && m.quality !== 'noisy' && m.quality !== 'good' ? 'failed' : 'result', quality: m.quality, text: m.text, error: m.text }); }
$('diag-net').onclick = () => { showReport({ text: 'Checking your connection...' }); send({ t: 'ui', cmd: 'netcheck' }); };
$('diag-report').onclick = () => send({ t: 'ui', cmd: 'save_report' });

$('sim-add').onclick = () => {
  send({ t: 'ui', cmd: 'sim_add', name: $('sim-add-name').value.trim() });
  $('sim-add-name').value = '';
};
$('sim-freeze').onchange = (e) => send({ t: 'ui', cmd: 'sim_freeze', value: e.target.checked });
$('sim-lookups').onchange = (e) => send({ t: 'ui', cmd: 'sim_lookups', value: e.target.checked });
$('sim-logout').onclick = () => send({ t: 'ui', cmd: 'sim_logout' });

window.__ov = { media, get state() { return S; } };
connect();
