"""Simulation: a pretend game, so the whole pipeline runs without World of Warcraft.

    SimWorld   who is "online in the game" and how far apart they are, as each observer's game sees it
    SimGame    one pretend game client: reads the REAL inbound roster file, looks the names up in the
               SimWorld, writes the REAL outbound BF2 file. The client under test cannot tell the difference.

So the same code that will talk to the addon is exercised end to end: file formats, CRCs, identity binding,
roster freshness, staleness, logout. Distances are set per observer, which is what makes the interesting
cases testable: Alice sees Bob at 18 yd while Bob sees Alice at 19; change one side to 60 and the link must
go.

A SimWorld can live in one process (tests, tools/run_sim.py) or in a small JSON file that several client
processes watch (``--sim-world``), so two real client processes can be walked around by editing the file or
with the UI's simulator panel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .filesource import FILE_NAME, FileSource
from .frame import (FLAG_ENABLED, FLAG_FILE, FLAG_ID_MISMATCH, FLAG_LOGOUT, FLAG_LOOKUPS, FLAG_ROSTER, FLAG_TRUNCATED,
                    R_PLAYER, BridgeResult, BridgeState, encode_state, make_state)
from .inbox import IN_NAME, InboxError, parse_text
from .source import BridgeSource

log = logging.getLogger("eqvoice.bridge.sim")

ROSTER_TTL = 15.0          # as in the addon: no new roster file for this long = the client is gone
RESULT_TTL = 1.3           # as in the addon: a lookup result is reported for this long
LOAD_DISTANCE = 86.0       # measured in game: by-name lookup worked at least this far


class SimWorld:
    def __init__(self, path: Optional[os.PathLike] = None, load_distance: float = LOAD_DISTANCE):
        self.path = Path(path) if path else None
        self.load_distance = load_distance
        self.chars: dict = {}          # name -> [x, y]
        self.views: dict = {}          # (observer, target) -> yards the observer's game reports
        self._stamp = None
        self.refresh()                 # join the world that is already there

    # --------------------------------------------------------------- editing

    def add(self, name: str, x: float = 0.0, y: float = 0.0) -> "SimWorld":
        self.refresh()
        self.chars[name] = [float(x), float(y)]
        return self.save()

    def remove(self, name: str) -> "SimWorld":
        self.refresh()
        self.chars.pop(name, None)
        for k in [k for k in self.views if name in k]:
            del self.views[k]
        return self.save()

    def move(self, name: str, x: float, y: float) -> "SimWorld":
        self.refresh()
        self.chars[name] = [float(x), float(y)]
        return self.save()

    def see(self, observer: str, target: str, yards: Optional[float]) -> "SimWorld":
        """Force what ``observer``'s game reports for ``target`` (None removes the override)."""
        self.refresh()
        if yards is None:
            self.views.pop((observer, target), None)
        else:
            self.views[(observer, target)] = float(yards)
        return self.save()

    def pair(self, a: str, b: str, a_sees_b: float, b_sees_a: float) -> "SimWorld":
        self.refresh()
        self.views[(a, b)], self.views[(b, a)] = float(a_sees_b), float(b_sees_a)
        return self.save()

    # --------------------------------------------------------------- queries

    def distance(self, observer: str, target: str) -> Optional[float]:
        """What ``observer``'s game answers for a by-name lookup of ``target``; None = not loaded / not there."""
        self.refresh()
        if observer not in self.chars or target not in self.chars or observer == target:
            return None
        d = self.views.get((observer, target))
        if d is None:
            (x1, y1), (x2, y2) = self.chars[observer], self.chars[target]
            d = math.hypot(x1 - x2, y1 - y2)
        return d if d <= self.load_distance else None

    # ------------------------------------------------------------------ file

    def to_json(self) -> dict:
        return {"chars": {n: {"x": p[0], "y": p[1]} for n, p in self.chars.items()},
                "views": {"%s>%s" % k: v for k, v in self.views.items()}}

    def save(self) -> "SimWorld":
        if self.path is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._stamp = self._file_stamp()
        return self

    def _file_stamp(self):
        s = self.path.stat()
        return (s.st_mtime_ns, s.st_size)

    def refresh(self) -> None:
        if self.path is None:
            return
        try:
            stamp = self._file_stamp()
            if stamp == self._stamp:
                return
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._stamp = stamp
        self.chars = {n: [float(p.get("x", 0)), float(p.get("y", 0))] for n, p in (d.get("chars") or {}).items()}
        self.views = {}
        for k, v in (d.get("views") or {}).items():
            obs, _, tgt = k.partition(">")
            if obs and tgt:
                self.views[(obs, tgt)] = float(v)


