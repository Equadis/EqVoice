"""The RNNoise noise suppressor (ui/rnnoise-worklet.js), run for real: the worklet processes synthetic audio in a real browser's offline audio context.

Synthetic stand-ins again (a room hiss, syllables of a voice, keystrokes): they prove the worklet loads, runs at the browser's block size and takes steady
noise out while a voice goes through; how it sounds on a real microphone is for ears."""
import json

import pytest

import edgeharness
from test_keyboard_filter import SIGNALS

pytestmark = pytest.mark.skipif(not edgeharness.available(), reason="needs a Chromium-based browser")

WORKLET = (edgeharness.UI / "rnnoise-worklet.js").read_text(encoding="utf-8")


def run(script, timeout=180):
    body = "(async () => {\n%s\n})();" % script
    html = edgeharness.page("const RNN = %s;\n%s\n%s" % (json.dumps(WORKLET), SIGNALS, body), scripts=())
    return edgeharness.run(html, timeout=timeout)


THROUGH = """
  async function suppress(sig) {
    const ctx = new OfflineAudioContext(1, sig.length, SR);
    const url = URL.createObjectURL(new Blob([RNN], { type: 'text/javascript' }));
    await ctx.audioWorklet.addModule(url);
    const buf = ctx.createBuffer(1, sig.length, SR); buf.getChannelData(0).set(sig);
    const src = ctx.createBufferSource(); src.buffer = buf;
    const node = new AudioWorkletNode(ctx, 'NoiseSuppressorWorklet', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
    src.connect(node); node.connect(ctx.destination); src.start();
    return (await ctx.startRendering()).getChannelData(0);
  }
"""


def test_the_suppressor_loads_and_takes_steady_noise_out():
    out = run(THROUGH + """
      const sig = new Float32Array(LEN);
      room(sig, -38);                                            // a fan: steady hiss all the way through
      const y = await suppress(sig);
      const win = (a, t0, t1) => db(rms(a, sec(t0), sec(t1)));
      done({ before: win(sig, 4, 10), after: win(y, 4, 10), len: y.length });
    """)
    assert out["len"] == 12 * 48000
    assert out["after"] < out["before"] - 10, "steady noise comes down by at least 10 dB (%.1f dB to %.1f dB)" % (out["before"], out["after"])


def test_a_voice_goes_through_and_keystrokes_are_pulled_down():
    out = run(THROUGH + """
      const talk = new Float32Array(LEN); speech(talk, sec(1), sec(11), -22); room(talk, -45);
      const yt = await suppress(talk);
      const typed = new Float32Array(LEN); room(typed, -45); typing(typed, sec(1), sec(11), 1.0, 0.008);
      const yk = await suppress(typed);
      const win = (a, t0, t1) => db(rms(a, sec(t0), sec(t1)));
      done({ voiceIn: win(talk, 2, 10), voiceOut: win(yt, 2, 10), keysIn: win(typed, 2, 10), keysOut: win(yk, 2, 10) });
    """)
    print(out)
    assert out["voiceOut"] > out["voiceIn"] - 12, "the voice is still there (%.1f dB in, %.1f dB out)" % (out["voiceIn"], out["voiceOut"])
    assert out["keysOut"] < out["keysIn"] - 3, "keystrokes come down (%.1f dB to %.1f dB)" % (out["keysIn"], out["keysOut"])
