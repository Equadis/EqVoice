"""The in-game panel's two small files, both in the game's CustomData folder next to the roster file.

    eqvoice-st[-<Character>].txt    desktop -> addon: what the panel shows (status)
    eqvoice-ctl[-<Character>].txt   addon -> desktop: what the player clicked (commands)

Status (written by the desktop client, read by the addon about twice a second):

    OVS1 <seq> <unix-ts> <crc> <character>
    S level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=2 conn=1 key=F9 game=1 hub=1 mic=ok app=0.2.0 boost=normal gate=auto gdb=-48 ...
    M Ready: Alice on Realm. 2 players in range, 1 connected.      headline, always one line
    N Second problem, third problem ...                             more lines (optional, at most 4)
    P <name> <yards> <connected 0|1> <speaking 0|1> <direct|relay|-> [<state> [<muted 0|1>]]    one per player, nearest first
    X <name> <name> ...                                             the mute list (optional, several lines when long)
    D <in|out> <n> <name of the device>                              the microphones and speakers Windows has (n = 1..8; the game's pickers add "system default" as 0)

``state`` says what the player's own EqVoice reports, for the little icon beside their name: ``l`` a raised voice, ``s`` speaking,
``o`` connected and quiet, ``m`` muted (or push to talk and the key not held), ``d`` deafened (which also means muted), ``-`` not known
(not connected yet). ``muted`` is 1 when YOU have muted them. A file from an older app has neither field: the addon takes both as unknown.

``crc`` is CRC-16/CCITT-FALSE of everything after the first newline, as in the roster file. The file is rewritten at least
every ``refresh_s`` even when nothing changed: the addon treats a sequence number that stops moving as "the desktop app is not
running" and says so in the panel.

Commands (written by the addon through a mod's file API, which is not atomic, so a torn read fails the CRC and is retried):

    OVC1 <seq> <unix-ts> <crc> <character>
    <id> <command> <argument>          up to 8 lines: the newest commands, oldest first

``id`` grows with every command (and across game restarts, because it starts from the wall clock), so the desktop applies each
exactly once and a file left over from an earlier session is ignored: ids older than the moment the app started are not replayed.
Commands: ``mode open|ptt``, ``mute 0|1``, ``deafen 0|1``, ``volume 0..100``, ``test 1`` (play the speaker test), ``input 0..200`` (the microphone's volume, %),
``calibrate 1|0`` (start or stop the microphone setup),
``boost off|low|normal|high``, ``key <the game's name for a key>`` (the "change key" button), ``gate off|auto|manual``, ``gatedb <dB>``,
``pmute <player> 0|1`` (the mute list), ``micdev <name>`` and ``spkdev <name>`` (pick the microphone or speakers by the name the status
file lists, or ``default``), ``watch 1`` (show me the microphone's level for the next few seconds: it is sent as ``lvl`` and the file is rewritten faster; with it ``gl`` (dB, the signal the noise gate judges), ``gt`` (dB, the bar in force) and ``go`` (1: the gate is open) are sent too).

parse_status and build_control exist for the tests and the cross-check with the addon's Lua (eqvoice-addon/EqVoice/Status.lua
and Control.lua are the real other halves).
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .frame import crc16
from .inbox import body_crc, replace_file

log = logging.getLogger("eqvoice.bridge.uifiles")

STATUS_MAGIC = "OVS1"
CONTROL_MAGIC = "OVC1"
MAX_PEERS = 40
MAX_MUTED = 60
STATES = "solmd-"
MAX_MORE = 4
MAX_COMMANDS = 8
TEXT_MAX = 160
_VALUE_BAD = re.compile(r"[\x00-\x20\x7f=|]")


class UiFileError(ValueError):
    pass


def file_suffix(inbox_name: str) -> str:
    """'.txt' for eqvoice-in.txt, '-Alice.txt' for eqvoice-in-Alice.txt: the two new files copy the roster file's naming."""
    base = "eqvoice-in"
    return inbox_name[len(base):] if inbox_name.startswith(base) else ".txt"


def status_path(inbox_path) -> Path:
    p = Path(inbox_path)
    return p.with_name("eqvoice-st" + file_suffix(p.name))


def control_path(inbox_path) -> Path:
    p = Path(inbox_path)
    return p.with_name("eqvoice-ctl" + file_suffix(p.name))


