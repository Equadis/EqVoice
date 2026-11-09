"""Bridge sources emit decoded frames; the monitor turns them into "what does the game say, and can it be trusted".

The monitor is where the fail-closed rule lives: if the bridge goes quiet the client keeps the last readings for
a few seconds (a hiccup must not cut a conversation) and then reports nobody (an unattended open mic must not
keep talking to people who may have walked away).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .frame import BridgeState


class BridgeSource:
    """Base class. ``run`` is a coroutine that calls ``emit(BridgeState)`` for every decoded frame."""

    kind = "none"
    inbox_path = None            # where the roster file for the addon goes, when this source knows

    def __init__(self):
        self.status: dict = {"kind": self.kind}

    async def run(self, emit: Callable[[BridgeState], None]) -> None:       # pragma: no cover
        raise NotImplementedError


@dataclass
class MonitorView:
    status: str                              # none | live | stale | lost
    state: Optional[BridgeState] = None
    age: float = 0.0
    identity: Optional[tuple] = None         # (realm, character, login_id) while the game reports one
    results: tuple = ()
    usable: bool = False                     # readings may be acted on right now
    reason: str = ""                         # why not, in words for the UI
    frames: int = 0
    interval_s: float = 0.0                  # measured time between new frames

    @property
    def enabled(self) -> bool:
        return self.state is not None and self.state.enabled


class BridgeMonitor:
    def __init__(self, stale_s: float = 4.0, hold_s: float = 8.0, clock: Callable[[], float] = time.monotonic):
        self.stale_s, self.hold_s, self.clock = stale_s, hold_s, clock
        self.state: Optional[BridgeState] = None
        self.last_change = 0.0
        self.frames = 0
        self._key: Optional[tuple] = None
        self.interval_s = 0.0

    def push(self, state: BridgeState) -> bool:
        """Record a decoded frame. True when it was new (the addon's counter or login id moved)."""
        key = (state.login_id, state.counter)
        if key == self._key:
            return False
        now = self.clock()
        if self._key is not None:
            gap = now - self.last_change
            self.interval_s = gap if self.interval_s == 0 else self.interval_s * 0.8 + gap * 0.2
        self._key = key
        self.last_change = now
        self.state = state
        self.frames += 1
        return True

    def view(self) -> MonitorView:
        if self.state is None:
            return MonitorView("none", reason="waiting for the EqVoice addon (log in to the game; /ev status)")
        age = self.clock() - self.last_change
        st = self.state
        if age <= self.stale_s:
            status = "live"
        elif age <= self.hold_s:
            status = "stale"
        else:
            status = "lost"
        reason = ""
        if status == "lost":
            reason = "no frames from the game for %.0fs" % age
        elif st.logout:
            reason = "the game is logging out"
        elif not st.enabled:
            reason = "the addon is switched off (/ev on)"
        elif not st.lookups:
            reason = "the addon cannot look players up (needs the ClassicAPI mod)"
        elif st.id_mismatch:
            reason = "the game says the roster file was written for another character"
        usable = status in ("live", "stale") and not reason
        return MonitorView(status=status, state=st, age=age, identity=st.identity if not st.logout else None,
                           results=st.results if usable else (), usable=usable, reason=reason,
                           frames=self.frames, interval_s=self.interval_s)
