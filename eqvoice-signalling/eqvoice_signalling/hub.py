"""The EqVoice hub: who is online, who may talk to whom, and the abuse limits. Protocol v2.

Pure logic. A connection is any object with ``ip``, ``send(dict)`` and ``close(code, reason)``; time and
randomness are injected. The aiohttp wrapper in server.py, the Cloudflare port and the unit tests all
drive exactly this code.

What the hub holds, per realm, only while a session is connected:

    character name  <->  ephemeral session id  (plus a public key and a server-issued token)

What it never holds: accounts, e-mail, IP addresses in any message or roster, positions, history.

Roster. Every session receives the list of the other online sessions *of its own realm*: ``[name, sid]``
pairs. That list is what lets a game client look nearby players up by name. It exists only as long as the
sessions do.

Mutual proximity. Knowing a name is not enough to get a voice connection. Each client reports the players
its own game currently sees nearby and how far away they are: ``proximity: [[name, yards, ts], ...]``. The
hub links two sessions only when

    * A reports B and B reports A, both reports fresh (``report_ttl``);
    * the two distances agree within ``distance_tolerance`` (they measure the same two objects);
    * the closer of the two is within ``link_radius``.

It tells both sides with ``peer_available`` and, after a short grace (``leave_grace``), ``peer_out_of_range``.
Signalling (offer / answer / ice / bye) is relayed only between linked sessions.

Spoof resistance, honestly. Nothing here can prove that a client really is the character it names: only the
game server could. What this adds up to: a session id that is 128 random bits, a signature proving key
possession, a server-issued token for resuming, a name held by one live session at a time, mutual reports
with agreeing distances before any signalling is relayed, and rate limits everywhere.
"""
from __future__ import annotations

import base64
import hmac
import json
import math
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROTOCOL_VERSION = 2
SID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")                       # 16 random bytes, base64url
REALM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '.\-]{0,31}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
REG_CONTEXT = b"OV2-REG\x00"
SIGNAL_KINDS = ("offer", "answer", "ice", "bye")


def valid_name(name) -> bool:
    """A character name: 2-12 Unicode letters, nothing else (no digits, spaces, punctuation)."""
    return isinstance(name, str) and 2 <= len(name) <= 12 and name.isalpha()


def round1(x: float) -> float:
    """One decimal, halves rounded up. Spelled out so the JavaScript port rounds the same way as Python."""
    return math.floor(x * 10 + 0.5) / 10


def _no_constants(name):
    raise ValueError("JSON constant %s is not allowed" % name)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > 200:
        raise ValueError("bad base64")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def registration_message(nonce: bytes, sid: str, realm: str, character: str) -> bytes:
    """The exact bytes a client signs to register ``character`` on ``realm`` as ``sid``."""
    return (REG_CONTEXT + nonce + b"\x00" + sid.encode("ascii") + b"\x00" + realm.encode("utf-8")
            + b"\x00" + character.encode("utf-8"))


def realm_key(realm: str) -> str:
    return " ".join(realm.lower().split())


@dataclass
class Limits:
    max_msg_bytes: int = 16 * 1024
    max_signal_data_bytes: int = 8 * 1024
    bucket_capacity: float = 100.0
    bucket_rate: float = 50.0
    prox_capacity: float = 8.0           # proximity messages: burst
    prox_rate: float = 4.0               # ... and per second, sustained
    strikes_to_close: int = 5
    link_signals_per_minute: int = 120
    register_timeout: float = 10.0
    idle_timeout: float = 30.0           # clients ping every 10 s
    reserve_seconds: float = 30.0        # a dropped session's name stays reserved for its own key
    max_conns_per_ip: int = 20
    max_connections: int = 10000         # all addresses together: a ceiling on what a flood of cheap connections can cost
    registers_per_ip_per_minute: int = 12
    max_report_entries: int = 64
    max_distance: float = 500.0
    report_ttl: float = 6.0              # a proximity report older than this counts for nothing
    leave_grace: float = 3.0             # a link survives this long without agreeing reports
    distance_tolerance: float = 12.0     # yards; both ends sample the same pair at different instants, movement adds skew
    link_radius: float = 90.0            # yards; beyond this two sessions are never linked (a raised voice carries 85, plus the leave margin)
    realm_cap: int = 5000
    roster_chunk: int = 300