def _word(v) -> str:
    """One field value: no spaces, '=' or '|' (the game treats '|' as an escape in text it draws)."""
    s = _VALUE_BAD.sub("_", str(v))
    return s or "-"


def _line(text) -> str:
    """Free text for a single line, safe to draw in the game."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).replace("|", "/").strip()
    return s[:TEXT_MAX]


def _yards(v) -> str:
    return "-" if v is None else ("%.1f" % float(v))


def _state(v) -> str:
    return v if isinstance(v, str) and len(v) == 1 and v in STATES else "-"


MAX_DEVICES = 8
MAX_DEVICE_NAME = 60


def _device_name(v) -> str:
    """A device's name as the game can show it: one line, no control characters, no `|` (the game's chat escape), cut to a length that fits."""
    s = " ".join(str(v or "").replace("|", "/").split())
    return (s[:MAX_DEVICE_NAME - 1] + "…") if len(s) > MAX_DEVICE_NAME else s


def build_status(seq: int, ts: int, character: str, fields: dict, headline: str, more=(), peers=(), muted=(), devices=None) -> str:
    lines = ["S " + " ".join("%s=%s" % (_word(k), _word(v)) for k, v in fields.items()), "M " + (_line(headline) or "-")]
    for text in list(more)[:MAX_MORE]:
        lines.append("N " + (_line(text) or "-"))
    for p in list(peers)[:MAX_PEERS]:
        lines.append("P %s %s %d %d %s %s %d" % (_word(p.get("name", "?")), _yards(p.get("dist")), 1 if p.get("connected") else 0,
                                                  1 if p.get("speaking") else 0, _word(p.get("path") or "-"),
                                                  _state(p.get("state")), 1 if p.get("muted") else 0))
    names = [_word(n) for n in list(muted)[:MAX_MUTED]]
    for i in range(0, len(names), 8):                     # eight names to a line keeps every line short
        lines.append("X " + " ".join(names[i:i + 8]))
    for kind in ("in", "out"):
        for i, name in enumerate(list((devices or {}).get(kind) or [])[:MAX_DEVICES], 1):
            lines.append("D %s %d %s" % (kind, i, _device_name(name) or "?"))
    body = "".join(line + "\n" for line in lines)
    return "%s %d %d %04X %s\n%s" % (STATUS_MAGIC, seq & 0xFFFF, ts, body_crc(body), character, body)


@dataclass
class ParsedStatus:
    seq: int
    ts: int
    character: str
    fields: dict = field(default_factory=dict)
    headline: str = ""
    more: list = field(default_factory=list)
    peers: list = field(default_factory=list)
    muted: list = field(default_factory=list)
    devices: dict = field(default_factory=lambda: {"in": [], "out": []})


def parse_status(text: str) -> ParsedStatus:
    if not isinstance(text, str):
        raise UiFileError("type")
    nl = text.find("\n")
    if nl < 0:
        raise UiFileError("noheader")
    head, body = text[:nl].replace("\r", ""), text[nl + 1:].replace("\r", "")
    parts = head.split(" ")
    if len(parts) < 5 or parts[0] != STATUS_MAGIC:
        raise UiFileError("magic")
    try:
        seq, ts = int(float(parts[1])), int(float(parts[2]))
    except ValueError:
        raise UiFileError("numbers") from None
    if parts[3].upper() != "%04X" % crc16(body.encode("utf-8")):
        raise UiFileError("crc")
    out = ParsedStatus(seq, ts, parts[4])
    for line in body.split("\n"):
        kind, rest = line[:1], line[2:]
        if kind == "S":
            for item in rest.split(" "):
                k, eq, v = item.partition("=")
                if eq:
                    out.fields[k] = v
        elif kind == "M":
            out.headline = rest
        elif kind == "N":
            out.more.append(rest)
        elif kind == "P":
            f = rest.split(" ")
            if len(f) >= 5:
                out.peers.append({"name": f[0], "dist": None if f[1] == "-" else float(f[1]), "connected": f[2] == "1",
                                  "speaking": f[3] == "1", "path": f[4],
                                  "state": _state(f[5]) if len(f) > 5 else "-", "muted": len(f) > 6 and f[6] == "1"})
        elif kind == "X":
            out.muted.extend(n for n in rest.split(" ") if n)
        elif kind == "D":
            k, _, tail = rest.partition(" ")
            n, _, name = tail.partition(" ")
            if k in ("in", "out") and n.isdigit() and name:
                out.devices[k].append(name)
    return out


def build_control(seq: int, wall: int, character: str, commands) -> str:
    """The addon's file, as the addon writes it (tests only). ``commands`` is [(id, command, argument), ...]."""
    body = "".join("%d %s %s\n" % (cid, cmd, arg) for cid, cmd, arg in list(commands)[-MAX_COMMANDS:])
    return "%s %d %d %04X %s\n%s" % (CONTROL_MAGIC, seq & 0xFFFFFFFF, wall, body_crc(body), character, body)


