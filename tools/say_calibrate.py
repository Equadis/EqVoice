"""Fit how far ordinary /say carries from the logs of an `/ev say` calibration (addon/Calibrate.lua).

    python tools/say_calibrate.py                        the latest realtest run (every client in it)
    python tools/say_calibrate.py --log Alice=home-a --log Bob=home-b      explicit logs (a home folder or a log file)
    python tools/say_calibrate.py --savedvars            no desktop clients were running? read what the addon kept in the game's saved variables
                                                         (written at /reload or logout; D:\\Octowow by default, or give the game folder)
    python tools/say_calibrate.py --apply-default        also write the fitted radius into the client's default config

Each log holds `TRACE addon saycal ...` lines, which the desktop client copied out of the bridge file:

    saycal start with <Partner>, one /say every 2.0 s
    saycal sent <n> to <Partner> at <yards> yd          this character spoke number n; the partner was this far away
    saycal heard <n> from <Sender> at <yards> yd         the partner's number n arrived
    saycal skipped <n> from <Sender> at <yards> yd       a gap: number n never arrived (distance from the samples around its due time)
    saycal heard ? from <Sender> at <yards> yd           arrived, but the other faction's /say hides the number

With both sides' logs, every message a character SENT is matched against what the partner HEARD, so a sample is
(distance when sent, heard yes/no). With one side's log only, the receiver's own heard/skipped lines are used.
The cutoff is the step position that misclassifies the fewest samples (heard when closer, silent when farther).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pipeline_report as pr  # noqa: E402

BUCKET = 5
SENT = re.compile(r"^saycal sent (\d+) to (\S+) at (\?|[\d.]+) yd")
HEARD = re.compile(r"^saycal heard (\?|\d+) from (\S+) at (\?|[\d.]+) yd")
SKIPPED = re.compile(r"^saycal skipped (\d+) from (\S+) at (\?|[\d.]+) yd")
START = re.compile(r"^saycal start with (\S+),")
STOP = re.compile(r"^saycal stop")


def yards(s: str) -> Optional[float]:
    return None if s == "?" else float(s)


class Log:
    """One character's calibration lines, split into sessions at each `saycal start`."""

    def __init__(self, label: str, events: list):
        self.label = label
        ids = [e for e in events if e.who == "desktop" and e.event == "identity"]
        self.character = ids[-1].get("character", label) if ids else label
        self.sessions: list = []
        cur = None
        for e in events:
            if e.who != "addon":
                continue
            if STOP.match(e.text) and cur is not None:
                cur["t1"] = e.ts or cur["t1"]
                continue
            m = START.match(e.text)
            if m:
                cur = {"partner": m.group(1), "t0": e.ts, "t1": e.ts, "sent": [], "heard": [], "skipped": []}
                self.sessions.append(cur)
                continue
            if cur is None:
                continue
            for rx, key in ((SENT, "sent"), (HEARD, "heard"), (SKIPPED, "skipped")):
                m = rx.match(e.text)
                if m:
                    seq = None if m.group(1) == "?" else int(m.group(1))
                    cur[key].append({"seq": seq, "who": m.group(2), "dist": yards(m.group(3)), "ts": e.ts})
                    cur["t1"] = e.ts or cur["t1"]
                    break


def unpack(text) -> list:
    """The addon's compact "seq:yards,seq:yards" lists, as entries like the trace lines give."""
    out = []
    for item in str(text or "").split(","):
        seq, _, d = item.partition(":")
        if not seq:
            continue
        out.append({"seq": int(seq) if seq.isdigit() else None, "who": "", "dist": None if d in ("", "?") else float(d), "ts": None})
    return out


class SavedLog:
    """One character's last calibration as the addon kept it in its saved variables (EqVoiceDB.saycal, written at /reload or logout).

    No times are kept, so sessions are matched by their partners alone. The saved copy does not say which character it belongs to
    (older addons); that is worked out from the partners: if A's partner is B and B's partner is A, A is the character B named.
    """

    def __init__(self, label: str, saycal: dict):
        self.label, self.character = label, str(saycal.get("character") or label)
        self.at = saycal.get("at")
        self.sessions = [{"partner": str(saycal.get("partner") or "?"), "t0": None, "t1": None, "sent": unpack(saycal.get("sent")),
                          "heard": unpack(saycal.get("heard")), "skipped": unpack(saycal.get("skipped"))}]


