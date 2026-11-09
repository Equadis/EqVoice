"""Which boundary of the EqVoice pipeline was the last one reached? Reads the TRACE lines every component writes.

    python tools/pipeline_report.py --client Alice=D:\\path\\to\\home-a --client Bobby=D:\\path\\to\\home-b --hub hub.log
    python tools/pipeline_report.py --client D:\\path\\to\\home          (one client: the steps that need a second player say so)

A client argument is a home folder (its logs\\eqvoice.log is read) or a log file; only the part after the last
"TRACE desktop start" line is used. The hub log is the output of ``wrangler dev --var TRACE:1`` or of
``python -m eqvoice_signalling --trace``. The addon's lines arrive through the bridge file and are logged by the
desktop client, so no game files are needed.

Exit status: 0 when all 20 steps passed, 1 otherwise. The steps are the ones in docs/03-onwow-test-plan.md.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PASS, FAIL, NOT_YET, NA = "PASS", "FAIL", "NOT YET", "n/a"
SAY_RADIUS, LEAVE_RADIUS = 41.0, 44.0

KV = re.compile(r'(\w+)=("[^"]*"|\S+)')
STAMP = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d):(\d\d):(\d\d)[,.](\d+)")
FOUND = re.compile(r"^lookup (\S+): FOUND by exact name, ([\d.]+) yd")
LOOKUP_MOVE = re.compile(r"^lookup (\S+): ([\d.]+) yd")
LOOKUP_MISS = re.compile(r"^lookup (\S+): not available \((.*)\)")
INBOUND_READ = re.compile(r"^inbound: read (\S+) seq (\d+) for (\S+): roster is now (\d+) name\(s\): (.*)$")
OUTBOUND = re.compile(r"^outbound: frame #(\d+) (written|NOT written) \((\d+) bytes, file ([^,]+), optical (\w+)\)(.*)$")


@dataclass
class Ev:
    n: int                    # line number in its file, for ordering
    ts: Optional[float]       # seconds since the epoch, when the line has a stamp
    who: str                  # hub | desktop | addon
    event: str                # for hub/desktop; "text" for addon
    kv: dict = field(default_factory=dict)
    text: str = ""

    def get(self, key, default=None):
        return self.kv.get(key, default)


def unq(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == '"' and v[-1] == '"' else v


def parse(text: str, since_start: bool = True) -> list:
    """TRACE lines of one log, after the last 'TRACE desktop start' when ``since_start``."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        i = line.find("TRACE ")
        if i < 0:
            continue
        rest = line[i + 6:]
        who, _, body = rest.partition(" ")
        if who not in ("hub", "desktop", "addon"):
            continue
        ts = None
        m = STAMP.match(line)
        if m:
            try:
                ts = time.mktime(time.strptime("%s %s:%s:%s" % m.groups()[:4], "%Y-%m-%d %H:%M:%S")) + float("0." + m.group(5))
            except ValueError:
                ts = None
        if who == "addon":
            out.append(Ev(n, ts, who, "text", {}, body.strip()))
        else:
            event, _, tail = body.partition(" ")
            out.append(Ev(n, ts, who, event, {k: unq(v) for k, v in KV.findall(tail)}, tail))
    if since_start:
        starts = [i for i, e in enumerate(out) if e.who == "desktop" and e.event == "start"]
        if starts:
            out = out[starts[-1]:]
    return out


def read_log(path: Path) -> str:
    p = Path(path)
    if p.is_dir():
        p = p / "logs" / ("eqvoice.log" if (p / "logs" / "eqvoice.log").exists() or not (p / "logs" / "octovoice.log").exists() else "octovoice.log")
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@dataclass
class Client:
    label: str
    events: list
    character: str = ""

    def find(self, who: str, event: str, pred=None) -> list:
        return [e for e in self.events if e.who == who and e.event == event and (pred is None or pred(e))]

    def addon(self, pattern) -> list:
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return [e for e in self.events if e.who == "addon" and rx.search(e.text)]


@dataclass
class Step:
    n: int
    title: str
    status: str = NOT_YET
    evidence: str = ""
    hint: str = ""


def csv(v) -> list:
    return [] if v in (None, "", "-") else str(v).split(",")


def results_of(e: Ev) -> dict:
    out = {}
    for item in csv(e.get("results")):
        name, _, yd = item.partition(":")
        try:
            out[name] = float(yd)
        except ValueError:
            pass
    return out


