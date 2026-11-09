"""Whole proximity scenarios against the peer state machine, with a fake host and clock."""
import random
import time

import pytest

from eqvoice_client.peers import PS, PeerConfig, PeerManager, voice_gain
from eqvoice_client.roster import Roster


def gain_for(distance, full, end, near=1.0, loud_full=None):
    """The plain-speech volume of the curve the Rig is configured with (boost radii 3 and 8 yd, power 1.6, no raised voice)."""
    return voice_gain(distance, {"boost": near, "boost_r": 0.0, "boost_fade": 10.0, "full": full, "full_loud": loud_full or full,
                                 "end": end, "power": 1.6})


ME, LOW, HIGH, MID = "MMMM", "AAAA", "ZZZZ", "NNNN"      # LOW < ME < HIGH: LOW offers to me, I offer to HIGH
LOWN, HIGHN, MIDN = "Lowie", "Higgs", "Midge"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Host:
    def __init__(self):
        self.signals, self.media_cmds, self.events = [], [], []

    def signal(self, to, kind, ep, data):
        self.signals.append((to, kind, ep, data))
        return True

    def media(self, cmd):
        self.media_cmds.append(cmd)

    def emit(self, event, **kw):
        self.events.append((event, kw))

    def cmds(self, name):
        return [c for c in self.media_cmds if c["cmd"] == name]

    def sigs(self, kind):
        return [s for s in self.signals if s[1] == kind]


class Rig:
    def __init__(self, **cfg):
        self.clock, self.host = Clock(), Host()
        c = dict(say_radius=40.0, full_radius=10.0, debounce_s=1.0, leave_grace_s=1.5, teardown_s=8.0, connect_timeout_s=10.0, max_peers=3,
                 retry_backoff_s=(3, 6, 12), forget_s=30.0, link_resync_s=5.0, short_cooldown_s=1.0, fail_closed_s=8.0)
        c.update(cfg)
        self.m = PeerManager(self.host, PeerConfig(**c), clock=self.clock,
                             ice_provider=lambda: [{"urls": ["stun:x"]}], rng=random.Random(1))
        self.roster = Roster()
        self.roster.sync([[LOWN, LOW], [HIGHN, HIGH], [MIDN, MID]])
        self.readings = {}
        self.m.set_self(ME)
        self.m.on_signalling(True)

    def feed(self):
        self.m.update_readings(dict(self.readings), self.roster)

    def tick(self, seconds=0.0, step=0.25):
        end = self.clock.t + seconds
        self.feed()
        self.m.tick()
        while self.clock.t < end - 1e-9:
            self.clock.advance(min(step, end - self.clock.t))
            self.feed()
            self.m.tick()

    def see(self, **dists):
        self.readings.update(dists)
        self.feed()

    def lose(self, *names):
        for n in names:
            self.readings.pop(n, None)
        self.feed()

    def state(self, name):
        p = self.m.peers.get(name.lower())
        return p.state if p else None

    def peer(self, name):
        return self.m.peers[name.lower()]

    def connect_as_initiator(self, dist=5.0):
        """Drive a full outgoing connection to Higgs to CONNECTED; returns the epoch."""
        self.see(**{HIGHN: dist})
        self.m.on_link(HIGH, True, dist)
        self.tick(1.5)
        assert self.state(HIGHN) == PS.CONNECTING
        ep = self.peer(HIGHN).ep
        self.m.on_media(HIGH, ep, {"type": "description", "sdp_type": "offer", "sdp": "OFFER"})
        self.m.on_signal(HIGH, "answer", ep, {"sdp": "ANSWER"})
        self.m.on_media(HIGH, ep, {"type": "state", "ice": "connected", "conn": "connected"})
        assert self.state(HIGHN) == PS.CONNECTED
        return ep

    def connect_as_answerer(self, dist=5.0):
        self.see(**{LOWN: dist})
        self.m.on_link(LOW, True, dist)
        self.tick(1.5)
        assert self.state(LOWN) == PS.NEARBY_PENDING, "the lower id offers; we wait"
        self.m.on_signal(LOW, "offer", 7, {"sdp": "OFFER"})
        assert self.state(LOWN) == PS.CONNECTING
        self.m.on_media(LOW, 7, {"type": "state", "ice": "connected", "conn": "connected"})
        assert self.state(LOWN) == PS.CONNECTED
        return 7


# ------------------------------------------------------------------ volume curve

def test_gain_curve():
    assert gain_for(0, 10, 40) == gain_for(10, 10, 40) == 1.0
    assert gain_for(40, 10, 40) == gain_for(80, 10, 40) == 0.0
    vals = [gain_for(d, 10, 40) for d in range(10, 41)]
    assert vals == sorted(vals, reverse=True), "never louder with distance"
    assert max(abs(a - b) for a, b in zip(vals, vals[1:])) < 0.12, "no jumps: each yard changes the volume a little"
    assert 0.35 < gain_for(20, 10, 40) < 0.55, "gradual in the middle"
    assert gain_for(30, 10, 40) < 0.2, "quiet near the edge"
    assert gain_for(39, 10, 40) < 0.02


# ---------------------------------------------------------------- initiating

