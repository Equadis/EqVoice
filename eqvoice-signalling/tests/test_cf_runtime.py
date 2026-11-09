"""The Cloudflare hub on the REAL Workers runtime (workerd), over real WebSockets. Opt-in:

    cd eqvoice-signalling-cf && npx wrangler dev --local --port 8787      (leave it running)
    EQVOICE_CF_URL=ws://127.0.0.1:8787/ws python -m pytest eqvoice-signalling/tests/test_cf_runtime.py

The same file works against a deployed Worker (wss://...workers.dev/ws), but then the address-cap test opens 21
sockets from your address, so leave it out there (-k "not cap"). Every test uses its own realm.

What this covers that the mock-runtime test cannot: that workerd really accepts the adapter's close codes,
hands `webSocketMessage` / `webSocketClose` what the adapter expects, rebuilds sessions from attachments after a
real hibernation, runs alarms, and verifies Ed25519 signatures with its own WebCrypto.
"""
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

import aiohttp
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eqvoice_signalling.hub import b64u, registration_message, unb64u  # noqa: E402

URL = os.environ.get("EQVOICE_CF_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set EQVOICE_CF_URL to a running wrangler dev / Worker")


def run(coro, timeout=120):
    return asyncio.run(asyncio.wait_for(coro, timeout))


