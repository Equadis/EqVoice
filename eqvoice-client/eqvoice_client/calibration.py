"""The microphone setup: three short recordings become the noise gate's bar and the microphone's volume.

The player is asked, in this order, to stay silent, to talk the way they normally do in the game, and to shout the way they would at someone
across the road. The page measures the microphone all the while (the level of the speech band, 200 Hz to 3.5 kHz, in decibels: the same
measure the noise gate works in, twenty readings a second, before any volume change) and hands the three lists of readings here. This module
is pure: readings in, settings out, so every rule below is tested without a browser.

What it decides
* **the bar** (``gate_db``): the level a voice must reach to open the gate. Halfway between the room's noise (the loudest it got while the
  player was silent) and the quiet end of ordinary speech, but never closer than 5 dB to the noise (a fan would open it) nor than 3 dB to the
  quiet end of speech (the end of a sentence would be cut). It is stated after the volume change below, because the gate comes after it.
* **the volume** (``input_gain``): left at 1.0 unless it has to move. A shout that came within 3 dB of the top of the scale (it would distort)
  pulls the volume down until the loudest shout sits at -6 dB; a microphone so quiet that ordinary speech sits below -50 dB is lifted, by at
  most 6 dB (the listeners' side lifts quiet microphones too, so this is only a start).
* **whether it worked** (``quality``): ``good``; ``noisy`` (the voice is less than 8 dB above the room: the bar is placed as well as it can be
  and the gate stays on automatic, which follows the noise); ``none`` (no voice was heard while the player was asked to talk: nothing changes).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

FLOOR_DB = -100.0           # what the page reports for digital silence
VOICED_ABOVE_DB = 6.0       # a reading counts as voice when it is this far above the room's noise
MIN_VOICED = 20             # about a second of voice (twenty readings a second) before the recording counts
MIN_SEPARATION_DB = 8.0     # the voice has to be this far above the room for the bar to be placed with confidence
CLOSEST_TO_NOISE_DB = 5.0   # the bar is never closer than this to the noise ...
CLOSEST_TO_SPEECH_DB = 3.0  # ... nor to the quiet end of ordinary speech
TOP_DB = -3.0               # a shout peaking above this is cut back ...
SHOUT_TARGET_DB = -6.0      # ... until its loudest sample sits here
QUIET_SPEECH_DB = -50.0     # ordinary speech below this is a quiet microphone: lift it ...
MAX_LIFT_DB = 6.0           # ... by at most this
GAIN_MIN, GAIN_MAX = 0.25, 2.0
GATE_MIN, GATE_MAX = -80.0, -20.0
GATE_STEP = 2.0


def percentile(values: Sequence[float], p: float) -> float:
    """The p-th percentile (0..100) of a non-empty list, by linear interpolation."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * max(0.0, min(100.0, p)) / 100.0
    lo = int(math.floor(k))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _number(v) -> Optional[float]:
    """A finite number, or None for anything else (the page is ours, but a reading is only trusted once it has been checked)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean(values: Optional[Sequence[float]]) -> List[float]:
    out = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(max(FLOOR_DB, min(0.0, f)))
    return out


@dataclass
class Result:
    ok: bool                          # settings were worked out (and should be applied)
    quality: str                      # good | noisy | none
    text: str                         # one or two sentences for the player
    gate_mode: str = "auto"
    gate_db: Optional[float] = None
    input_gain: Optional[float] = None
    facts: dict = field(default_factory=dict)      # the numbers behind it, for the log


def _round_to(v: float, step: float) -> float:
    return round(v / step) * step


def analyse(quiet, normal, loud, loud_peak_db: Optional[float] = None) -> Result:
    """The three recordings' readings (dB, taken before any volume change) and the loudest sample of the shout (dBFS) -> what to set."""
    q, n, l = _clean(quiet), _clean(normal), _clean(loud)
    if len(q) < MIN_VOICED:
        return Result(False, "none", "The silent part was too short to measure. Please try again.")
    floor = percentile(q, 95)                     # the loudest the room got (a cough is let go: 5% of the readings)
    noise_med = percentile(q, 50)
    voiced = [x for x in n if x > floor + VOICED_ABOVE_DB]
    facts = {"floor_db": round(floor, 1), "noise_median_db": round(noise_med, 1), "normal_readings": len(n), "voiced_normal": len(voiced)}
    if len(voiced) < MIN_VOICED:
        return Result(False, "none", "I did not hear you talk. Check that the right microphone is chosen and that it is not muted, then try again.",
                      facts=facts)
    speech_med = percentile(voiced, 50)
    speech_lo = percentile(voiced, 20)
    separation = speech_med - floor
    voiced_loud = [x for x in l if x > floor + VOICED_ABOVE_DB]
    shout_hi = percentile(voiced_loud, 90) if len(voiced_loud) >= MIN_VOICED // 2 else None
    facts.update({"speech_median_db": round(speech_med, 1), "speech_low_db": round(speech_lo, 1), "separation_db": round(separation, 1),
                  "shout_p90_db": None if shout_hi is None else round(shout_hi, 1), "shout_peak_dbfs": _number(loud_peak_db)})

    # the volume: only moved when it has to be
    gain_db = 0.0
    peak = _number(loud_peak_db)
    if peak is not None and peak > TOP_DB:
        gain_db = SHOUT_TARGET_DB - peak                                     # a negative number: turn it down
    elif speech_med < QUIET_SPEECH_DB:
        gain_db = min(MAX_LIFT_DB, QUIET_SPEECH_DB - speech_med)
    gain = max(GAIN_MIN, min(GAIN_MAX, 10 ** (gain_db / 20.0)))
    gain = round(gain * 20) / 20.0                                           # steps of 0.05
    gain_db = 20 * math.log10(gain)
    facts["input_gain"] = gain

    # the bar, as the gate will see it (after the volume change)
    if separation < MIN_SEPARATION_DB:
        bar = floor + CLOSEST_TO_NOISE_DB
        quality = "noisy"
    else:
        mid = floor + (speech_lo - floor) / 2.0
        bar = max(floor + CLOSEST_TO_NOISE_DB, min(speech_lo - CLOSEST_TO_SPEECH_DB, mid))
        quality = "good"
    gate_db = max(GATE_MIN, min(GATE_MAX, _round_to(bar + gain_db, GATE_STEP)))
    facts["gate_db"] = gate_db

    if quality == "good":
        text = "Done. Your voice is %d dB above your room, so the noise gate is set to %d dB." % (round(separation), round(gate_db))
        if gain < 0.99:
            text += " Your microphone was loud when you shouted, so I turned it down to %d%%." % round(gain * 100)
        elif gain > 1.01:
            text += " Your microphone is quiet, so I turned it up to %d%%." % round(gain * 100)
        return Result(True, "good", text, "manual", gate_db, gain, facts)
    text = ("Your voice is only %d dB above your room, so the gate cannot tell them apart well. I left the noise gate on automatic, which follows "
            "the noise. A quieter room, or a microphone closer to your mouth, will help." % max(0, round(separation)))
    return Result(True, "noisy", text, "auto", gate_db, gain, facts)
