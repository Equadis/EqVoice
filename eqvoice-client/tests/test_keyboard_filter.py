"""The keyboard filter (ui/gate-worklet.js), run for real: the worklet processes synthetic audio in a real browser's offline audio context.

The signals: a room hiss, a person talking (syllables with vowels, consonant bursts and pauses), keystrokes (a sharp noise burst, a low
thump and a second click on release), a mouse click. They are synthetic, so this proves the principle and the numbers on stand-ins: a real
keyboard can sound different, and the filter's settings (sustain, hang, margin) are there to be tuned by ear."""
import json

import pytest

import edgeharness

pytestmark = pytest.mark.skipif(not edgeharness.available(), reason="needs a Chromium-based browser")

WORKLET = (edgeharness.UI / "gate-worklet.js").read_text(encoding="utf-8")

SIGNALS = """
  const SR = 48000, LEN = 12 * SR;
  let seed = 4242; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const db = (x) => 20 * Math.log10(x + 1e-12);
  const rms = (a, from, to) => { let s = 0; for (let i = from; i < to; i++) s += a[i] * a[i]; return Math.sqrt(s / Math.max(1, to - from)); };
  const sec = (t) => Math.round(t * SR);

  function room(sig, level) { const amp = Math.pow(10, level / 20) * Math.sqrt(3); for (let i = 0; i < sig.length; i++) sig[i] += (rnd() * 2 - 1) * amp; }

  // a voice: syllables of 240 ms (a short consonant burst, a vowel built from harmonics with formant bumps, a pause every ninth one)
  function speech(sig, from, to, level) {
    const amp = Math.pow(10, level / 20), syl = sec(0.24);
    let n = 0;
    for (let t0 = from; t0 + syl <= to; t0 += syl, n++) {
      if (n % 9 === 8) continue;
      const f0 = 110 + 25 * Math.sin(n * 0.7), burst = sec(0.012);
      for (let i = 0; i < burst; i++) sig[t0 + i] += (rnd() * 2 - 1) * amp * 0.5 * (1 - i / burst);
      const vs = t0 + sec(0.03), ve = t0 + sec(0.2);
      const tmp = new Float32Array(ve - vs);
      for (let i = 0; i < tmp.length; i++) {
        const t = i / SR; let v = 0;
        for (let k = 1; k <= 28; k++) {
          const f = f0 * k; if (f > 4000) break;
          const formant = 1 + 3 * Math.exp(-Math.pow((f - 700) / 250, 2)) + 2 * Math.exp(-Math.pow((f - 1500) / 350, 2));
          v += Math.sin(2 * Math.PI * f * t) * formant / k;
        }
        const env = Math.min(1, i / sec(0.02), (tmp.length - i) / sec(0.03));
        tmp[i] = v * env;
      }
      const r = rms(tmp, 0, tmp.length) || 1;
      for (let i = 0; i < tmp.length; i++) sig[vs + i] += tmp[i] / r * amp;
    }
  }

  // a keystroke: a sharp broadband burst (8 ms), a low thump (25 ms), and a softer click when the key comes back up
  function key(sig, at, strength, ring) {
    const burst = sec(0.008), thump = sec(0.06), tau = sec(ring || 0.008);
    for (let i = 0; i < burst; i++) sig[at + i] += (rnd() - rnd()) * 0.5 * strength * Math.exp(-i / sec(0.0025));
    for (let i = 0; i < thump; i++) sig[at + i] += Math.sin(2 * Math.PI * 160 * i / SR) * 0.12 * strength * Math.exp(-i / tau);
    const up = at + sec(0.07);
    for (let i = 0; i < burst; i++) sig[up + i] += (rnd() - rnd()) * 0.15 * strength * Math.exp(-i / sec(0.002));
  }
  function typing(sig, from, to, strength, ring) { for (let t = from; t < to; t += sec(0.09 + rnd() * 0.13)) key(sig, t, strength, ring); }
  function mouse(sig, at) { for (let i = 0; i < sec(0.004); i++) sig[at + i] += (rnd() - rnd()) * 0.4 * Math.exp(-i / sec(0.001)); }

  async function through(sig, opts) {
    const ctx = new OfflineAudioContext(1, sig.length, SR);
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }));
    await ctx.audioWorklet.addModule(url);
    const buf = ctx.createBuffer(1, sig.length, SR); buf.getChannelData(0).set(sig);
    const src = ctx.createBufferSource(); src.buffer = buf;
    const node = new AudioWorkletNode(ctx, 'voice-gate', { processorOptions: opts || {}, outputChannelCount: [1] });
    src.connect(node); node.connect(ctx.destination); src.start();
    const out = await ctx.startRendering();
    return out.getChannelData(0);
  }
"""