class SimGame:
    """One pretend WoW client. ``step(now)`` does what one tick of the addon does.

    ``char_files`` makes it behave like the addon does when its character name is plain letters: it writes
    eqvoice.txt AND eqvoice-<Character>.txt, and reads eqvoice-in-<Character>.txt before eqvoice-in.txt.
    It narrates itself through the same ``OVT`` trace lines as the addon, in the same words.
    """

    def __init__(self, world: SimWorld, character: str, realm: str, folder: os.PathLike, login_id: int = 0x5151,
                 interval: float = 0.5, checks: int = 80, report: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 char_files: bool = False):
        self.world, self.character, self.realm = world, character, realm
        self.folder = Path(folder)
        self.char_files = char_files
        data = self.folder / "CustomData"
        self.legacy_out = data / FILE_NAME
        self.legacy_in = data / IN_NAME
        # what a client that is configured for this game reads and writes
        self.out_path = data / ("eqvoice-%s.txt" % character) if char_files else self.legacy_out
        self.in_path = data / ("eqvoice-in-%s.txt" % character) if char_files else self.legacy_in
        self.login_id = login_id
        self.interval, self.checks, self.report = interval, checks, report
        self.clock, self.wall = clock, wall
        # switches a test can flip
        self.enabled = True
        self.lookups = True              # False = no ClassicAPI
        self.frozen = False              # game minimised / hung: no frames at all
        self.loaded_in = True            # False = logged out of the world (loading screen)
        self.logging_out = False
        # state
        self.counter = 0
        self.roster: list = []
        self.cursor = 0
        self.seq: Optional[int] = None
        self.seq_changed_at = -1e9
        self.config: dict = {}
        self.id_mismatch = False
        self.known: dict = {}            # name -> (yards, time)
        self.checked = self.loaded = self.misses = 0
        self.frames_written = 0
        self.inbox_bad = 0
        self.last_text = ""
        self.inbox_seen = False
        self.source = ""
        self._said_absent = False
        self._lookup_state: dict = {}    # name -> ("loaded"|"missing", traced yards, traced at)
        self._trace_seq = 0
        self._trace_ring: list = []
        self._last_summary = None
        self._last_out_trace = -1e9
        self._started = False

    # ----------------------------------------------------------------- trace

    def trace(self, text: str) -> None:
        self._trace_seq += 1
        self._trace_ring.append((self._trace_seq, int(self.wall()), text))
        del self._trace_ring[:-16]

    def _trace_text(self) -> str:
        return "".join("OVT %d %d %s\n" % e for e in self._trace_ring)

    # --------------------------------------------------------------- inbound

    def _read_inbox(self):
        names = [self.in_path] + ([self.legacy_in] if self.char_files else [])
        for p in names:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text:
                return text, "CustomData\\" + p.name
        return None, ""

    def _poll_inbox(self, now: float) -> None:
        text, where = self._read_inbox()
        if text is None:
            if not self._said_absent:
                self._said_absent = True
                who = ("eqvoice-in-%s.txt or " % self.character) if self.char_files else ""
                self.trace("inbound: no roster file yet (looking in CustomData for %seqvoice-in.txt)" % who)
            return
        self._said_absent = False
        self.inbox_seen = True
        self.source = where
        if text == self.last_text:
            return
        self.last_text = text
        try:
            got = parse_text(text)
        except InboxError as e:
            self.inbox_bad += 1
            self.trace("inbound: %s is not a valid roster file (%s), %d bytes, starts: %s" % (where, e, len(text), text[:40].replace("\n", " ")))
            return
        was_mismatch = self.id_mismatch
        self.id_mismatch = got.character != self.character
        if self.id_mismatch:
            if not was_mismatch:
                self.trace("inbound: IGNORED, written for %s (this game is %s)" % (got.character, self.character))
            return                                  # written for another character: ignored, flagged
        if got.seq != self.seq:
            self.seq, self.seq_changed_at = got.seq, now
        if got.names != self.roster or not self._started_roster:
            shown = ",".join(got.names[:6]) + (",... (%d in all)" % len(got.names) if len(got.names) > 6 else "")
            self.trace("inbound: read %s seq %d for %s: roster is now %d name(s): %s" % (where, got.seq, got.character, len(got.names), shown))
        self._started_roster = True
        self.roster = got.names
        self.config = got.config
        if "checks" in got.config:
            self.checks = int(got.config["checks"])
        if "interval" in got.config:
            self.interval = float(got.config["interval"])
        if "report" in got.config:
            self.report = float(got.config["report"])

    _started_roster = False

    def roster_fresh(self, now: float) -> bool:
        return self.seq is not None and not self.id_mismatch and now - self.seq_changed_at <= ROSTER_TTL

    # ------------------------------------------------------------------ scan

    def _note_lookup(self, name: str, d: Optional[float], now: float) -> None:
        state, shown, at = self._lookup_state.get(name, (None, 0.0, -1e9))
        if d is not None:
            if state != "loaded":
                self.trace("lookup %s: FOUND by exact name, %.1f yd" % (name, d))
                self._lookup_state[name] = ("loaded", d, now)
            elif abs(d - shown) >= 3 and now - at >= 2:
                self.trace("lookup %s: %.1f yd" % (name, d))
                self._lookup_state[name] = ("loaded", d, now)
        elif state != "missing":
            if state == "loaded" or len(self.roster) <= 10:
                self.trace("lookup %s: not available (no such player loaded near you)" % name)
            self._lookup_state[name] = ("missing", 0.0, now)

    def _scan(self, now: float) -> None:
        n = len(self.roster)
        self.checked = self.loaded = self.misses = 0
        if n == 0:
            return
        budget = min(n, self.checks)
        members = set(self.roster)
        recent = [nm for nm, (_, t) in self.known.items() if nm in members and now - t <= RESULT_TTL * 2]
        order = list(dict.fromkeys(recent + [self.roster[(self.cursor + i) % n] for i in range(budget)]))[: max(budget, len(recent))]
        self.cursor = (self.cursor + budget) % n
        for name in order:
            self.checked += 1
            d = self.world.distance(self.character, name)
            if d is None:
                self.misses += 1
                self.known.pop(name, None)
            else:
                self.loaded += 1
                self.known[name] = (d, now)
            self._note_lookup(name, d, now)

    def results(self, now: float) -> list:
        out = []
        roster = set(self.roster)
        for name, (d, t) in list(self.known.items()):
            if name not in roster or now - t > RESULT_TTL:
                continue
            if d <= self.report:
                out.append(BridgeResult(name, d, R_PLAYER))
        out.sort(key=lambda r: (r.dist, r.name))
        return out

    # --------------------------------------------------------------- outbound

    def state(self, now: float) -> BridgeState:
        fresh = self.roster_fresh(now) and self.enabled and self.lookups
        flags = FLAG_FILE
        if self.enabled:
            flags |= FLAG_ENABLED
        if self.roster_fresh(now):
            flags |= FLAG_ROSTER
        if self.lookups:
            flags |= FLAG_LOOKUPS
        if self.id_mismatch:
            flags |= FLAG_ID_MISMATCH
        if self.logging_out:
            flags |= FLAG_LOGOUT
        results = self.results(now) if fresh else []
        if len(results) > 200:
            results, flags = results[:200], flags | FLAG_TRUNCATED
        return make_state(counter=self.counter & 0xFFFF, flags=flags, login_id=self.login_id, wall=int(self.wall()) & 0xFFFFFFFF,
                          ack=(self.seq or 0) & 0xFFFF, roster_size=len(self.roster), checked=self.checked,
                          loaded=self.loaded, misses=self.misses, interval_ms=int(self.interval * 1000), cost=12,
                          character=self.character if self.loaded_in else "", realm=self.realm if self.loaded_in else "",
                          results=results)

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for attempt in range(20):                  # Windows refuses to replace a file the client has open at that instant: try again
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01)

    def write_state(self, state: BridgeState) -> None:
        raw = encode_state(state)
        parts = state.results[:4]
        summary = ", ".join("%s %.1f yd" % (r.name, r.dist) for r in parts)
        now = self.clock()
        if summary != self._last_summary or now - self._last_out_trace >= 10:
            self._last_summary, self._last_out_trace = summary, now
            self.trace("outbound: frame #%d written (%d bytes, file WriteCustomFile, optical off), %d result(s)%s" % (
                state.counter, len(raw), len(state.results), (": " + summary) if summary else ""))
        text = "OVB2 " + raw.hex().upper() + "\n" + self._trace_text()
        self._write(self.legacy_out, text)
        if self.char_files:
            self._write(self.out_path, text)
        self.frames_written += 1

    def step(self, now: Optional[float] = None) -> Optional[BridgeState]:
        """One addon tick: read the roster file, look names up, publish. None while 'frozen'."""
        now = self.clock() if now is None else now
        if self.frozen:
            return None
        if not self._started:
            self._started = True
            names = ("eqvoice-%s.txt / eqvoice-in-%s.txt" % (self.character, self.character)) if self.char_files else "eqvoice.txt / eqvoice-in.txt"
            self.trace("identity: %s on %s, login id %d, file names %s" % (self.character, self.realm, self.login_id, names))
        self._poll_inbox(now)
        if self.enabled and self.lookups and self.roster_fresh(now):
            self._scan(now)
        else:
            self.known.clear()
            self.checked = self.loaded = self.misses = 0
        self.counter += 1
        st = self.state(now)
        self.write_state(st)
        return st

    def logout(self) -> BridgeState:
        """PLAYER_LOGOUT: one last frame that says so."""
        self.logging_out = True
        self.counter += 1
        self.trace("logout: sending the final frame so the desktop client leaves the roster")
        st = self.state(self.clock())
        self.write_state(st)
        return st

    def login(self, character: str, realm: Optional[str] = None, login_id: Optional[int] = None) -> None:
        """A different character (or the same one again) enters the world: new login id, fresh state."""
        self.character = character
        self.realm = realm or self.realm
        self.login_id = login_id if login_id is not None else (self.login_id * 1103515245 + 12345) & 0xFFFFFFFF
        self.logging_out = False
        self.roster, self.known, self.seq, self.id_mismatch = [], {}, None, False
        self.last_text = ""
        self._lookup_state.clear()
        self._trace_seq, self._trace_ring, self._started = 0, [], False
        if self.char_files:
            data = self.folder / "CustomData"
            self.out_path = data / ("eqvoice-%s.txt" % character)
            self.in_path = data / ("eqvoice-in-%s.txt" % character)

    async def run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:                                  # noqa: BLE001
                log.exception("simulated game step failed")
            await asyncio.sleep(self.interval)


class SimSource(BridgeSource):
    """A SimGame plus the ordinary file source reading what it writes."""

    kind = "sim"

    def __init__(self, game: SimGame, poll_hz: float = 10.0, character: str = ""):
        super().__init__()
        self.game = game
        self.file = FileSource([game.out_path], poll_hz, character)

    @property
    def inbox_path(self) -> Path:                 # type: ignore[override]
        return self.game.in_path

    @property
    def status(self) -> dict:                     # type: ignore[override]
        return {"kind": "sim", "character": self.game.character, "realm": self.game.realm, "frozen": self.game.frozen,
                "enabled": self.game.enabled, "lookups": self.game.lookups, "frames": self.game.frames_written,
                "file": self.file.status}

    @status.setter
    def status(self, _v) -> None:
        pass

    async def run(self, emit: Callable[[BridgeState], None]) -> None:
        game_task = asyncio.create_task(self.game.run(), name="sim-game")
        try:
            await self.file.run(emit)
        finally:
            game_task.cancel()
