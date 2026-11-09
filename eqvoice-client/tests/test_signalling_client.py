"""The signalling client (protocol v2) against the real hub code over real sockets."""
import asyncio

import pytest
from aiohttp.test_utils import TestServer

from eqvoice_client import identity
from eqvoice_client.signalling import SignallingClient
from eqvoice_signalling.hub import Hub, Limits
from eqvoice_signalling.server import make_app

REALM = "Testrealm"


def run(coro, timeout=40):
    return asyncio.run(asyncio.wait_for(coro, timeout))


class Endpoint:
    def __init__(self, url, tmp, name, code="", realm=REALM):
        self.events = []
        self.home = tmp / name
        self.home.mkdir(exist_ok=True)
        self.name, self.realm = name, realm
        self.client = SignallingClient(url, code, self.events.append)
        self.task = None
        self.ident = None

    def start(self, login=1, name=None, realm=None):
        self.ident = identity.load_or_create(self.home, realm or self.realm, name or self.name, login)
        self.client.set_identity(self.ident)
        if self.task is None:
            self.task = asyncio.create_task(self.client.run())
        return self.ident

    async def wait(self, pred, timeout=8.0):
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            for e in self.events:
                if pred(e):
                    return e
            await asyncio.sleep(0.02)
        raise AssertionError("timed out; events so far: %r; status %r" % (self.events[-6:], self.client.status()))

    async def stop(self):
        await self.client.stop()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass


async def two(tmp_path, hub=None):
    server = TestServer(make_app(hub or Hub(ice_servers=[{"urls": ["stun:example.org"]}])))
    await server.start_server()
    url = "ws://127.0.0.1:%d/ws" % server.port
    a, b = Endpoint(url, tmp_path, "Alice"), Endpoint(url, tmp_path, "Bobby")
    a.start()
    await a.wait(lambda e: e["t"] == "_registered")
    b.start()
    await b.wait(lambda e: e["t"] == "_registered")
    return server, a, b


def test_register_roster_and_roster_updates(tmp_path):
    async def go():
        server, a, b = await two(tmp_path)
        reg_b = next(e for e in b.events if e["t"] == "_registered")
        assert reg_b["roster"] == [["Alice", a.ident.sid]], "Bobby learns who was already online"
        up = await a.wait(lambda e: e["t"] == "roster_update")
        assert up["add"] == [["Bobby", b.ident.sid]], "and Alice is told about Bobby"
        assert a.client.registered and a.client.server_ice == [{"urls": ["stun:example.org"]}]
        assert b.ident.token, "the hub's token is kept for resuming"
        await b.stop()
        gone = await a.wait(lambda e: e["t"] == "roster_update" and e.get("remove"))
        assert gone["remove"] == [b.ident.sid], "a clean stop leaves the roster at once"
        await a.stop()
        await server.close()
    run(go())


def test_proximity_makes_a_link_and_signals_flow(tmp_path):
    async def go():
        server, a, b = await two(tmp_path)
        a.client.report_proximity([("Bobby", 18.0)])
        b.client.report_proximity([("Alice", 19.0)])
        got_a = await a.wait(lambda e: e["t"] == "peer_available")
        got_b = await b.wait(lambda e: e["t"] == "peer_available")
        assert got_a["sid"] == b.ident.sid and got_a["character"] == "Bobby" and got_a["distance"] == pytest.approx(18.5)
        assert got_b["sid"] == a.ident.sid
        assert a.client.send_signal(b.ident.sid, "offer", 5, {"sdp": "x"})
        got = await b.wait(lambda e: e["t"] == "offer")
        assert got == {"t": "offer", "from": a.ident.sid, "ep": 5, "data": {"sdp": "x"}}
        # B's game stops seeing A: A hears the link drop
        b.client.report_proximity([])
        await a.wait(lambda e: e["t"] == "peer_out_of_range")
        a.events.clear()
        assert a.client.send_signal(b.ident.sid, "offer", 1, {})                  # the hub answers not_linked
        err = await a.wait(lambda e: e["t"] == "error")
        assert err["code"] == "not_linked"
        await a.stop(); await b.stop()
        await server.close()
    run(go(), 60)


def test_one_sided_report_never_links(tmp_path):
    async def go():
        server, a, b = await two(tmp_path)
        a.client.report_proximity([("Bobby", 5.0)])
        await asyncio.sleep(1.5)
        assert not [e for e in a.events + b.events if e["t"] == "peer_available"], "knowing a name is not enough"
        await a.stop(); await b.stop()
        await server.close()
    run(go())


def test_proximity_updates_are_coalesced_and_the_last_value_wins(tmp_path):
    async def go():
        hub = Hub()
        server, a, b = await two(tmp_path, hub)
        before = a.client.prox_sent
        for i in range(60):
            a.client.report_proximity([("Bobby", 10.0 + (i % 7) * 3.0)])
            await asyncio.sleep(0.01)
        a.client.report_proximity([("Bobby", 33.0)])
        await asyncio.sleep(1.2)
        sent = a.client.prox_sent - before
        assert sent <= 4, "a jittery game must not hammer the server (sent %d)" % sent
        st = hub.by_sid[a.ident.sid]
        assert st.reports["bobby"][0] == 33.0
        await a.stop(); await b.stop()
        await server.close()
    run(go())