def run(script, timeout=120):
    body = "(async () => {\n%s\n})();" % script
    html = edgeharness.page("const WORKLET = %s;\n%s\n%s" % (json.dumps(WORKLET), SIGNALS, body), scripts=())
    return edgeharness.run(html, timeout=timeout)


def test_typing_and_clicks_are_taken_out_and_speech_is_left_alone():
    r = run("""
      const sig = new Float32Array(LEN);
      room(sig, -62);
      typing(sig, sec(2.0), sec(3.8), 1);                 // 1.8 s of typing, no voice
      speech(sig, sec(4.0), sec(7.2), -24);               // 3.2 s of talking
      mouse(sig, sec(7.6)); mouse(sig, sec(7.9));         // two mouse clicks
      typing(sig, sec(8.2), sec(10.0), 1.6);              // louder typing, still no voice
      typing(sig, sec(10.2), sec(11.8), 1.2, 0.015);      // a boomy keyboard: the thump rings for 15 ms
      const out = await through(sig);
      const seg = (a, b) => [db(rms(sig, sec(a), sec(b))), db(rms(out, sec(a), sec(b)))];
      const first = (from) => [db(rms(sig, sec(from), sec(from + 0.12))), db(rms(out, sec(from + 0.053), sec(from + 0.053 + 0.12)))];
      done({ room: seg(0.2, 1.8), typing1: seg(2.0, 3.8), speech: seg(3.9, 7.4), mouse: seg(7.5, 8.1), typing2: seg(8.2, 10.4), boomy: seg(10.2, 11.9), onset: first(4.0) });
    """)
    inn, out = r["typing1"]
    assert out < inn - 25, "keystrokes on their own are taken out (by at least 25 dB): in %.1f dB, out %.1f dB" % (inn, out)
    inn, out = r["typing2"]
    assert out < inn - 25, "so is louder typing: in %.1f dB, out %.1f dB" % (inn, out)
    inn, out = r["boomy"]
    assert out < inn - 25, "and a boomy keyboard whose thump rings on: in %.1f dB, out %.1f dB" % (inn, out)
    inn, out = r["mouse"]
    assert out < inn - 25, "and mouse clicks: in %.1f dB, out %.1f dB" % (inn, out)
    assert r["room"][1] < -75, "and the room's hiss is not sent: %.1f dB" % r["room"][1]
    inn, out = r["speech"]
    assert out > inn - 1.0, "the voice goes through at the same loudness (within 1 dB): in %.1f dB, out %.1f dB" % (inn, out)
    inn, out = r["onset"]
    assert out > inn - 3.0, "and its first 120 ms are not clipped (look-ahead): in %.1f dB, out %.1f dB" % (inn, out)


def test_a_quiet_voice_in_a_quiet_room_and_a_loud_voice_in_a_noisy_room_both_pass():
    r = run("""
      const quiet = new Float32Array(LEN); room(quiet, -72); speech(quiet, sec(2), sec(8), -46);
      const noisy = new Float32Array(LEN); room(noisy, -48); speech(noisy, sec(2), sec(8), -22);
      const qo = await through(quiet), no = await through(noisy);
      done({ quiet: [db(rms(quiet, sec(2.1), sec(8.1))), db(rms(qo, sec(2.1), sec(8.1)))],
             noisy: [db(rms(noisy, sec(2.1), sec(8.1))), db(rms(no, sec(2.1), sec(8.1)))] });
    """)
    assert r["quiet"][1] > r["quiet"][0] - 1.5, "a voice 46 dB below full scale in a very quiet room still opens the gate: %r" % r["quiet"]
    assert r["noisy"][1] > r["noisy"][0] - 1.5, "a voice over a noisy room (a fan at -48 dB) still opens it: %r" % r["noisy"]


