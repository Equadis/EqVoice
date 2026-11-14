"""Per-peer state machine: game distances and the server's verdict in, WebRTC connections and volume out.

    (no record)         UNKNOWN: nobody by that name is online, or the game cannot see them
    SEEN                the game can look them up (they are loaded near us) but they are outside say_radius
    NEARBY_PENDING      inside say_radius; waiting out the debounce and for the server to confirm that
                        the other side sees us too (mutual proximity)
    CONNECTING          WebRTC negotiation in progress
    CONNECTED           audio flowing; volume follows distance
    OUT_OF_RANGE_GRACE  left range, or the server withdrew the link, or the game stopped reporting: faded to
                        silence and no longer transmitting, connection kept for ``teardown_s`` so walking
                        back is instant and a missed scan costs nothing
    DISCONNECTED        closed; cooling down before another attempt

Who decides what. The game's distance decides volume (boosted right next to each other, full to ``full_radius``,
falling smoothly to nothing at the end of the curve: see ``voice_gain``) and when to start a connection. The server decides whether two clients may talk at
all: a connection is only started or accepted while the server says the link is up (both sides reported each
other, with distances that agree). Losing the link, or the game going quiet, fades the voice out; only after
``teardown_s`` of that is the connection actually closed.

Pure logic: time comes from ``clock`` and every side effect goes through the ``host`` object (signalling
send, media-engine command, event). tests/test_peers.py runs whole scenarios in microseconds that way.
"""
from __future__ import annotations

import enum
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import netdiag
from .trace import trace

log = logging.getLogger("eqvoice.peers")


class PS(str, enum.Enum):
    SEEN = "SEEN"
    NEARBY_PENDING = "NEARBY_PENDING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    OUT_OF_RANGE_GRACE = "OUT_OF_RANGE_GRACE"
    DISCONNECTED = "DISCONNECTED"


ACTIVE = (PS.CONNECTING, PS.CONNECTED, PS.OUT_OF_RANGE_GRACE)
UNKNOWN = "UNKNOWN"


@dataclass
class PeerConfig:
    say_radius: float = 41.0          # measured: /say reaches 40.8 to 41.2 yd on this server (docs/08); the app takes it from config.proximity
    full_radius: float = 30.0         # a voice is at its full volume up to here (a raised voice: loud_full_radius)
    peak_radius: float = 0.0          # where the volume curve ends and calls are kept up to (0 = say_radius)
    near_gain: float = 1.0            # a voice is this many times its own loudness inside boost_radius (the boost option)
    boost_radius: float = 0.0         # the full boost is held this far (0: none held: it eases away from the first inch) ...
    boost_fade_radius: float = 10.0   # ... and has eased away to nothing by here
    loud_full_radius: float = 0.0     # a raised voice stays at full volume up to here (0 = no further than full_radius)
    curve_power: float = 1.6          # how the volume falls between full_radius and the end: (1 - t) ** power
    leave_hysteresis: float = 3.0
    debounce_s: float = 0.8
    leave_grace_s: float = 1.5
    teardown_s: float = 8.0
    connect_timeout_s: float = 20.0
    max_peers: int = 8
    fade_in_ms: int = 300
    fade_out_ms: int = 500
    retry_backoff_s: tuple = (3, 6, 12, 24, 48)
    forget_s: float = 30.0
    fail_closed_s: float = 8.0
    link_resync_s: float = 6.0
    short_cooldown_s: float = 1.5
    gain_step: float = 0.03
    gain_hz: float = 4.0

    @property
    def reach(self) -> float:
        """The distance inside which a player is a candidate for a call: the furthest a voice can ever carry."""
        return max(self.say_radius, self.peak_radius)

    @property
    def wide(self) -> bool:
        """A raised voice stays at full volume further than an ordinary one, so the page needs the distance itself to work the volume out."""
        return self.loud_full_radius > self.full_radius

    @property
    def curve(self) -> dict:
        """What the media page needs to apply the distance curve itself (a raised voice stays louder further out than the page is told here).
        Same keys as ``DEFAULT_CURVE`` in ui/voicemodel.js."""
        return {"boost": self.near_gain, "boost_r": self.boost_radius, "boost_fade": self.boost_fade_radius, "full": self.full_radius,
                "full_loud": max(self.loud_full_radius, self.full_radius), "end": self.reach, "power": self.curve_power}

    @classmethod
    def from_section(cls, s) -> "PeerConfig":
        return cls(say_radius=float(s.say_radius), full_radius=float(s.full_radius),
                   peak_radius=float(s.get("peak_radius", 0.0) or 0.0), near_gain=float(s.get("near_gain", 1.0) or 1.0),
                   boost_radius=float(s.get("boost_radius", 0.0)), boost_fade_radius=float(s.get("boost_fade_radius", 10.0)),
                   loud_full_radius=float(s.get("loud_full_radius", 0.0) or 0.0), curve_power=float(s.get("curve_power", 1.6)),
                   leave_hysteresis=float(s.leave_hysteresis), debounce_s=s.debounce_s, leave_grace_s=s.leave_grace_s,
                   teardown_s=s.teardown_s, connect_timeout_s=s.connect_timeout_s, max_peers=s.max_peers,
                   fade_in_ms=s.fade_in_ms, fade_out_ms=s.fade_out_ms, retry_backoff_s=tuple(s.retry_backoff_s),
                   forget_s=s.forget_s, fail_closed_s=s.fail_closed_s)


def voice_gain(distance: float, curve: dict, loudness: float = 0.0) -> float:
    """How loud a voice is at ``distance`` yards, as a multiple of its own loudness (the same curve as ``curveGain`` in ui/voicemodel.js,
    which the page applies itself so that it can use how loud the speaker is being):

        the boost           ``boost`` times (2 = doubled) at 0 yd, easing away smoothly (a cosine, in decibels) to nothing at
                            ``boost_fade``: no flat stretch and no step, every yard closer is a little louder
        .. full             1: the voice's own loudness
        full .. end         falling to nothing at ``end``, as (1 - t) ** power

    ``loudness`` (0 = ordinary speech, 1 = shouting) moves ``full`` towards ``full_loud``."""
    return boost_for(distance, curve) * _rolloff(distance, curve, loudness)


