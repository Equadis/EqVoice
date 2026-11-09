"""Conformance: recorded conversations that every implementation of the hub must reproduce message for message.

The Python hub is the reference. A scenario is a list of steps (connect, send, advance, disconnect, ...). The
generator (tools/gen_conformance.py) runs each scenario against the Python hub and records what every
connection received; protocol/conformance.json then holds steps and expected transcripts. The Python tests and
the JavaScript hub (eqvoice-signalling-cf/src/hub.js, run in headless Chromium) replay the same steps and must
produce the same transcripts.

Everything that could differ between runs is pinned: the clock is manual, the random source is a fixed
xorshift stream (JavaScript implements the same one), Ed25519 signatures are deterministic, keys and session
ids derive from names.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eqvoice_signalling.hub import Hub, Limits, b64u, registration_message, unb64u

_UNSET = object()
WALL = 1.7e9
CLOCK0 = 1000.0


def make_rng(seed: int = 0x9E3779B9):
    """xorshift32, one output byte per step: trivially identical in Python and JavaScript."""
    state = [seed]

    def rng(n: int) -> bytes:
        out = bytearray()
        for _ in range(n):
            x = state[0]
            x ^= (x << 13) & 0xFFFFFFFF
            x ^= x >> 17
            x ^= (x << 5) & 0xFFFFFFFF
            state[0] = x
            out.append((x >> 24) & 0xFF)
        return bytes(out)
    return rng


def key_for(name: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("key:" + name).encode()).digest())


def pub_of(key: Ed25519PrivateKey) -> str:
    return b64u(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def sid_for(name: str) -> str:
    return b64u(hashlib.sha256(("sid:" + name).encode()).digest()[:16])


class Conn:
    def __init__(self, name: str, ip: str, owner: "Replayer"):
        self.name, self.ip, self.owner = name, ip, owner

    def send(self, msg: dict) -> None:
        self.owner.events.append([self.name, msg])

    def close(self, code: int, reason: str) -> None:
        self.owner.events.append([self.name, "close", code, reason])


class Replayer:
    """Applies steps to a Python Hub and collects what happened."""

    def __init__(self, setup: dict):
        self.now = CLOCK0
        limits = Limits(**setup.get("limits", {}))
        self.hub = Hub(limits=limits, clock=lambda: self.now, rng=make_rng(), join_code=setup.get("join_code"),
                       ice_servers=setup.get("ice", []), wall_clock=lambda: WALL)
        self.conns: dict = {}
        self.events: list = []
        self.hello: dict = {}

    def apply(self, step: dict) -> list:
        self.events = []
        op = step["op"]
        if op == "connect":
            c = Conn(step["conn"], step.get("ip", "10.0.0.1"), self)
            self.conns[step["conn"]] = c
            if not self.hub.connect(c):
                self.events.append([c.name, "refused"])
            hello = next((e[1] for e in self.events if len(e) == 2 and isinstance(e[1], dict) and e[1].get("t") == "hello"), None)
            if hello:
                self.hello[c.name] = hello
        elif op == "send":
            self.hub.handle(self.conns[step["conn"]], step["text"])
        elif op == "rawbytes":
            self.hub.handle(self.conns[step["conn"]], bytes.fromhex(step["hex"]))
        elif op == "advance":
            end = self.now + step["seconds"]
            while self.now < end - 1e-9:
                self.now += min(step.get("step", 1.0), end - self.now)
                self.hub.tick()
        elif op == "disconnect":
            self.hub.disconnect(self.conns[step["conn"]], step.get("reserve", True))
        else:
            raise ValueError("unknown op " + op)
        return self.events


def replay(doc: dict) -> dict:
    r = Replayer(doc["setup"])
    transcript = [r.apply(s) for s in doc["steps"]]
    return {"transcript": transcript, "stats": r.hub.stats()}


class Recorder:
    """The scenario DSL. Each call runs the step against a live Python hub (the nonce is needed to sign) and
    records it in fully expanded, literal form."""

    def __init__(self, name: str, limits: Optional[dict] = None, join_code: Optional[str] = None, ice: Optional[list] = None):
        self.name = name
        self.setup = {"limits": limits or {}, "join_code": join_code, "ice": ice or []}
        self.steps: list = []
        self.live = Replayer(self.setup)

    def _do(self, step: dict) -> list:
        self.steps.append(step)
        return self.live.apply(step)

    def connect(self, c: str, ip: str = "10.0.0.1"):
        return self._do({"op": "connect", "conn": c, "ip": ip})

    def send(self, c: str, msg) -> list:
        return self._do({"op": "send", "conn": c, "text": json.dumps(msg, separators=(",", ":"))})

    def raw(self, c: str, text: str) -> list:
        return self._do({"op": "send", "conn": c, "text": text})

    def rawbytes(self, c: str, data: bytes) -> list:
        return self._do({"op": "rawbytes", "conn": c, "hex": data.hex()})

    def advance(self, seconds: float, step: float = 1.0):
        return self._do({"op": "advance", "seconds": seconds, "step": step})

    def disconnect(self, c: str, reserve: bool = True):
        return self._do({"op": "disconnect", "conn": c, "reserve": reserve})

    def register(self, c: str, name, *, sid=_UNSET, realm="Testrealm", key: Optional[str] = None,
                 token: Optional[str] = None, code=None, **override) -> list:
        """Sign a registration with the key named ``key`` (default: the character) over this connection's nonce.

        Any field may be replaced with nonsense (``sid=12345``): the signature then covers the sensible value,
        so the hub's field checks, not its signature check, are what get exercised."""
        nonce = unb64u(self.live.hello[c]["nonce"])
        character = override.pop("character", name)
        default_sid = sid_for(c + ":" + name)
        msg_sid = default_sid if sid is _UNSET else sid
        k = key_for(key or name)

        def signed(value, fallback):
            return value if isinstance(value, str) else fallback
        sign_char = override.pop("sign_character", signed(character, name))
        sign_realm = override.pop("sign_realm", signed(realm, "Testrealm"))
        sign_sid = override.pop("sign_sid", signed(msg_sid, default_sid))
        sign_nonce = override.pop("sign_nonce", nonce)
        msg = {"t": "register", "v": 2, "sid": msg_sid, "realm": realm, "character": character, "pub": pub_of(k)}
        msg["sig"] = b64u(k.sign(registration_message(sign_nonce, sign_sid, sign_realm, sign_char)))
        if token is not None:
            msg["token"] = token
        if code is not None:
            msg["code"] = code
        msg.update(override)
        return self.send(c, msg)

    def doc(self) -> dict:
        d = {"name": self.name, "setup": self.setup, "steps": self.steps}
        d.update(replay(d))
        return d

    # --- conveniences used by many scenarios

    def token_of(self, events: list) -> Optional[str]:
        for e in events:
            if len(e) == 2 and isinstance(e[1], dict) and e[1].get("t") == "registered":
                return e[1]["token"]
        return None