def test_switched_off_it_changes_nothing_and_a_steady_voice_stays_open():
    r = run("""
      const sig = new Float32Array(LEN);
      room(sig, -62); typing(sig, sec(1), sec(3), 1); speech(sig, sec(4), sec(6), -24);
      const off = await through(sig, { enabled: false });
      let maxDiff = 0; for (let i = 0; i < sig.length; i++) maxDiff = Math.max(maxDiff, Math.abs(off[i] - sig[i]));
      // a held note ("aaaah") for 5 s, steady: it must open once and stay open
      const held = new Float32Array(LEN); room(held, -62);
      for (let i = sec(2); i < sec(7); i++) held[i] += 0.05 * (Math.sin(2 * Math.PI * 140 * i / SR) + 0.6 * Math.sin(2 * Math.PI * 280 * i / SR) + 0.4 * Math.sin(2 * Math.PI * 700 * i / SR));
      const ho = await through(held);
      done({ maxDiff, held: [db(rms(held, sec(2.1), sec(7))), db(rms(ho, sec(2.1), sec(7)))],
             tail: db(rms(ho, sec(7.5), sec(9))) });
    """)
    assert r["maxDiff"] == 0, "with the filter off the audio is passed through untouched"
    assert r["held"][1] > r["held"][0] - 0.5, "a held note stays open all the way through: %r" % r["held"]
    assert r["tail"] < -70, "and the gate closes after it: %.1f dB" % r["tail"]


def test_typing_straight_after_talking_does_not_keep_the_gate_open():
    """The case that fooled the first version: once talking had opened the gate, the next keystroke came before the gate closed, and the
    one after that, and so on: typing went through for as long as it went on."""
    r = run("""
      const sig = new Float32Array(LEN);
      room(sig, -62);
      speech(sig, sec(1.0), sec(3.5), -24);
      typing(sig, sec(3.6), sec(11.0), 1.3, 0.012);       // seven seconds of typing starting right after the last word
      const out = await through(sig);
      const seg = (a, b) => [db(rms(sig, sec(a), sec(b))), db(rms(out, sec(a), sec(b)))];
      done({ talk: seg(1.1, 3.4), early: seg(4.2, 6.0), late: seg(6.0, 11.0) });
    """)
    inn, out = r["talk"]
    assert out > inn - 1.0, "the talking before it is untouched: in %.1f dB, out %.1f dB" % (inn, out)
    for key in ("early", "late"):
        inn, out = r[key]
        assert out < inn - 25, "typing %s after talking is taken out: in %.1f dB, out %.1f dB" % (key, inn, out)


# ---------------------------------------------------- audio captured the way a real microphone delivers it (a live stream)

