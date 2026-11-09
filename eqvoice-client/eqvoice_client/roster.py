"""The roster: every other EqVoice character online on this realm, as the signalling server reports them.

It is only a list of names the game should look up. Being on it means nothing for voice: that needs mutual
proximity confirmed by the server.

Names are matched case-insensitively (the hub does the same) but displayed and sent to the game as the
server spelled them, because the game's by-name lookup is exact.
"""
from __future__ import annotations

import random
from typing import Iterable, Optional


class Roster:
    def __init__(self, rng: Optional[random.Random] = None):
        self.by_key: dict = {}            # name.lower() -> (name, sid)
        self.by_sid: dict = {}            # sid -> name.lower()
        self.version = 0                  # bumps on any change
        self._rng = rng or random.Random()
        self._window = 0

    def __len__(self) -> int:
        return len(self.by_key)

    def __contains__(self, name: str) -> bool:
        return name.lower() in self.by_key

    def clear(self) -> None:
        if self.by_key:
            self.version += 1
        self.by_key.clear()
        self.by_sid.clear()

    def replace(self, entries: Iterable) -> None:
        self.clear()
        self.add(entries)

    def sync(self, entries: Iterable) -> tuple:
        """Make the roster exactly ``entries``. Returns (names whose sid changed, sids that disappeared)."""
        new = {}
        for item in entries:
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
                new[item[0].lower()] = (item[0], item[1])
        replaced = [n for k, (n, s) in new.items() if k in self.by_key and self.by_key[k][1] != s]
        keep = {v[1] for v in new.values()}
        removed = [s for s in self.by_sid if s not in keep]
        self.by_key, self.by_sid = {}, {}
        for k, (n, s) in new.items():
            self.by_key[k] = (n, s)
            self.by_sid[s] = k
        self.version += 1
        return replaced, removed

    def add(self, entries: Iterable) -> list:
        """Add [name, sid] pairs. Returns the names whose sid changed (the same character logged in again)."""
        replaced = []
        for item in entries:
            if not (isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str)):
                continue
            name, sid = item
            key = name.lower()
            old = self.by_key.get(key)
            if old is not None:
                if old[1] == sid:
                    continue
                self.by_sid.pop(old[1], None)
                replaced.append(name)
            self.by_key[key] = (name, sid)
            self.by_sid[sid] = key
            self.version += 1
        return replaced

    def remove(self, sids: Iterable) -> list:
        """Remove by session id. Returns the names removed."""
        gone = []
        for sid in sids:
            key = self.by_sid.pop(sid, None)
            if key is None:
                continue
            entry = self.by_key.get(key)
            if entry is not None and entry[1] == sid:
                del self.by_key[key]
                gone.append(entry[0])
                self.version += 1
        return gone

    def sid_of(self, name: str) -> Optional[str]:
        e = self.by_key.get(name.lower())
        return e[1] if e else None

    def name_of(self, sid: str) -> Optional[str]:
        key = self.by_sid.get(sid)
        return self.by_key[key][0] if key in self.by_key else None

    def names(self) -> list:
        return sorted(n for n, _ in self.by_key.values())

    def select(self, limit: int, priority: Iterable = ()) -> list:
        """The names to hand the game. Everyone when they fit; otherwise the priority names (the ones
        nearby or recently nearby) plus a window of the rest that moves each call, so over a few refreshes
        every name gets its turn."""
        names = self.names()
        if len(names) <= limit:
            return names
        pri = [self.by_key[p.lower()][0] for p in priority if p.lower() in self.by_key]
        pri = list(dict.fromkeys(pri))[:limit]
        chosen = set(pri)
        rest = [n for n in names if n not in chosen]
        room = limit - len(pri)
        if room > 0 and rest:
            start = (self._window * room) % len(rest)
            window = (rest + rest)[start:start + min(room, len(rest))]
            self._window += 1
            pri.extend(window)
        return sorted(set(pri))