def logs_from_savedvars(game_dir) -> list:
    import savedvars
    logs = []
    for f in sorted(Path(game_dir).glob("WTF/Account/*/SavedVariables/EqVoice.lua")):
        try:
            db = savedvars.parse(f.read_text(encoding="utf-8", errors="replace")).get("EqVoiceDB") or {}
        except Exception:                                   # noqa: BLE001
            continue
        if isinstance(db.get("saycal"), dict) and db["saycal"].get("partner"):
            logs.append(SavedLog(f.parent.parent.name, db["saycal"]))
    named = [l for l in logs if l.character != l.label]
    if len(logs) == 2 and not named:                        # two files that name each other: each is the character the other one named
        a, b = logs
        if a.sessions[0]["partner"] != b.sessions[0]["partner"]:
            a.character, b.character = b.sessions[0]["partner"], a.sessions[0]["partner"]
    return logs


def overlap(a: dict, b: dict) -> float:
    """Seconds the two sessions have in common; negative when they are apart (people press start a few seconds apart)."""
    if a["t0"] is None or b["t0"] is None:
        return 1.0
    return min(a["t1"], b["t1"]) - max(a["t0"], b["t0"])


def samples_from(logs: list, notes: Optional[list] = None) -> list:
    """[{dist, heard, how}] for every message that can be judged. ``notes`` collects what was left out, and why."""
    notes = notes if notes is not None else []
    out: list = []
    used = set()                                            # receiver sessions already matched to a sender
    for s in logs:
        for ss in s.sessions:
            if not ss["sent"]:
                continue
            partner = next((l for l in logs if l is not s and l.character.lower() == ss["partner"].lower()), None)
            rs = None
            if partner is not None:
                cands = [r for r in partner.sessions if r["partner"].lower() == s.character.lower() and overlap(ss, r) > -30.0]
                rs = max(cands, key=lambda r: overlap(ss, r), default=None)
            if rs is None:
                continue
            used.add((partner.label, id(rs)))
            heard_by_seq = {h["seq"]: h for h in rs["heard"] if h["seq"] is not None}
            garbled = [h for h in rs["heard"] if h["seq"] is None]
            sent = ss["sent"]
            near = [m for m in sent if m["dist"] is not None and m["dist"] <= 20.0]
            if near and not rs["heard"]:
                # lines said at close range that the listener never received, and it received nothing else either: the speaker's
                # lines are not delivered at all (muted / no permission), which says nothing about range
                notes.append("%s's %d lines never arrived, even at %.0f yd: that character cannot /say here, so its lines are ignored "
                             "(use `/ev say listen` on it next time)" % (s.character, len(sent), min(m["dist"] for m in near)))
                continue
            if heard_by_seq:
                first, reach = min(heard_by_seq), max((h["dist"] or 0.0) for h in heard_by_seq.values())
                lead = [m for m in sent if m["seq"] is not None and m["seq"] < first and m["dist"] is not None and m["dist"] <= reach]
                if lead:
                    notes.append("%s's first %d line(s) (numbers %s) were said before %s's side was listening (nothing was heard until number %d, "
                                 "though they were within the range later heard): left out" % (
                                     s.character, len(lead), ",".join(str(m["seq"]) for m in lead), partner.character, first))
                    sent = [m for m in sent if m not in lead]
            for m in sent:
                h = heard_by_seq.get(m["seq"])
                how = "matched by number"
                if h is None:
                    g = next((g for g in garbled if g["ts"] is not None and m["ts"] is not None and -0.5 <= g["ts"] - m["ts"] <= 1.6), None)
                    if g is not None:
                        h, how = g, "matched by time (the other faction's /say hides the number)"
                d = m["dist"] if m["dist"] is not None else (h["dist"] if h else None)
                if d is not None:
                    out.append({"dist": d, "heard": h is not None, "how": how})
    # a receiver whose sender's log is missing still knows what it heard and which numbers were skipped
    for r in logs:
        for rs in r.sessions:
            if (r.label, id(rs)) in used:
                continue
            for h in rs["heard"]:
                if h["dist"] is not None:
                    out.append({"dist": h["dist"], "heard": True, "how": "receiver only"})
            for k in rs["skipped"]:
                if k["dist"] is not None:
                    out.append({"dist": k["dist"], "heard": False, "how": "receiver only (gap in the numbers)"})
    return out