CAPTURE = """
(async () => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
  const ctx = new AudioContext(); await ctx.resume();
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(256, 1, 1);
  const rec = [];
  proc.onaudioprocess = (e) => { rec.push(Float32Array.from(e.inputBuffer.getChannelData(0))); };
  const z = ctx.createGain(); z.gain.value = 0;
  src.connect(proc); proc.connect(z); z.connect(ctx.destination);
  await new Promise((r) => setTimeout(r, 7000));
  const all = new Float32Array(rec.length * 256); rec.forEach((b, i) => all.set(b, i * 256));
  // the gate's own class, run block by block over what the browser really delivered
  let Gate = null;
  new Function('AudioWorkletProcessor', 'registerProcessor', 'sampleRate', %s)(class { constructor() { this.port = { onmessage: null, postMessage() {} }; } }, (n, c) => { Gate = c; }, ctx.sampleRate);
  const g = new Gate({ processorOptions: {} });
  const inB = new Float32Array(128), outB = new Float32Array(128);
  let inE = 0, outE = 0, open = 0, blocks = 0;
  for (let s = 0; s + 128 <= all.length; s += 128) {
    inB.set(all.subarray(s, s + 128));
    g.process([[inB]], [[outB]]);
    if (s > 48000 * 1.5) { for (let i = 0; i < 128; i++) { inE += inB[i] * inB[i]; outE += outB[i] * outB[i]; } blocks++; if (g.open) open++; }
  }
  done({ rate: ctx.sampleRate, openFraction: open / blocks, inDb: 10 * Math.log10(inE / (blocks * 128) + 1e-12), outDb: 10 * Math.log10(outE / (blocks * 128) + 1e-12) });
})();
"""


def capture(wav):
    html = edgeharness.page("const x = 0;\n" + CAPTURE % json.dumps(WORKLET), scripts=())
    return edgeharness.run(html, timeout=90, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                                   "--use-file-for-fake-audio-capture=" + str(wav)])


def test_on_real_captured_typing_the_gate_stays_shut_and_on_real_captured_speech_it_opens(tmp_path):
    import wavs
    typing = capture(wavs.typing_wav(tmp_path / "typing.wav"))
    speech = capture(wavs.speech_wav(tmp_path / "speech.wav"))
    print("\n[gate] captured typing: open %.1f%% of the time, %.1f dB in, %.1f dB out" % (typing["openFraction"] * 100, typing["inDb"], typing["outDb"]))
    print("[gate] captured speech: open %.1f%% of the time, %.1f dB in, %.1f dB out" % (speech["openFraction"] * 100, speech["inDb"], speech["outDb"]))
    assert typing["openFraction"] < 0.05, "typing hardly ever opens the gate: %r" % typing
    assert typing["outDb"] < typing["inDb"] - 25, "and what gets through is at least 25 dB down: %r" % typing
    assert speech["openFraction"] > 0.6 and speech["outDb"] > speech["inDb"] - 1.0, "speech opens it and goes through at its own loudness: %r" % speech


# ------------------------------------------------------------------ the noise gate: automatic, or a bar the player sets

BAR = """
  async function throughX(sig, opts, post) {
    const ctx = new OfflineAudioContext(1, sig.length, SR);
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }));
    await ctx.audioWorklet.addModule(url);
    const buf = ctx.createBuffer(1, sig.length, SR); buf.getChannelData(0).set(sig);
    const src = ctx.createBufferSource(); src.buffer = buf;
    const node = new AudioWorkletNode(ctx, 'voice-gate', { processorOptions: opts || {}, outputChannelCount: [1] });
    const msgs = [];
    node.port.onmessage = (e) => { if (e.data && e.data.hb) msgs.push(e.data); };
    if (post) node.port.postMessage(post);
    src.connect(node); node.connect(ctx.destination); src.start();
    const out = await ctx.startRendering();
    await new Promise((r) => setTimeout(r, 300));
    return { out: out.getChannelData(0), msgs };
  }
"""