def test_initiator_happy_path():
    r = Rig()
    r.see(**{HIGHN: 25.0})
    assert r.state(HIGHN) == PS.SEEN
    r.tick(0.25)
    assert r.state(HIGHN) == PS.NEARBY_PENDING
    r.tick(5)
    assert r.state(HIGHN) == PS.NEARBY_PENDING, "no confirmation from the server yet: nothing is attempted"
    assert not r.host.cmds("open")
    r.m.on_link(HIGH, True, 24.0)
    r.tick(0.1)
    assert r.state(HIGHN) == PS.CONNECTING
    open_cmd = r.host.cmds("open")[0]
    assert open_cmd["initiator"] is True and open_cmd["ice"] == [{"urls": ["stun:x"]}] and open_cmd["sid"] == HIGH
    ep = open_cmd["ep"]
    r.m.on_media(HIGH, ep, {"type": "description", "sdp_type": "offer", "sdp": "OFFER"})
    assert r.host.sigs("offer") == [(HIGH, "offer", ep, {"sdp": "OFFER"})]
    r.m.on_media(HIGH, ep, {"type": "candidate", "candidate": {"candidate": "c1"}})
    assert r.host.sigs("ice")[0][3] == {"candidate": {"candidate": "c1"}}
    r.m.on_signal(HIGH, "answer", ep, {"sdp": "ANSWER"})
    assert r.host.cmds("remote")[0]["type"] == "answer"
    r.m.on_signal(HIGH, "ice", ep, {"candidate": {"candidate": "r1"}})
    assert r.host.cmds("candidate")[0]["candidate"] == {"candidate": "r1"}
    r.m.on_media(HIGH, ep, {"type": "state", "ice": "checking", "conn": "connecting"})
    assert r.state(HIGHN) == PS.CONNECTING
    r.m.on_media(HIGH, ep, {"type": "state", "ice": "connected", "conn": "connected"})
    assert r.state(HIGHN) == PS.CONNECTED
    g = r.host.cmds("gain")[-1]
    assert g["value"] == pytest.approx(gain_for(25.0, 10, 40), abs=0.001) and g["ms"] == 300 and g["send"] is True, \
        "fades in at the volume for 25 yards, and starts transmitting to them"


def test_the_lower_session_id_offers_the_higher_waits():
    r = Rig()
    r.see(**{LOWN: 10.0})
    r.m.on_link(LOW, True, 10.0)
    r.tick(3)
    assert r.state(LOWN) == PS.NEARBY_PENDING and not r.host.cmds("open"), "ME > LOW: LOW makes the offer"


def test_a_player_outside_say_radius_is_only_seen():
    r = Rig()
    r.see(**{HIGHN: 55.0})
    r.m.on_link(HIGH, True, 55.0)
    r.tick(5)
    assert r.state(HIGHN) == PS.SEEN and not r.host.cmds("open"), "linked or not, 55 yards is not talking distance"
    r.see(**{HIGHN: 39.0})
    r.tick(0.25)
    assert r.state(HIGHN) == PS.NEARBY_PENDING
    r.tick(1.5)
    assert r.state(HIGHN) == PS.CONNECTING


def test_the_server_must_confirm_before_anything_connects():
    """One-sided proximity: the game sees them at 5 yards, the server never links us."""
    r = Rig()
    r.see(**{HIGHN: 5.0})
    r.tick(30)
    assert r.state(HIGHN) == PS.NEARBY_PENDING and not r.host.cmds("open") and not r.host.signals


def test_debounce_flicker_never_connects():
    r = Rig(debounce_s=2.0)
    r.m.on_link(HIGH, True, 20.0)
    for _ in range(5):
        r.see(**{HIGHN: 20.0})
        r.tick(1.0)
        r.lose(HIGHN)                          # gone again before the debounce
        r.tick(0.25)
        assert r.state(HIGHN) is None
        r.tick(0.5)
    assert not r.host.cmds("open")


def test_nothing_is_attempted_until_registered():
    r = Rig()
    r.m.on_signalling(False)
    r.see(**{HIGHN: 20.0})
    r.m.on_link(HIGH, True, 20.0)
    r.tick(3)
    assert not r.host.cmds("open")
    r.m.on_signalling(True)
    r.tick(1.5)
    assert r.host.cmds("open")


def test_nothing_is_attempted_without_a_media_engine():
    r = Rig()
    r.m.media_ready = False
    r.see(**{HIGHN: 20.0})
    r.m.on_link(HIGH, True, 20.0)
    r.tick(3)
    assert not r.host.cmds("open")
    r.m.set_media_ready(True)
    r.tick(0.5)
    assert r.host.cmds("open")


# ------------------------------------------------------------------ answering

def test_answerer_happy_path_and_refusals():
    r = Rig()
    ep = r.connect_as_answerer(dist=12.0)
    o = r.host.cmds("open")[0]
    assert o["initiator"] is False and o["ep"] == ep
    assert r.host.cmds("remote")[0]["type"] == "offer"
    r.m.on_media(LOW, ep, {"type": "description", "sdp_type": "answer", "sdp": "ANSWER"})
    assert r.host.sigs("answer") == [(LOW, "answer", ep, {"sdp": "ANSWER"})]