def fit(samples: list) -> Optional[dict]:
    """Where /say stops, from lines heard and lines not heard.

    A line that was HEARD proves the listener was in range at that distance; a line that was NOT heard proves nothing by itself (the server
    can drop lines for other reasons: a chat flood limit, a loading screen, the listener not yet listening). So the estimate trusts hits:
    the range is at least the farthest distance a line was heard from, and at most the nearest miss beyond that. Misses nearer than the
    farthest hit are "dropouts": counted, reported, and kept out of the estimate.
    """
    if not samples:
        return None
    hits = sorted(s["dist"] for s in samples if s["heard"])
    misses = sorted(s["dist"] for s in samples if not s["heard"])
    n = len(samples)
    far_hit = hits[-1] if hits else None
    beyond = [d for d in misses if far_hit is None or d > far_hit]
    near_miss = beyond[0] if beyond else None
    dropouts = [d for d in misses if far_hit is not None and d <= far_hit]
    if far_hit is None:
        rng, gap = (misses[0] - 1.0 if misses else 0.0), (None, misses[0] if misses else None)
    elif near_miss is None:
        rng, gap = far_hit, (far_hit, None)                              # never went far enough: only a lower bound
    else:
        rng, gap = (far_hit + near_miss) / 2.0, (far_hit, near_miss)
    return {"range": rng, "errors": len(dropouts), "n": n, "heard_n": len(hits), "plateau": gap, "gap": gap,
            "farthest_heard": far_hit, "nearest_missed": near_miss, "dropouts": dropouts}