def test_a_manual_bar_decides_what_opens_the_gate_and_the_automatic_one_follows_the_room():
    r = run(BAR + """
      const loud = new Float32Array(LEN); room(loud, -72); speech(loud, sec(2), sec(8), -30);
      const quiet = new Float32Array(LEN); room(quiet, -72); speech(quiet, sec(2), sec(8), -58);
      const seg = (s, o) => [db(rms(s, sec(2.1), sec(8.1))), db(rms(o, sec(2.1), sec(8.1)))];
      const a = await throughX(quiet, { mode: 'auto' });
      const m1 = await throughX(quiet, { mode: 'manual', thresholdDb: -48 });         // a bar above this quiet voice: it stays shut
      const m2 = await throughX(quiet, { mode: 'manual', thresholdDb: -70 });         // a sensitive bar: it opens
      const m3 = await throughX(loud, { mode: 'manual', thresholdDb: -48 });         // a loud voice is over the bar
      done({ auto: seg(quiet, a.out), shut: seg(quiet, m1.out), sensitive: seg(quiet, m2.out), loud: seg(loud, m3.out) });
    """)
    assert r["auto"][1] > r["auto"][0] - 1.5, "automatic: a quiet voice in a quiet room opens it: %r" % r["auto"]
    assert r["shut"][1] < r["shut"][0] - 30, "manual, bar above the voice: nothing goes through: %r" % r["shut"]
    assert r["sensitive"][1] > r["sensitive"][0] - 1.5, "manual, bar below the voice: it goes through whole: %r" % r["sensitive"]
    assert r["loud"][1] > r["loud"][0] - 1.5, "and a voice well over the bar goes through: %r" % r["loud"]


DIRECT = """
  // the worklet's own class, run by hand block by block, so that a message can arrive at an exact moment (an offline render runs far ahead of its messages)
  function direct(sig, opts, events) {
    let Cls = null;
    class Base { constructor() { this.port = { onmessage: null, postMessage() {} }; } }
    new Function('AudioWorkletProcessor', 'registerProcessor', 'sampleRate', WORKLET)(Base, (n, c) => { Cls = c; }, SR);
    const g = new Cls({ processorOptions: opts || {} });
    const out = new Float32Array(sig.length);
    const ev = (events || []).slice().sort((a, b) => a.at - b.at);
    let k = 0;
    for (let i = 0; i + 128 <= sig.length; i += 128) {
      while (k < ev.length && ev[k].at <= i / SR) { g.port.onmessage({ data: ev[k].msg }); k++; }
      const o = new Float32Array(128);
      g.process([[sig.subarray(i, i + 128)]], [[o]]);
      out.set(o, i);
    }
    return out;
  }
"""


def test_the_bar_can_be_moved_while_the_gate_runs():
    r = run(DIRECT + """
      const sig = new Float32Array(LEN); room(sig, -72); speech(sig, sec(2), sec(11), -50);
      const seg = (s, o, a, b) => [db(rms(s, sec(a), sec(b))), db(rms(o, sec(a), sec(b)))];
      // a low bar, then at 6 s the player moves it above the voice
      const up = direct(sig, { mode: 'manual', thresholdDb: -75 }, [{ at: 6, msg: { thresholdDb: -30 } }]);
      // a high bar, then at 6 s it is moved below the voice
      const down = direct(sig, { mode: 'manual', thresholdDb: -30 }, [{ at: 6, msg: { thresholdDb: -75 } }]);
      // automatic, switched to a high manual bar at 6 s, and back to automatic at 9 s
      const swap = direct(sig, { mode: 'auto' }, [{ at: 6, msg: { mode: 'manual', thresholdDb: -30 } }, { at: 9, msg: { mode: 'auto' } }]);
      // nonsense changes nothing
      const junk = direct(sig, { mode: 'manual', thresholdDb: -75 }, [{ at: 3, msg: { thresholdDb: 'loud', mode: 'sideways' } }, { at: 4, msg: null }, { at: 5, msg: { thresholdDb: NaN } }]);
      done({ upBefore: seg(sig, up, 2.2, 5.8), upAfter: seg(sig, up, 6.4, 10.8), downBefore: seg(sig, down, 2.2, 5.8), downAfter: seg(sig, down, 6.4, 10.8),
             swapA: seg(sig, swap, 2.2, 5.8), swapM: seg(sig, swap, 6.4, 8.8), swapB: seg(sig, swap, 9.4, 10.8), junk: seg(sig, junk, 2.2, 10.8) });
    """)
    assert r["upBefore"][1] > r["upBefore"][0] - 1.5 and r["upAfter"][1] < r["upAfter"][0] - 30, "bar moved above the voice: it was open, then shut: %r" % r
    assert r["downBefore"][1] < r["downBefore"][0] - 30 and r["downAfter"][1] > r["downAfter"][0] - 1.5, "bar moved below it: shut, then open: %r" % r
    assert r["swapA"][1] > r["swapA"][0] - 1.5 and r["swapM"][1] < r["swapM"][0] - 30 and r["swapB"][1] > r["swapB"][0] - 1.5,         "automatic, then a high manual bar, then automatic again: %r" % r
    assert r["junk"][1] > r["junk"][0] - 1.5, "a bar that is not a number, a mode that does not exist and an empty message change nothing: %r" % r["junk"]


