"""aiohttp wrapper around Hub: WebSocket at /ws, plus /health and /stats.

No media ever passes through here. A client sends a few JSON messages to find a
peer and to swap WebRTC session descriptions, then the audio flows peer to peer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import Optional

from aiohttp import WSMsgType, web

from .hub import Hub, Limits

log = logging.getLogger("eqvoice.signalling")

HUB = web.AppKey("hub", Hub)
TRUST_PROXY = web.AppKey("trust_proxy", bool)
CONNS = web.AppKey("conns", set)

def log_trace(event: str, fields: dict) -> None:
    """The hub's narration as one greppable line: TRACE hub <event> key=value ... (names appear: for tests, not production)."""
    log.info("TRACE hub %s %s", event, " ".join("%s=%s" % (k, v) for k, v in fields.items()))


DEFAULT_ICE = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun.cloudflare.com:3478"]}]


class WsConn:
    """Adapts an aiohttp WebSocket to the tiny interface Hub expects."""

    def __init__(self, ws: web.WebSocketResponse, ip: str):
        self.ws, self.ip = ws, ip
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.close_info: Optional[tuple] = None

    def send(self, msg: dict) -> None:
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:            # a client that will not read is dropped
            self.close(1013, "slow consumer")

    def close(self, code: int, reason: str) -> None:
        if self.close_info is None:
            self.close_info = (code, reason)
            try:
                self.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass


def client_ip(request: web.Request, trust_proxy: bool) -> str:
    if trust_proxy:
        for header in ("CF-Connecting-IP", "X-Forwarded-For"):
            v = request.headers.get(header)
            if v:
                return v.split(",")[0].strip()
    return request.remote or "?"


async def ws_handler(request: web.Request) -> web.StreamResponse:
    hub: Hub = request.app[HUB]
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=hub.limits.max_msg_bytes, autoping=True)
    await ws.prepare(request)
    conn = WsConn(ws, client_ip(request, request.app[TRUST_PROXY]))
    if not hub.connect(conn):
        await ws.close(code=1013, message=b"too many connections")
        return ws
    request.app[CONNS].add(conn)

    async def writer() -> None:
        while True:
            msg = await conn.queue.get()
            if msg is None:
                break
            await ws.send_str(json.dumps(msg, separators=(",", ":")))
        # The hub asked for this connection to end (rate limit, timeout, ...):
        # flush what was queued, then close, which also ends the read loop below.
        code, reason = conn.close_info or (1000, "")
        await ws.close(code=code, message=reason.encode()[:100])

    wtask = asyncio.create_task(writer())
    try:
        async for m in ws:
            if m.type == WSMsgType.TEXT:
                hub.handle(conn, m.data)
            elif m.type == WSMsgType.BINARY:
                hub.handle(conn, m.data)
            elif m.type == WSMsgType.ERROR:
                break
            if conn.close_info is not None:
                break
    finally:
        request.app[CONNS].discard(conn)
        hub.disconnect(conn)
        conn.close(1000, "")
        try:
            await asyncio.wait_for(wtask, 2)
        except (asyncio.TimeoutError, Exception):
            wtask.cancel()
        if not ws.closed:
            code, reason = conn.close_info or (1000, "")
            await ws.close(code=code, message=reason.encode()[:100])
    return ws


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok\n")


async def stats(request: web.Request) -> web.Response:
    return web.json_response(request.app[HUB].stats())


async def _ticker(app: web.Application):
    async def run() -> None:
        while True:
            await asyncio.sleep(1)
            app[HUB].tick()
    task = asyncio.create_task(run())
    yield
    task.cancel()


async def _close_all(app: web.Application) -> None:
    """On shutdown tell every client to reconnect, instead of waiting for them to leave."""
    for conn in list(app[CONNS]):
        conn.close(1012, "server restarting")
    await asyncio.sleep(0.1)


def make_app(hub: Optional[Hub] = None, trust_proxy: bool = False) -> web.Application:
    app = web.Application()
    app[HUB] = hub or Hub(ice_servers=DEFAULT_ICE)
    app[TRUST_PROXY] = trust_proxy
    app[CONNS] = set()
    app.on_shutdown.append(_close_all)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    app.router.add_get("/stats", stats)
    app.cleanup_ctx.append(_ticker)
    return app


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="eqvoice_signalling", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--trust-proxy", action="store_true",
                   help="read the client address from CF-Connecting-IP / X-Forwarded-For")
    p.add_argument("--join-code", default=None, help="optional shared code clients must present")
    p.add_argument("--stun", action="append", default=None,
                   help="STUN/TURN url offered to clients (repeatable); default: public Google and Cloudflare STUN")
    p.add_argument("--lease", type=float, default=30.0, help="seconds a dropped session's character name stays reserved for its own key")
    p.add_argument("--link-radius", type=float, default=90.0, help="yards: sessions farther apart than this are never linked")
    p.add_argument("--distance-tolerance", type=float, default=12.0, help="yards: how far two reports of one pair may disagree")
    p.add_argument("--max-conns-per-ip", type=int, default=20)
    p.add_argument("--max-connections", type=int, default=10000, help="refuse new connections beyond this many in total (all addresses)")
    p.add_argument("--registers-per-minute", type=int, default=12, help="registrations allowed per client address per minute")
    p.add_argument("--trace", action="store_true",
                   help="narrate registrations, rosters, proximity reports and links in the log (shows character names; for testing)")
    p.add_argument("--log-level", default="INFO")
    a = p.parse_args(argv)
    logging.basicConfig(level=a.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    ice = [{"urls": a.stun}] if a.stun else DEFAULT_ICE
    hub = Hub(Limits(reserve_seconds=a.lease, max_conns_per_ip=a.max_conns_per_ip, max_connections=a.max_connections,
                     registers_per_ip_per_minute=a.registers_per_minute, link_radius=a.link_radius,
                     distance_tolerance=a.distance_tolerance),
              join_code=a.join_code, ice_servers=ice, trace=log_trace if a.trace else None)
    log.info("eqvoice signalling on %s:%d (join code %s)", a.host, a.port, "on" if a.join_code else "off")
    web.run_app(make_app(hub, a.trust_proxy), host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
