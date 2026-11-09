"""File bridge: the addon writes one line, 'OVB2 <hex>', through a mod's file API.

Looks in the places those APIs write: <game>\\CustomData\\eqvoice.txt (Nampower WriteCustomFile) and
<game>\\Imports\\eqvoice.txt (ExportFile). The inbound file (client -> addon) sits next to it.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional

from .claims import CLAIMS, Claims
from .frame import BridgeState, FrameError, decode_bytes
from .inbox import IN_NAME
from .source import BridgeSource

log = logging.getLogger("eqvoice.bridge.file")

FILE_NAME = "eqvoice.txt"


def file_name(character: str = "") -> str:
    """eqvoice.txt, or eqvoice-<Character>.txt when two game instances share one folder (the addon writes both)."""
    return "eqvoice-%s.txt" % character if character else FILE_NAME


def inbox_name(character: str = "") -> str:
    return "eqvoice-in-%s.txt" % character if character else IN_NAME


def valid_file_character(character: str) -> bool:
    """The addon only makes per-character files for 2-24 plain ASCII letters."""
    return 2 <= len(character) <= 24 and character.isascii() and character.isalpha()


def candidate_paths(wow_dir: str, file_path: str = "", character: str = "") -> list:
    if file_path:
        return [Path(file_path)]
    if not wow_dir:
        return []
    root = Path(wow_dir)
    name = file_name(character)
    return [root / "CustomData" / name, root / "Imports" / name]


def inbox_path(wow_dir: str, file_path: str = "", seen_path: Optional[str] = None) -> Optional[Path]:
    """Where to write the roster: beside the outbound file the addon is actually producing."""
    if seen_path:
        return Path(seen_path).with_name(IN_NAME)
    if file_path:
        return Path(file_path).with_name(IN_NAME)
    if wow_dir:
        return Path(wow_dir) / "CustomData" / IN_NAME
    return None


def split_file(text: str) -> tuple:
    """(first line, trace lines). The file is the frame line, then the addon's `OVT <seq> <wall> <text>` lines."""
    lines = text.replace("\r", "").split("\n")
    traces = []
    for ln in lines[1:]:
        if ln.startswith("OVT "):
            parts = ln.split(" ", 3)
            if len(parts) == 4 and parts[1].isdigit() and parts[2].isdigit():
                traces.append((int(parts[1]), int(parts[2]), parts[3]))
    return lines[0].strip(), traces


def parse_line(text: str) -> BridgeState:
    head, _ = split_file(text)
    if not head.startswith("OVB2 "):
        raise FrameError("magic")
    try:
        raw = bytes.fromhex(head[5:].strip())
    except ValueError:
        raise FrameError("hex") from None
    return decode_bytes(raw)


class FileSource(BridgeSource):
    kind = "file"

    def __init__(self, paths: list, poll_hz: float = 10.0, character: str = ""):
        super().__init__()
        self.character = character
        self.on_trace = None                 # callable(seq, wall, text, state) for each new addon trace line
        self._trace_login, self._trace_seq = None, 0
        self.paths = [Path(p) for p in paths]
        self.interval = 1.0 / max(poll_hz, 1.0)
        self.status = {"kind": "file", "paths": [str(p) for p in self.paths], "path": None, "ok": 0, "fail": 0,
                       "last_error": "", "searching": True}

    @property
    def inbox_path(self) -> Optional[Path]:       # type: ignore[override]
        """Beside the outbound file the addon is actually producing (CustomData or Imports folder)."""
        base = Path(self.status["path"]) if self.status.get("path") else (self.paths[0] if self.paths else None)
        return base.with_name(inbox_name(self.character)) if base else None

    def poll_once(self) -> Optional[BridgeState]:
        """Newest valid frame among the candidate files, or None."""
        best, best_m, best_traces = None, -1.0, []
        for p in self.paths:
            try:
                st = p.stat()
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue            # not there yet, or the game has it open for a moment
            self.status["searching"] = False
            try:
                head, traces = split_file(text)
                state = parse_line(head)
            except FrameError as e:
                self.status["fail"] += 1
                self.status["last_error"] = "%s: %s" % (p.name, e.code)
                continue
            if st.st_mtime >= best_m:
                best, best_m, best_traces = state, st.st_mtime, traces
                self.status["path"] = str(p)
                self.status["mtime"] = st.st_mtime
        if best is not None:
            self.status["ok"] += 1
            self._forward_trace(best, best_traces)
        return best

    def _forward_trace(self, state: BridgeState, traces: list) -> None:
        """Hand each addon trace line on once (the file repeats the newest 16 every time it is written)."""
        if self._trace_login != state.login_id:
            self._trace_login, self._trace_seq = state.login_id, 0
        for seq, wall, text in traces:
            if seq > self._trace_seq:
                self._trace_seq = seq
                if self.on_trace is not None:
                    try:
                        self.on_trace(seq, wall, text, state)
                    except Exception:                                        # noqa: BLE001
                        log.exception("trace callback failed")

    async def run(self, emit: Callable[[BridgeState], None]) -> None:
        loop = asyncio.get_running_loop()
        while True:
            state = await loop.run_in_executor(None, self.poll_once)
            if state is not None:
                emit(state)
            await asyncio.sleep(self.interval)