@pytest.mark.parametrize("setup,why", [
    ("unlinked", "not linked"),
    ("far", "does not see them"),
    ("unseen", "not linked"),
    ("no_media", "no media engine"),
])
def test_offers_are_refused_when_they_should_be(setup, why):
    r = Rig()
    if setup != "unseen":
        r.see(**{LOWN: 60.0 if setup == "far" else 10.0})
    if setup != "unlinked":
        r.m.on_link(LOW, True, 10.0)
    if setup == "no_media":
        r.m.media_ready = False
    r.tick(0.25)
    r.m.on_signal(LOW, "offer", 3, {"sdp": "x"})
    assert r.host.sigs("bye") == [(LOW, "bye", 3, {})], why
    assert not r.host.cmds("open")


def test_an_offer_from_the_higher_id_is_refused():
    r = Rig()
    r.see(**{HIGHN: 10.0})
    r.m.on_link(HIGH, True, 10.0)
    r.tick(0.25)
    r.m.on_signal(HIGH, "offer", 3, {"sdp": "x"})
    assert r.host.sigs("bye") and not r.host.cmds("open"), "only the lower id offers, so glare cannot happen"


def test_an_offer_just_past_say_radius_is_accepted_without_flip_flopping():
    """The offerer measured 39, we measure 41.5: inside the leave threshold, so we accept and stay."""
    r = Rig()
    r.see(**{LOWN: 41.5})
    r.m.on_link(LOW, True, 40.0)
    r.tick(0.25)
    r.m.on_signal(LOW, "offer", 5, {"sdp": "x"})
    assert r.state(LOWN) == PS.CONNECTING
    r.m.on_media(LOW, 5, {"type": "state", "ice": "connected", "conn": "connected"})
    r.tick(5)
    assert r.state(LOWN) == PS.CONNECTED, "41.5 yards: still connected (silent), not bouncing in and out"
    assert r.host.cmds("gain")[-1]["value"] == 0.0


def test_duplicate_and_superseding_offers():
    r = Rig()
    ep = r.connect_as_answerer()
    n = len(r.host.cmds("open"))
    r.m.on_signal(LOW, "offer", ep, {"sdp": "again"})
    assert len(r.host.cmds("open")) == n, "a repeated offer is ignored"
    r.m.on_signal(LOW, "offer", ep + 1, {"sdp": "newer"})
    assert len(r.host.cmds("open")) == n + 1 and r.host.cmds("close"), "a newer attempt replaces the old connection"
    assert r.peer(LOWN).ep == ep + 1


def test_ice_that_beats_its_offer_is_kept_and_replayed():
    r = Rig()
    r.see(**{LOWN: 10.0})
    r.m.on_link(LOW, True, 10.0)
    r.tick(1.5)
    r.m.on_signal(LOW, "ice", 9, {"candidate": {"candidate": "early"}})
    assert not r.host.cmds("candidate")
    r.m.on_signal(LOW, "offer", 9, {"sdp": "x"})
    assert [c["candidate"] for c in r.host.cmds("candidate")] == [{"candidate": "early"}]


# --------------------------------------------------------- volume while connected

def test_volume_follows_distance_without_chatter():
    r = Rig()
    r.connect_as_initiator(dist=5.0)
    r.host.media_cmds.clear()
    r.tick(1.0)
    assert not r.host.cmds("gain"), "full volume and not moving: nothing to say"
    for d in (6, 8, 12, 16, 20, 24, 28):           # walking away, one step per tick
        r.see(**{HIGHN: float(d)})
        r.tick(0.25)
    gains = r.host.cmds("gain")
    vals = [g["value"] for g in gains]
    assert vals == sorted(vals, reverse=True) and vals[-1] == pytest.approx(gain_for(28, 10, 40), abs=0.03)
    assert len(gains) <= 7 and all(0 < g["ms"] <= 400 for g in gains), "smoothed ramps, at most 4 per second"
    r.see(**{HIGHN: 28.2})
    r.tick(1.0)
    assert len(r.host.cmds("gain")) == len(gains), "a 0.2 yard wiggle changes nothing"


def test_volume_updates_are_rate_limited_to_four_a_second():
    r = Rig()
    r.connect_as_initiator(dist=12.0)
    r.host.media_cmds.clear()
    d = 12.0
    for _ in range(40):                            # 10 seconds of 0.25 s ticks, fast walking
        d += 0.7
        r.see(**{HIGHN: d})
        r.tick(0.05, step=0.05)
    assert len(r.host.cmds("gain")) <= 4 * 2 + 2


def test_inaudible_at_say_radius_and_not_transmitting():
    r = Rig()
    r.connect_as_initiator(dist=20.0)
    r.see(**{HIGHN: 40.0})
    r.tick(0.5)
    g = r.host.cmds("gain")[-1]
    assert g["value"] == 0.0 and g["send"] is False, "at 40 yards we neither hear them nor send to them"
    assert r.state(HIGHN) == PS.CONNECTED, "but the connection stays, ready for them to come back"


# ----------------------------------------------------------------- leaving range