def boost_for(distance: float, curve: dict) -> float:
    """The boost alone: ``boost`` ** (a half-cosine from 1 right next to them to 0 at ``boost_fade``). ``boost_r`` yards of full boost
    can be held first (0 by default)."""
    boost = max(1.0, curve.get("boost", 1.0))
    hold, fade = curve.get("boost_r", 0.0), curve.get("boost_fade", 0.0)
    if boost == 1.0 or distance >= fade:
        return 1.0
    t = 0.0 if distance <= hold else (distance - hold) / (fade - hold)
    return boost ** (0.5 * (1.0 + math.cos(math.pi * t)))


def _rolloff(distance: float, curve: dict, loudness: float) -> float:
    full = curve["full"]
    flat = full + max(0.0, min(1.0, loudness)) * max(0.0, curve.get("full_loud", full) - full)
    if distance <= flat:
        return 1.0
    if distance >= curve["end"]:
        return 0.0
    return (1.0 - (distance - flat) / (curve["end"] - flat)) ** curve.get("power", 1.6)


@dataclass(eq=False)
class Peer:
    name: str
    sid: str
    state: PS
    since: float
    dist: Optional[float] = None          # the game's latest reading; None = not reported right now
    last_dist: Optional[float] = None
    inside: bool = False
    near_since: Optional[float] = None
    out_since: Optional[float] = None
    link_dist: Optional[float] = None     # the server's figure for the pair
    initiator: bool = False
    ep: int = 0
    attempts: int = 0
    cooldown_until: float = 0.0
    ice: str = ""
    conn: str = ""
    level: float = 0.0
    err: str = ""
    capped: bool = False
    connected_at: Optional[float] = None
    gain: float = 0.0                     # what the page was last told
    gain_t: float = -1e9
    sent_dist: Optional[float] = None     # the distance the page was last told
    az: Optional[float] = None            # where they are: degrees from straight ahead of you, clockwise (None: the game cannot say)
    sent_az: Optional[float] = None       # ... as the page was last told
    sent_az_ep: int = 0
    sent_gate: bool = False               # ... and whether it was told the voice may be heard at all
    hold_gain: float = 0.0                # volume to keep while a reading is briefly missing
    stats: dict = field(default_factory=dict)
    early_ice: list = field(default_factory=list)      # (ep, candidate) heard before the offer that owns it
    # what the current WebRTC attempt has learned about its network path (reset by _begin_attempt)
    cands_local: dict = field(default_factory=dict)    # candidate type -> how many this PC gathered
    cands_remote: dict = field(default_factory=dict)   # ... how many the other side sent
    cands_logged: int = 0
    ice_errors: list = field(default_factory=list)     # STUN/TURN problems the browser reported
    open_t: float = 0.0                                # when this attempt started (our clock)
    gather_ms: Optional[float] = None                  # how long address gathering took
    pairinfo: Optional[dict] = None                    # the selected candidate pair, as the page described it
    path: str = ""                                     # relayed | direct-nat | direct-host | direct
    result_settled: bool = False                       # the 5-second "settled" webrtc_result line has been written
    rate_mark: Optional[tuple] = None                  # (time, rx bytes, tx bytes, packets lost, packets received)
    stats_due: float = 0.0
    rate_t: Optional[float] = None                     # smoothed audio bitrates, from the page's byte counters
    rate_rx: float = 0.0
    rate_tx: float = 0.0
    in_kbps: float = 0.0
    out_kbps: float = 0.0
    vlevel: int = 0                                    # how they sound to us, steadied by the page (ui/voicemodel.js VoiceActivity): 0 nothing, 1 speaking, 2 a raised voice
    vlevel_seen: bool = False                          # ... and whether the page has ever said (without it, 'speaking' falls back to the packets we receive)
    rmute: Optional[bool] = None                       # what THEIR EqVoice says over the voice connection: their microphone is closed ...
    rdeaf: Optional[bool] = None                       # ... and whether they have deafened themselves (None: no word from them yet)


