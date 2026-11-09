"""The same hub, through real WebSockets on a real local port."""
import asyncio
import json
import secrets

import aiohttp
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eqvoice_signalling.hub import Hub, Limits, b64u, registration_message, unb64u
from eqvoice_signalling.server import make_app


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


class Peer:
    def __init__(self, session, url, name, realm="Testrealm"):
        self.session, self.url, self.name, self.realm = session, url, name, realm
        self.key = Ed25519PrivateKey.generate()
        self.sid = secrets.token_urlsafe(16)

    async def open(self):
        self.ws = await self.session.ws_connect(self.url)
        self.hello = await self.recv("hello")
        return self

    async def recv(self, t=None, timeout=5):
        while True:
            m = await self.ws.receive(timeout=timeout)
            if m.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(m.data)
                if t is None or d["t"] == t:
                    return d
            elif m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                raise EOFError("closed %s" % (m.data,))

    async def register(self):
        nonce = unb64u(self.hello["nonce"])
        pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        await self.ws.send_json({"t": "register", "v": 2, "sid": self.sid, "realm": self.realm, "character": self.name,
                                 "pub": b64u(pub), "sig": b64u(self.key.sign(registration_message(nonce, self.sid, self.realm, self.name)))})
        return await self.recv()


def test_two_peers_roster_proximity_and_signals():
    async def go():
        server = TestServer(make_app(Hub(ice_servers=[{"urls": ["stun:example.org:3478"]}])))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        async with aiohttp.ClientSession() as s:
            a = await Peer(s, url, "Alice").open()
            b = await Peer(s, url, "Bobby").open()
            assert a.hello["ice"] == [{"urls": ["stun:example.org:3478"]}]
            ra = await a.register()
            assert ra["t"] == "registered" and ra["roster"] == []
            rb = await b.register()
            assert rb["roster"] == [["Alice", a.sid]]
            assert await a.recv("roster_update") == {"t": "roster_update", "add": [["Bobby", b.sid]]}
            await a.ws.send_json({"t": "proximity", "peers": [["Bobby", 22.0, 1]]})
            await b.ws.send_json({"t": "proximity", "peers": [["Alice", 21.0, 1]]})
            pa = await a.recv("peer_available")
            pb = await b.recv("peer_available")
            assert pa["sid"] == b.sid and pa["character"] == "Bobby" and pa["distance"] == 21.5
            assert pb["sid"] == a.sid
            await a.ws.send_json({"t": "offer", "to": b.sid, "ep": 3, "data": {"sdp": "v=0"}})
            got = await b.recv("offer")
            assert got == {"t": "offer", "from": a.sid, "ep": 3, "data": {"sdp": "v=0"}}
            await b.ws.send_json({"t": "answer", "to": a.sid, "ep": 3, "data": {"sdp": "v=1"}})
            assert (await a.recv("answer"))["ep"] == 3
            async with s.get("http://127.0.0.1:%d/stats" % server.port) as r:
                st = await r.json()
            assert st["sessions"] == 2 and st["links"] == 1 and st["largest_realm"] == 2
            blob = json.dumps(st)
            assert "Alice" not in blob and a.sid not in blob and "127.0.0.1" not in blob
            async with s.get("http://127.0.0.1:%d/health" % server.port) as r:
                assert (await r.text()).strip() == "ok"
            await b.ws.close()
            assert (await a.recv("peer_out_of_range"))["sid"] == b.sid
            assert await a.recv("roster_update") == {"t": "roster_update", "remove": [b.sid]}
            await a.ws.close()
        await server.close()
    run(go())


def test_binary_and_oversize_are_handled():
    async def go():
        server = TestServer(make_app(Hub(limits=Limits(max_msg_bytes=2048))))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        async with aiohttp.ClientSession() as s:
            a = await Peer(s, url, "Alice").open()
            await a.ws.send_bytes(b"\x00\x01")
            assert (await a.recv("error"))["code"] == "bad_msg"
            await a.ws.send_str("x" * 5000)
            m = await a.ws.receive(timeout=5)
            while m.type == aiohttp.WSMsgType.TEXT:
                m = await a.ws.receive(timeout=5)
            assert m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
        await server.close()
    run(go())


def test_unregistered_connection_is_dropped_by_the_ticker():
    async def go():
        hub = Hub(limits=Limits(register_timeout=1.0))
        server = TestServer(make_app(hub))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        async with aiohttp.ClientSession() as s:
            a = await Peer(s, url, "Alice").open()
            assert (await a.recv("error", timeout=5))["code"] == "register_timeout"
            m = await a.ws.receive(timeout=5)
            assert m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
        await server.close()
    run(go())


def test_connection_cap_per_ip():
    async def go():
        server = TestServer(make_app(Hub(limits=Limits(max_conns_per_ip=2))))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        async with aiohttp.ClientSession() as s:
            a = await Peer(s, url, "Alice").open()
            b = await Peer(s, url, "Bobby").open()
            ws = await s.ws_connect(url)
            m = await ws.receive(timeout=5)
            assert m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
            await a.ws.close(); await b.ws.close()
        await server.close()
    run(go())


def test_idle_sessions_are_expired_by_the_ticker():
    async def go():
        hub = Hub(limits=Limits(idle_timeout=1.5))
        server = TestServer(make_app(hub))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        async with aiohttp.ClientSession() as s:
            a = await Peer(s, url, "Alice").open()
            await a.register()
            b = await Peer(s, url, "Bobby").open()
            await b.register()
            for _ in range(5):                                # Bobby keeps the session alive, Alice does not
                await asyncio.sleep(0.5)
                await b.ws.send_json({"t": "ping"})
            upd = []
            while True:
                try:
                    d = await b.recv(timeout=0.5)
                except Exception:
                    break
                upd.append(d)
            assert any(d["t"] == "roster_update" and d.get("remove") == [a.sid] for d in upd), upd
            assert hub.stats()["sessions"] == 1
        await server.close()
    run(go())