def test_leaving_range_fades_then_tears_down_later():
    r = Rig()
    ep = r.connect_as_initiator(dist=8.0)
    r.host.media_cmds.clear()
    r.see(**{HIGHN: 60.0})
    r.tick(1.0)
    assert r.state(HIGHN) == PS.CONNECTED, "within the leave grace: nothing drastic"
    r.tick(1.0)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE
    g = r.host.cmds("gain")[-1]
    assert g["value"] == 0.0 and g["send"] is False, "faded out and stopped transmitting"
    assert not r.host.cmds("close") and not r.host.sigs("bye"), "the connection is NOT closed yet"
    r.tick(5)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE and not r.host.cmds("close")
    r.tick(3)
    assert r.state(HIGHN) == PS.DISCONNECTED
    assert r.host.cmds("close")[0]["ep"] == ep and r.host.sigs("bye")[0][0] == HIGH, "closed after the teardown delay"


def test_walking_back_before_teardown_reuses_the_connection():
    r = Rig()
    ep = r.connect_as_initiator(dist=8.0)
    r.see(**{HIGHN: 60.0})
    r.tick(4.0)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE
    opens = len(r.host.cmds("open"))
    r.see(**{HIGHN: 20.0})
    r.tick(0.5)
    assert r.state(HIGHN) == PS.CONNECTED and r.peer(HIGHN).ep == ep
    assert len(r.host.cmds("open")) == opens and not r.host.cmds("close"), "no new WebRTC negotiation"
    g = r.host.cmds("gain")[-1]
    assert g["value"] == pytest.approx(gain_for(20, 10, 40), abs=0.001) and g["ms"] == 300 and g["send"] is True


def test_one_missed_scan_changes_nothing():
    r = Rig()
    r.connect_as_initiator(dist=15.0)
    r.host.media_cmds.clear()
    r.lose(HIGHN)                                  # the game did not report them for one tick
    r.tick(1.0)
    assert r.state(HIGHN) == PS.CONNECTED
    r.see(**{HIGHN: 15.5})
    r.tick(1.0)
    assert r.state(HIGHN) == PS.CONNECTED
    assert not any(g["value"] == 0.0 for g in r.host.cmds("gain")), "never faded"
    assert not r.host.cmds("close")


def test_missing_readings_hold_the_volume_then_fade():
    r = Rig()
    r.connect_as_initiator(dist=18.0)
    held = r.host.cmds("gain")[-1]["value"]
    r.host.media_cmds.clear()
    r.lose(HIGHN)
    r.tick(1.2)
    assert not r.host.cmds("gain"), "volume held while a reading is briefly missing (%.2f)" % held
    r.tick(0.6)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE and r.host.cmds("gain")[-1]["value"] == 0.0


def test_hysteresis_at_the_edge():
    r = Rig()
    r.connect_as_initiator(dist=39.0)
    for d in (41.0, 39.5, 42.5, 40.2, 42.9):
        r.see(**{HIGHN: d})
        r.tick(0.5)
    assert r.state(HIGHN) == PS.CONNECTED, "jittering around the edge does not drop the connection"
    r.see(**{HIGHN: 43.5})
    r.tick(2.0)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE, "past 43 yards it has really left"
    r.see(**{HIGHN: 41.0})
    r.tick(0.5)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE, "coming back needs to be inside 40, not just 43"
    r.see(**{HIGHN: 39.0})
    r.tick(0.5)
    assert r.state(HIGHN) == PS.CONNECTED


def test_pending_peer_that_leaves_goes_back_to_seen():
    r = Rig()
    r.see(**{HIGHN: 30.0})
    r.tick(0.5)
    assert r.state(HIGHN) == PS.NEARBY_PENDING
    r.see(**{HIGHN: 50.0})
    r.tick(0.5)
    assert r.state(HIGHN) == PS.SEEN
    r.lose(HIGHN)
    r.tick(0.5)
    assert r.state(HIGHN) is None


# --------------------------------------------------------------- the server's say

def test_server_withdrawing_the_link_fades_at_once():
    r = Rig()
    ep = r.connect_as_initiator(dist=10.0)
    r.host.media_cmds.clear()
    r.m.on_link(HIGH, False)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE
    assert r.host.cmds("gain")[-1]["value"] == 0.0, "the other side stopped seeing us (or its game went quiet)"
    r.tick(2.0)
    r.m.on_link(HIGH, True, 11.0)
    r.tick(0.5)
    assert r.state(HIGHN) == PS.CONNECTED and r.peer(HIGHN).ep == ep and not r.host.cmds("open")


def test_link_dropping_while_connecting_abandons_the_attempt():
    r = Rig()
    r.see(**{HIGHN: 10.0})
    r.m.on_link(HIGH, True, 10.0)
    r.tick(1.5)
    assert r.state(HIGHN) == PS.CONNECTING
    r.m.on_link(HIGH, False)
    assert r.state(HIGHN) == PS.DISCONNECTED and r.host.cmds("close")


def test_link_distance_is_reported_in_the_snapshot():
    r = Rig()
    r.see(**{HIGHN: 10.0})
    r.m.on_link(HIGH, True, 11.5)
    snap = r.m.snapshot()[0]
    assert snap["linked"] is True and snap["link_dist"] == 11.5 and snap["dist"] == 10.0 and snap["name"] == HIGHN