def test_typing_is_still_kept_out_with_a_sensitive_manual_bar():
    r = run(BAR + """
      const sig = new Float32Array(LEN); room(sig, -70);
      typing(sig, sec(2.0), sec(5.0), 1);
      speech(sig, sec(6), sec(9), -26);
      const m = await throughX(sig, { mode: 'manual', thresholdDb: -62 });
      const seg = (a, b) => [db(rms(sig, sec(a), sec(b))), db(rms(m.out, sec(a), sec(b)))];
      done({ typing: seg(2.0, 5.0), speech: seg(6.1, 9.0) });
    """)
    assert r["typing"][1] < r["typing"][0] - 20, "keystrokes are brief, so a low bar does not let them in: %r" % r["typing"]
    assert r["speech"][1] > r["speech"][0] - 1.5, "and the voice passes: %r" % r["speech"]


def test_a_steady_noise_over_a_manual_bar_goes_through_and_the_automatic_gate_learns_to_ignore_it():
    """The difference the two modes have to be honest about: a manual bar is only a level. A fan louder than the bar opens it; the
    automatic gate treats a steady sound as the room."""
    r = run(BAR + """
      const sig = new Float32Array(LEN); room(sig, -45);
      const m = await throughX(sig, { mode: 'manual', thresholdDb: -55 });
      const a = await throughX(sig, { mode: 'auto' });
      const seg = (s, o) => [db(rms(s, sec(4), sec(11))), db(rms(o, sec(4), sec(11)))];
      done({ manual: seg(sig, m.out), auto: seg(sig, a.out) });
    """)
    assert r["manual"][1] > r["manual"][0] - 3, "manual: a hiss over the bar is sent: %r" % r["manual"]
    assert r["auto"][1] < r["auto"][0] - 25, "automatic: a steady hiss is the room, and is not: %r" % r["auto"]


def test_the_gate_reports_the_voice_level_and_the_bar_for_the_meter():
    r = run(BAR + """
      const sig = new Float32Array(LEN); room(sig, -72); speech(sig, sec(2), sec(10), -40);
      const m = await throughX(sig, { mode: 'manual', thresholdDb: -50 });
      const a = await throughX(sig, { mode: 'auto' });
      const mid = (msgs) => msgs.slice(Math.floor(msgs.length * 0.4), Math.floor(msgs.length * 0.6));
      const lv = (msgs) => Math.max(...mid(msgs).map((x) => x.level));
      done({ n: m.msgs.length, mLevel: lv(m.msgs), mThr: Array.from(new Set(m.msgs.slice(5).map((x) => x.thr))), aThr: a.msgs[a.msgs.length - 1].thr, aFloor: a.msgs[a.msgs.length - 1].floor,
             openSeen: m.msgs.some((x) => x.isOpen), shutSeen: m.msgs.some((x) => !x.isOpen) });
    """)
    assert r["n"] >= 100, "about ten reports a second: %r" % r["n"]
    assert -70 < r["mLevel"] < -35, "the level is the voice in the speech band: %r" % r["mLevel"]
    assert r["mThr"] and set(r["mThr"]) <= {-50.0, -53.0}, "manual: the bar is the player's own (3 dB lower once open): %r" % r["mThr"]
    assert r["openSeen"] and r["shutSeen"]
    assert abs(r["aThr"] - max(-68, r["aFloor"] + 9)) < 0.2, "automatic: the bar is the room's floor + 9 dB: %r / %r" % (r["aThr"], r["aFloor"])
