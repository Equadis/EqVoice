"""Shared test scaffolding: a hub, a pretend game world, and any number of clients with scripted or real media pages.

Every scenario built on this walks the full pipeline: game -> bridge file -> client -> hub -> peers -> media.
"""
import asyncio
import json
import secrets

import aiohttp
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eqvoice_client.app import App
from eqvoice_client.bridge.sim import SimGame, SimSource, SimWorld
from eqvoice_client.config import Config
from eqvoice_signalling.hub import Hub, Limits, b64u, registration_message, unb64u
from eqvoice_signalling.server import make_app

TOKEN = "testtoken"
REALM = "Testrealm"
FAR = 300.0                       # nobody is loaded this far away


def run(coro, timeout=60):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def make_cfg(home, url, character, **bridge):
    b = {"source": "sim", "stale_s": 1.0, "hold_s": 3.0, "ghost_s": 6.0}
    b.update(bridge)
    return Config.load(home, {
        "signalling_url": url,
        "ui": {"port": 0, "token": TOKEN, "open_browser": False},
        "bridge": b,
        "sim": {"character": character, "realm": REALM},
        "roster": {"refresh_s": 1.0, "scan_interval_s": 0.1},
        "proximity": dict(debounce_s=0.3, leave_grace_s=0.6, teardown_s=2.0, connect_timeout_s=6.0,
                          retry_backoff_s=[1], forget_s=5, fail_closed_s=3.0),
        "audio": {"mode": "ptt", "keyboard_filter": False},          # the fake microphone beeps; a 30 ms beep is what the keyboard filter drops
    })


class FakePage:
    """Acts like the browser page: records commands, answers `open` like a working WebRTC stack would."""

    def __init__(self, app, auto=True, ua="FakePage/1", role=""):
        self.app, self.auto, self.ua, self.role = app, auto, ua, role      # role: "engine", "ui" or nothing, as the real page says in its hello
        self.cmds, self.states, self.logs, self.reports = [], [], [], []
        self.cal_results = []                    # what the app told the window about a microphone setup
        self.cal_progress = []                   # and its live level, ten times a second, while the setup runs
        self.ws = None
        self.task = None

    async def connect(self, session):
        self.ws = await session.ws_connect("http://127.0.0.1:%d/ws/ui?token=%s" % (self.app.port, TOKEN),
                                           headers={"Origin": "http://127.0.0.1:%d" % self.app.port})
        await self.ws.send_json({"t": "hello", "ua": self.ua, "role": self.role})
        self.task = asyncio.create_task(self._read())

    async def _read(self):
        async for m in self.ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            d = json.loads(m.data)
            if d["t"] == "state":
                self.states.append(d)
                self.states = self.states[-5:]
            elif d["t"] == "log":
                self.logs.append(d["line"])
            elif d["t"] == "report":
                self.reports.append(d)
            elif d["t"] == "cal_result":
                self.cal_results.append(d)
            elif d["t"] == "cal_progress":
                self.cal_progress.append(d)
            elif d["t"] == "cmd":
                self.cmds.append(d["cmd"])
                if self.auto:
                    await self._auto(d["cmd"])

    async def _auto(self, c):
        if c["cmd"] == "open" and c["initiator"]:
            await self.ws.send_json({"t": "peer", "sid": c["sid"], "ep": c["ep"], "type": "description",
                                     "sdp_type": "offer", "sdp": "v=0 fake offer"})
        elif c["cmd"] == "remote" and c["type"] == "offer":
            await self.ws.send_json({"t": "peer", "sid": c["sid"], "ep": c["ep"], "type": "description",
                                     "sdp_type": "answer", "sdp": "v=0 fake answer"})
            await self.ws.send_json({"t": "peer", "sid": c["sid"], "ep": c["ep"], "type": "state",
                                     "ice": "connected", "conn": "connected"})
        elif c["cmd"] == "remote" and c["type"] == "answer":
            await self.ws.send_json({"t": "peer", "sid": c["sid"], "ep": c["ep"], "type": "state",
                                     "ice": "connected", "conn": "connected"})

    def named(self, name):
        return [c for c in self.cmds if c["cmd"] == name]

    def last_gain(self):
        """The newest gain command, or an empty stand-in (value/send None) before there is one."""
        g = self.named("gain")
        return g[-1] if g else {"value": None, "send": None}

    async def close(self):
        await self.ws.close()
        self.task.cancel()


