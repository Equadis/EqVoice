"""Recordings for a real engine's fake microphone: someone typing, someone talking, someone talking and then shouting.

Synthetic (pure Python, deterministic): a voice is syllables of harmonics with formant bumps, consonant bursts and pauses; a keystroke is
a sharp noise burst, a low thump and a softer click on release. 16 kHz mono 16-bit, which is what Chromium's
--use-file-for-fake-audio-capture reads (it resamples). The file loops while the microphone is open."""
import math
import wave
from array import array

SR = 16000


class Rng:
    def __init__(self, seed):
        self.s = seed

    def __call__(self):
        self.s = (self.s * 1103515245 + 12345) % 2147483648
        return self.s / 2147483648


def _sec(t):
    return int(round(t * SR))


def _rms(a):
    return math.sqrt(sum(x * x for x in a) / max(1, len(a)))


def room(sig, level_db, rnd):
    amp = 10 ** (level_db / 20) * math.sqrt(3)
    for i in range(len(sig)):
        sig[i] += (rnd() * 2 - 1) * amp


def speech(sig, start, end, level_db, rnd):
    """Syllables of 240 ms: a consonant burst, a vowel of harmonics (formants near 700 and 1500 Hz), a pause every ninth syllable."""
    amp, syl = 10 ** (level_db / 20), _sec(0.24)
    n, t0 = 0, start
    while t0 + syl <= end:
        if n % 9 != 8:
            f0 = 110 + 25 * math.sin(n * 0.7)
            burst = _sec(0.012)
            for i in range(burst):
                sig[t0 + i] += (rnd() * 2 - 1) * amp * 0.5 * (1 - i / burst)
            vs, ve = t0 + _sec(0.03), t0 + _sec(0.2)
            harmonics = []
            k = 1
            while f0 * k < 3800:
                f = f0 * k
                formant = 1 + 3 * math.exp(-((f - 700) / 250) ** 2) + 2 * math.exp(-((f - 1500) / 350) ** 2)
                harmonics.append((2 * math.pi * f / SR, formant / k))
                k += 1
            tmp = []
            for i in range(ve - vs):
                v = sum(math.sin(w * i) * a for w, a in harmonics)
                env = min(1.0, i / _sec(0.02), (ve - vs - i) / _sec(0.03))
                tmp.append(v * env)
            r = _rms(tmp) or 1.0
            for i, v in enumerate(tmp):
                sig[vs + i] += v / r * amp
        t0 += syl
        n += 1


def key(sig, at, strength, rnd):
    burst, thump = _sec(0.008), _sec(0.025)
    for i in range(burst):
        sig[at + i] += (rnd() - rnd()) * 0.5 * strength * math.exp(-i / _sec(0.0025))
    for i in range(thump):
        sig[at + i] += math.sin(2 * math.pi * 160 * i / SR) * 0.12 * strength * math.exp(-i / _sec(0.008))
    up = at + _sec(0.07)
    for i in range(burst):
        sig[up + i] += (rnd() - rnd()) * 0.15 * strength * math.exp(-i / _sec(0.002))


def typing(sig, start, end, strength, rnd):
    t = start
    while t < end:
        key(sig, t, strength, rnd)
        t += _sec(0.09 + rnd() * 0.13)


def write(path, sig):
    pcm = array("h", (max(-32767, min(32767, int(round(x * 32767)))) for x in sig))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def typing_wav(path, seconds=12.0):
    """Room hiss and nothing but typing."""
    rnd = Rng(4242)
    sig = [0.0] * _sec(seconds)
    room(sig, -62, rnd)
    typing(sig, _sec(0.3), _sec(seconds - 0.4), 1.2, rnd)
    return write(path, sig)


def speech_wav(path, seconds=12.0, level_db=-24):
    """Room hiss and someone talking steadily (a little typing in the gaps would only hide the point)."""
    rnd = Rng(777)
    sig = [0.0] * _sec(seconds)
    room(sig, -62, rnd)
    speech(sig, _sec(0.3), _sec(seconds - 0.4), level_db, rnd)
    return write(path, sig)


def shouting_wav(path, normal_s=30.0, shout_s=2.5, tail_s=3.5, level_db=-24, shout_db=14):
    """Someone talking normally for a long while, then shouting for a couple of seconds, then back to normal. Loops."""
    rnd = Rng(31337)
    total = normal_s + shout_s + tail_s
    sig = [0.0] * _sec(total)
    room(sig, -62, rnd)
    speech(sig, _sec(0.2), _sec(normal_s), level_db, rnd)
    speech(sig, _sec(normal_s), _sec(normal_s + shout_s), level_db + shout_db, rnd)
    speech(sig, _sec(normal_s + shout_s), _sec(total - 0.1), level_db, rnd)
    return write(path, sig)