@dataclass
class ParsedControl:
    seq: int
    wall: int
    character: str
    commands: list = field(default_factory=list)       # [(id, command, argument)]


def parse_control(text: str) -> ParsedControl:
    if not isinstance(text, str):
        raise UiFileError("type")
    nl = text.find("\n")
    if nl < 0:
        raise UiFileError("noheader")
    head, body = text[:nl].replace("\r", ""), text[nl + 1:].replace("\r", "")
    parts = head.split(" ")
    if len(parts) < 5 or parts[0] != CONTROL_MAGIC:
        raise UiFileError("magic")
    try:
        seq, wall = int(float(parts[1])), int(float(parts[2]))
    except ValueError:
        raise UiFileError("numbers") from None
    if parts[3].upper() != "%04X" % crc16(body.encode("utf-8")):
        raise UiFileError("crc")
    out = ParsedControl(seq, wall, parts[4])
    for line in body.split("\n"):
        f = line.split(" ")
        if len(f) >= 3 and f[0].isdigit():
            out.commands.append((int(f[0]), f[1], " ".join(f[2:])))
    return out


class StatusWriter:
    """Writes the status file. Cheap to call often: it touches the disk only when something changed (at most every
    ``min_interval_s``) or ``refresh_s`` has passed."""

    def __init__(self, path, refresh_s: float = 2.0, min_interval_s: float = 0.4, clock=time.time,
                 rng: Optional[random.Random] = None):
        self.path = Path(path)
        self.refresh_s, self.min_interval_s, self.clock = refresh_s, min_interval_s, clock
        self.seq = (rng or random.Random()).randrange(1, 60000)
        self.last_key = None
        self.last_write = -1e9
        self.writes = 0
        self.errors = 0
        self.last_error = ""

    def write(self, character: str, fields: dict, headline: str, more=(), peers=(), muted=(), force: bool = False, devices=None) -> bool:
        key = (character, tuple(fields.items()), headline, tuple(more), tuple(tuple(sorted(p.items())) for p in peers), tuple(muted),
               tuple(tuple(v) for v in ((devices or {}).get("in") or (), (devices or {}).get("out") or ())))
        now = self.clock()
        since = now - self.last_write
        changed = key != self.last_key
        if not force and not ((changed and since >= self.min_interval_s) or since >= self.refresh_s):
            return False
        self.seq = self.seq % 65535 + 1
        text = build_status(self.seq, int(now), character, fields, headline, more, peers, muted, devices)
        try:
            replace_file(self.path, text)
        except OSError as e:
            self.errors += 1
            self.last_error = str(e)
            log.warning("could not write %s: %s", self.path.name, e)
            return False
        self.last_key, self.last_write = key, now
        self.writes += 1
        return True

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
        self.last_key = None


class ControlReader:
    """Reads the addon's command file and hands each command out once. ``since`` is the wall-clock time the app started:
    commands whose id (wall clock x 100 + a counter) is older than that were issued in an earlier session and are skipped."""

    def __init__(self, path, since: Optional[float] = None, clock=time.time):
        self.path = Path(path)
        self.since = (clock() if since is None else since) - 2.0
        self.last_id = 0
        self._sig = None
        self.errors = 0
        self.last_error = ""
        self.applied = 0

    def poll(self) -> list:
        """New commands, oldest first: [(id, command, argument)]."""
        try:
            st = self.path.stat()
        except OSError:
            self._sig = None
            return []
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._sig:
            return []
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_control(text)
        except (OSError, UiFileError) as e:
            self.errors += 1
            self.last_error = str(e)
            return []                       # torn or mid-write: do not remember the signature, look again next time
        self._sig = sig
        out = []
        for cid, cmd, arg in parsed.commands:
            if cid > self.last_id and cid / 100.0 >= self.since:
                out.append((cid, cmd, arg))
            self.last_id = max(self.last_id, cid)
        self.applied += len(out)
        return out
