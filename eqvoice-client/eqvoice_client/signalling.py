"""Signalling client (protocol v2): one WebSocket to the hub, with reconnects.

    connecting -> connected (hello) -> registered (roster received) -> ... -> backoff -> connecting ...

What goes up: ``register`` (once, with the character from the game and a signature), ``proximity`` (who the
game currently sees nearby and how far, coalesced and rate limited), the WebRTC ``offer/answer/ice/bye``
messages, ``ping``. What comes down: the ``registered`` roster and ``roster_update`` changes,
``peer_available`` / ``peer_out_of_range`` (the hub's verdict on mutual proximity), and relayed signals.
Everything goes to ``on_event`` as plain dicts; underscore events are the client's own (``_state``,
``_registered``, ``_down``).

The client idles until it has an identity (the game reported a character). Changing identity (character
switch, new login, logout) sends ``unregister`` so the old name leaves the roster immediately.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Callable, Optional

import aiohttp

from .identity import Identity
from .trace import trace

log = logging.getLogger("eqvoice.signalling")

PROTOCOL_VERSION = 2
PING_EVERY = 10.0
PROX_MIN_INTERVAL = 0.5          # proximity messages leave at most this often (the hub allows 4/s sustained)
PROX_KEEPALIVE = 4.0             # an unchanged non-empty report is repeated this often (the hub forgets after 6 s)
PROX_MOVE = 2.0                  # yards a distance must change by to be worth a message


class SignallingClient:
    def __init__(self, url: str, join_code: str, on_event: Callable[[dict], None],
                 rng: Optional[random.Random] = None, clock: Callable[[], float] = time.monotonic):
        self.url, self.join_code, self.on_event = url, join_code, on_event
        self.rng, self.clock = rng or random.Random(), clock
        self.ident: Optional[Identity] = None
        self.state = "idle"
        self.last_error = ""
        self.server_ice: list = []
        self.limits: dict = {}
        self.rtt_ms: Optional[float] = None
        self.connects = 0
        self.registered_at: Optional[float] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._finished = asyncio.Event()
        self._outq: Optional[asyncio.Queue] = None      # signals leave in the order they were queued
        self._wake = asyncio.Event()
        self._stop = False
        self._prox: Optional[list] = None               # [(name, yards)] wanted
        self._prox_sent: Optional[list] = None
        self._prox_t = -1e9
        self._ping_t = 0.0
        self.sent = self.received = 0
        self.prox_sent = 0

    # ----------------------------------------------------------------- api

    @property
    def registered(self) -> bool:
        return self.state == "registered"

    def set_identity(self, ident: Optional[Identity]) -> None:
        if ident is self.ident:
            return
        self.ident = ident
        self._prox = self._prox_sent = None
        log.info("identity: %s", "none" if ident is None else "%s on %s" % (ident.character, ident.realm))
        self._wake.set()                 # the running session notices, says goodbye and ends

    def report_proximity(self, entries) -> None:
        """``entries``: [(name, yards)] the game sees right now. Sent coalesced; an empty list withdraws everything."""
        self._prox = sorted((n, round(float(d), 1)) for n, d in entries)[: int(self.limits.get("max_report", 64))]
        self._wake.set()

    def send_signal(self, to: str, kind: str, ep: int, data: dict) -> bool:
        if self.state != "registered" or self._ws is None or self._ws.closed or self._outq is None:
            return False
        try:
            self._outq.put_nowait({"t": kind, "to": to, "ep": ep, "data": data})
        except asyncio.QueueFull:
            log.warning("signalling queue full; dropping a %s for %s", kind, to)
            return False
        return True

    async def stop(self) -> None:
        """End the session politely (the hub drops our name from the roster at once) and wait for run() to finish."""
        self._stop = True
        self._wake.set()
        if self._running:
            try:
                await asyncio.wait_for(self._finished.wait(), 4.0)
            except asyncio.TimeoutError:
                pass

    def status(self) -> dict:
        return {"state": self.state, "url": self.url, "sid": self.ident.sid if self.ident else None,
                "error": self.last_error, "rtt_ms": self.rtt_ms, "connects": self.connects, "sent": self.sent,
                "received": self.received, "proximity_msgs": self.prox_sent}

    # ------------------------------------------------------------ internals

    async def _leave(self, ws, goodbye: bool) -> None:
        """Close the socket (only ever called by the task that owns it, after its reader has stopped)."""
        if goodbye and not ws.closed:
            try:
                await ws.send_str(json.dumps({"t": "unregister"}))
            except Exception:                                # noqa: BLE001
                pass
        try:
            await asyncio.wait_for(ws.close(), 2.0)
        except Exception:                                    # noqa: BLE001
            pass

    async def _send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_str(json.dumps(msg, separators=(",", ":")))
            self.sent += 1
        except (ConnectionError, aiohttp.ClientError, RuntimeError) as e:
            log.debug("send failed: %s", e)

    def _set_state(self, state: str, err: str = "") -> None:
        if state != self.state or err:
            self.state = state
            if err:
                self.last_error = err
            self.on_event({"t": "_state", "state": state, "error": self.last_error})

    async def run(self) -> None:
        self._running = True
        self._finished.clear()
        try:
            await self._run()
        finally:
            self._running = False
            self._finished.set()

    async def _run(self) -> None:
        backoff = 1.0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10)) as session:
            while not self._stop:
                ident = self.ident
                if ident is None:
                    self._set_state("idle")
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                started = time.monotonic()
                try:
                    await self._session(session, ident)
                    err = ""
                except asyncio.CancelledError:
                    raise
                except Exception as e:                      # noqa: BLE001 - reconnect on anything
                    err = "%s: %s" % (type(e).__name__, e)
                if self._stop:
                    break
                if err:
                    log.warning("signalling: %s", err)
                self._set_state("backoff" if self.ident is not None else "idle", err)
                self.on_event({"t": "_down"})
                if self.ident is not ident:
                    backoff = 1.0                           # a new identity connects straight away
                    continue
                if time.monotonic() - started > 30:
                    backoff = 1.0
                delay = min(backoff, 30.0) * (0.75 + self.rng.random() * 0.5)
                backoff = min(backoff * 2, 30.0)
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), delay)
                except asyncio.TimeoutError:
                    pass

    async def _session(self, session: aiohttp.ClientSession, ident: Identity) -> None:
        self._set_state("connecting")
        ws = await session.ws_connect(self.url, heartbeat=25, max_msg_size=256 * 1024)
        self._ws = ws
        self.connects += 1
        try:
            hello = await self._expect(ws, "hello", 10, ident)
            if hello.get("v") != PROTOCOL_VERSION:
                raise RuntimeError("server speaks protocol v%s, this client needs v%d" % (hello.get("v"), PROTOCOL_VERSION))
            self.server_ice = hello.get("ice") or []
            self.limits = hello.get("limits") or {}
            self._set_state("connected")
            pub, sig = ident.registration(hello["nonce"])
            reg = {"t": "register", "v": PROTOCOL_VERSION, "sid": ident.sid, "realm": ident.realm,
                   "character": ident.character, "pub": pub, "sig": sig}
            if self.join_code:
                reg["code"] = self.join_code
            if ident.token:
                reg["token"] = ident.token
            await self._send(reg)
            ack = await self._expect(ws, "registered", 10, ident)
            ident.token = ack.get("token", "") or ident.token
            self.registered_at = time.monotonic()
            self._prox_sent, self._prox_t = None, -1e9
            self._set_state("registered")
            log.info("registered as %s on %s (roster %d+%s, lease %ss)", ident.character, ident.realm,
                     len(ack.get("roster") or []), ack.get("more", 0), ack.get("lease"))
            self.on_event({"t": "_registered", "sid": ident.sid, "token": ident.token, "roster": ack.get("roster") or [],
                           "more": ack.get("more", 0), "lease": ack.get("lease")})
            self._ping_t = time.monotonic()
            self._outq = asyncio.Queue(maxsize=500)
            reader = asyncio.create_task(self._read(ws))
            writer = asyncio.create_task(self._drain())
            try:
                while not self._stop and not reader.done() and self.ident is ident:
                    await self._flush_proximity()
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), 0.25)
                    except asyncio.TimeoutError:
                        pass
                    if time.monotonic() - self._ping_t >= PING_EVERY:
                        self._ping_t = time.monotonic()
                        await self._send({"t": "ping", "ts": time.time()})
                if reader.done() and reader.exception():
                    raise reader.exception()
            finally:
                reader.cancel()
                writer.cancel()
                await asyncio.gather(reader, writer, return_exceptions=True)
                self._outq = None
        finally:
            self._ws = None
            self._prox_sent = None
            await self._leave(ws, goodbye=self.ident is not ident or self._stop)

    async def _drain(self) -> None:
        """Send queued signals in order, paced below the hub's rate limit (burst 100, 50/s sustained)."""
        q = self._outq
        tokens, last = 60.0, time.monotonic()
        while True:
            msg = await q.get()
            now = time.monotonic()
            tokens = min(60.0, tokens + (now - last) * 40.0)
            last = now
            if tokens < 1:
                await asyncio.sleep((1 - tokens) / 40.0)
                tokens, last = 1.0, time.monotonic()
            tokens -= 1
            await self._send(msg)

    @staticmethod
    def _moved(new: list, old: list) -> bool:
        if [n for n, _ in new] != [n for n, _ in old]:
            return True
        return any(abs(a[1] - b[1]) >= PROX_MOVE for a, b in zip(new, old))

    async def _flush_proximity(self) -> None:
        want, sent = self._prox, self._prox_sent
        if want is None:
            return
        now = self.clock()
        if sent is None:
            if not want:
                return                                  # nothing to withdraw on a fresh session
            changed = True
        else:
            changed = self._moved(want, sent)
            if not changed and (not want or now - self._prox_t < PROX_KEEPALIVE):
                return
        if now - self._prox_t < PROX_MIN_INTERVAL:
            return
        self._prox_sent, self._prox_t = list(want), now
        ts = int(time.time())
        await self._send({"t": "proximity", "peers": [[n, d, ts] for n, d in want]})
        self.prox_sent += 1
        trace("proximity_sent", to="hub", peers=["%s:%.1f" % (n, d) for n, d in want] or "(none)")

    async def _expect(self, ws, kind: str, timeout: float, ident: Identity) -> dict:
        end = time.monotonic() + timeout
        while True:
            if self._stop or self.ident is not ident:
                raise ConnectionAbortedError("identity changed while connecting")
            left = end - time.monotonic()
            if left <= 0:
                raise asyncio.TimeoutError("no %s from the server" % kind)
            try:
                m = await ws.receive(timeout=min(0.5, left))
            except asyncio.TimeoutError:
                continue
            if m.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(m.data)
                self.received += 1
                if d.get("t") == kind:
                    return d
                if d.get("t") == "error":
                    raise RuntimeError("server refused: %s (%s)" % (d.get("code"), d.get("msg")))
            elif m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                raise ConnectionError("closed while waiting for %s" % kind)

    async def _read(self, ws) -> None:
        async for m in ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                d = json.loads(m.data)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            self.received += 1
            t = d.get("t")
            if t == "pong":
                ts = d.get("ts")
                self.rtt_ms = round((time.time() - ts) * 1000, 1) if isinstance(ts, (int, float)) else self.rtt_ms
            elif t in ("roster_update", "peer_available", "peer_out_of_range", "offer", "answer", "ice", "bye"):
                self.on_event(d)
            elif t == "error":
                # not_linked is the normal answer to a message that crossed the end of a link; not worth a warning
                (log.debug if d.get("code") == "not_linked" else log.warning)("server error: %s %s", d.get("code"), d.get("msg"))
                self.on_event(d)
        raise ConnectionError("server closed the connection")