class Bucket:
    def __init__(self, capacity: float, rate: float, now: float):
        self.capacity, self.rate, self.tokens, self.t = capacity, rate, capacity, now

    def take(self, now: float) -> bool:
        self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


@dataclass(eq=False)
class Session:
    conn: object
    nonce: bytes
    born: float
    last_seen: float
    bucket: Bucket
    prox_bucket: Bucket
    sid: Optional[str] = None
    pub: Optional[bytes] = None
    realm: str = ""
    rkey: str = ""
    character: str = ""
    ckey: str = ""
    token: str = ""
    reports: dict = field(default_factory=dict)       # target ckey -> (distance, server time)
    links: set = field(default_factory=set)           # sids
    strikes: deque = field(default_factory=deque)
    link_budget: dict = field(default_factory=dict)
    closed: bool = False
    registered_at: float = 0.0


@dataclass
class Link:
    a: str
    b: str
    last_ok: float
    dist: float


@dataclass
class Reservation:
    pub: bytes
    sid: str
    token: str
    expires: float


class Hub:
    def __init__(self, limits: Optional[Limits] = None, clock: Callable[[], float] = time.monotonic,
                 rng: Callable[[int], bytes] = os.urandom, join_code: Optional[str] = None,
                 ice_servers: Optional[list] = None, wall_clock: Callable[[], float] = time.time,
                 trace: Optional[Callable[[str, dict], None]] = None):
        self.limits = limits or Limits()
        self.trace = trace                        # trace(event, fields): narration for debugging a deployment; off by default
        self.clock, self.rng, self.wall = clock, rng, wall_clock
        self.join_code = join_code
        self.ice_servers = ice_servers if ice_servers is not None else []
        self.states: dict = {}                    # id(conn) -> Session
        self.by_sid: dict = {}                    # sid -> Session
        self.realms: dict = {}                    # rkey -> {ckey -> Session}
        self.reserved: dict = {}                  # (rkey, ckey) -> Reservation
        self.links: dict = {}                     # frozenset({sidA, sidB}) -> Link
        self.reported_by: dict = {}               # (rkey, target ckey) -> {sid of reporters: None}, in insertion order
        self.ip_conns: dict = {}
        self.ip_regs: dict = {}
        self.counters = {"connections": 0, "registered": 0, "rejected": 0, "signals": 0, "rate_limited": 0,
                         "errors": 0, "links_up": 0, "proximity": 0, "roster_msgs": 0, "takeovers": 0}

    # ------------------------------------------------------------ plumbing

    def _t(self, event: str, **fields) -> None:
        """Narrate one event when a trace sink is attached. Fields may hold character names: keep it off in production."""
        if self.trace is not None:
            self.trace(event, fields)

    def _send(self, s: Session, msg: dict) -> None:
        if not s.closed:
            s.conn.send(msg)

    def _error(self, s: Session, code: str, text: str = "", ref=None) -> None:
        self.counters["errors"] += 1
        self._t("error", to=s.character or "(unregistered)", code=code, text=text)
        msg = {"t": "error", "code": code, "msg": text}
        if ref is not None:
            msg["ref"] = ref
        self._send(s, msg)

    def _kill(self, s: Session, code: int, reason: str, reserve: bool = True) -> None:
        if s.closed:
            return
        s.conn.close(code, reason)
        self.disconnect(s.conn, reserve=reserve)

    def _broadcast(self, rkey: str, msg: dict, exclude: Optional[Session] = None) -> None:
        for other in self.realms.get(rkey, {}).values():
            if other is not exclude:
                self._send(other, msg)
                self.counters["roster_msgs"] += 1

    # ------------------------------------------------------------ lifecycle

    def connect(self, conn) -> bool:
        """False when the caller should refuse the connection (global or per-IP cap)."""
        ip = conn.ip
        if len(self.states) >= self.limits.max_connections or self.ip_conns.get(ip, 0) >= self.limits.max_conns_per_ip:
            self.counters["rejected"] += 1
            return False
        self.ip_conns[ip] = self.ip_conns.get(ip, 0) + 1
        now, L = self.clock(), self.limits
        s = Session(conn=conn, nonce=self.rng(16), born=now, last_seen=now,
                    bucket=Bucket(L.bucket_capacity, L.bucket_rate, now),
                    prox_bucket=Bucket(L.prox_capacity, L.prox_rate, now))
        self.states[id(conn)] = s
        self.counters["connections"] += 1
        self._send(s, {
            "t": "hello", "v": PROTOCOL_VERSION, "nonce": b64u(s.nonce), "now": self.wall(),
            "join_code": bool(self.join_code), "ice": self.ice_servers,
            "limits": {"idle": L.idle_timeout, "report_ttl": L.report_ttl, "link_radius": L.link_radius,
                       "max_report": L.max_report_entries},
        })
        return True

    def disconnect(self, conn, reserve: bool = True) -> None:
        """``reserve=False`` (a clean ``unregister``) frees the name at once; a dropped connection keeps it
        reserved for its own key for ``reserve_seconds`` so a network blip cannot be exploited."""
        s = self.states.pop(id(conn), None)
        if s is None or s.closed:
            return
        s.closed = True
        ip = conn.ip
        self.ip_conns[ip] = max(0, self.ip_conns.get(ip, 1) - 1)
        if not self.ip_conns[ip]:
            del self.ip_conns[ip]
        if s.sid is None:
            return
        self._t("left", character=s.character, realm=s.realm, clean=not reserve)
        # links first, while the roster still lists this session
        for pk in [k for k in self.links if s.sid in k]:
            self._drop_link(pk, notify_both=True, why="a side disconnected")
        for key in [(s.rkey, ck) for ck in s.reports]:
            reporters = self.reported_by.get(key)
            if reporters is not None:
                reporters.pop(s.sid, None)
                if not reporters:
                    del self.reported_by[key]
        if self.by_sid.get(s.sid) is s:
            del self.by_sid[s.sid]
        realm = self.realms.get(s.rkey)
        if realm is not None and realm.get(s.ckey) is s:
            del realm[s.ckey]
            if not realm:
                del self.realms[s.rkey]
            self._broadcast(s.rkey, {"t": "roster_update", "remove": [s.sid]})
            self._t("roster_update", remove=s.character, sent_to=len(realm))
        if reserve:
            self.reserved[(s.rkey, s.ckey)] = Reservation(s.pub, s.sid, s.token, self.clock() + self.limits.reserve_seconds)

    def tick(self) -> None:
        now = self.clock()
        L = self.limits
        for s in list(self.states.values()):
            if s.sid is None and now - s.born > L.register_timeout:
                self._error(s, "register_timeout", "register within %ds" % L.register_timeout)
                self._kill(s, 4408, "register timeout")
            elif now - s.last_seen > L.idle_timeout:
                self._kill(s, 4408, "idle")
        for key in [k for k, r in self.reserved.items() if r.expires <= now]:
            del self.reserved[key]
        for ip in [i for i, q in self.ip_regs.items() if not q or now - q[-1] > 60]:
            del self.ip_regs[ip]
        for pk in list(self.links):
            link = self.links.get(pk)
            if link is None:
                continue
            a, b = self.by_sid.get(link.a), self.by_sid.get(link.b)
            if a is None or b is None:
                self._drop_link(pk, notify_both=True, why="a side is gone")
            else:
                self._evaluate(a, b, now)

    # ------------------------------------------------------------- dispatch

    def handle(self, conn, raw) -> None:
        s = self.states.get(id(conn))
        if s is None:
            return
        now = self.clock()
        s.last_seen = now
        if isinstance(raw, (bytes, bytearray)):
            self._error(s, "bad_msg", "text frames only")
            return
        if len(raw) > self.limits.max_msg_bytes:
            self._error(s, "too_big", "message too large")
            self._kill(s, 1009, "too big")
            return
        if not s.bucket.take(now):
            self.counters["rate_limited"] += 1
            s.strikes.append(now)
            while s.strikes and now - s.strikes[0] > 60:
                s.strikes.popleft()
            self._error(s, "rate_limited", "slow down")
            if len(s.strikes) >= self.limits.strikes_to_close:
                self._kill(s, 1008, "rate limit")
            return
        try:
            msg = json.loads(raw, parse_constant=_no_constants)
        except ValueError:
            self._error(s, "bad_json")
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
            self._error(s, "bad_msg", "need an object with a string 't'")
            return
        ref = msg.get("id") if isinstance(msg.get("id"), (int, str)) and not isinstance(msg.get("id"), bool) else None
        kind = msg["t"]
        if kind == "ping":
            pong = {"t": "pong", "now": self.wall()}
            if isinstance(msg.get("ts"), (int, float)) and not isinstance(msg.get("ts"), bool):
                pong["ts"] = msg["ts"]
            self._send(s, pong)
        elif kind == "register":
            self._register(s, msg, ref)
        elif s.sid is None:
            self._error(s, "not_registered", "register first", ref)
        elif kind == "proximity":
            self._proximity(s, msg, ref)
        elif kind in SIGNAL_KINDS:
            self._signal(s, kind, msg, ref)
        elif kind == "unregister":
            self._kill(s, 1000, "bye", reserve=False)
        else:
            self._error(s, "bad_msg", "unknown type", ref)

    # ------------------------------------------------------------- register

    def _register(self, s: Session, msg: dict, ref) -> None:
        L = self.limits
        if s.sid is not None:
            self._error(s, "already_registered", "reconnect to change identity", ref)
            return
        if msg.get("v") != PROTOCOL_VERSION:
            self._error(s, "unsupported_version", "speak v%d" % PROTOCOL_VERSION, ref)
            return
        now = self.clock()
        regs = self.ip_regs.setdefault(s.conn.ip, deque())
        while regs and now - regs[0] > 60:
            regs.popleft()
        if len(regs) >= L.registers_per_ip_per_minute:
            self._error(s, "ip_rate", "too many registrations from this address", ref)
            return
        regs.append(now)
        sid, realm, character = msg.get("sid"), msg.get("realm"), msg.get("character")
        if not isinstance(sid, str) or not SID_RE.match(sid):
            self._error(s, "bad_sid", "session id must be 22 url-safe base64 characters", ref)
            return
        if not isinstance(realm, str) or not REALM_RE.match(realm):
            self._error(s, "bad_realm", "malformed realm name", ref)
            return
        if not valid_name(character):
            self._error(s, "bad_name", "malformed character name", ref)
            return
        if self.join_code is not None:
            code = msg.get("code")
            if not isinstance(code, str) or not hmac.compare_digest(code.encode(), self.join_code.encode()):
                self._error(s, "bad_code", "wrong join code", ref)
                return
        try:
            pub = unb64u(msg.get("pub"))
            sig = unb64u(msg.get("sig"))
            if len(pub) != 32 or len(sig) != 64:
                raise ValueError("length")
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, registration_message(s.nonce, sid, realm, character))
        except (ValueError, InvalidSignature, TypeError):
            self._error(s, "bad_sig", "registration signature did not verify", ref)
            return
        rkey, ckey = realm_key(realm), character.lower()
        holder = self.realms.get(rkey, {}).get(ckey)
        existing = self.by_sid.get(sid)
        if existing is not None and existing is not holder:
            self._error(s, "sid_taken", "session id in use", ref)
            return
        if holder is not None:
            if holder.pub != pub:
                self._error(s, "name_in_use", "that character is already online", ref)
                return
            self.counters["takeovers"] += 1
            self._kill(holder, 4409, "replaced by a newer connection")          # same key reconnecting
        else:
            res = self.reserved.get((rkey, ckey))
            if res is not None and (res.pub != pub or (res.token and msg.get("token") != res.token)):
                self._error(s, "name_in_use", "that character was just online; retry shortly", ref)
                return
        realm_members = self.realms.setdefault(rkey, {})
        if len(realm_members) >= L.realm_cap:
            self._error(s, "realm_full", "too many sessions on this realm", ref)
            return
        self.reserved.pop((rkey, ckey), None)
        s.sid, s.pub, s.realm, s.rkey, s.character, s.ckey = sid, pub, realm, rkey, character, ckey
        s.token = b64u(self.rng(18))
        s.registered_at = now
        self.by_sid[sid] = s
        realm_members[ckey] = s
        self.counters["registered"] += 1
        roster = [[o.character, o.sid] for o in realm_members.values() if o is not s]
        first, rest = roster[:L.roster_chunk], roster[L.roster_chunk:]
        reply = {"t": "registered", "sid": sid, "token": s.token, "lease": L.reserve_seconds, "now": self.wall(),
                 "roster": first, "more": len(rest)}
        if ref is not None:
            reply["id"] = ref
        self._send(s, reply)
        self._t("registered", character=character, realm=realm, sid=sid[:8], roster=len(roster))
        self._t("roster_sent", to=character, entries=len(first), more=len(rest))
        while rest:
            self._send(s, {"t": "roster_update", "add": rest[:L.roster_chunk]})
            self.counters["roster_msgs"] += 1
            rest = rest[L.roster_chunk:]
        self._broadcast(rkey, {"t": "roster_update", "add": [[character, sid]]}, exclude=s)
        self._t("roster_update", add=character, sent_to=max(0, len(realm_members) - 1))
        # reports other sessions already hold about this name may now complete a pair
        for rep_sid in list(self.reported_by.get((rkey, ckey), {})):
            other = self.by_sid.get(rep_sid)
            if other is not None and other is not s:
                self._evaluate(s, other, now)

    # ------------------------------------------------------------ proximity

    def _proximity(self, s: Session, msg: dict, ref) -> None:
        now = self.clock()
        if not s.prox_bucket.take(now):
            self._error(s, "rate_limited", "proximity updates are limited", ref)
            return
        peers = msg.get("peers")
        L = self.limits
        if not isinstance(peers, list):
            self._error(s, "bad_msg", "peers must be a list", ref)
            return
        if len(peers) > L.max_report_entries:
            self._error(s, "too_many", "at most %d entries" % L.max_report_entries, ref)
            return
        new = {}
        for item in peers:
            if not isinstance(item, list) or len(item) < 2 or not valid_name(item[0]):
                self._error(s, "bad_msg", "entries are [name, yards, ts]", ref)
                return
            d = item[1]
            if isinstance(d, bool) or not isinstance(d, (int, float)) or d != d or d < 0 or d > L.max_distance:
                self._error(s, "bad_msg", "bad distance", ref)
                return
            if item[0].lower() == s.ckey:
                continue
            new[item[0].lower()] = (float(d), now)
        self.counters["proximity"] += 1
        self._t("proximity", frm=s.character, peers=",".join("%s:%.1f" % (item[0], float(item[1])) for item in peers) or "(none)")
        old = s.reports
        s.reports = new
        for ck in old:
            if ck not in new:
                reporters = self.reported_by.get((s.rkey, ck))
                if reporters is not None:
                    reporters.pop(s.sid, None)
                    if not reporters:
                        del self.reported_by[(s.rkey, ck)]
        for ck in new:
            self.reported_by.setdefault((s.rkey, ck), {})[s.sid] = None
        for ck in list(old) + [k for k in new if k not in old]:           # a fixed order: transcripts must repeat
            other = self.realms.get(s.rkey, {}).get(ck)
            if other is not None and other is not s:
                self._evaluate(s, other, now)

    def _evaluate(self, a: Session, b: Session, now: float) -> None:
        """Decide whether a and b are mutually near, and raise or lower their link."""
        L = self.limits
        ra, rb = a.reports.get(b.ckey), b.reports.get(a.ckey)
        ok = (ra is not None and rb is not None and now - ra[1] <= L.report_ttl and now - rb[1] <= L.report_ttl
              and abs(ra[0] - rb[0]) <= L.distance_tolerance and min(ra[0], rb[0]) <= L.link_radius)
        pk = frozenset((a.sid, b.sid))
        link = self.links.get(pk)
        if ok:
            dist = (ra[0] + rb[0]) / 2.0
            if link is None:
                self.links[pk] = Link(a.sid, b.sid, now, dist)
                a.links.add(b.sid)
                b.links.add(a.sid)
                self.counters["links_up"] += 1
                self._t("link_up", a=a.character, b=b.character, distance=round1(dist), reported="%.1f/%.1f" % (ra[0], rb[0]))
                self._send(a, {"t": "peer_available", "sid": b.sid, "character": b.character, "distance": round1(dist)})
                self._send(b, {"t": "peer_available", "sid": a.sid, "character": a.character, "distance": round1(dist)})
            else:
                link.last_ok, link.dist = now, dist
        elif link is not None and now - link.last_ok >= L.leave_grace:
            self._drop_link(pk, notify_both=True, why=self._why_not(ra, rb, now))

    def _why_not(self, ra, rb, now: float) -> str:
        L = self.limits
        if ra is None or rb is None:
            return "only one side reports the other"
        if now - ra[1] > L.report_ttl or now - rb[1] > L.report_ttl:
            return "a report went stale"
        if abs(ra[0] - rb[0]) > L.distance_tolerance:
            return "the two distances disagree (%.1f vs %.1f)" % (ra[0], rb[0])
        return "farther than %.0f yd (%.1f / %.1f)" % (L.link_radius, ra[0], rb[0])

    def _drop_link(self, pk, notify_both: bool, why: str = "") -> None:
        link = self.links.pop(pk, None)
        if link is None:
            return
        a, b = self.by_sid.get(link.a), self.by_sid.get(link.b)
        self._t("link_down", a=a.character if a else "?", b=b.character if b else "?", why=why)
        if a is not None:
            a.links.discard(link.b)
        if b is not None:
            b.links.discard(link.a)
        if notify_both:
            if a is not None and b is not None:
                self._send(a, {"t": "peer_out_of_range", "sid": b.sid, "character": b.character})
                self._send(b, {"t": "peer_out_of_range", "sid": a.sid, "character": a.character})
            elif a is not None:
                self._send(a, {"t": "peer_out_of_range", "sid": link.b, "character": ""})
            elif b is not None:
                self._send(b, {"t": "peer_out_of_range", "sid": link.a, "character": ""})

    # --------------------------------------------------------------- signal

    def _signal(self, s: Session, kind: str, msg: dict, ref) -> None:
        to, data, ep = msg.get("to"), msg.get("data"), msg.get("ep", 0)
        if not isinstance(to, str) or not isinstance(ep, int) or isinstance(ep, bool) or not (0 <= ep < 2 ** 31):
            self._error(s, "bad_msg", "bad signal", ref)
            return
        # Same answer for "offline" and "not nearby", so this cannot be used to find out who is online.
        if to not in s.links:
            self._error(s, "not_linked", "peer unavailable", ref)
            return
        if len(json.dumps(data, separators=(",", ":"))) > self.limits.max_signal_data_bytes:
            self._error(s, "too_big", "signal payload too large", ref)
            return
        now = self.clock()
        q = s.link_budget.setdefault(to, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.limits.link_signals_per_minute:
            self._error(s, "rate_limited", "too many signals to this peer", ref)
            return
        q.append(now)
        peer = self.by_sid.get(to)
        if peer is None:
            self._error(s, "not_linked", "peer unavailable", ref)
            return
        self.counters["signals"] += 1
        self._t("signal", kind=kind, frm=s.character, to=peer.character, ep=ep)
        self._send(peer, {"t": kind, "from": s.sid, "ep": ep, "data": data})

    # ----------------------------------------------------------------- misc

    def stats(self) -> dict:
        return {
            "connections": len(self.states),
            "sessions": len(self.by_sid),
            "realms": len(self.realms),
            "largest_realm": max((len(r) for r in self.realms.values()), default=0),
            "links": len(self.links),
            "reserved": len(self.reserved),
            "totals": dict(self.counters),
        }