async def until(pred, timeout=10.0, what=""):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        v = pred()
        if v:
            return v
        await asyncio.sleep(0.03)
    raise AssertionError("timed out waiting for " + what)


def pstate(app, name):
    for p in app.peers.snapshot():
        if p["name"] == name:
            return p["state"]
    return None


class Scene:
    """A hub, a pretend game world, and any number of clients."""

    def __init__(self, tmp_path, limits=None, ice_servers=None, hub_url=None, realm=None, **hub_kw):
        """``hub_url``: use a hub that is already running (wrangler dev, a Worker) instead of an in-process one; tests
        that inspect ``sc.hub`` cannot run that way. ``realm`` keeps runs apart on a hub that outlives them."""
        self.tmp = tmp_path
        self.hub_url, self.realm = hub_url, realm or REALM
        if ice_servers is None:
            ice_servers = [{"urls": ["stun:example.org"]}]
        self.hub = None if hub_url else Hub(limits=limits or Limits(leave_grace=1.0), ice_servers=ice_servers, **hub_kw)
        self.world = SimWorld()
        self.server = None
        self.apps, self.games, self.pages = {}, {}, {}
        self.session = None

    @property
    def url(self):
        return self.hub_url or "ws://127.0.0.1:%d/ws" % self.server.port

    async def __aenter__(self):
        if not self.hub_url:
            self.server = TestServer(make_app(self.hub))
            await self.server.start_server()
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        for p in self.pages.values():
            try:
                await p.close()
            except Exception:                                   # noqa: BLE001
                pass
        for a in self.apps.values():
            await a.stop()
        await self.session.close()
        if self.server is not None:
            await self.server.close()

    async def add(self, name, at=None, page=True, login_id=None, real_browser=False, cfg=None, **bridge):
        """Add a client. ``page`` adds a scripted media page; ``real_browser`` launches headless Chromium instead."""
        x, y = at if at is not None else (FAR * (len(self.apps) + 1), 0.0)
        self.world.add(name, x, y)
        game = SimGame(self.world, name, self.realm, self.tmp / name / "game", login_id=login_id or 0x1000 + len(self.apps),
                       interval=0.1)
        config = make_cfg(self.tmp / name, self.url, name, **bridge)
        config.sim.realm = self.realm
        for k, v in (cfg or {}).items():
            config[k].update(v) if isinstance(v, dict) else config.__setitem__(k, v)
        if real_browser:
            app = App(config, SimSource(game, poll_hz=20), launch_browser=True, headless=True, fake_media=True, mute_audio=True)
        else:
            app = App(config, SimSource(game, poll_hz=20), launch_browser=False)
        await app.start()
        self.apps[name], self.games[name] = app, game
        if page and not real_browser:
            pg = FakePage(app)
            await pg.connect(self.session)
            self.pages[name] = pg
        return app

    def move(self, name, x, y=0.0):
        self.world.move(name, x, y)

    def vanish(self, name):
        """Out of everyone's lookup range (as if they teleported or logged out of the area)."""
        self.world.move(name, 9000.0, 9000.0)

    def walk_to(self, a, b, yards):
        """Put ``b`` ``yards`` east of ``a``."""
        ax, ay = self.world.chars[a]
        self.world.move(b, ax + yards, ay)




# ------------------------------------------------------------- phantom sessions

class PhantomConn:
    def __init__(self, ip):
        self.ip, self.out, self.closed = ip, [], None

    def send(self, msg):
        self.out.append(msg)

    def close(self, code, reason):
        self.closed = (code, reason)


def phantoms(hub, n, prefix="Ph"):
    """Register ``n`` extra online characters directly on the hub (no sockets)."""
    def name(i):
        return prefix + "".join(chr(97 + (i // 26 ** k) % 26) for k in range(3))
    conns = []
    for i in range(n):
        c = PhantomConn("10.1.%d.%d" % (i // 250, i % 250 + 1))
        assert hub.connect(c)
        hello = c.out.pop(0)
        key = Ed25519PrivateKey.generate()
        sid = b64u(secrets.token_bytes(16))
        pub = b64u(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        sig = b64u(key.sign(registration_message(unb64u(hello["nonce"]), sid, REALM, name(i))))
        hub.handle(c, json.dumps({"t": "register", "v": 2, "sid": sid, "realm": REALM, "character": name(i), "pub": pub, "sig": sig}))
        assert any(m["t"] == "registered" for m in c.out), c.out
        conns.append(c)
    return conns