class Raw:
    """One client socket, no protocol niceties: the test decides what to send and when."""

    def __init__(self, session):
        self.session, self.ws, self.msgs, self.key = session, None, [], None
        self.sid = b64u(secrets.token_bytes(16))

    async def open(self):
        self.ws = await self.session.ws_connect(URL, heartbeat=None)
        self.pump = asyncio.create_task(self._pump())
        self.hello = await self.wait(lambda m: m.get("t") == "hello", 10)
        return self

    async def _pump(self):
        try:
            async for m in self.ws:
                if m.type == aiohttp.WSMsgType.TEXT:
                    self.msgs.append(json.loads(m.data))
        except Exception:                                      # noqa: BLE001
            pass

    async def wait(self, pred, timeout=10.0):
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            for m in self.msgs:
                if pred(m):
                    self.msgs.remove(m)
                    return m
            await asyncio.sleep(0.02)
        raise AssertionError("timed out; unseen messages: %r; close code %r" % (self.msgs[-5:], self.ws.close_code))

    async def send(self, msg):
        await self.ws.send_str(msg if isinstance(msg, str) else json.dumps(msg, separators=(",", ":")))

    async def register(self, name, realm, key=None):
        self.key = key or Ed25519PrivateKey.generate()
        pub = b64u(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        nonce = unb64u(self.hello["nonce"])
        sig = b64u(self.key.sign(registration_message(nonce, self.sid, realm, name)))
        await self.send({"t": "register", "v": 2, "sid": self.sid, "realm": realm, "character": name, "pub": pub, "sig": sig})
        self.name, self.realm = name, realm
        return await self.wait(lambda m: m.get("t") in ("registered", "error"), 10)

    async def closed(self, timeout=30.0):
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            if self.ws.closed:
                return self.ws.close_code
            await asyncio.sleep(0.05)
        raise AssertionError("socket never closed; messages %r" % self.msgs[-5:])

    async def drop(self):
        self.pump.cancel()
        await self.ws.close()


def realm():
    return "Rt" + secrets.token_hex(4)


def letters(n=6):
    return "".join(chr(97 + secrets.randbelow(26)) for _ in range(n)).capitalize()


def test_register_roster_link_relay_and_leave():
    async def go():
        async with aiohttp.ClientSession() as s:
            r = realm()
            a, b = await Raw(s).open(), await Raw(s).open()
            assert a.hello["v"] == 2 and a.hello["ice"], a.hello
            na, nb = letters(), letters()
            ra = await a.register(na, r)
            assert ra["t"] == "registered" and ra["roster"] == [], ra
            rb = await b.register(nb, r)
            assert rb["roster"] == [[na, a.sid]], rb
            up = await a.wait(lambda m: m.get("t") == "roster_update")
            assert up["add"] == [[nb, b.sid]]
            await a.send({"t": "proximity", "peers": [[nb, 18.0, 1]]})
            await b.send({"t": "proximity", "peers": [[na, 19.0, 1]]})
            pa = await a.wait(lambda m: m.get("t") == "peer_available")
            await b.wait(lambda m: m.get("t") == "peer_available")
            assert pa["distance"] == 18.5 and pa["sid"] == b.sid
            await a.send({"t": "offer", "to": b.sid, "ep": 3, "data": {"sdp": "x"}})
            got = await b.wait(lambda m: m.get("t") == "offer")
            assert got == {"t": "offer", "from": a.sid, "ep": 3, "data": {"sdp": "x"}}
            await a.send({"t": "unregister"})
            code = await a.closed(10)
            assert code == 1000
            gone = await b.wait(lambda m: m.get("t") == "roster_update" and m.get("remove"))
            assert gone["remove"] == [a.sid]
            await b.wait(lambda m: m.get("t") == "peer_out_of_range")
            await b.drop()
    run(go())


def test_wrong_signature_and_a_malformed_name_are_refused():
    async def go():
        async with aiohttp.ClientSession() as s:
            a = await Raw(s).open()
            a.key = Ed25519PrivateKey.generate()
            pub = b64u(a.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
            sig = b64u(a.key.sign(b"not the registration message"))
            await a.send({"t": "register", "v": 2, "sid": a.sid, "realm": realm(), "character": letters(), "pub": pub, "sig": sig})
            assert (await a.wait(lambda m: m.get("t") == "error"))["code"] == "bad_sig"
            r = await a.register("Al1ce", realm())
            assert r["t"] == "error" and r["code"] == "bad_name"
            await a.drop()
    run(go())


def test_the_same_key_takes_over_and_another_key_is_refused():
    async def go():
        async with aiohttp.ClientSession() as s:
            r, name = realm(), letters()
            a = await Raw(s).open()
            ra = await a.register(name, r)
            assert ra["t"] == "registered"
            imp = await Raw(s).open()
            assert (await imp.register(name, r))["code"] == "name_in_use"
            again = await Raw(s).open()
            again.sid = b64u(secrets.token_bytes(16))
            rr = await again.register(name, r, key=a.key)
            assert rr["t"] == "registered", rr
            assert await a.closed(10) == 4409, "the old socket is told it was replaced, with a code Workers may send"
            await again.drop(); await imp.drop()
    run(go())


def test_an_oversize_message_closes_with_a_legal_code():
    async def go():
        async with aiohttp.ClientSession() as s:
            a = await Raw(s).open()
            await a.send("x" * 17000)
            assert (await a.wait(lambda m: m.get("t") == "error"))["code"] == "too_big"
            assert await a.closed(10) == 4009            # the hub's 1009, mapped into the range a Worker may send
    run(go())


def test_flooding_gets_rate_limit_errors_then_a_close():
    async def go():
        async with aiohttp.ClientSession() as s:
            a = await Raw(s).open()
            for _ in range(400):
                await a.send({"t": "ping"})
            code = await a.closed(15)
            assert code == 4008
            assert any(m.get("code") == "rate_limited" for m in a.msgs)
    run(go())


def test_a_socket_that_never_registers_is_closed_by_the_alarm():
    async def go():
        async with aiohttp.ClientSession() as s:
            a = await Raw(s).open()
            code = await a.closed(40)
            assert code == 4408
            assert any(m.get("code") == "register_timeout" for m in a.msgs)
    run(go(), 60)


def test_sessions_survive_the_object_hibernating():
    """Two silent clients for 25 s: the Durable Object is evicted and restarted from the sockets' attachments."""
    async def go():
        async with aiohttp.ClientSession() as s:
            r = realm()
            a, b = await Raw(s).open(), await Raw(s).open()
            na, nb = letters(), letters()
            assert (await a.register(na, r))["t"] == "registered"
            assert (await b.register(nb, r))["t"] == "registered"
            await asyncio.sleep(25)
            late = await Raw(s).open()
            nl = letters()
            rl = await late.register(nl, r)
            assert sorted(x[0] for x in rl["roster"]) == sorted([na, nb]), "the roster was rebuilt: %r" % rl
            await a.send({"t": "proximity", "peers": [[nb, 12.0, 1]]})
            await b.send({"t": "proximity", "peers": [[na, 12.0, 1]]})
            await a.wait(lambda m: m.get("t") == "peer_available")
            await a.send({"t": "offer", "to": b.sid, "ep": 1, "data": {}})
            await b.wait(lambda m: m.get("t") == "offer")
            imp = await Raw(s).open()
            assert (await imp.register(na, r))["code"] == "name_in_use", "a restored name is still held"
            for x in (a, b, late, imp):
                await x.drop()
    run(go(), 120)


@pytest.mark.skipif(os.environ.get("EQVOICE_CF_SKIP_CAP") == "1", reason="would open 21 sockets from this address")
def test_the_per_address_cap_refuses_the_extra_connection():
    async def go():
        async with aiohttp.ClientSession() as s:
            socks = []
            try:
                for _ in range(20):
                    socks.append(await Raw(s).open())
                extra = Raw(s)
                extra.ws = await s.ws_connect(URL, heartbeat=None)
                extra.pump = asyncio.create_task(extra._pump())
                assert await extra.closed(10) == 4013
            finally:
                for x in socks:
                    await x.drop()
    run(go())