def buckets(samples: list) -> list:
    b: dict = {}
    for s in samples:
        k = int(s["dist"] // BUCKET) * BUCKET
        e = b.setdefault(k, [0, 0])
        e[0] += 1
        e[1] += 1 if s["heard"] else 0
    return [(k, v[0], v[1]) for k, v in sorted(b.items())]


def recommendation(rng: float) -> dict:
    r = int(round(rng))
    return {"say_radius": float(r), "leave_hysteresis": 3.0, "hub_link_radius_needed": float(r + 3)}


def render(samples: list, f: Optional[dict], notes: Optional[list] = None) -> str:
    out = ["/say calibration: %d message(s) judged" % len(samples)]
    for n in notes or []:
        out.append("  note: " + n)
    if f is None:
        out.append("  nothing to fit. Both characters need `/ev say with <other>` and `/ev say start`, and the desktop clients must have")
        out.append("  been running (python tools/realtest.py run) so the addon's lines reached the logs.")
        return "\n".join(out)
    out.append("")
    out.append("  yards      messages   heard")
    for k, n, h in buckets(samples):
        pct = 100.0 * h / n
        out.append("  %3d-%3d   %6d   %5.0f%%  %s" % (k, k + BUCKET, n, pct, "#" * int(round(pct / 5.0))))
    out.append("")
    if f["heard_n"] == 0:
        out.append("  /say was not heard at all in %d lines. Wrong partner name, or the characters never came within /say range." % f["n"])
    elif f["nearest_missed"] is None:
        out.append("  /say was heard as far as %.1f yd, and no line was missed beyond that: walk further apart (to ~80 yd) to find the edge." % f["farthest_heard"])
        out.append("  (This is only a lower bound.)")
    else:
        out.append("  /say carried to about %.1f yards: heard as far as %.1f yd, and the first line missed beyond that was at %.1f yd." % (
            f["range"], f["farthest_heard"], f["nearest_missed"]))
    if f["dropouts"]:
        d = f["dropouts"]
        out.append("  %d line(s) were NOT heard although a line was heard from farther away (they were at %.1f to %.1f yd): temporary dropouts, such as a "
                   "chat flood limit on the server" % (len(d), min(d), max(d)))
        out.append("  or the listener not receiving for a moment, not range. They are left out of the estimate. (Lines every 2 s are safe; every 1 s is not.)")
        if len(d) > 0.3 * f["n"]:
            out.append("  !! a lot of lines were lost inside the range: repeat the pass with the default 2 s interval and walk slower")
    lo, hi = f["gap"]
    if lo is not None and hi is not None and hi - lo > 3:
        out.append("  The edge is somewhere between %.1f and %.1f yd: no line was sent from between them." % (lo, hi))
        if lo <= 40.0 <= hi:
            out.append("  The common server default of 40 yd is inside that range. To narrow it: stand still at about 38, 40 and 42 yd for ten lines each "
                       "(`/ev say show` prints the distance), or walk very slowly through that stretch.")
    elif lo is not None and hi is not None:
        out.append("  Pinned down to %.1f to %.1f yd (a width of %.1f yd). The distance is sampled up to half a second before each line, so expect about "
                   "one yard of play either way." % (lo, hi, hi - lo))
    rec = recommendation(f["range"])
    out.append("")
    out.append("  recommended: say_radius %.0f (config.json \"proximity\", or `--say-radius %.0f`)" % (rec["say_radius"], rec["say_radius"]))
    if rec["hub_link_radius_needed"] > 45:
        out.append("  !! the hub only links pairs within 45 yd: raise it to at least %.0f (LINK_RADIUS for the Worker, --link-radius for the Python hub)"
                   % rec["hub_link_radius_needed"])
    return "\n".join(out)


def apply_default(radius: float, config_py: Optional[Path] = None) -> str:
    """Write the fitted radius into the client's DEFAULTS (say_radius only: how far a voice is at full volume is its own setting, full_radius)."""
    p = config_py or (ROOT / "eqvoice-client" / "eqvoice_client" / "config.py")
    t = p.read_text(encoding="utf-8")
    rec = recommendation(radius)
    new = re.sub(r'("say_radius": )[\d.]+(,)', lambda m: m.group(1) + ("%.1f" % rec["say_radius"]) + m.group(2), t, count=1)
    if new == t:
        return "config defaults already say_radius %.0f" % rec["say_radius"]
    p.write_text(new, encoding="utf-8")
    return "wrote say_radius %.1f into %s (tests that assert the old default need updating)" % (rec["say_radius"], p)


HISTORY = Path(r"D:\Octowow\.eco-work\say-calibration\history.json")


def run_key(logs: list) -> str:
    """Identifies one calibration pass (the addon's own timestamps), so analysing the same saved data twice counts it once."""
    return "|".join("%s@%s" % (l.character, getattr(l, "at", "?")) for l in sorted(logs, key=lambda x: x.character))


def history_add(path: Path, key: str, samples: list) -> dict:
    import json
    hist = {}
    try:
        hist = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    hist[key] = [{"dist": s["dist"], "heard": s["heard"], "how": s["how"]} for s in samples]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return hist


def load_logs(items: list) -> list:
    logs = []
    for item in items:
        label, sep, path = item.partition("=")
        if not sep:
            label, path = Path(item).name or item, item
        p = Path(path)
        if p.is_dir() and (p / "logs").exists() is False and (p / "eqvoice.log").exists():
            p = p / "eqvoice.log"
        logs.append(Log(label, pr.parse(pr.read_log(p))))
    return logs


def latest_run_homes() -> list:
    runs = Path(r"D:\Octowow\.eco-work\realtest")
    latest = Path((runs / "latest.txt").read_text(encoding="utf-8").strip())
    names = (latest / "clients.txt").read_text(encoding="utf-8").split() if (latest / "clients.txt").exists() else []
    return ["%s=%s" % (n, runs / ("home-" + n)) for n in dict.fromkeys(names)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", action="append", default=[], metavar="[LABEL=]HOME_OR_LOG")
    ap.add_argument("--apply-default", action="store_true", help="write the fitted radius into the client's default config")
    ap.add_argument("--history", default=str(HISTORY), metavar="FILE",
                    help="with --savedvars: every pass analysed is kept here and all of them are fitted together (the game keeps only the last)")
    ap.add_argument("--fresh", action="store_true", help="with --savedvars: fit only the pass in the saved variables, ignoring the history")
    ap.add_argument("--savedvars", nargs="?", const=r"D:\Octowow", default=None, metavar="GAME_DIR",
                    help="read the addon's saved variables (WTF\\Account\\*\\SavedVariables\\EqVoice.lua) instead of client logs")
    a = ap.parse_args(argv)
    notes: list = []
    if a.savedvars:
        logs = logs_from_savedvars(a.savedvars)
        if not logs:
            ap.error("no /say calibration found in %s: type /reload in the game after /ev say stop, then try again" % a.savedvars)
        notes.append("from the saved variables of %s (one calibration per character: the last one)" % ", ".join(
            "%s [%s]" % (l.character, l.label) for l in logs))
    else:
        items = a.log or latest_run_homes()
        if not items:
            ap.error("no logs: give --log, run python tools/realtest.py run first, or use --savedvars")
        logs = load_logs(items)
    samples = samples_from(logs, notes)
    if a.savedvars and not a.fresh:
        hist = history_add(Path(a.history), run_key(logs), samples)
        if len(hist) > 1:
            notes.append("fitting %d passes together (kept in %s); this pass has %d lines" % (len(hist), a.history, len(samples)))
        samples = [s for v in hist.values() for s in v]
    f = fit(samples)
    print(render(samples, f, notes))
    if a.apply_default and f is not None:
        print()
        print(apply_default(f["range"]))
    return 0 if f is not None else 1


if __name__ == "__main__":
    sys.exit(main())