# ----------------------------------------------------------------- signalling loss

def test_brief_signalling_loss_does_not_cut_the_call_and_the_link_resyncs():
    r = Rig()
    r.connect_as_initiator(dist=10.0)
    r.m.on_signalling(False)
    r.tick(5)
    assert r.state(HIGHN) == PS.CONNECTED, "5 seconds without the server: the game still says they are here"
    r.m.on_signalling(True)
    r.tick(1)
    r.m.on_link(HIGH, True, 10.0)                  # the hub re-confirms after we re-report
    r.tick(6)
    assert r.state(HIGHN) == PS.CONNECTED


def test_unconfirmed_links_lapse_after_reconnecting():
    r = Rig()
    r.connect_as_initiator(dist=10.0)
    r.m.on_signalling(False)
    r.tick(1)
    r.m.on_signalling(True)
    r.tick(5.5)                                    # the hub never re-confirms (the other side is gone)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE


def test_long_signalling_loss_fails_closed():
    r = Rig()
    r.connect_as_initiator(dist=10.0)
    r.m.on_signalling(False)
    r.tick(7.5)
    assert r.state(HIGHN) == PS.CONNECTED
    r.tick(1.5)
    assert r.state(HIGHN) == PS.OUT_OF_RANGE_GRACE, "after fail_closed_s without the server: everyone fades out"
    assert r.host.cmds("gain")[-1]["value"] == 0.0 and r.host.cmds("gain")[-1]["send"] is False


# ------------------------------------------------------- identity and the roster

def test_peer_going_offline_is_closed_and_forgotten():
    r = Rig()
    r.connect_as_initiator(dist=10.0)
    gone = r.roster.remove([HIGH])
    r.m.roster_changed(removed_sids=[HIGH])
    assert gone == [HIGHN] and r.state(HIGHN) is None
    assert r.host.cmds("close") and not r.host.sigs("bye"), "they are gone: no goodbye needed"
    r.feed()
    assert r.state(HIGHN) is None, "and a stale reading for a name that left the roster is ignored"


def test_same_character_logging_in_again_gets_a_clean_slate():
    r = Rig()
    old_ep = r.connect_as_initiator(dist=10.0)
    replaced = r.roster.add([[HIGHN, "ZZZ2"]])
    assert replaced == [HIGHN]
    r.m.roster_changed(replaced=replaced)
    assert r.state(HIGHN) is None and r.host.cmds("close")
    r.m.on_link("ZZZ2", True, 10.0)
    r.tick(1.5)
    assert r.state(HIGHN) == PS.CONNECTING and r.peer(HIGHN).sid == "ZZZ2" and r.peer(HIGHN).ep != old_ep


def test_our_own_identity_changing_drops_everything():
    r = Rig()
    r.connect_as_initiator(dist=10.0)
    r.m.set_self("NEWSID")
    assert r.m.peers == {} and r.m.linked == {} and r.host.cmds("close")
    r.m.set_self("NEWSID")                         # idempotent


def test_media_page_restart_closes_calls():
    r = Rig()
    r.connect_as_initiator(dist=10.0)
    r.m.set_media_ready(False)
    assert r.state(HIGHN) == PS.DISCONNECTED and r.host.cmds("close")


# ------------------------------------------------------------------ failure paths

def test_connect_timeout_and_backoff():
    r = Rig()
    r.see(**{HIGHN: 10.0})
    r.m.on_link(HIGH, True, 10.0)
    r.tick(1.5)
    assert r.state(HIGHN) == PS.CONNECTING
    r.tick(10.5)
    assert r.state(HIGHN) == PS.DISCONNECTED and r.peer(HIGHN).attempts == 1 and "timeout" in r.peer(HIGHN).err
    assert r.m.snapshot()[0]["retry_in"] > 2
    r.tick(3.5)
    assert r.state(HIGHN) == PS.CONNECTING, "retried after the backoff"
    r.tick(10.5)
    assert r.peer(HIGHN).attempts == 2
    assert r.peer(HIGHN).cooldown_until - r.clock.t > 4, "and the second wait is longer"


def test_ice_failure_and_media_error_are_failures():
    r = Rig()
    ep = r.connect_as_initiator()
    r.m.on_media(HIGH, ep, {"type": "state", "ice": "failed", "conn": "failed"})
    assert r.state(HIGHN) == PS.DISCONNECTED and "failed" in r.peer(HIGHN).err
    r2 = Rig()
    r2.see(**{HIGHN: 10.0})
    r2.m.on_link(HIGH, True, 10.0)
    r2.tick(1.5)
    r2.m.on_media(HIGH, r2.peer(HIGHN).ep, {"type": "error", "msg": "boom"})
    assert r2.state(HIGHN) == PS.DISCONNECTED and "boom" in r2.peer(HIGHN).err