def test_tiny_movements_are_not_sent_but_a_keepalive_is(tmp_path):
    async def go():
        server, a, b = await two(tmp_path)
        a.client.report_proximity([("Bobby", 20.0)])
        await asyncio.sleep(0.8)
        n = a.client.prox_sent
        for d in (20.4, 19.8, 20.9, 20.2):
            a.client.report_proximity([("Bobby", d)])
            await asyncio.sleep(0.3)
        assert a.client.prox_sent == n, "under two yards of movement: nothing to say"
        await asyncio.sleep(3.5)
        assert a.client.prox_sent == n + 1, "but the server forgets quiet clients, so an unchanged report is repeated"
        a.client.report_proximity([])
        await asyncio.sleep(0.8)
        assert a.client.prox_sent == n + 2, "emptying the list is sent immediately"
        await asyncio.sleep(4.5)
        assert a.client.prox_sent == n + 2, "and an empty report is not repeated"
        await a.stop(); await b.stop()
        await server.close()
    run(go(), 60)


def test_reconnects_and_resumes_after_the_server_restarts(tmp_path):
    async def go():
        hub = Hub()
        server = TestServer(make_app(hub))
        await server.start_server()
        port = server.port
        a = Endpoint("ws://127.0.0.1:%d/ws" % port, tmp_path, "Alice")
        a.client.rng.seed(1)
        a.start()
        await a.wait(lambda e: e["t"] == "_registered")
        n = len([e for e in a.events if e["t"] == "_registered"])
        await server.close()
        await a.wait(lambda e: e["t"] == "_down")
        server2 = TestServer(make_app(Hub()), port=port)
        await server2.start_server()
        await a.wait(lambda e: e["t"] == "_registered" and len([x for x in a.events if x["t"] == "_registered"]) > n, timeout=20)
        assert a.client.state == "registered"
        await a.stop()
        await server2.close()
    run(go(), 60)


def test_changing_identity_unregisters_the_old_name_and_registers_the_new(tmp_path):
    async def go():
        hub = Hub()
        server = TestServer(make_app(hub))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        a = Endpoint(url, tmp_path, "Alice")
        a.start()
        await a.wait(lambda e: e["t"] == "_registered" and e["sid"] == a.ident.sid)
        old = a.ident.sid
        a.client.set_identity(identity.load_or_create(a.home, REALM, "Altname", 9))     # the player switched character
        await a.wait(lambda e: e["t"] == "_registered" and e["sid"] != old)
        assert set(hub.by_sid) == {a.client.ident.sid} and old not in hub.by_sid
        assert [s.character for s in hub.by_sid.values()] == ["Altname"]
        a.client.set_identity(None)                                                     # logout
        await a.wait(lambda e: e["t"] == "_state" and e["state"] == "idle", timeout=10)
        await asyncio.sleep(0.3)
        assert hub.stats()["sessions"] == 0, "logging out leaves the roster immediately"
        await a.stop()
        await server.close()
    run(go(), 60)


def test_name_in_use_is_reported_not_fatal(tmp_path):
    async def go():
        server = TestServer(make_app(Hub()))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        a = Endpoint(url, tmp_path, "Alice")
        a.start()
        await a.wait(lambda e: e["t"] == "_registered")
        impostor = Endpoint(url, tmp_path, "Imp")
        impostor.start(name="Alice")                                   # same character, different key
        await impostor.wait(lambda e: e["t"] == "_state" and e["state"] == "backoff" and "name_in_use" in e["error"])
        assert a.client.state == "registered", "the real holder is untouched"
        await a.stop(); await impostor.stop()
        await server.close()
    run(go())


def test_join_code_is_sent(tmp_path):
    async def go():
        server = TestServer(make_app(Hub(join_code="s3cret")))
        await server.start_server()
        url = "ws://127.0.0.1:%d/ws" % server.port
        good, bad = Endpoint(url, tmp_path, "Alice", code="s3cret"), Endpoint(url, tmp_path, "Bobby", code="nope")
        good.start(); bad.start()
        await good.wait(lambda e: e["t"] == "_registered")
        await bad.wait(lambda e: e["t"] == "_state" and "bad_code" in e["error"])
        await good.stop(); await bad.stop()
        await server.close()
    run(go())


def test_signals_leave_in_the_order_they_were_queued(tmp_path):
    async def go():
        server, a, b = await two(tmp_path)
        a.client.report_proximity([("Bobby", 10.0)]); b.client.report_proximity([("Alice", 10.0)])
        await a.wait(lambda e: e["t"] == "peer_available")
        assert a.client.send_signal(b.ident.sid, "offer", 1, {"n": 0})
        for i in range(1, 60):
            assert a.client.send_signal(b.ident.sid, "ice", 1, {"n": i})
        await b.wait(lambda e: e["t"] == "ice" and e["data"]["n"] == 59)
        got = [e["data"]["n"] for e in b.events if e["t"] in ("offer", "ice")]
        assert got == list(range(60)), "an ICE candidate must never overtake the offer"
        await a.stop(); await b.stop()
        await server.close()
    run(go())


def test_old_server_protocol_is_refused_clearly(tmp_path):
    async def go():
        from aiohttp import web

        async def ws(request):
            sock = web.WebSocketResponse()
            await sock.prepare(request)
            await sock.send_json({"t": "hello", "v": 1, "nonce": "AAAA"})
            await asyncio.sleep(0.5)
            return sock
        app = web.Application()
        app.router.add_get("/ws", ws)
        server = TestServer(app)
        await server.start_server()
        a = Endpoint("ws://127.0.0.1:%d/ws" % server.port, tmp_path, "Alice")
        a.start()
        await a.wait(lambda e: e["t"] == "_state" and "protocol" in e.get("error", ""))
        await a.stop()
        await server.close()
    run(go())
