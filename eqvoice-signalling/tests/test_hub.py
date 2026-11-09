"""Hub v2: sessions, roster, mutual proximity. No sockets: a fake clock, fake connections, real signatures."""
import json
import secrets
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from eqvoice_signalling.hub import Hub, Limits, b64u, registration_message, unb64u

REALM = "Testrealm"


class FakeConn:
    def __init__(self, ip="10.0.0.1"):
        self.ip = ip
        self.out = []
        self.closed = None

    def send(self, msg):
        self.out.append(msg)

    def close(self, code, reason):
        self.closed = (code, reason)

    def take(self, t=None):
        got = [m for m in self.out if t is None or m["t"] == t]
        self.out = [m for m in self.out if not (t is None or m["t"] == t)]
        return got


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Client:
    """One simulated desktop client."""

    def __init__(self, world, name, realm=REALM, key=None, ip="10.0.0.1", sid=None, token=None, register=True, code=None):
        self.world, self.name, self.realm = world, name, realm
        self.key = key or Ed25519PrivateKey.generate()
        self.sid = sid or secrets.token_urlsafe(16)
        self.token = token
        self.conn = world.conn(ip)
        self.hello = self.conn.take("hello")[0]
        self.reply = None
        if register:
            self.register(code=code)

    @property
    def pub(self):
        return b64u(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def register(self, code=None, **override):
        nonce = unb64u(self.hello["nonce"])
        msg = {"t": "register", "v": 2, "sid": self.sid, "realm": self.realm, "character": self.name, "pub": self.pub,
               "sig": b64u(self.key.sign(registration_message(nonce, self.sid, self.realm, self.name)))}
        if self.token:
            msg["token"] = self.token
        if code is not None:
            msg["code"] = code
        msg.update(override)
        self.world.send(self.conn, msg)
        regs = self.conn.take("registered")
        self.reply = regs[0] if regs else None
        if self.reply:
            self.token = self.reply["token"]
        return self.reply

    def send(self, msg):
        self.world.send(self.conn, msg)

    def report(self, *entries):
        self.send({"t": "proximity", "peers": [list(e) for e in entries]})

    def take(self, t=None):
        return self.conn.take(t)

    def errors(self):
        return [m["code"] for m in self.conn.take("error")]


class World:
    def __init__(self, **kw):
        self.clock = Clock()
        self.hub = Hub(clock=self.clock, wall_clock=lambda: 1.7e9, **kw)

    def conn(self, ip="10.0.0.1"):
        c = FakeConn(ip)
        assert self.hub.connect(c)
        return c

    def send(self, c, msg):
        self.hub.handle(c, msg if isinstance(msg, (str, bytes)) else json.dumps(msg))

    def advance(self, seconds, step=1.0):
        end = self.clock.t + seconds
        while self.clock.t < end - 1e-9:
            self.clock.t += min(step, end - self.clock.t)
            self.hub.tick()


def pair_up(w, a, b, da=20.0, db=21.0):
    """Both report each other; returns when a link exists."""
    a.report((b.name, da, 1))
    b.report((a.name, db, 1))


# 1 ----------------------------------------------------------------- registration

def test_hello_and_registration():
    w = World(ice_servers=[{"urls": ["stun:x"]}])
    a = Client(w, "Alice")
    h = a.hello
    assert h["v"] == 2 and len(unb64u(h["nonce"])) == 16 and h["ice"] == [{"urls": ["stun:x"]}]
    assert h["limits"]["link_radius"] == 90.0 and h["join_code"] is False
    r = a.reply
    assert r["sid"] == a.sid and len(r["token"]) >= 20 and r["lease"] == 30.0 and r["roster"] == [] and r["more"] == 0
    assert w.hub.stats()["sessions"] == 1


@pytest.mark.parametrize("field,value,code", [
    ("sid", "short", "bad_sid"), ("sid", "A" * 21 + "!", "bad_sid"), ("sid", 12345, "bad_sid"), ("sid", None, "bad_sid"),
    ("realm", "", "bad_realm"), ("realm", "x" * 40, "bad_realm"), ("realm", "bad/realm", "bad_realm"), ("realm", 5, "bad_realm"),
    ("character", "A", "bad_name"), ("character", "Thirteenchars", "bad_name"), ("character", "Al1ce", "bad_name"),
    ("character", "Bob Smith", "bad_name"), ("character", "x;DROP", "bad_name"), ("character", "", "bad_name"), ("character", 7, "bad_name"),
    ("v", 1, "unsupported_version"), ("v", None, "unsupported_version"),
])
def test_malformed_registration_is_refused(field, value, code):
    w = World()
    a = Client(w, "Alice", register=False)
    a.register(**{field: value})
    assert a.errors() == [code]
    assert w.hub.stats()["sessions"] == 0


def test_signature_must_match_every_field():
    w = World()
    a = Client(w, "Alice", register=False)
    nonce = unb64u(a.hello["nonce"])
    for sig_over in [(a.sid, REALM, "Bobby"), (a.sid, "Otherrealm", "Alice"), (secrets.token_urlsafe(16), REALM, "Alice")]:
        a.register(sig=b64u(a.key.sign(registration_message(nonce, *sig_over))))
        assert a.errors() == ["bad_sig"]
    other = Ed25519PrivateKey.generate()
    a.register(sig=b64u(other.sign(registration_message(nonce, a.sid, REALM, "Alice"))))
    assert a.errors() == ["bad_sig"], "signed with another key"
    a.register(pub="!!!", sig="!!!")
    assert a.errors() == ["bad_sig"]
    assert a.register() is not None, "and the honest registration still works"


def test_registration_cannot_be_replayed_on_another_connection():
    w = World()
    a = Client(w, "Alice", register=False)
    nonce = unb64u(a.hello["nonce"])
    sig = b64u(a.key.sign(registration_message(nonce, a.sid, REALM, "Alice")))
    b = Client(w, "Alice", register=False, key=a.key, sid=a.sid)
    b.register(sig=sig)                      # the signature was made for the other connection's nonce
    assert b.errors() == ["bad_sig"]


def test_register_twice_and_before_registering():
    w = World()
    c = Client(w, "Alice", register=False)
    c.send({"t": "proximity", "peers": []})
    c.send({"t": "offer", "to": "x", "ep": 1, "data": {}})
    assert c.errors() == ["not_registered", "not_registered"]
    c.register()
    c.take()
    c.register()
    assert c.errors() == ["already_registered"]


def test_join_code():
    w = World(join_code="s3cret")
    assert Client(w, "Alice").reply is None
    a = Client(w, "Alice", register=False)
    a.register(code="wrong")
    assert a.errors() == ["bad_code"]
    b = Client(w, "Bobby", register=False)
    assert b.hello["join_code"] is True
    assert b.register(code="s3cret") is not None


# 2 ----------------------------------------------------------------- roster

def test_roster_is_sent_on_register_and_updated_on_join_and_leave():
    w = World()
    a = Client(w, "Alice")
    a.take()
    b = Client(w, "Bobby")
    assert b.reply["roster"] == [["Alice", a.sid]], "a new session learns who is online"
    assert a.take("roster_update") == [{"t": "roster_update", "add": [["Bobby", b.sid]]}], "and everyone else learns about it"
    c = Client(w, "Carol")
    assert sorted(map(tuple, c.reply["roster"])) == sorted([("Alice", a.sid), ("Bobby", b.sid)])
    a.take(); b.take()
    w.hub.disconnect(c.conn)
    assert a.take("roster_update") == [{"t": "roster_update", "remove": [c.sid]}]
    assert b.take("roster_update") == [{"t": "roster_update", "remove": [c.sid]}]
    assert w.hub.stats()["sessions"] == 2


def test_the_roster_holds_only_names_and_session_ids():
    w = World()
    a = Client(w, "Alice", ip="203.0.113.9")
    b = Client(w, "Bobby", ip="198.51.100.4")
    blob = json.dumps(b.reply)
    assert "203.0.113.9" not in blob and "198.51.100.4" not in blob, "no addresses in the roster"
    assert set(b.reply["roster"][0]) == {"Alice", a.sid}
    assert set(b.reply) == {"t", "sid", "token", "lease", "now", "roster", "more"}, "nothing else is volunteered"


def test_large_rosters_arrive_in_chunks():
    w = World(limits=Limits(roster_chunk=50, max_conns_per_ip=1000, registers_per_ip_per_minute=1000))
    others = [Client(w, "Aa" + "abcdefghij"[i // 100] + "abcdefghij"[(i // 10) % 10] + "abcdefghij"[i % 10]) for i in range(120)]
    late = Client(w, "Zeta")
    got = list(late.reply["roster"])
    assert late.reply["more"] == 70 and len(got) == 50
    for m in late.take("roster_update"):
        got += m["add"]
    assert len(got) == 120 and len({n for n, _ in got}) == 120


# 3 ----------------------------------------------------------------- realm isolation

def test_realms_are_isolated_and_case_insensitive():
    w = World()
    a = Client(w, "Alice", realm="Alpha")
    b = Client(w, "Bobby", realm="Beta")
    a2 = Client(w, "Carol", realm="alpha")
    assert b.reply["roster"] == [], "another realm is invisible"
    assert [n for n, _ in a2.reply["roster"]] == ["Alice"], "realm names compare case-insensitively"
    assert not b.take("roster_update"), "and gets no roster traffic"
    a.report(("Bobby", 5, 1))
    b.report(("Alice", 5, 1))
    assert not a.take("peer_available") and not b.take("peer_available"), "no link across realms"
    # the same character name on two realms is two different sessions
    x = Client(w, "Dave", realm="Alpha")
    y = Client(w, "Dave", realm="Beta")
    assert x.reply and y.reply


# 4 ----------------------------------------------------------------- duplicates

def test_a_second_login_by_another_key_is_refused_and_by_the_same_key_takes_over():
    w = World()
    a = Client(w, "Alice")
    b = Client(w, "Bobby")
    a.take(); b.take()
    impostor = Client(w, "Alice", register=False)
    impostor.register()
    assert impostor.errors() == ["name_in_use"], "a live name cannot be taken by a different key"
    assert a.conn.closed is None
    again = Client(w, "alice", register=False)     # even with different case
    again.register()
    assert again.errors() == ["name_in_use"]
    # the same desktop reconnecting before the old socket was noticed to be dead
    a2 = Client(w, "Alice", key=a.key, sid=a.sid, token=a.token)
    assert a2.reply is not None, "same key: takeover"
    assert a.conn.closed == (4409, "replaced by a newer connection")
    members = w.hub.realms["testrealm"]
    assert [m.character for m in members.values()].count("Alice") == 1, "one roster entry"
    updates = b.take("roster_update")
    assert any("remove" in m for m in updates) and any("add" in m for m in updates), "the neighbours see the old session go and the new one arrive"
    assert w.hub.stats()["totals"]["takeovers"] == 1
    assert w.hub.stats()["sessions"] == 2


def test_session_id_collisions_are_refused():
    w = World()
    a = Client(w, "Alice")
    b = Client(w, "Bobby", sid=a.sid, register=False)
    b.register()
    assert b.errors() == ["sid_taken"]


# 5 ----------------------------------------------------------------- character change

def test_changing_character_means_a_new_session_and_frees_the_old_name():
    w = World()
    a = Client(w, "Alice")
    b = Client(w, "Bobby")
    a.take(); b.take()
    # the player logs out of Alice and into Carol: the client drops its connection and registers again
    key = a.key
    w.hub.disconnect(a.conn)
    assert [m for m in b.take("roster_update")] == [{"t": "roster_update", "remove": [a.sid]}]
    c = Client(w, "Carol", key=key, sid=secrets.token_urlsafe(16))
    assert c.reply and [n for n, _ in c.reply["roster"]] == ["Bobby"]
    assert b.take("roster_update") == [{"t": "roster_update", "add": [["Carol", c.sid]]}]
    names = sorted(m.character for m in w.hub.realms["testrealm"].values())
    assert names == ["Bobby", "Carol"], "Alice is gone from the roster, Carol is in"


def test_a_dropped_name_is_reserved_for_its_own_key_only():
    w = World()
    a = Client(w, "Alice")
    token = a.token
    w.hub.disconnect(a.conn)
    thief = Client(w, "Alice", register=False)
    thief.register()
    assert thief.errors() == ["name_in_use"], "nobody else can take the name straight away"
    wrong_token = Client(w, "Alice", key=a.key, sid=secrets.token_urlsafe(16), register=False)
    wrong_token.token = "x" * 24
    wrong_token.register()
    assert wrong_token.errors() == ["name_in_use"], "the right key with a wrong token is refused"
    resume = Client(w, "Alice", key=a.key, sid=secrets.token_urlsafe(16), token=token)
    assert resume.reply is not None, "the owner resumes with key and token"
    assert resume.token != token, "and gets a fresh token"


def test_the_reservation_expires():
    w = World(limits=Limits(reserve_seconds=30))
    a = Client(w, "Alice")
    w.hub.disconnect(a.conn)
    w.advance(31)
    anyone = Client(w, "Alice")
    assert anyone.reply is not None
    assert w.hub.stats()["reserved"] == 0


# 6 ----------------------------------------------------------------- expiry and cleanup

def test_idle_sessions_expire_and_leave_the_roster():
    w = World(limits=Limits(idle_timeout=30, register_timeout=5))
    lurker = Client(w, "Lurker", register=False)
    a = Client(w, "Alice")
    b = Client(w, "Bobby")
    a.take(); b.take()
    w.advance(6)
    assert lurker.conn.closed and lurker.conn.closed[0] == 4408, "never registered"
    for _ in range(3):
        w.advance(10)
        b.send({"t": "ping"})                        # Bobby keeps talking; Alice goes silent
    assert a.conn.closed is not None, "silence for 30 s ends a session"
    assert b.conn.closed is None
    assert [m["remove"] for m in b.take("roster_update") if "remove" in m] == [[a.sid]], "and the roster drops it promptly"
    assert w.hub.stats()["sessions"] == 1


def test_disconnect_cleans_links_reports_and_roster():
    w = World()
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    pair_up(w, a, b)
    assert a.take("peer_available") and b.take("peer_available")
    st = w.hub.stats()
    assert st["links"] == 1
    w.hub.disconnect(b.conn)
    assert [m["sid"] for m in a.take("peer_out_of_range")] == [b.sid]
    st = w.hub.stats()
    assert st["links"] == 0 and st["sessions"] == 1
    assert ("testrealm", "alice") not in w.hub.reported_by, "the departed session's own reports are forgotten"
    # a report about a name nobody holds any more is harmless and never links
    a.report(("Bobby", 5, 1))
    assert not a.take("peer_available")


def test_connection_cap_and_registration_rate_per_address():
    w = World(limits=Limits(max_conns_per_ip=2, registers_per_ip_per_minute=3))
    c1 = w.conn("1.1.1.1")
    w.conn("1.1.1.1")
    assert not w.hub.connect(FakeConn("1.1.1.1"))
    assert w.hub.connect(FakeConn("2.2.2.2"))
    w.hub.disconnect(c1)
    assert w.hub.connect(FakeConn("1.1.1.1"))
    w2 = World(limits=Limits(registers_per_ip_per_minute=3, max_conns_per_ip=100))
    names = ["Alice", "Bobby", "Carol", "Dwight"]
    results = [Client(w2, n).reply for n in names]
    assert results[:3] == [r for r in results[:3] if r] and results[3] is None, "the fourth registration in a minute is refused"
    w2.advance(61)
    assert Client(w2, "Edward").reply is not None


def test_a_global_connection_cap_protects_the_whole_hub():
    w = World(limits=Limits(max_connections=3, max_conns_per_ip=100))
    conns = [FakeConn("10.0.0.%d" % i) for i in range(5)]
    assert [w.hub.connect(c) for c in conns] == [True, True, True, False, False], "the fourth connection from anywhere is refused"
    assert w.hub.counters["rejected"] == 2
    w.hub.disconnect(conns[0])
    assert w.hub.connect(conns[3]) and not w.hub.connect(conns[4]), "a freed slot is usable again"
    assert Limits().max_connections == 10000, "the default is a ceiling, not a restriction"


# 7 ----------------------------------------------------------------- mutual proximity

def test_two_reports_that_agree_link_the_pair():
    w = World()
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    a.take(); b.take()
    a.report(("Bobby", 22.0, 1))
    assert not a.take("peer_available") and not b.take("peer_available"), "one side alone is nothing"
    b.report(("Alice", 21.0, 1))
    pa, pb = a.take("peer_available"), b.take("peer_available")
    assert pa == [{"t": "peer_available", "sid": b.sid, "character": "Bobby", "distance": 21.5}]
    assert pb == [{"t": "peer_available", "sid": a.sid, "character": "Alice", "distance": 21.5}]
    assert w.hub.stats()["links"] == 1
    # steady reports do not repeat the announcement
    a.report(("Bobby", 20.0, 2)); b.report(("Alice", 19.0, 2))
    assert not a.take("peer_available") and not b.take("peer_available")


def test_knowing_a_name_is_not_enough():
    w = World()
    victim, bystander, attacker = Client(w, "Victim"), Client(w, "Bystander"), Client(w, "Attacker")
    # the attacker claims to see the victim at every distance; the victim does not see the attacker
    for d in range(0, 45, 3):
        attacker.report(("Victim", float(d), 1))
        w.clock.t += 0.3
    victim.report(("Bystander", 10.0, 1))
    bystander.report(("Victim", 10.0, 1))
    assert not attacker.take("peer_available"), "one-sided reports never link"
    # guessing the victim's own report is the only way in: the victim reports Attacker at 30, attacker guesses 30
    victim.report(("Attacker", 30.0, 2))
    attacker.report(("Victim", 33.0, 2))
    assert attacker.take("peer_available"), "an agreeing pair links; that is a player who really is near the victim"
    # ... and only if the victim's game actually lists the attacker's name


def test_disagreeing_distances_do_not_link():
    w = World(limits=Limits(distance_tolerance=8.0))
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    a.report(("Bobby", 10.0, 1)); b.report(("Alice", 25.0, 1))
    assert not a.take("peer_available") and not b.take("peer_available"), "10 vs 25 yards is not the same pair of objects"
    b.report(("Alice", 17.9, 2))
    assert a.take("peer_available"), "within tolerance links"


def test_out_of_radius_pairs_do_not_link():
    w = World(limits=Limits(link_radius=45.0))
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    a.report(("Bobby", 60.0, 1)); b.report(("Alice", 61.0, 1))
    assert not a.take("peer_available"), "agreeing but 60 yards apart"
    a.report(("Bobby", 44.0, 2)); b.report(("Alice", 45.0, 2))
    assert a.take("peer_available") and b.take("peer_available")


def test_stale_reports_do_not_link():
    w = World(limits=Limits(report_ttl=6.0))
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    a.report(("Bobby", 20.0, 1))
    w.clock.t += 7
    b.report(("Alice", 20.0, 1))
    assert not a.take("peer_available") and not b.take("peer_available"), "Alice's report was 7 s old"
    a.report(("Bobby", 20.0, 2))
    assert a.take("peer_available")


def test_a_link_survives_a_short_gap_and_then_goes_quiet():
    w = World(limits=Limits(report_ttl=6.0, leave_grace=3.0))
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    pair_up(w, a, b)
    a.take(); b.take()
    # Bobby stops reporting (his game lost track for a moment); Alice keeps reporting
    for _ in range(5):
        w.advance(1)
        a.report(("Bobby", 20.0, 1))
    assert not a.take("peer_out_of_range"), "reports are still fresh for report_ttl: no flapping on a missed scan"
    for _ in range(6):
        w.advance(1)
        a.report(("Bobby", 20.0, 1))
    outs = a.take("peer_out_of_range")
    assert outs == [{"t": "peer_out_of_range", "sid": b.sid, "character": "Bobby"}], "after the reports age out and the grace passes"
    assert b.take("peer_out_of_range")
    assert w.hub.stats()["links"] == 0
    # and he comes back
    b.report(("Alice", 18.0, 2)); a.report(("Bobby", 19.0, 2))
    assert a.take("peer_available")


def test_walking_apart_drops_the_link_after_the_grace():
    w = World(limits=Limits(leave_grace=3.0, link_radius=45.0))
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    pair_up(w, a, b, 40, 41)
    a.take(); b.take()
    a.report(("Bobby", 52.0, 3)); b.report(("Alice", 52.5, 3))
    assert not a.take("peer_out_of_range"), "outside the radius, but inside the grace period"
    w.advance(2)
    a.report(("Bobby", 53.0, 4)); b.report(("Alice", 53.5, 4))
    assert not a.take("peer_out_of_range")
    w.advance(2)
    a.report(("Bobby", 54.0, 5)); b.report(("Alice", 54.5, 5))
    assert a.take("peer_out_of_range") and b.take("peer_out_of_range")


def test_one_report_message_replaces_the_previous_set():
    w = World()
    a, b, c = Client(w, "Alice"), Client(w, "Bobby"), Client(w, "Carol")
    a.report(("Bobby", 10, 1), ("Carol", 12, 1))
    b.report(("Alice", 10, 1)); c.report(("Alice", 12, 1))
    assert len(a.take("peer_available")) == 2
    a.report(("Carol", 12, 2))                       # Bobby is no longer listed
    w.advance(4)
    a.report(("Carol", 12, 3)); c.report(("Alice", 12, 3))
    outs = [m["character"] for m in a.take("peer_out_of_range")]
    assert outs == ["Bobby"]
    assert w.hub.stats()["links"] == 1


def test_registering_after_being_reported_completes_the_pair():
    w = World()
    a = Client(w, "Alice")
    a.report(("Bobby", 15.0, 1))                     # Bobby is not online yet
    b = Client(w, "Bobby")
    assert not a.take("peer_available")
    b.report(("Alice", 16.0, 1))
    assert a.take("peer_available") and b.take("peer_available")


def test_reports_about_unknown_or_other_realm_names_leak_nothing():
    w = World()
    a = Client(w, "Alice", realm="Alpha")
    b = Client(w, "Bobby", realm="Beta")
    a.report(("Bobby", 10, 1), ("Nobodyhere", 10, 1))
    assert not a.take()
    assert w.hub.stats()["links"] == 0


# 8 ----------------------------------------------------------------- relay

def test_signalling_relays_only_between_linked_sessions():
    w = World()
    a, b, c = Client(w, "Alice"), Client(w, "Bobby"), Client(w, "Carol")
    a.take(); b.take(); c.take()
    a.send({"t": "offer", "to": b.sid, "ep": 1, "data": {"sdp": "x"}})
    assert a.errors() == ["not_linked"], "no link yet"
    pair_up(w, a, b)
    a.take(); b.take()
    a.send({"t": "offer", "to": b.sid, "ep": 7, "data": {"sdp": "v=0"}, "from": c.sid})
    assert b.take("offer") == [{"t": "offer", "from": a.sid, "ep": 7, "data": {"sdp": "v=0"}}], "from is stamped by the hub"
    b.send({"t": "answer", "to": a.sid, "ep": 7, "data": {"sdp": "v=1"}})
    assert a.take("answer")[0]["ep"] == 7
    a.send({"t": "ice", "to": b.sid, "ep": 7, "data": {"candidate": {"candidate": "c"}}})
    a.send({"t": "bye", "to": b.sid, "ep": 7, "data": {}})
    assert [m["t"] for m in b.take()] == ["ice", "bye"], "ordered delivery"
    a.send({"t": "offer", "to": c.sid, "ep": 1, "data": {}})
    a.send({"t": "offer", "to": secrets.token_urlsafe(16), "ep": 1, "data": {}})
    assert a.errors() == ["not_linked", "not_linked"], "unlinked and unknown targets look the same"
    assert not c.take("offer")


@pytest.mark.parametrize("bad", [{"to": 5}, {"ep": -1}, {"ep": "1"}, {"ep": True}, {"ep": 2 ** 40}])
def test_signal_validation(bad):
    w = World()
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    pair_up(w, a, b)
    a.take(); b.take()
    msg = {"t": "offer", "to": b.sid, "ep": 1, "data": {}}
    msg.update(bad)
    a.send(msg)
    assert a.errors() == ["bad_msg"]


def test_signal_size_and_budget():
    w = World(limits=Limits(max_signal_data_bytes=100, link_signals_per_minute=5, bucket_capacity=1000))
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    pair_up(w, a, b)
    a.take(); b.take()
    a.send({"t": "ice", "to": b.sid, "ep": 1, "data": {"c": "x" * 200}})
    assert a.errors() == ["too_big"]
    for _ in range(5):
        a.send({"t": "ice", "to": b.sid, "ep": 1, "data": {}})
    assert not a.errors()
    a.send({"t": "ice", "to": b.sid, "ep": 1, "data": {}})
    assert a.errors() == ["rate_limited"]
    w.clock.t += 61
    a.send({"t": "ice", "to": b.sid, "ep": 1, "data": {}})
    assert not a.errors()


# 9 ----------------------------------------------------------------- malformed input

@pytest.mark.parametrize("peers", [
    "nope", [["Bobby"]], [["Bobby", "far"]], [["Bobby", -1]], [["Bobby", 600]], [["Bobby", True]], [["Bobby", float("nan")]],
    [[5, 5]], [["B", 5]], [["Bob by", 5]], [["Bobby;x", 5]], ["Bobby"], [None], [["Bobby", 5]] * 65,
])
def test_malformed_proximity_is_rejected(peers):
    w = World()
    a = Client(w, "Alice")
    a.take()
    a.send({"t": "proximity", "peers": peers})
    errs = a.errors()
    assert errs and errs[0] in ("bad_msg", "too_many", "bad_json"), "NaN is not JSON: it dies in the parser"
    assert w.hub.stats()["links"] == 0


def test_garbage_never_raises_or_kills_a_good_session():
    w = World()
    a = Client(w, "Alice")
    a.take()
    for raw in ["", "not json", "[]", "null", "5", '{"t":5}', '{"t":"zzz"}', b"\x00\x01", "{" * 10, '{"t":"proximity"}']:
        w.send(a.conn, raw)
    assert set(a.errors()) <= {"bad_json", "bad_msg"}
    assert a.conn.closed is None
    w.send(a.conn, "x" * 20000)
    assert a.conn.closed and a.conn.closed[0] == 1009


def test_own_name_in_a_report_is_ignored_and_ids_echo_in_errors():
    w = World()
    a = Client(w, "Alice")
    a.take()
    a.report(("Alice", 0, 1))
    assert not a.take()
    a.send({"t": "offer", "to": "zzz", "ep": 1, "data": {}, "id": 42})
    assert a.take("error")[0]["ref"] == 42


def test_rate_limits_and_strikes():
    w = World(limits=Limits(bucket_capacity=5, bucket_rate=0.0001, strikes_to_close=3))
    a = Client(w, "Alice")
    a.take()
    for _ in range(4):
        w.send(a.conn, {"t": "ping"})
    assert [m["t"] for m in a.take()] == ["pong"] * 4
    for _ in range(10):
        w.send(a.conn, {"t": "ping"})
    assert a.conn.closed and a.conn.closed[0] == 1008
    assert w.hub.stats()["sessions"] == 0
    w2 = World(limits=Limits(prox_capacity=3, prox_rate=0.0001, bucket_capacity=1000))
    b = Client(w2, "Bobby")
    b.take()
    for _ in range(5):
        b.report(("Alice", 5, 1))
    assert b.errors().count("rate_limited") == 2, "proximity updates have their own, tighter limit"


def test_ping_pong_and_unregister():
    w = World()
    a = Client(w, "Alice")
    a.take()
    a.send({"t": "ping", "ts": 123.5})
    assert a.take("pong")[0]["ts"] == 123.5
    a.send({"t": "unregister"})
    assert a.conn.closed is not None and w.hub.stats()["sessions"] == 0


def test_a_clean_unregister_frees_the_name_at_once():
    """Logging out and straight back in (a new key) must not wait out the reservation a dropped link gets."""
    w = World()
    a = Client(w, "Alice")
    a.send({"t": "unregister"})
    assert w.hub.stats()["reserved"] == 0
    again = Client(w, "Alice")
    assert again.reply is not None, "a new login of the same character registers immediately"
    again.conn.closed = None
    w.hub.disconnect(again.conn)                              # a dropped connection, by contrast, is reserved
    thief = Client(w, "Alice")
    assert thief.errors() == ["name_in_use"]


def test_stats_carry_no_names_or_ids():
    w = World()
    a, b = Client(w, "Alice"), Client(w, "Bobby")
    pair_up(w, a, b)
    blob = json.dumps(w.hub.stats())
    for secret in ("Alice", "Bobby", a.sid, b.sid):
        assert secret not in blob
    assert w.hub.stats()["largest_realm"] == 2


# 10 ---------------------------------------------------------------- scale

def test_hundreds_of_sessions_register_report_and_link():
    n = 600
    w = World(limits=Limits(max_conns_per_ip=10 ** 6, registers_per_ip_per_minute=10 ** 6, bucket_capacity=10 ** 6,
                            prox_capacity=10 ** 6))
    letters = "abcdefghijklmnopqrstuvwxyz"
    names = ["Pl" + letters[i // 676 % 26] + letters[i // 26 % 26] + letters[i % 26] for i in range(n)]
    t0 = time.perf_counter()
    clients = [Client(w, name) for name in names]
    t_reg = time.perf_counter() - t0
    assert w.hub.stats()["sessions"] == n and w.hub.stats()["largest_realm"] == n
    # every session heard about everyone, either in the registration reply or as updates
    last = clients[-1]
    assert len(last.reply["roster"]) == 300 and last.reply["more"] == n - 1 - 300
    known = {nm for nm, _ in last.reply["roster"]}
    for m in last.take("roster_update"):
        known |= {nm for nm, _ in m.get("add", [])}
    assert len(known) == n - 1
    first_updates = sum(len(c.take("roster_update")) for c in clients[:50])
    assert first_updates >= 50 * (n - 1) - 50 * 50, "early joiners received an update for each later join"
    # a ring of neighbours: everyone reports the next 4 players at agreeing distances
    t1 = time.perf_counter()
    for i, c in enumerate(clients):
        c.report(*[(names[(i + k) % n], 10.0 + k, 1) for k in range(1, 5)] + [(names[(i - k) % n], 10.0 + k, 1) for k in range(1, 5)])
    t_prox = time.perf_counter() - t1
    st = w.hub.stats()
    assert st["links"] == n * 4, "every player is linked to the four on each side (%d)" % st["links"]
    # a tick over all links, and then everybody leaves
    t2 = time.perf_counter()
    w.hub.tick()
    t_tick = time.perf_counter() - t2
    t3 = time.perf_counter()
    for c in clients:
        w.hub.disconnect(c.conn)
    t_leave = time.perf_counter() - t3
    assert w.hub.stats()["sessions"] == 0 and w.hub.stats()["links"] == 0 and not w.hub.realms and not w.hub.reported_by
    print("\n[scale] %d sessions: register all %.2fs (%.2f ms each), report all %.2fs (%.2f ms each), tick over %d links %.3fs, "
          "disconnect all %.2fs" % (n, t_reg, t_reg / n * 1000, t_prox, t_prox / n * 1000, n * 4, t_tick, t_leave))
    assert t_reg < 20 and t_prox < 10 and t_tick < 2 and t_leave < 20, "generous bounds; the printed numbers are the real result"