def test_a_refused_offer_backs_off_instead_of_hammering():
    r = Rig()
    r.see(**{HIGHN: 10.0})
    r.m.on_link(HIGH, True, 10.0)
    r.tick(1.5)
    ep = r.peer(HIGHN).ep
    r.m.on_signal(HIGH, "bye", ep, {})             # the other side refused
    assert r.state(HIGHN) == PS.DISCONNECTED and r.peer(HIGHN).attempts == 1
    r.tick(2.5)
    assert r.state(HIGHN) == PS.DISCONNECTED, "not retried at the short cooldown"
    r.tick(1.0)
    assert r.state(HIGHN) == PS.CONNECTING


def test_remote_bye_on_a_live_call_disconnects_quietly():
    r = Rig()
    ep = r.connect_as_initiator()
    r.host.signals.clear()
    r.m.on_signal(HIGH, "bye", ep, {})
    assert r.state(HIGHN) == PS.DISCONNECTED and not r.host.sigs("bye"), "we do not echo their goodbye"


def test_capacity_limit():
    r = Rig(max_peers=1)
    r.connect_as_initiator(dist=10.0)
    r.see(**{MIDN: 10.0})
    r.m.on_link(MID, True, 10.0)
    r.tick(3)
    assert r.state(MIDN) == PS.NEARBY_PENDING and r.peer(MIDN).capped is True and len(r.host.cmds("open")) == 1


def test_shutdown_says_goodbye():
    r = Rig()
    r.connect_as_initiator()
    r.m.shutdown()
    assert r.host.cmds("close") and r.host.sigs("bye")
    assert r.m.peers == {}


# ------------------------------------------------------------------------- scale

def test_a_crowd_of_hundreds_is_cheap():
    """300 readings a tick, for 10 seconds of ticks, must take a small fraction of real time."""
    r = Rig()
    names = ["Player%03d" % i for i in range(300)]
    r.roster.sync([[n, "S%03d" % i] for i, n in enumerate(names)])
    t0 = time.perf_counter()
    for k in range(40):
        r.readings = {n: 5.0 + (i * 0.3 + k) % 45 for i, n in enumerate(names)}
        r.tick(0.25)
    elapsed = time.perf_counter() - t0
    assert len(r.m.peers) == 300
    assert elapsed < 3.0, "40 ticks of a 300-player crowd took %.2fs" % elapsed
    assert r.m.counts()["NEARBY_PENDING"] > 0


# ------------------------------------------------------------------ a raised voice carries further, a close voice is doubled

WIDE = dict(say_radius=41.0, full_radius=30.0, loud_full_radius=45.0, peak_radius=85.0, near_gain=2.0, leave_hysteresis=3.0)
CURVE = PeerConfig(**WIDE).curve


def test_the_curve_boosts_a_voice_right_next_to_you_is_full_to_30_yd_and_is_quiet_at_70():
    g = lambda d, loud=0.0: voice_gain(d, CURVE, loud)
    assert g(0) == 2.0, "right next to each other: twice the voice's own loudness"
    assert g(5) == pytest.approx(2 ** 0.5, abs=1e-9), "half way to 10 yd the boost is half of it, in decibels (+3 dB)"
    assert g(10) == g(15) == g(30) == pytest.approx(1.0), "gone by 10 yd; 30 yd is still 100%"
    assert g(40) < 1.0 and 0.08 < g(70) < 0.18, "70 yd is quiet (%.3f of full)" % g(70)
    assert g(85) == 0.0 and g(120) == 0.0, "and gone at the end of the curve"


def test_the_boost_scales_with_distance_it_does_not_jump():
    """Every yard closer is a little louder, from 10 yd to nothing: no flat stretch for the boost to start at, and no step anywhere."""
    g = lambda d: voice_gain(d, CURVE, 0.0)
    assert all(g(d) > g(d + 1) for d in range(0, 10)), "each yard closer is louder than the one before: there is no plateau"
    fine = [g(d / 20.0) for d in range(0, 1701)]               # 0 .. 85 yd in 5 cm
    assert fine == sorted(fine, reverse=True), "never louder with distance"
    assert max(abs(a - b) for a, b in zip(fine, fine[1:])) < 0.01, "and no step anywhere along it (the biggest 5 cm change is %.4f)" % max(abs(a - b) for a, b in zip(fine, fine[1:]))
    boosts = [g(d / 20.0) for d in range(0, 201)]
    slope = lambda i: abs(boosts[i + 1] - boosts[i]) * 20
    assert slope(0) < 0.01 and slope(199) < 0.01, "it eases in and out: the volume does not kink where the boost starts or ends"
    held = dict(CURVE, boost_r=3.0)
    assert voice_gain(0, held) == voice_gain(3, held) == 2.0 and voice_gain(10, held) == pytest.approx(1.0), "a held stretch can still be configured"


def test_a_raised_voice_stays_at_full_volume_further_out_but_ends_in_the_same_place():
    g = lambda d, loud: voice_gain(d, CURVE, loud)
    assert g(45, 1.0) == 1.0 and g(45, 0.0) < 0.7, "a shout is still at full volume at 45 yd; talking is not"
    assert g(70, 1.0) > g(70, 0.0) > 0.0
    assert g(85, 1.0) == g(85, 0.0) == 0.0, "the end does not move: that is how far calls are kept"
    assert g(60, 0.0) < g(60, 0.5) < g(60, 1.0), "and a half-raised voice is in between"