class PeerManager:
    def __init__(self, host, cfg: PeerConfig, clock: Callable[[], float] = time.monotonic,
                 ice_provider: Callable[[], list] = lambda: [], rng: Optional[random.Random] = None):
        self.host, self.cfg, self.clock, self.ice_provider = host, cfg, clock, ice_provider
        self.peers: dict = {}                 # name.lower() -> Peer
        self.by_sid: dict = {}                # sid -> name.lower()
        self.my_sid: Optional[str] = None
        self.registered = False
        self.media_ready = True               # False while no media page is connected
        self.linked: dict = {}                # sid -> the server's distance for the pair
        self.stale_links: set = set()
        self.resync_until: Optional[float] = None
        self.sig_down_since: Optional[float] = None
        self._ep = (rng or random.Random()).randrange(1, 1 << 20)
        self.log_ips = False                   # put real addresses in the candidate lines (default: only their kind)
        self.setup_ms: list = []               # how long each successful connection took to set up (last 50)
        self.muted: set = set()                # name.lower() of the players whose voice you do not want to hear (the mute list)
        self.blocked: set = set()              # name.lower() of the players who must not hear you: your microphone is never sent to them
        self.volumes: dict = {}                # name.lower() -> 0.0 .. 2.0: how loud that one player is for you

    # ------------------------------------------------------------ the mute list

    def is_muted(self, name: str) -> bool:
        return str(name).lower() in self.muted

    def set_muted(self, name: str, on: bool) -> None:
        """Add or take someone off the mute list. Their connection stays up (they can still hear you); only their voice stops playing here."""
        key = str(name).strip().lower()
        if not key:
            return
        (self.muted.add if on else self.muted.discard)(key)
        p = self.peers.get(key)
        if p is not None and p.ep:
            self.host.media({"cmd": "peer_mute", "sid": p.sid, "ep": p.ep, "value": bool(on)})

    # ------------------------------------------------------------ block, and one player's volume

    def is_blocked(self, name: str) -> bool:
        return str(name).lower() in self.blocked

    def set_blocked(self, name: str, on: bool) -> None:
        """Block: that player stops receiving your voice (the page is told to send them nothing, at once). You still hear them unless you
        also mute them; the connection stays up, so taking the block off needs no new call."""
        key = str(name).strip().lower()
        if not key:
            return
        (self.blocked.add if on else self.blocked.discard)(key)
        p = self.peers.get(key)
        if p is not None and p.ep and p.state in (PS.CONNECTED, PS.OUT_OF_RANGE_GRACE):
            self._send_gain(p, p.gain, int(1000 / self.cfg.gain_hz) + 50, gate=p.sent_gate)

    def volume_of(self, name: str) -> float:
        return float(self.volumes.get(str(name).lower(), 1.0))

    def set_volume(self, name: str, value: float) -> None:
        key = str(name).strip().lower()
        if not key:
            return
        v = max(0.0, min(2.0, float(value)))
        if abs(v - 1.0) < 0.005:
            self.volumes.pop(key, None)
        else:
            self.volumes[key] = v
        p = self.peers.get(key)
        if p is not None and p.ep:
            self.host.media({"cmd": "volume", "sid": p.sid, "value": round(v, 2)})

    def _peer_prefs(self, p: Peer) -> None:
        """Right after a voice connection is opened: this player's own volume (the page starts every voice at 100%)."""
        v = self.volume_of(p.name)
        if abs(v - 1.0) >= 0.005:
            self.host.media({"cmd": "volume", "sid": p.sid, "value": round(v, 2)})

    # ------------------------------------------------------------ where each voice comes from

    def update_bearings(self, bearings: dict) -> None:
        """``bearings``: lower-case name -> degrees from straight ahead, clockwise (the game's direction file). A player it does not list has
        no direction (their voice is centred). The page is told when a direction moves by two degrees or more."""
        for key, p in self.peers.items():
            az = bearings.get(key)
            p.az = az
            if not p.ep or p.state not in (PS.CONNECTED, PS.OUT_OF_RANGE_GRACE, PS.CONNECTING):
                continue
            same_call = p.sent_az_ep == p.ep
            if same_call and ((az is None and p.sent_az is None) or (az is not None and p.sent_az is not None and abs(((az - p.sent_az + 180.0) % 360.0) - 180.0) < 2.0)):
                continue
            p.sent_az, p.sent_az_ep = az, p.ep
            self.host.media({"cmd": "dir", "sid": p.sid, "ep": p.ep, "az": None if az is None else round(az, 1)})

    # ------------------------------------------------------------- helpers

    def _next_ep(self) -> int:
        self._ep += 1
        return self._ep

    def _turn_configured(self) -> bool:
        for s in self.ice_provider() or []:
            urls = s.get("urls") if isinstance(s, dict) else None
            for u in ([urls] if isinstance(urls, str) else urls or []):
                if str(u).startswith(("turn:", "turns:")):
                    return True
        return False

    def _begin_attempt(self, p: Peer) -> None:
        """A new WebRTC attempt starts: forget what the last one learned about the network."""
        p.cands_local, p.cands_remote, p.cands_logged, p.ice_errors = {}, {}, 0, []
        p.open_t, p.gather_ms, p.pairinfo, p.path, p.rate_mark, p.stats_due = self.clock(), None, None, "", None, 0.0
        p.rate_t, p.in_kbps, p.out_kbps = None, 0.0, 0.0
        p.result_settled = False

    def _note_candidate(self, p: Peer, side: str, cand) -> None:
        c = netdiag.parse_candidate(cand)
        if c is None:
            return
        counts = p.cands_local if side == "local" else p.cands_remote
        counts[c["type"]] = counts.get(c["type"], 0) + 1
        if p.cands_logged < 24:
            p.cands_logged += 1
            extra = {"address": "%s:%s" % (c["address"], c["port"])} if self.log_ips else {}
            trace("webrtc_candidate", name=p.name, side=side, type=c["type"], proto=c["proto"], network=c["net"], **extra)

    def _explain(self, p: Peer, why: str) -> str:
        """Log, and return, the likeliest reason an attempt ended (only meaningful before it ever connected)."""
        if p.connected_at is not None:
            text = "the connection was up for %.0f s and then dropped" % max(0.0, self.clock() - p.connected_at)
            code = "dropped"
        else:
            code, text = netdiag.diagnose(p.cands_local, p.cands_remote, p.ice_errors, p.ice, self._turn_configured())
        trace("webrtc_result", name=p.name, result="FAILED", reason=code, explanation=text, local_candidates=netdiag.fmt_counts(p.cands_local),
              remote_candidates=netdiag.fmt_counts(p.cands_remote), setup_ms=round((self.clock() - p.open_t) * 1000) if p.open_t else None)
        trace("webrtc_failed", name=p.name, why=why, reason=code, explanation=text, local_candidates=netdiag.fmt_counts(p.cands_local),
              remote_candidates=netdiag.fmt_counts(p.cands_remote), ice=p.ice or "-", connection=p.conn or "-",
              stun_errors=len(p.ice_errors), gather_ms=p.gather_ms, attempt=p.attempts + 1, turn_available=self._turn_configured())
        return text

    def _enter(self, p: Peer, new: PS, why: str = "") -> None:
        old = p.state
        p.state, p.since = new, self.clock()
        log.info("TRACE desktop peer name=%s %s -> %s%s", p.name, old.value, new.value, (' why="' + why + '"') if why else "")
        self.host.emit("peer_state", sid=p.sid, name=p.name, old=old.value, new=new.value, why=why)

    def _active_count(self) -> int:
        return sum(1 for p in self.peers.values() if p.state in ACTIVE)

    def _drop_record(self, p: Peer, why: str) -> None:
        self.peers.pop(p.name.lower(), None)
        if self.by_sid.get(p.sid) == p.name.lower():
            del self.by_sid[p.sid]
        self.host.emit("peer_state", sid=p.sid, name=p.name, old=p.state.value, new=UNKNOWN, why=why)
        log.info('TRACE desktop peer name=%s %s -> UNKNOWN why="%s"', p.name, p.state.value, why)

    def _send_gain(self, p: Peer, value: float, ms: int, gate: bool = True) -> None:
        """Tell the page how loud this voice is (``value``, for an ordinary voice at the current distance), where they are (``dist``),
        and whether they may be heard at all (``gate``: false while leaving, so even a shout is silent). With a raised-voice range the
        page works the volume out itself from ``dist`` and how loud the speaker is being; ``value`` is then the plain-speech answer.
        We transmit to a peer for as long as they could hear us, which is the whole call range, not only where an ordinary voice carries."""
        p.gain, p.gain_t, p.sent_dist, p.sent_gate = value, self.clock(), p.dist, gate
        self.host.media({"cmd": "gain", "sid": p.sid, "ep": p.ep, "value": round(value, 3), "ms": ms, "gate": gate,
                         "dist": None if p.dist is None else round(p.dist, 1),
                         "send": gate and (value > 0 or self.cfg.wide) and not self.is_blocked(p.name)})

    def _fade_out(self, p: Peer) -> None:
        if p.gain > 0 or p.gain_t < 0 or p.sent_gate:
            self._send_gain(p, 0.0, self.cfg.fade_out_ms, gate=False)

    def _close(self, p: Peer, notify: bool) -> None:
        if p.ep:
            self.host.media({"cmd": "close", "sid": p.sid, "ep": p.ep})
            if notify and self.registered and p.sid in self.linked:      # the hub only relays between linked peers
                self.host.signal(p.sid, "bye", p.ep, {})
        p.ice = p.conn = ""
        p.level = 0.0
        p.in_kbps = p.out_kbps = 0.0
        p.gain, p.gain_t, p.hold_gain = 0.0, -1e9, 0.0
        p.sent_dist, p.sent_gate = None, False
        p.rmute = p.rdeaf = None
        p.connected_at = None

    def _to_disconnected(self, p: Peer, why: str, cooldown: float, notify: bool = True) -> None:
        if p.ep:
            trace("webrtc_closed", name=p.name, why=why, ep=p.ep, goodbye_sent=bool(notify and self.registered and p.sid in self.linked))
        self._close(p, notify)
        p.cooldown_until = self.clock() + cooldown
        self._enter(p, PS.DISCONNECTED, why)

    def _fail(self, p: Peer, why: str) -> None:
        text = self._explain(p, why) if p.state == PS.CONNECTING or p.connected_at is not None else ""
        p.attempts += 1
        p.err = (why + " - " + text) if text else why
        back = self.cfg.retry_backoff_s[min(p.attempts - 1, len(self.cfg.retry_backoff_s) - 1)]
        self._to_disconnected(p, why, back)

    def _wants_link(self, p: Peer) -> bool:
        return p.sid in self.linked

    # -------------------------------------------------------------- inputs

    def set_self(self, sid: Optional[str]) -> None:
        """A new identity (character, login, or logout): nothing carries over."""
        if sid == self.my_sid:
            return
        for p in list(self.peers.values()):
            if p.state in ACTIVE:
                self._close(p, notify=False)
        self.peers.clear()
        self.by_sid.clear()
        self.linked.clear()
        self.stale_links.clear()
        self.resync_until = None
        self.my_sid = sid
        log.info("own session id is now %s", sid)

    def on_signalling(self, registered: bool) -> None:
        """Signalling session established (True) or lost (False)."""
        now = self.clock()
        was = self.registered
        self.registered = registered
        if registered:
            self.sig_down_since = None
            if not was and self.linked:
                self.stale_links = set(self.linked)          # confirmed again, or dropped, within link_resync_s
                self.resync_until = now + self.cfg.link_resync_s
        else:
            if was or self.sig_down_since is None:
                self.sig_down_since = now
            self.resync_until = None

    def set_media_ready(self, ready: bool) -> None:
        """The page that owns the WebRTC sessions connected (True) or went away (False)."""
        self.media_ready = ready
        self.media_reset()          # sessions held by a previous page are gone either way

    def roster_changed(self, replaced: list = (), removed_sids: list = ()) -> None:
        """A name got a new session id (they logged in again) or an id left the roster (they logged out)."""
        for name in replaced:
            p = self.peers.get(name.lower())
            if p is not None:
                if p.state in ACTIVE:
                    self._close(p, notify=False)
                self.linked.pop(p.sid, None)
                self.stale_links.discard(p.sid)
                self._drop_record(p, "logged in again")
        for sid in removed_sids:
            key = self.by_sid.get(sid)
            self.linked.pop(sid, None)
            self.stale_links.discard(sid)
            p = self.peers.get(key) if key else None
            if p is not None:
                if p.state in ACTIVE:
                    self._close(p, notify=False)
                self._drop_record(p, "went offline")

    def update_readings(self, readings: dict, roster) -> None:
        """``readings``: character name -> yards, as the game currently reports them (empty = nobody)."""
        now = self.clock()
        seen = set()
        for name, dist in readings.items():
            key = name.lower()
            sid = roster.sid_of(name)
            if sid is None or sid == self.my_sid:
                continue
            seen.add(key)
            p = self.peers.get(key)
            if p is not None and p.sid != sid:                # the roster moved on without telling us
                self.roster_changed(replaced=[p.name])
                p = None
            if p is None:
                p = Peer(name=roster.name_of(sid) or name, sid=sid, state=PS.SEEN, since=now)
                self.peers[key] = p
                self.by_sid[sid] = key
                self.host.emit("peer_state", sid=sid, name=p.name, old=UNKNOWN, new=PS.SEEN.value, why="game sees them")
                log.info("TRACE desktop peer name=%s UNKNOWN -> SEEN why=\"the game sees them at %.1f yd\"", p.name, dist)
            self._presence(p, float(dist), now)
        for key, p in list(self.peers.items()):
            if key not in seen:
                self._presence(p, None, now)

    def _presence(self, p: Peer, dist: Optional[float], now: float) -> None:
        c = self.cfg
        p.dist = dist
        if dist is not None:
            p.last_dist = dist
        was = p.inside
        limit = c.reach + c.leave_hysteresis if (was or p.state in (PS.CONNECTING, PS.CONNECTED)) else c.reach
        p.inside = dist is not None and dist <= limit
        if p.inside and not was:
            p.near_since, p.out_since = now, None
        elif was and not p.inside:
            p.near_since, p.out_since = None, now
        elif not p.inside and p.out_since is None:
            p.out_since = now

    def on_link(self, sid: str, up: bool, distance: Optional[float] = None) -> None:
        """The server's verdict: both of us see each other (up) or no longer do."""
        if up:
            self.linked[sid] = distance
            self.stale_links.discard(sid)
        else:
            self.linked.pop(sid, None)
            self.stale_links.discard(sid)
        key = self.by_sid.get(sid)
        p = self.peers.get(key) if key else None
        if p is None:
            return
        p.link_dist = distance if up else None
        if up:
            return                                    # the tick decides what a restored link means
        if p.state == PS.CONNECTED:
            self._enter(p, PS.OUT_OF_RANGE_GRACE, "server withdrew the link")
            self._fade_out(p)
        elif p.state == PS.CONNECTING:
            self._to_disconnected(p, "link dropped while connecting", self.cfg.short_cooldown_s)

    def on_signal(self, frm: str, kind: str, ep: int, data: dict) -> None:
        key = self.by_sid.get(frm)
        p = self.peers.get(key) if key else None
        if kind == "offer":
            self._on_offer(p, frm, ep, data)
        elif p is None:
            return
        elif kind == "answer":
            if p.state == PS.CONNECTING and p.initiator and ep == p.ep:
                self.host.media({"cmd": "remote", "sid": frm, "ep": ep, "type": "answer", "sdp": data.get("sdp", "")})
        elif kind == "ice":
            cand = data.get("candidate")
            if not cand:
                return
            if p.state in ACTIVE and ep == p.ep:
                self._note_candidate(p, "remote", cand)
                self.host.media({"cmd": "candidate", "sid": frm, "ep": ep, "candidate": cand})
            elif p.state not in ACTIVE and not p.initiator and ep > p.ep and len(p.early_ice) < 64:
                p.early_ice.append((ep, cand))             # the offer that owns it is still on its way
        elif kind == "bye":
            if p.state in ACTIVE and ep >= p.ep:
                cooldown = self.cfg.short_cooldown_s
                if p.state == PS.CONNECTING and p.initiator:      # refused: do not ask again at once
                    p.attempts += 1
                    cooldown = self.cfg.retry_backoff_s[min(p.attempts - 1, len(self.cfg.retry_backoff_s) - 1)]
                self._to_disconnected(p, "peer closed the connection", cooldown, notify=False)

    def _on_offer(self, p: Optional[Peer], frm: str, ep: int, data: dict) -> None:
        def refuse(why):
            log.info("refusing offer from %s: %s", frm, why)
            self.host.signal(frm, "bye", ep, {})

        c = self.cfg
        if not self.media_ready:
            return refuse("no media engine")
        if self.my_sid is None or not (frm < self.my_sid):
            return refuse("only the lower session id offers")
        if p is None or not self._wants_link(p):
            return refuse("not linked")
        if p.dist is None or p.dist > c.reach + c.leave_hysteresis:
            return refuse("the game does not see them in range")
        if p.state in ACTIVE:
            if ep <= p.ep:
                return                                   # duplicate or stale
            self._close(p, notify=False)                 # a newer attempt supersedes
        elif self._active_count() >= c.max_peers:
            p.capped = True
            return refuse("at capacity")
        p.ep, p.initiator, p.err, p.capped = ep, False, "", False
        self._begin_attempt(p)
        p.inside = True                                  # accepted at the leave threshold: no instant flip-flop
        p.near_since, p.out_since = self.clock(), None
        self._enter(p, PS.CONNECTING, "offer received")
        self.host.media({"cmd": "open", "sid": frm, "ep": ep, "initiator": False, "ice": self.ice_provider(), "mute": self.is_muted(p.name)})
        self._peer_prefs(p)
        self.host.media({"cmd": "remote", "sid": frm, "ep": ep, "type": "offer", "sdp": data.get("sdp", "")})
        early, p.early_ice = p.early_ice, []
        for e, cand in early:
            if e == ep:
                self._note_candidate(p, "remote", cand)
                self.host.media({"cmd": "candidate", "sid": frm, "ep": ep, "candidate": cand})

    def on_media(self, sid: str, ep: int, evt: dict) -> None:
        key = self.by_sid.get(sid)
        p = self.peers.get(key) if key else None
        if p is None or ep != p.ep:
            return
        t = evt.get("type")
        if t == "description":
            trace("webrtc_signal", name=p.name, sent=evt["sdp_type"], ep=ep)
            self.host.signal(sid, evt["sdp_type"], ep, {"sdp": evt["sdp"]})
        elif t == "candidate":
            self._note_candidate(p, "local", evt["candidate"])
            p.ice_sent = getattr(p, "ice_sent", 0) + 1
            if p.ice_sent in (1, 5, 20):
                trace("webrtc_signal", name=p.name, sent="ice candidate #%d" % p.ice_sent, ep=ep)
            self.host.signal(sid, "ice", ep, {"candidate": evt["candidate"]})
        elif t == "state":
            before = (p.ice, p.conn)
            p.ice, p.conn = evt.get("ice", ""), evt.get("conn", "")
            if (p.ice, p.conn) != before:
                trace("webrtc", name=p.name, ice=p.ice, connection=p.conn, gathering=evt.get("gather"), ms=evt.get("ms"), ep=ep,
                      pair=(p.stats or {}).get("pair"))
            ok = p.conn == "connected" or p.ice in ("connected", "completed")
            bad = p.conn == "failed" or p.ice == "failed" or p.conn == "closed"
            if p.state == PS.CONNECTING and ok:
                total_ms = round((self.clock() - p.open_t) * 1000) if p.open_t else None
                if total_ms is not None:
                    self.setup_ms = (self.setup_ms + [total_ms])[-50:]
                trace("webrtc_connected", name=p.name, ice_ms=evt.get("ms"),
                      total_ms=total_ms, attempt=p.attempts + 1,
                      local_candidates=netdiag.fmt_counts(p.cands_local), remote_candidates=netdiag.fmt_counts(p.cands_remote),
                      gather_ms=p.gather_ms, role="offerer" if p.initiator else "answerer")
                p.attempts = 0
                p.err = ""
                p.connected_at = self.clock()
                self._enter(p, PS.CONNECTED, "media connected")
                g = self._target_gain(p)
                p.hold_gain = g
                self._send_gain(p, g, self.cfg.fade_in_ms)
            elif bad and p.state in ACTIVE:
                self._fail(p, "connection %s (ice %s)" % (p.conn or "?", p.ice or "?"))
        elif t == "gathering":
            if evt.get("state") == "complete":
                p.gather_ms = evt.get("ms")
                trace("webrtc_gathered", name=p.name, gather_ms=p.gather_ms, local_candidates=netdiag.fmt_counts(p.cands_local),
                      stun_errors=len(p.ice_errors),
                      public_address_found=bool(p.cands_local.get("srflx") or p.cands_local.get("relay")))
        elif t == "ice_error":
            if len(p.ice_errors) < 16:
                p.ice_errors.append({"code": evt.get("code"), "url": evt.get("url", ""), "text": evt.get("text", "")})
                trace("webrtc_ice_error", name=p.name, code=evt.get("code"), server=evt.get("url") or "-", text=evt.get("text") or "-",
                      ms=evt.get("ms"))
        elif t == "vlevel":
            try:
                lv = max(0, min(2, int(evt.get("level", 0))))
            except (TypeError, ValueError):
                return
            if lv != p.vlevel:
                trace("voice_level", name=p.name, level=("nothing", "speaking", "loud")[lv])
            p.vlevel, p.vlevel_seen = lv, True
        elif t == "stats":
            p.level = float(evt.get("level", 0.0))
            prev = p.stats or {}
            p.stats = {k: v for k, v in evt.items() if k not in ("type",)}
            self._note_path(p, p.stats)
            self._update_rates(p, p.stats)
            self._webrtc_stats(p, p.stats)
            self._narrate_audio(p, prev, p.stats)
        elif t == "vstate":
            mute, deaf = bool(evt.get("mute")), bool(evt.get("deaf"))
            if (mute, deaf) != (p.rmute, p.rdeaf):
                p.rmute, p.rdeaf = mute, deaf
                trace("peer_voice_state", name=p.name, muted=mute, deafened=deaf)
        elif t == "error":
            if p.state in ACTIVE:
                self._fail(p, "media error: %s" % evt.get("msg", "?"))

    def _note_path(self, p: Peer, st: dict) -> None:
        """Say once which kind of path WebRTC chose, and whether a TURN relay carries it."""
        pi = st.get("pairinfo")
        if not isinstance(pi, dict) or pi == p.pairinfo or p.state not in ACTIVE:
            return
        p.pairinfo = pi
        p.path = netdiag.classify_pair(str(pi.get("lt", "")), str(pi.get("rt", "")))
        trace("webrtc_path", name=p.name, path=p.path, meaning=netdiag.PATH_WORDS.get(p.path, "?"),
              pair="%s>%s" % (pi.get("lt"), pi.get("rt")), proto=pi.get("proto") or "-", local_network=pi.get("lnet") or "-",
              remote_network=pi.get("rnet") or "-", turn_used=p.path == "relayed", relay_protocol=pi.get("rp") or None,
              interface=pi.get("net") or None)
        self._emit_result(p, st, "connected")

    def _emit_result(self, p: Peer, st: dict, phase: str) -> None:
        """THE one line to read after a test: how this call is carried (direct, via STUN-found addresses, via a TURN relay), over which
        ICE pair, and how it performs (round trip, jitter, loss each way, set-up time). Written when the path is first known and
        again once it has settled (about 5 s later, with real statistics)."""
        pi = p.pairinfo or {}
        label = netdiag.RESULT_LABELS.get(p.path, "UNKNOWN")
        lost, got = float(st.get("lost", 0) or 0), float(st.get("pr", 0) or 0)
        rlost = st.get("rlost")
        trace("webrtc_result", name=p.name, result=label, meaning=netdiag.PATH_WORDS.get(p.path, "?"), phase=phase,
              pair="%s>%s" % (pi.get("lt"), pi.get("rt")), proto=pi.get("proto") or "-", local_network=pi.get("lnet") or "-",
              remote_network=pi.get("rnet") or "-", turn_used=p.path == "relayed", rtt_ms=st.get("rtt"),
              jitter_ms=round(float(st.get("jitter", 0) or 0) * 1000, 1),
              loss_in_pct=round(100.0 * lost / (lost + got), 1) if lost + got > 0 else 0.0,
              loss_out_pct=round(float(rlost) * 100, 1) if rlost is not None else None,
              setup_ms=round((p.connected_at - p.open_t) * 1000) if p.connected_at and p.open_t else None)

    WEBRTC_STATS_EVERY_S = 5.0

    def _update_rates(self, p: Peer, st: dict) -> None:
        """Smoothed audio bitrate in and out for this peer (so the mesh total can be read at any moment)."""
        now = self.clock()
        rx, tx = float(st.get("rx", 0) or 0), float(st.get("tx", 0) or 0)
        if p.rate_t is not None and now - p.rate_t >= 0.2:
            dt = now - p.rate_t
            p.in_kbps = 0.5 * p.in_kbps + 0.5 * max(0.0, (rx - p.rate_rx) * 8 / dt / 1000)
            p.out_kbps = 0.5 * p.out_kbps + 0.5 * max(0.0, (tx - p.rate_tx) * 8 / dt / 1000)
        if p.rate_t is None or now - p.rate_t >= 0.2:
            p.rate_t, p.rate_rx, p.rate_tx = now, rx, tx

    def aggregate(self) -> dict:
        """The mesh at a glance: connections, total audio bitrates, and how long connections took to set up."""
        live = [p for p in self.peers.values() if p.state == PS.CONNECTED]
        ms = self.setup_ms
        return {"active": self._active_count(), "connected": len(live),
                "in_kbps": round(sum(p.in_kbps for p in live), 1), "out_kbps": round(sum(p.out_kbps for p in live), 1),
                "setup_ms_avg": round(sum(ms) / len(ms)) if ms else None, "setup_ms_max": max(ms) if ms else None,
                "setup_ms_last": ms[-1] if ms else None, "setups": len(ms)}

    def _webrtc_stats(self, p: Peer, st: dict) -> None:
        """A line every few seconds while connected: round trip, jitter, loss both ways and the actual bitrates."""
        if p.state != PS.CONNECTED:
            return
        now = self.clock()
        rx, tx = float(st.get("rx", 0) or 0), float(st.get("tx", 0) or 0)
        lost, got = float(st.get("lost", 0) or 0), float(st.get("pr", 0) or 0)
        if p.rate_mark is None:
            p.rate_mark, p.stats_due = (now, rx, tx, lost, got), now + self.WEBRTC_STATS_EVERY_S
            return
        if not p.result_settled and p.path and now >= p.stats_due:
            p.result_settled = True
            self._emit_result(p, st, "settled")
        if now < p.stats_due:
            return
        t0, rx0, tx0, lost0, got0 = p.rate_mark
        dt = max(1e-6, now - t0)
        dl, dg = lost - lost0, got - got0
        rlost = st.get("rlost")
        p.rate_mark, p.stats_due = (now, rx, tx, lost, got), now + self.WEBRTC_STATS_EVERY_S
        trace("webrtc_stats", name=p.name, path=p.path or "-", rtt_ms=st.get("rtt"),
              jitter_ms=round(float(st.get("jitter", 0) or 0) * 1000, 1),
              loss_in_pct=round(100.0 * dl / (dl + dg), 1) if dl + dg > 0 else 0.0,
              loss_out_pct=round(float(rlost) * 100, 1) if rlost is not None else None,
              in_kbps=round((rx - rx0) * 8 / dt / 1000, 1), out_kbps=round((tx - tx0) * 8 / dt / 1000, 1),
              send_budget_kbps=st.get("avail"))

    # thresholds per ~0.5 s stats interval: speech decoded adds audible energy (quiet speech through a USB mic measured
    # 0.001-0.002 on 2026-10-17); DTX comfort noise adds almost none (~1e-6)
    RX_ENERGY = 0.0004
    TX_BYTES = 250
    FLOW_EVERY_S = 5.0

    def _narrate_audio(self, p: Peer, prev: dict, cur: dict) -> None:
        """One line when audio starts or stops flowing in either direction (hysteresis: 3 quiet samples to call it silent)."""
        if p.state not in ACTIVE:
            return
        de = float(cur.get("energy", 0) or 0) - float(prev.get("energy", 0) or 0)
        db = float(cur.get("tx", 0) or 0) - float(prev.get("tx", 0) or 0)
        # A connection that has just opened sends a burst of setup packets with nobody talking: only count bytes as
        # "your voice" while the host says the microphone is open (push-to-talk down, or open mic).
        talking = getattr(self.host, "tx_enabled", True)
        # Packets that arrive carrying almost no sound are a different fault from no packets: the far side's microphone is
        # too quiet or off. Say so once instead of leaving the listener with silence and no explanation.
        rb = float(cur.get("rx", 0) or 0) - float(prev.get("rx", 0) or 0)
        quiet_packets = de < self.RX_ENERGY and prev.get("rx") is not None and rb >= self.TX_BYTES
        if quiet_packets and not getattr(p, "audio_rx_on", False) and not getattr(p, "audio_rx_weak", False):
            p.audio_rx_weak = True
            trace("audio_rx", name=p.name, state="ARRIVING_QUIET", energy_gain=round(de, 5), bytes_in_interval=int(rb),
                  meaning="packets arrive but carry almost no sound: their microphone is too quiet or not open")
        elif not quiet_packets:
            p.audio_rx_weak = False
        # a running total every few seconds, so a run can be read afterwards even when nothing crossed a threshold
        now = self.clock()
        if now >= getattr(p, "flow_due", 0.0):
            p.flow_due = now + self.FLOW_EVERY_S
            trace("audio_flow", name=p.name, rx_bytes=cur.get("rx"), rx_energy_total=cur.get("energy"), tx_bytes=cur.get("tx"),
                  heard_level=cur.get("level"), playing=cur.get("playing"), element_volume=cur.get("vol"), mic_open=bool(talking))
        for kind, hot in (("rx", de >= self.RX_ENERGY and prev.get("energy") is not None),
                          ("tx", db >= self.TX_BYTES and prev.get("tx") is not None and talking)):
            on_attr, quiet_attr = "audio_%s_on" % kind, "audio_%s_quiet" % kind
            on = getattr(p, on_attr, False)
            if hot:
                setattr(p, quiet_attr, 0)
                if not on:
                    setattr(p, on_attr, True)
                    if kind == "rx":
                        trace("audio_rx", name=p.name, state="RECEIVING", energy_gain=round(de, 4), their_voice_reaches_you=True,
                              playback_gain=round(p.gain, 2), rx_bytes=cur.get("rx"), playing=cur.get("playing"),
                              muted=cur.get("muted"), element_volume=cur.get("vol"), speakers=cur.get("sink") or "system default")
                    else:
                        trace("audio_tx", name=p.name, state="SENDING", bytes_in_interval=int(db), your_voice_reaches_them=True,
                              tx_bytes=cur.get("tx"))
            elif on:
                q = getattr(p, quiet_attr, 0) + 1
                setattr(p, quiet_attr, q)
                if q >= 3:
                    setattr(p, on_attr, False)
                    trace("audio_%s" % kind, name=p.name, state="silent")

    def media_reset(self) -> None:
        """The media page went away or came back: every WebRTC session it held is gone."""
        for p in list(self.peers.values()):
            if p.state in ACTIVE:
                self._to_disconnected(p, "media engine restarted", self.cfg.short_cooldown_s, notify=True)

    # ---------------------------------------------------------------- tick

    def _target_gain(self, p: Peer) -> float:
        if p.dist is not None:
            return voice_gain(p.dist, self.cfg.curve)
        return p.hold_gain                      # a reading is briefly missing: keep the last volume

    def _gone_for(self, p: Peer, now: float) -> bool:
        """Outside the leave radius (or unseen) for longer than the leave grace."""
        return (not p.inside) and p.out_since is not None and now - p.out_since >= self.cfg.leave_grace_s

    def tick(self) -> None:
        now = self.clock()
        c = self.cfg
        if self.resync_until is not None and now >= self.resync_until:
            for sid in list(self.stale_links):
                self.on_link(sid, False)
            self.stale_links.clear()
            self.resync_until = None
        if self.sig_down_since is not None and now - self.sig_down_since >= c.fail_closed_s and self.linked:
            log.warning("signalling has been down for %.0fs: fading everyone out", now - self.sig_down_since)
            for sid in list(self.linked):
                self.on_link(sid, False)
        # nearest first: when there are more candidates than call slots (a crowd, now that a shout carries 85 yd), the ones next to you get the slots
        for key, p in sorted(self.peers.items(), key=lambda kv: kv[1].dist if kv[1].dist is not None else 1e9):
            st = p.state
            if st == PS.SEEN:
                if p.dist is None:
                    self._drop_record(p, "no longer seen")
                elif p.inside:
                    self._enter(p, PS.NEARBY_PENDING, "inside %.0f yd" % c.reach)
                    st = p.state
            if st == PS.NEARBY_PENDING:
                if not p.inside:
                    if p.dist is None:
                        self._drop_record(p, "no longer seen")
                    else:
                        self._enter(p, PS.SEEN, "left the radius before connecting")
                else:
                    self._maybe_connect(p, now)
            elif st == PS.CONNECTING:
                if now - p.since > c.connect_timeout_s:
                    self._fail(p, "connect timeout")
                elif self._gone_for(p, now):
                    self._to_disconnected(p, "left range while connecting", c.short_cooldown_s)
            elif st == PS.CONNECTED:
                if self._gone_for(p, now):
                    self._enter(p, PS.OUT_OF_RANGE_GRACE, "left range")
                    self._fade_out(p)
                else:
                    self._follow_distance(p, now)
            elif st == PS.OUT_OF_RANGE_GRACE:
                if p.inside and self._wants_link(p) and self.media_ready:
                    self._enter(p, PS.CONNECTED, "back in range")
                    g = self._target_gain(p)
                    p.hold_gain = g
                    self._send_gain(p, g, c.fade_in_ms)
                elif now - p.since >= c.teardown_s:
                    self._to_disconnected(p, "out of range", c.short_cooldown_s)
            elif st == PS.DISCONNECTED:
                if now >= p.cooldown_until:
                    if p.dist is None and now - p.since > c.forget_s:
                        self._drop_record(p, "gone")
                    elif p.dist is not None:
                        self._enter(p, PS.NEARBY_PENDING if p.inside else PS.SEEN, "cooldown over")

    def _follow_distance(self, p: Peer, now: float) -> None:
        g = self._target_gain(p)
        if p.dist is not None:
            p.hold_gain = g
        step = self.cfg.gain_step
        due = now - p.gain_t >= 1.0 / self.cfg.gain_hz
        edge = g in (0.0, 1.0, self.cfg.near_gain) and p.gain != g
        # a raised voice stays louder further out than the plain-speech volume says, so the page needs the distance itself as it changes
        moved = self.cfg.wide and p.dist is not None and (p.sent_dist is None or abs(p.dist - p.sent_dist) >= 0.25)
        if (abs(g - p.gain) >= step or edge or moved) and due:
            self._send_gain(p, g, int(1000 / self.cfg.gain_hz) + 50)

    def _maybe_connect(self, p: Peer, now: float) -> None:
        c = self.cfg
        if not self.registered or not self.media_ready or self.my_sid is None or not self._wants_link(p):
            return
        if p.near_since is None or now - p.near_since < c.debounce_s or now < p.cooldown_until:
            return
        if self._active_count() >= c.max_peers:
            p.capped = True
            return
        p.capped = False
        if self.my_sid < p.sid:                     # the lower id makes the offer
            p.ep, p.initiator, p.err = self._next_ep(), True, ""
            self._begin_attempt(p)
            self._enter(p, PS.CONNECTING, "initiating")
            self.host.media({"cmd": "open", "sid": p.sid, "ep": p.ep, "initiator": True, "ice": self.ice_provider(), "mute": self.is_muted(p.name)})
            self._peer_prefs(p)

    # ------------------------------------------------------------ reporting

    def nearby_count(self) -> int:
        return sum(1 for p in self.peers.values() if p.inside)

    def counts(self) -> dict:
        out = {s.value: 0 for s in PS}
        for p in self.peers.values():
            out[p.state.value] += 1
        return out

    def voice_state(self, p: Peer) -> str:
        """What the little icon beside this player's name should show, as one letter (the status file's ``state``):
        d deafened, m muted (or push to talk with the key up), l a raised voice, s speaking, o connected and quiet, - nothing known.
        Their own EqVoice says muted and deafened (instantly, over the voice connection); speaking and loud are what we hear, steadied by
        the page (a breath or a key click does not flip it). Until the page has ever reported, speaking is what packets are arriving."""
        if p.state != PS.CONNECTED:
            return "-"
        if p.rdeaf:
            return "d"
        if p.rmute:
            return "m"
        if p.vlevel_seen:
            if p.vlevel >= 2:
                return "l"
            if p.vlevel == 1:
                return "s"
        elif getattr(p, "audio_rx_on", False):
            return "s"
        return "o" if p.rmute is not None else "-"

    def snapshot(self) -> list:
        now = self.clock()
        out = []
        for p in sorted(self.peers.values(), key=lambda x: (x.dist is None, x.dist or 0.0, x.name)):
            out.append({
                "sid": p.sid, "name": p.name, "state": p.state.value, "for": round(now - p.since, 1),
                "dist": None if p.dist is None else round(p.dist, 1), "inside": p.inside,
                "linked": p.sid in self.linked, "link_dist": p.link_dist, "gain": round(p.gain, 2),
                "initiator": p.initiator, "ice": p.ice, "conn": p.conn, "level": round(p.level, 3),
                "attempts": p.attempts, "err": p.err, "capped": p.capped, "stats": p.stats, "path": p.path,
                "vstate": self.voice_state(p), "vlevel": p.vlevel, "muted_by_me": self.is_muted(p.name),
                "candidates": {"local": dict(p.cands_local), "remote": dict(p.cands_remote)},
                "retry_in": round(max(0.0, p.cooldown_until - now), 1) if p.state == PS.DISCONNECTED else 0,
            })
        return out

    def shutdown(self) -> None:
        for p in list(self.peers.values()):
            if p.state in ACTIVE:
                self._close(p, notify=True)
        self.peers.clear()
        self.by_sid.clear()