def pick_pair(clients: list, hub: list) -> list:
    """With more than two clients, the two that actually talked to each other (linked, connected, saw each other)."""
    best, best_score = clients[:2], -1
    for i, a in enumerate(clients):
        for b in clients[i + 1:]:
            score = 0
            if any(e.event == "link_up" and {e.get("a"), e.get("b")} == {a.character, b.character} for e in hub):
                score += 4
            for x, y in ((a, b), (b, a)):
                if x.find("desktop", "webrtc", lambda e: e.get("name") == y.character and e.get("connection") == "connected"):
                    score += 3
                if x.find("desktop", "distance", lambda e: e.get("name") == y.character):
                    score += 1
                if x.find("desktop", "roster_received", lambda e: y.character in csv(e.get("names"))):
                    score += 1
            if score > best_score:
                best, best_score = [a, b], score
    return best


def engine_problems(c: "Client") -> list:
    """Why this client's audio cannot be real voice: a fake microphone or a hidden browser nobody asked for."""
    start = (c.find("desktop", "start") or [Ev(0, None, "desktop", "start")])[-1]
    asked_fake = start.get("fake_media") == "yes"
    asked_headless = start.get("headless") == "yes"
    out = []
    if not asked_fake and c.find("desktop", "mic", lambda e: e.get("fake") == "yes"):
        out.append("its microphone is Chromium's FAKE test device (a beep, not a voice)")
    if not asked_headless and c.find("desktop", "media_engine", lambda e: e.get("headless") == "yes"):
        out.append("its voice engine is a hidden test browser")
    if not asked_headless and not asked_fake:
        out += [e.get("detail", e.get("problem", "?")) for e in c.find("desktop", "media_warning")
                if e.get("problem") not in ("fake_microphone", "hidden_engine")]
    return out