def test_the_boost_option_is_a_multiplier_and_off_removes_it():
    off = dict(CURVE, boost=1.0)
    assert voice_gain(1, off) == 1.0 and voice_gain(5, off) == 1.0
    high = dict(CURVE, boost=3.0)
    assert voice_gain(0, high) == 3.0 and voice_gain(5, high) == pytest.approx(3 ** 0.5) and voice_gain(10, high) == pytest.approx(1.0) and voice_gain(30, high) == 1.0


def test_a_player_beyond_an_ordinary_voice_is_still_called_up_to_the_peak_radius():
    r = Rig(**WIDE)
    r.see(**{HIGHN: 70.0})
    r.m.on_link(HIGH, True, 70.0)
    r.tick(1.5)
    assert r.state(HIGHN) == PS.CONNECTING, "70 yd is past an ordinary voice (41) but inside what a shout can reach (85): the call is made"
    r2 = Rig(**WIDE)
    r2.see(**{HIGHN: 90.0})
    r2.m.on_link(HIGH, True, 90.0)
    r2.tick(1.5)
    assert r2.state(HIGHN) == PS.NEARBY_PENDING or r2.state(HIGHN) == PS.SEEN, "90 yd is past anyone's reach: no call"
    assert r2.state(HIGHN) != PS.CONNECTING


def test_without_a_peak_radius_everything_is_as_before():
    r = Rig(say_radius=40.0, full_radius=10.0)
    r.see(**{HIGHN: 60.0})
    r.m.on_link(HIGH, True, 60.0)
    r.tick(1.5)
    assert r.state(HIGHN) == PS.SEEN, "an old-style config still ends at say_radius"


def test_the_page_is_told_the_distance_and_keeps_getting_it_as_it_changes():
    r = Rig(**WIDE)
    r.connect_as_initiator(dist=2.0)
    first = r.host.cmds("gain")[-1]
    assert first["value"] == pytest.approx(voice_gain(2.0, CURVE), abs=0.001) and 1.7 < first["value"] < 2.0, "a little under double at 2 yd: it scales"
    assert first["gate"] is True and first["dist"] == 2.0 and first["send"] is True, first
    r.see(**{HIGHN: 60.0})
    r.tick(1.0)
    g = r.host.cmds("gain")[-1]
    assert g["value"] == pytest.approx(voice_gain(60.0, CURVE), abs=0.002) and g["dist"] == 60.0 and g["gate"] is True, \
        "the page is told the distance so that it can work out how loud a raised voice is there"
    assert g["send"] is True
    n = len(r.host.cmds("gain"))
    r.see(**{HIGHN: 60.1})
    r.tick(1.0)
    assert len(r.host.cmds("gain")) == n, "a tenth of a yard is not worth a message"
    r.see(**{HIGHN: 60.4})
    r.tick(1.0)
    assert len(r.host.cmds("gain")) == n + 1 and r.host.cmds("gain")[-1]["dist"] == 60.4, "but a quarter of a yard is: the page follows the distance closely"
    r.see(**{HIGHN: 63.0})
    r.tick(1.0)
    assert r.host.cmds("gain")[-1]["dist"] == 63.0, "but the distance keeps moving as they do"


def test_leaving_range_closes_the_gate_so_even_a_shout_is_silent():
    r = Rig(**WIDE)
    r.connect_as_initiator(dist=60.0)
    r.tick(1.0)
    assert r.host.cmds("gain")[-1]["value"] > 0.0 and r.host.cmds("gain")[-1]["gate"] is True
    r.lose(HIGHN)
    r.tick(3.0)
    last = r.host.cmds("gain")[-1]
    assert last["gate"] is False and last["send"] is False and last["value"] == 0.0, "gone from the game's view: fade out, shut the gate, stop sending: %r" % last


def test_the_page_can_be_given_the_curve_it_needs():
    assert CURVE == {"boost": 2.0, "boost_r": 0.0, "boost_fade": 10.0, "full": 30.0, "full_loud": 45.0, "end": 85.0, "power": 1.6}
    assert PeerConfig(**WIDE).wide is True
    plain = PeerConfig(say_radius=41.0, full_radius=10.0)
    assert plain.curve == {"boost": 1.0, "boost_r": 0.0, "boost_fade": 10.0, "full": 10.0, "full_loud": 10.0, "end": 41.0, "power": 1.6}
    assert plain.wide is False


def test_when_there_are_not_enough_call_slots_the_nearest_players_get_them():
    r = Rig(**dict(WIDE, max_peers=1))
    r.see(**{HIGHN: 70.0})                 # seen first, and further away ...
    r.see(**{MIDN: 20.0})                  # ... than this one
    r.m.on_link(HIGH, True, 70.0)
    r.m.on_link(MID, True, 20.0)
    r.tick(2.0)
    assert r.state(MIDN) == PS.CONNECTING and r.state(HIGHN) != PS.CONNECTING, "the slot goes to the player at 20 yd, not the one who was listed first"
    assert r.peer(HIGHN).capped is True


# ------------------------------------------------------------------ the mute list and what each player is doing