_CHAR_FILE = re.compile(r"eqvoice-([A-Za-z]{2,24})\.txt")


def is_bridge_file(path: Path) -> bool:
    """The addon's own file (it starts with an OVB2 frame), not one of the app's (eqvoice-in.txt, -st, -ctl have names that look the same)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"OVB2 "
    except OSError:
        return False


class ClaimingFileSource(FileSource):
    """For a copy of EqVoice started by double-click, which does not know which game window it is for.

    Two game windows on one PC write one file each (eqvoice-<Character>.txt, beside the shared eqvoice.txt that whichever wrote last owns).
    This source looks at the fresh ones, takes the first character no other EqVoice has (``claims``), and from then on reads only that
    character's file, so each copy keeps to its own game. When the game stops writing for a while the claim is given back, so a different
    character can be taken. A character whose name has no per-character file (not plain ASCII letters) falls back to the shared file, once nobody
    else is using it."""

    FRESH_S = 20.0               # a file written this recently belongs to a game that is running
    RELEASE_S = 120.0            # a claimed character whose game has been silent this long is let go
    PLAIN_AFTER_S = 4.0          # no per-character file but a fresh shared one for this long: use the shared one

    def __init__(self, wow_dir: str, poll_hz: float = 10.0, claims: Optional[Claims] = None):
        super().__init__([], poll_hz, "")
        self.wow_dir = wow_dir
        self.claims = claims or CLAIMS
        self.claimed = ""                      # the character this copy serves ("" = none yet; "*" = the shared file)
        self._plain_since: Optional[float] = None
        self._pinned_at = 0.0
        self.status.update({"claimed": "", "taken_by_others": [], "fresh": []})

    def _roots(self) -> list:
        return [Path(self.wow_dir) / "CustomData", Path(self.wow_dir) / "Imports"]

    def fresh_characters(self, now: Optional[float] = None) -> list:
        """Names of the characters whose game is writing a per-character file right now (sorted)."""
        now = time.time() if now is None else now
        out = set()
        for root in self._roots():
            try:
                files = list(root.glob("eqvoice-*.txt"))
            except OSError:
                continue
            for f in files:
                m = _CHAR_FILE.fullmatch(f.name)
                try:
                    if m and now - f.stat().st_mtime <= self.FRESH_S and is_bridge_file(f):
                        out.add(m.group(1))
                except OSError:
                    continue
        return sorted(out)

    def _plain_fresh(self, now: float) -> bool:
        for root in self._roots():
            try:
                p = root / FILE_NAME
                if now - p.stat().st_mtime <= self.FRESH_S and is_bridge_file(p):
                    return True
            except OSError:
                continue
        return False

    def _pin(self, name: str) -> None:
        self.claimed = name
        self._pinned_at = time.time()
        self.character = "" if name == "*" else name
        self.paths = candidate_paths(self.wow_dir, "", self.character)
        self.status.update({"claimed": name, "paths": [str(p) for p in self.paths], "searching": True})
        log.info("this EqVoice serves %s", "the shared game file" if name == "*" else name)

    def _let_go(self) -> None:
        log.info("the game for %s has been silent for %.0f s: letting the character go", self.claimed, self.RELEASE_S)
        self.claims.release(self.claimed if self.claimed != "*" else "*plain*")
        self.claimed, self.character, self.paths = "", "", []
        self._plain_since = None
        self.status.update({"claimed": "", "path": None, "paths": [], "searching": True})

    def poll_once(self) -> Optional[BridgeState]:
        now = time.time()
        if not self.claimed:
            names = self.fresh_characters(now)
            mine = next((n for n in names if self.claims.try_claim(n)), None)
            self.status["fresh"] = names
            self.status["taken_by_others"] = [n for n in names if n != mine and n.lower() not in self.claims.held]
            if mine:
                self._pin(mine)
            elif not names and self._plain_fresh(now):
                self._plain_since = self._plain_since or now
                if now - self._plain_since >= self.PLAIN_AFTER_S and self.claims.try_claim("*plain*"):
                    self._pin("*")
            else:
                self._plain_since = None
            if not self.claimed:
                return None
        state = super().poll_once()
        newest = None
        for p in self.paths:
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            newest = m if newest is None else max(newest, m)
        if (newest is not None and now - newest > self.RELEASE_S) or (newest is None and now - self._pinned_at > self.RELEASE_S):
            self._let_go()                                       # the game went quiet (or its file never came / was deleted) for this long
        return state