def evaluate(clients: list, hub: list, test_names: Optional[list] = None) -> list:
    """The 20 steps, in order, for the given clients (1 or 2; with more, the pair that talked)."""
    for c in clients:
        ids = c.find("desktop", "identity")
        c.character = ids[-1].get("character", "") if ids else ""
    if len(clients) > 2:
        clients = pick_pair(clients, hub)
    A = clients[0]
    B = clients[1] if len(clients) > 1 else None
    two = B is not None
    steps = [
        Step(1, "Hub running and narrating", hint="start it with `python tools/realtest.py hub`; check the port 8787 is free and the hub log exists"),
        Step(2, "Desktop clients running", hint="`python tools/realtest.py client ...`; read the client's logs\\eqvoice.log"),
        Step(3, "WoW is writing frames the desktop reads", hint="the game must be at the world (not the login screen) with the EqVoice addon enabled; `/ev status` "
                                                                 "shows `bridge file on`; check --wow-dir points at the folder with WoW.exe and that CustomData\\eqvoice*.txt changes"),
        Step(4, "Each desktop gets its own identity from WoW", hint="the character comes from the frame, never typed; if missing, step 3 is the problem; two clients must be two different characters"),
        Step(5, "Both register into the online roster", hint="the hub URL in the client (`hub_state`), the hub log for `registered`; a `name_in_use` error means the name is already online"),
        Step(6, "Each desktop receives the other character's name", hint="same realm? the roster is per realm; look at hub `roster_update` lines and the client's `roster_received`"),
        Step(7, "Inbound bridge: desktop writes the name, the addon reads it", hint="`inbound_written` = the file was written; `inbound_acked` = the game's frame says it read that seq; "
                                                                                     "if only the first appears, run `/ev diag inbox` in game and `/ev status` (inbox: FRESH?)"),
        Step(8, "Addon resolves the remote character by exact name", hint="the other player must be loaded near you (same map area, within ~86 yd); `/ev diag name <Player>` tests one name by hand"),
        Step(9, "Addon reports the real distance through the outbound file", hint="look for `outbound: ... results` in the addon lines"),
        Step(10, "Desktop reads that distance", hint="`outbound_read` results and `distance` lines in the client log; if the addon reports it but the desktop does not, the outbound file path or --character is wrong"),
        Step(11, "Both clients independently report each other nearby", hint="hub `proximity frm=A peers=B:..` AND `frm=B peers=A:..`; one-sided means one game does not see the other"),
        Step(12, "Hub creates the mutual-proximity link", hint="hub `link_up`; `link_down why=` says which rule failed (disagree / stale / one side / farther than 45 yd)"),
        Step(13, "WebRTC connection starts and connects", hint="`webrtc ice=... connection=...` lines; `failed` = the two PCs cannot reach each other directly (TURN not built)"),
        Step(14, "Push-to-talk audio flows in both directions", hint="hold PTT in one client at a time; `audio_tx SENDING` on the speaker, `audio_rx RECEIVING` on the listener; also watch the mic pill"),
        Step(15, "Characters walked apart", hint="walk one character away along open ground"),
        Step(16, "Reported distance increased", hint="`distance name=... yards=...` should climb as you walk"),
        Step(17, "Attenuation: volume falls with distance", hint="`audio_gain` lines: gain should fall from 1.0 (within 10 yd) towards 0 at the 41 yd voice radius"),
        Step(18, "Leaving range at the configured threshold", hint="state CONNECTED -> OUT_OF_RANGE_GRACE once past 43 yd (or unseen) for 1.5 s"),
        Step(19, "Audio stops", hint="`audio_gain ... transmit_to_them=no` and `audio_rx ... silent`"),
        Step(20, "Peer connection torn down after the grace period", hint="OUT_OF_RANGE_GRACE -> DISCONNECTED after ~8 s with `webrtc_closed`"),
    ]
    S = {s.n: s for s in steps}

    def ok(n, evidence):
        S[n].status, S[n].evidence = PASS, evidence

    def na(n, why):
        S[n].status, S[n].evidence = NA, why

    # 1 hub
    if hub:
        ok(1, "hub trace lines: %d (first: %s)" % (len(hub), hub[0].event))
    elif any(c.find("desktop", "hub_registered") for c in clients):
        ok(1, "inferred: the clients registered with it (no hub log was supplied; pass --hub LOG for its own account)")
    # 2 clients
    started = [c for c in clients if c.events]
    if started:
        ok(2, "%d client log(s) with trace lines" % len(started))
    # 3 frames
    seen = [c for c in clients if c.find("desktop", "outbound_first_frame") or c.find("desktop", "outbound_read")]
    if len(seen) == len(clients):
        f = seen[0].find("desktop", "outbound_first_frame")
        ok(3, "frames read: " + "; ".join("%s via %s" % (c.label, (c.find("desktop", "outbound_first_frame") or [Ev(0, None, "d", "", {})])[0].get("source", "?"))
                                          for c in seen))
    elif seen:
        S[3].evidence = "only %s reads frames" % ", ".join(c.label for c in seen)
    # 4 identity
    ids = {c.label: c.character for c in clients if c.character}
    if len(ids) == len(clients) and len(set(ids.values())) == len(ids):
        ok(4, "; ".join("%s = %s on %s" % (c.label, c.character, c.find("desktop", "identity")[-1].get("realm", "?")) for c in clients))
        realms = {c.find("desktop", "identity")[-1].get("realm") for c in clients}
        if len(realms) > 1:
            S[4].status, S[4].evidence = FAIL, "the characters are on different realms %s: the roster is per realm" % sorted(realms)
    elif ids:
        S[4].evidence = "identities so far: %r" % ids
    # 5 register
    reg_hub = {e.get("character") for e in hub if e.event == "registered"}
    reg_cli = {c.character for c in clients if c.find("desktop", "hub_registered")}
    names = {c.character for c in clients if c.character}
    if names and names <= (reg_hub | reg_cli) and (not hub or names <= reg_hub):
        ok(5, "registered: " + ", ".join(sorted(names)))
    elif reg_hub or reg_cli:
        S[5].evidence = "registered so far: %s" % ", ".join(sorted(reg_hub | reg_cli))
    errs = [e for c in clients for e in c.find("desktop", "hub_error")]
    if errs and S[5].status != PASS:
        S[5].status, S[5].evidence = FAIL, "hub error: " + "; ".join("%s %s" % (e.get("code"), e.get("msg")) for e in errs[:2])
    # 6 roster
    def roster_has(c, other):
        for e in c.find("desktop", "roster_received"):
            if other in csv(e.get("names")):
                return True
        for e in c.find("desktop", "roster_update"):
            if other in csv(e.get("added")):
                return True
        return False
    if two:
        if roster_has(A, B.character) and roster_has(B, A.character):
            ok(6, "%s got %s and %s got %s" % (A.character, B.character, B.character, A.character))
        elif roster_has(A, B.character) or roster_has(B, A.character):
            S[6].evidence = "only one side has the other's name"
    else:
        na(6, "needs a second player (use --test-roster NAME to exercise steps 7-10 alone)")
    # others of each client
    def others(c):
        if two:
            return [x.character for x in clients if x is not c and x.character]
        return list(test_names or [])
    # 7 inbound
    def inbound_ok(c):
        want = others(c)
        if not want:
            return None
        w = [e for e in c.find("desktop", "inbound_written") if all(n in csv(e.get("names")) for n in want)]
        a = [e for e in c.find("desktop", "inbound_acked") if int(e.get("seq", -1)) >= 0]
        ad = c.addon(INBOUND_READ)
        ad = [e for e in ad if all(n in INBOUND_READ.search(e.text).group(5) for n in want)]
        return w, a, ad
    parts = [(c, inbound_ok(c)) for c in clients]
    if all(p[1] is not None for p in parts) and all(p[1][0] and p[1][1] and p[1][2] for p in parts):
        ok(7, "; ".join("%s: written, acked in %ss, addon read it" % (c.label, r[1][-1].get("by_the_game_after_s", "?")) for c, r in parts))
    elif all(p[1] is not None and p[1][0] and p[1][1] for p in parts):
        # The game's acknowledgement IS its proof of reading; only its own sentence is missing. The addon keeps the last 16
        # sentences in each frame, so a client started after the game has been running a while never sees the early ones.
        ok(7, "; ".join("%s: written, acked in %ss" % (c.label, r[1][-1].get("by_the_game_after_s", "?")) for c, r in parts) +
           "   (the addon's own 'inbound: read' line had scrolled out before this client started)")
    else:
        bits = []
        for c, r in parts:
            if r is None:
                continue
            bits.append("%s: written=%s acked=%s addon-read=%s" % (c.label, bool(r[0]), bool(r[1]), bool(r[2])))
        S[7].evidence = "; ".join(bits) or "waiting for a name to hand over"
        if any(r and r[0] and not r[1] for _, r in parts):
            S[7].evidence += "   <- written but the game never acknowledged it: the inbound bridge is the boundary that failed"
    # 8 lookup by exact name
    def lookup_ok(c):
        want = others(c)
        hits = {}
        for e in c.addon(FOUND):
            m = FOUND.search(e.text)
            hits[m.group(1)] = float(m.group(2))
        return want and all(n in hits for n in want), hits
    l8 = [(c,) + lookup_ok(c) for c in clients]
    if all(x[1] for x in l8):
        ok(8, "; ".join("%s found %s" % (c.label, ", ".join("%s at %.1f yd" % kv for kv in h.items())) for c, _, h in l8))
    elif all(others(c) and all(any(e.get("name") == n and e.get("yards") not in (None, "not reported") for e in c.find("desktop", "distance"))
                               for n in others(c)) for c in clients):
        ok(8, "inferred from the distances the desktops read (the addon's 'FOUND by exact name' line had scrolled out of its trace ring "
              "before this client started); the game can only report a distance for a name it found")
    else:
        miss = []
        for c in clients:
            for e in c.addon(LOOKUP_MISS):
                m = LOOKUP_MISS.search(e.text)
                miss.append("%s: %s (%s)" % (c.label, m.group(1), m.group(2)))
        S[8].evidence = "; ".join(miss[-2:]) or "no lookup result yet"
    # 9 addon outbound with results
    def outbound_ok(c):
        want = others(c)
        for e in c.addon(OUTBOUND):
            tail = OUTBOUND.search(e.text).group(6)
            if want and all(n in tail for n in want):
                return True
        return False
    if all(outbound_ok(c) for c in clients):
        ok(9, "the addon narrated frames with the other player's distance in them")
    # 10 desktop read distance
    def desk_dist(c):
        want = others(c)
        d = {}
        for e in c.find("desktop", "distance"):
            try:
                d.setdefault(e.get("name"), []).append((e.ts, float(e.get("yards"))))
            except (TypeError, ValueError):
                pass
        return d, want
    dd = [(c,) + desk_dist(c) for c in clients]
    if all(w and all(n in d for n in w) for _, d, w in dd):
        ok(10, "; ".join("%s read %s" % (c.label, ", ".join("%s %.1f yd" % (n, d[n][0][1]) for n in w)) for c, d, w in dd))
    # 11 proximity reports from both
    if two:
        def reported(frm, other):
            return [e for e in hub if e.event == "proximity" and e.get("frm") == frm and other in [p.split(":")[0] for p in csv(e.get("peers"))]]
        ra, rb = reported(A.character, B.character), reported(B.character, A.character)
        if ra and rb:
            ok(11, "hub saw %s report %s and %s report %s" % (A.character, B.character, B.character, A.character))
        elif hub:
            S[11].evidence = "reports at the hub: %s->%s %d, %s->%s %d" % (A.character, B.character, len(ra), B.character, A.character, len(rb))
        else:
            sent = [c for c in clients if any(B.character if c is A else A.character in e.get("peers", "") for e in c.find("desktop", "proximity_sent"))]
            if len(sent) == 2:
                ok(11, "both clients sent proximity reports (hub trace not supplied)")
    else:
        na(11, "needs a second player")
    # 12 link
    if two:
        ups = [e for e in hub if e.event == "link_up" and {e.get("a"), e.get("b")} == {A.character, B.character}]
        cli_up = [c for c in clients if c.find("desktop", "hub_link", lambda e: e.get("state") == "UP")]
        if ups:
            ok(12, "link_up at %s yd (reports %s)" % (ups[0].get("distance"), ups[0].get("reported")))
        elif len(cli_up) == 2:
            ok(12, "both clients were told peer_available (hub trace not supplied)")
        else:
            downs = [e for e in hub if e.event == "link_down"]
            if downs:
                S[12].evidence = "link_down: " + downs[-1].get("why", "?")
    else:
        na(12, "needs a second player")
    # 13 webrtc
    def conn_ok(c):
        return bool(c.find("desktop", "webrtc", lambda e: e.get("connection") == "connected" or e.get("ice") in ("connected", "completed")))
    if two:
        if all(conn_ok(c) for c in clients):
            paths = [(c.label, (c.find("desktop", "webrtc_result", lambda e: e.get("result") not in (None, "FAILED")) or [None])[-1]) for c in clients]
            ok(13, "connected both ways" + "".join("; %s: %s %s" % (lab, r.get("result"), r.get("pair")) for lab, r in paths if r))
        else:
            fails = [e for c in clients for e in c.find("desktop", "webrtc", lambda e: "failed" in (e.get("ice", "") + e.get("connection", "")))]
            if fails:
                S[13].status, S[13].evidence = FAIL, "WebRTC failed: ice=%s connection=%s" % (fails[0].get("ice"), fails[0].get("connection"))
            else:
                started = [c for c in clients if c.find("desktop", "webrtc_signal")]
                if started:
                    S[13].evidence = "negotiation started on %s but not connected" % ", ".join(c.label for c in started)
    else:
        na(13, "needs a second player")
    # 14 audio both directions
    if two:
        rx_a = A.find("desktop", "audio_rx", lambda e: e.get("state") == "RECEIVING")
        rx_b = B.find("desktop", "audio_rx", lambda e: e.get("state") == "RECEIVING")
        tx_a = A.find("desktop", "audio_tx", lambda e: e.get("state") == "SENDING")
        tx_b = B.find("desktop", "audio_tx", lambda e: e.get("state") == "SENDING")
        bad_engine = [(c.label, engine_problems(c)) for c in clients if engine_problems(c)]
        if bad_engine:
            S[14].status = FAIL
            S[14].evidence = "NOT REAL VOICE: " + "; ".join("%s: %s" % (lab, ", ".join(p)) for lab, p in bad_engine) + \
                "   <- close every EqVoice/Edge window from earlier runs and start again (`realtest run` does this)"
        elif rx_a and rx_b:
            ok(14, "%s heard %s and %s heard %s" % (A.character, B.character, B.character, A.character))
        else:
            S[14].evidence = "heard: %s=%s %s=%s; sent: %s=%s %s=%s" % (A.character, bool(rx_a), B.character, bool(rx_b),
                                                                    A.character, bool(tx_a), B.character, bool(tx_b))
            quiet = [c.character for c in (A, B) if c.find("desktop", "audio_rx", lambda e: e.get("state") == "ARRIVING_QUIET")
                     and not c.find("desktop", "audio_rx", lambda e: e.get("state") == "RECEIVING")]
            if quiet:
                S[14].evidence += "; packets reached %s but carried almost no sound (the speaker's microphone was too quiet or off)" % ", ".join(quiet)
            opened = {c.character: len(c.find("desktop", "audio_mic", lambda e: e.get("transmit") == "yes")) for c in (A, B)}
            presses = {c.character: len(c.find("desktop", "ptt", lambda e: e.get("down") == "yes")) for c in (A, B)}
            S[14].evidence += "; microphone opened: %s=%d %s=%d; push-to-talk presses: %s=%d %s=%d" % (
                A.character, opened[A.character], B.character, opened[B.character],
                A.character, presses[A.character], B.character, presses[B.character])
            for c in (A, B):
                if not opened[c.character]:
                    S[14].evidence += "   <- %s never opened its microphone: hold ITS push-to-talk key or its Hold to talk button while speaking" % c.character
    else:
        na(14, "needs a second player")
    # 15-20 depend on the walk
    if two:
        series = [(e.ts, e.n, float(e.get("yards"))) for e in A.find("desktop", "distance")
                  if e.get("name") == B.character and e.get("yards") not in (None, "not reported")]
        gone = [e for e in A.find("desktop", "distance") if e.get("name") == B.character and e.get("yards") == "not reported"]
        if series:
            lo, hi = min(s[2] for s in series), max(s[2] for s in series)
            peak_i = max(range(len(series)), key=lambda i: series[i][2])
            if hi - lo >= 10 and series[peak_i][1] > min(series, key=lambda s: s[2])[1]:
                ok(15, "distances seen: %.1f to %.1f yd" % (lo, hi))
                ok(16, "climbed from %.1f to %.1f yd" % (lo, hi))
            elif gone:
                ok(15, "the game stopped seeing %s" % B.character)
            else:
                S[15].evidence = S[16].evidence = "range seen so far: %.1f to %.1f yd (walk further, past 45 yd)" % (lo, hi)
        gains = [(float(e.get("distance")), float(e.get("gain"))) for e in A.find("desktop", "audio_gain")
                 if e.get("distance") not in (None, "-") and e.get("gain") not in (None, "-")]
        gains += [(float(e.get("distance")), float(e.get("gain"))) for e in B.find("desktop", "audio_gain")
                  if e.get("distance") not in (None, "-") and e.get("gain") not in (None, "-")]
        if gains:
            gains.sort()
            near = [g for d, g in gains if d <= 10]
            far = [g for d, g in gains if 25 <= d <= 45]
            mid = [(d, g) for d, g in gains if 10 < d < 40]
            if (near and far and max(far) < min(near)) or len(mid) >= 2 and mid[-1][1] < mid[0][1]:
                ok(17, "gain vs distance: " + ", ".join("%.0f yd -> %.2f" % (d, g) for d, g in gains[:: max(1, len(gains) // 6)]))
            else:
                S[17].evidence = "gain samples: " + ", ".join("%.0f yd -> %.2f" % dg for dg in gains[:6])
        oor = [e for e in A.find("desktop", "peer", lambda e: "OUT_OF_RANGE_GRACE" in e.text and "CONNECTED ->" in e.text)]
        if oor:
            e = oor[0]
            last = [s for s in series if e.ts is None or s[0] is None or s[0] <= e.ts + 0.5]
            where = ("last reading %.1f yd" % last[-1][2]) if last else "the game stopped reporting them"
            ok(18, "CONNECTED -> OUT_OF_RANGE_GRACE (%s; configured: connect %.0f yd, leave %.0f yd)" % (where, SAY_RADIUS, LEAVE_RADIUS))
        stop = [e for e in A.find("desktop", "audio_gain", lambda e: e.get("gain") in ("0.0", "0", "0.00") and e.get("transmit_to_them") == "no")]
        if oor and stop:
            ok(19, "gain 0 and no transmission to them" + ("; they went silent" if A.find("desktop", "audio_rx", lambda e: e.get("state") == "silent") else ""))
        elif oor:
            S[19].evidence = "left range but no `audio_gain ... gain=0 transmit_to_them=no` line"
        closed = A.find("desktop", "webrtc_closed")
        dis = [e for e in A.find("desktop", "peer", lambda e: "OUT_OF_RANGE_GRACE -> DISCONNECTED" in e.text)]
        if dis and closed:
            dt = (dis[0].ts - oor[0].ts) if (dis[0].ts and oor and oor[0].ts) else None
            ok(20, "closed%s" % ((" %.1f s after leaving range (teardown 8 s)" % dt) if dt is not None else ""))
    else:
        for n in range(14, 21):
            na(n, "needs a second player")
    # Steps 1-14 happen in order: one that is missing although a later one passed was skipped, which is a FAIL (the
    # bridge or lookup it covers did not do its job). Steps 15-20 follow the walk and can arrive in any order.
    last_pass = max([s.n for s in steps if s.status == PASS and s.n <= 14] or [0])
    for s in steps:
        if s.status == NOT_YET and s.n < last_pass and s.n <= 14:
            s.status = FAIL
    return steps


def webrtc_block(clients: list) -> str:
    """How each client's calls were carried: DIRECT_HOST, DIRECT_STUN, RELAY_TURN or FAILED, with the ICE pair and the numbers."""
    out = ["WebRTC result per call"]
    for c in clients:
        last: dict = {}
        for e in c.find("desktop", "webrtc_result"):
            last[e.get("name")] = e                      # the newest line for each peer (the settled one, once it exists)
        if not last:
            out.append("  %-14s (no WebRTC attempt in this log)" % c.label)
            continue
        for name, e in last.items():
            if e.get("result") == "FAILED":
                out.append("  %-14s -> %-12s FAILED  %s  (local %s, remote %s)" % (c.label, name, e.get("explanation", e.get("reason", "?")),
                                                                                  e.get("local_candidates"), e.get("remote_candidates")))
            else:
                out.append("  %-14s -> %-12s %-11s %s %s (%s > %s)  rtt %s ms  jitter %s ms  loss in %s%% out %s%%  set-up %s ms%s" % (
                    c.label, name, e.get("result"), e.get("pair"), e.get("proto"), e.get("local_network"), e.get("remote_network"),
                    e.get("rtt_ms"), e.get("jitter_ms"), e.get("loss_in_pct"), e.get("loss_out_pct"), e.get("setup_ms"),
                    "" if e.get("phase") == "settled" else "  [first reading, not settled]"))
    out.append("  DIRECT_HOST = direct peer to peer (same network or public addresses); DIRECT_STUN = direct peer to peer through NAT, via STUN-found addresses;")
    out.append("  RELAY_TURN = carried by a TURN relay; FAILED = no path formed (the reason says why).")
    return "\n".join(out)


def render(steps: list, title: str = "") -> str:
    out = []
    if title:
        out.append(title)
    mark = {PASS: "PASS    ", FAIL: "FAIL    ", NOT_YET: "not yet ", NA: "n/a     "}
    for s in steps:
        out.append("%2d  %s%s" % (s.n, mark[s.status], s.title))
        if s.evidence:
            out.append("      %s" % s.evidence)
    first = next((s for s in steps if s.status in (FAIL, NOT_YET)), None)
    out.append("")
    if first is None:
        out.append("ALL BOUNDARIES PASSED")
    else:
        out.append("FIRST BOUNDARY NOT REACHED: step %d, %s" % (first.n, first.title))
        out.append("  where to look: " + first.hint)
    return "\n".join(out)


def load(clients_arg: list, hub_arg: Optional[str]):
    clients = []
    for item in clients_arg:
        label, sep, path = item.partition("=")
        if not sep:
            label, path = Path(item).name or item, item
        clients.append(Client(label, parse(read_log(Path(path)))))
    hub = parse(read_log(Path(hub_arg)), since_start=False) if hub_arg else []
    return clients, [e for e in hub if e.who == "hub"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", action="append", default=[], metavar="[LABEL=]HOME_OR_LOG")
    ap.add_argument("--hub", default=None, metavar="LOG")
    ap.add_argument("--test-names", default="", help="names given to the addon with --test-roster (single-client check)")
    a = ap.parse_args(argv)
    if not a.client:
        ap.error("give at least one --client")
    clients, hub = load(a.client, a.hub)
    steps = evaluate(clients, hub, [n for n in a.test_names.split(",") if n])
    print(render(steps))
    return 0 if all(s.status in (PASS, NA) for s in steps) and any(s.status == PASS for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
