"""The desktop -> addon direction of the file bridge: a small text file the addon polls.

    OVI1 <seq> <unix-ts> <crc> <character>
    C radius=40 interval=0.5 report=60 checks=80       configuration (optional)
    R Alice,Bob,Carol                                  roster: names for the addon to look up (several lines allowed)
    T free text                                        debug message, logged by the addon

``crc`` is CRC-16/CCITT-FALSE of everything after the first newline, four upper-case hex digits. The writer
replaces the file atomically (temp file, then rename), so a reader sees the old file or the new one; the CRC is
the belt to that pair of braces, because the game reads through a mod API whose behaviour while we write is
not documented. ``character`` is the character this client believes the game is running: the addon ignores the
file unless it matches the character actually logged in.

The sequence number must keep changing: the addon treats an unchanged one for 15 s as "the client is gone" and
drops the roster (fail closed). The writer therefore rewrites the file every few seconds even when the roster is
the same.

parse_text is a port of the addon's Lua parser (eqvoice-addon/EqVoice/Inbox.lua), used by the simulator and
cross-checked against the real Lua in tests/test_inbox.py.
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

log = logging.getLogger("eqvoice.bridge.inbox")

MAGIC = "OVI1"
MAX_NAMES = 2000
NAME_MIN, NAME_MAX = 2, 24
IN_NAME = "eqvoice-in.txt"
_BAD_NAME = re.compile(r"[\x00-\x20\x7f]")


class InboxError(ValueError):
    pass


def body_crc(body: str) -> int:
    return crc16(body.replace("\r", "").encode("utf-8"))


def _fmt(v) -> str:
    return str(int(v)) if float(v).is_integer() else ("%g" % v)


def build_text(seq: int, ts: int, character: str, names, config: Optional[dict] = None, test: Optional[str] = None) -> str:
    lines = []
    if config:
        lines.append("C " + " ".join("%s=%s" % (k, _fmt(v)) for k, v in config.items()))
    names = list(names)
    for i in range(0, len(names), 25):
        lines.append("R " + ",".join(names[i: i + 25]))
    if test:
        lines.append("T " + test.replace("\n", " "))
    body = "".join(line + "\n" for line in lines)
    return "%s %d %d %04X %s\n%s" % (MAGIC, seq & 0xFFFF, ts, body_crc(body), character, body)


@dataclass
class ParsedInbox:
    seq: int
    ts: int
    character: str
    config: dict = field(default_factory=dict)
    names: list = field(default_factory=list)
    test: Optional[str] = None


def parse_text(text: str) -> ParsedInbox:
    """The addon's parser, in Python. Raises InboxError with the same reasons the Lua side gives."""
    if not isinstance(text, str):
        raise InboxError("type")
    nl = text.find("\n")
    if nl < 0:
        raise InboxError("noheader")
    head, body = text[:nl].replace("\r", ""), text[nl + 1:].replace("\r", "")
    parts = head.split(" ")
    if len(parts) < 5 or parts[0] != MAGIC:
        raise InboxError("magic")
    try:
        seq, ts = int(float(parts[1])), int(float(parts[2]))
    except ValueError:
        raise InboxError("numbers") from None
    if parts[3].upper() != "%04X" % crc16(body.encode("utf-8")):
        raise InboxError("crc")
    out = ParsedInbox(seq=seq, ts=ts, character=parts[4])
    seen = set()
    for line in body.split("\n"):
        kind, rest = line[:1], line[2:]
        if kind == "C":
            for item in rest.split(" "):
                if "=" in item:
                    k, _, v = item.partition("=")
                    try:
                        out.config[k] = float(v)
                    except ValueError:
                        pass
        elif kind == "R":
            for name in rest.split(","):
                if NAME_MIN <= len(name.encode("utf-8")) <= NAME_MAX and not _BAD_NAME.search(name) \
                        and name not in seen and len(out.names) < MAX_NAMES:
                    seen.add(name)
                    out.names.append(name)
        elif kind == "T":
            out.test = rest
    return out


def replace_file(path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file, then rename), retrying while the game has the target open."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    last = None
    for _ in range(8):                     # the game may be reading the target at this instant
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.02)
    raise last                              # type: ignore[misc]


class InboxWriter:
    """Writes the inbound file. ``write`` is cheap to call often: it only touches the disk when something
    changed or ``refresh_s`` has passed."""

    def __init__(self, path, refresh_s: float = 4.0, clock=time.time, rng: Optional[random.Random] = None):
        self.path = Path(path)
        self.refresh_s, self.clock = refresh_s, clock
        self.seq = (rng or random.Random()).randrange(1, 60000)
        self.last_key = None
        self.last_write = 0.0
        self.writes = 0
        self.errors = 0
        self.last_error = ""

    def write(self, character: str, names, config: Optional[dict] = None, test: Optional[str] = None,
              force: bool = False) -> bool:
        """True when the file was (re)written."""
        names = sorted(names)
        key = (character, tuple(names), tuple(sorted((config or {}).items())), test)
        now = self.clock()
        if not force and key == self.last_key and now - self.last_write < self.refresh_s:
            return False
        self.seq = self.seq % 65535 + 1
        text = build_text(self.seq, int(now), character, names, config, test)
        try:
            self._replace(text)
        except OSError as e:
            self.errors += 1
            self.last_error = str(e)
            log.warning("could not write %s: %s", self.path.name, e)
            return False
        self.last_key, self.last_write = key, now
        self.writes += 1
        return True

    def _replace(self, text: str) -> None:
        replace_file(self.path, text)

    def clear(self) -> None:
        """Remove the file (client shutting down): the addon sees 'no file' and stops looking names up."""
        try:
            self.path.unlink()
        except OSError:
            pass
        self.last_key = None