def test_a_muted_player_is_marked_in_the_open_command_and_the_page_is_told_when_the_list_changes():
    r = Rig()
    r.m.set_muted("Higgs", True)
    assert r.m.is_muted("HIGGS") and r.m.is_muted(HIGHN) and not r.m.is_muted(LOWN), "names are matched without regard to case"
    r.see(**{HIGHN: 5.0})
    r.m.on_link(HIGH, True, 5.0)
    r.tick(1.5)
    opened = r.host.cmds("open")[-1]
    assert opened["sid"] == HIGH and opened["mute"] is True, "the page starts playing nothing from them"
    ep = r.peer(HIGHN).ep
    r.m.on_media(HIGH, ep, {"type": "state", "ice": "connected", "conn": "connected"})
    r.host.media_cmds.clear()
    r.m.set_muted("higgs", False)
    pm = r.host.cmds("peer_mute")
    assert len(pm) == 1 and pm[0]["sid"] == HIGH and pm[0]["ep"] == ep and pm[0]["value"] is False, "taking them off the list reaches the live connection"
    r.m.set_muted("Midge", True)
    assert len(r.host.cmds("peer_mute")) == 1, "nobody by that name is connected: nothing to tell the page"
    r.m.set_muted("   ", True)
    assert r.m.muted == {"midge"}, "a blank name is not a name"


def test_an_answering_connection_to_a_muted_player_is_muted_from_its_first_moment():
    r = Rig()
    r.m.set_muted(LOWN, True)
    r.connect_as_answerer()
    assert r.host.cmds("open")[-1]["mute"] is True


def test_the_voice_connection_carries_what_the_other_player_is_doing():
    r = Rig()
    ep = r.connect_as_initiator()
    p = r.peer(HIGHN)
    assert r.m.voice_state(p) == "-", "connected, but no word from them yet: nothing to claim"
    r.m.on_media(HIGH, ep, {"type": "vstate", "mute": False, "deaf": False})
    assert (p.rmute, p.rdeaf) == (False, False) and r.m.voice_state(p) == "o", "their microphone is open and quiet"
    p.audio_rx_on = True
    assert r.m.voice_state(p) == "s", "and when voice is arriving, speaking"
    r.m.on_media(HIGH, ep, {"type": "vstate", "mute": True, "deaf": False})
    assert r.m.voice_state(p) == "m", "muted wins over a tail of audio still arriving"
    r.m.on_media(HIGH, ep, {"type": "vstate", "mute": True, "deaf": True})
    assert r.m.voice_state(p) == "d", "deafened wins over muted"
    snap = [s for s in r.m.snapshot() if s["name"] == HIGHN][0]
    assert snap["vstate"] == "d" and snap["muted_by_me"] is False
    r.m.on_media(HIGH, ep + 1, {"type": "vstate", "mute": False, "deaf": False})
    assert r.m.voice_state(p) == "d", "a report from an earlier connection is ignored"
    r.lose(HIGHN)
    r.tick(3.0)
    assert r.m.voice_state(p) == "-", "out of range: nothing is claimed"
    r.m.shutdown()


def test_the_page_says_how_loud_a_voice_is_and_the_icon_letter_follows_it():
    r = Rig()
    ep = r.connect_as_initiator()
    p = r.peer(HIGHN)
    r.m.on_media(HIGH, ep, {"type": "vstate", "mute": False, "deaf": False})
    assert r.m.voice_state(p) == "o" and p.vlevel == 0 and not p.vlevel_seen, "connected and quiet: idle (vol_low)"
    p.audio_rx_on = True
    assert r.m.voice_state(p) == "s", "until the page has ever reported, packets arriving mean speaking"
    r.m.on_media(HIGH, ep, {"type": "vlevel", "level": 0})
    assert p.vlevel_seen and r.m.voice_state(p) == "o", "once the page reports, the page decides: packets arriving can be a tail of silence"
    r.m.on_media(HIGH, ep, {"type": "vlevel", "level": 1})
    assert r.m.voice_state(p) == "s", "ordinary speech (vol_mid)"
    r.m.on_media(HIGH, ep, {"type": "vlevel", "level": 2})
    assert r.m.voice_state(p) == "l", "a raised voice (vol_high)"
    snap = [s for s in r.m.snapshot() if s["name"] == HIGHN][0]
    assert snap["vlevel"] == 2 and snap["vstate"] == "l"
    r.m.on_media(HIGH, ep, {"type": "vstate", "mute": True, "deaf": False})
    assert r.m.voice_state(p) == "m", "muted wins over a loud tail"
    r.m.on_media(HIGH, ep, {"type": "vstate", "mute": False, "deaf": False})
    for bad in (7, -3, "x", None):
        r.m.on_media(HIGH, ep, {"type": "vlevel", "level": bad})
    assert p.vlevel in (0, 2), "a nonsense level is ignored or clamped, never trusted"
    r.m.on_media(HIGH, ep + 1, {"type": "vlevel", "level": 1})
    assert p.vlevel != 1 or p.vlevel_seen, "a report from an earlier connection is ignored"
    r.m.shutdown()


def test_nothing_is_claimed_for_a_player_who_is_not_connected():
    r = Rig()
    r.see(**{HIGHN: 5.0})
    assert r.m.voice_state(r.peer(HIGHN)) == "-"
