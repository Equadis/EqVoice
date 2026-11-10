"""The client: wires bridge, identity, roster, signalling, peer state machine, voice controls and media page.

Data flow (see docs/06-roster-pipeline.md):

    game (addon) --OVB2 file/pixels--> BridgeMonitor --> identity (realm, character, login id)
                                            |                     |
                                            |                     v
                                            |              SignallingClient <--> hub (roster, mutual proximity)
                                            |                     |   ^
        eqvoice-in.txt <-- InboxWriter <--+-- Roster <----------+   |
            (names to look up)              |                         |
                                            v                         v
                                      readings (name -> yards) --> PeerManager --> media page (WebRTC, volume)

The game is the only source of "who am I" and "how far away is everyone". The hub is the only source of "who
is online" and "do they see me too". Voice needs both, and either one going quiet fades it out.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from aiohttp import WSMsgType, web

from . import __version__, browser, calibration, identity as idmod, installer
from .bridge.filesource import ClaimingFileSource, FileSource, candidate_paths, valid_file_character
from .bridge.inbox import IN_NAME, InboxWriter
from .bridge.optical import OpticalSource
from .bridge.sim import SimGame, SimSource, SimWorld
from .bridge.source import BridgeMonitor, BridgeSource
from .bridge import uifiles
from .bridge.uifiles import ControlReader, StatusWriter, control_path, status_path
from .config import Config
from .logbuf import RING, ensure_logging
from .peers import PS, PeerConfig, PeerManager
from .ptt import KeyPoller, vk_from_code, vk_from_mouse, vk_from_wow
from .roster import Roster
from .signalling import SignallingClient
from .resources import ResourceMeter, process_table, tree_pids
from .trace import addon_line, names as tnames, trace

log = logging.getLogger("eqvoice.app")

UI_DIR = Path(__file__).parent / "ui"
TICK = 0.25
METRICS_EVERY = 30.0
RESOURCES_EVERY = 10.0
DEFAULT_STUN = [{"urls": ["stun:stun.l.google.com:19302"]}]


class AutoSource(BridgeSource):
    """Runs several sources at once; whichever one is fed keeps the bridge alive."""

    kind = "auto"

    def __init__(self, parts: list):
        super().__init__()
        self.parts = parts

    @property
    def status(self) -> dict:                      # type: ignore[override]
        return {"kind": "auto", "parts": [p.status for p in self.parts]}

    @status.setter
    def status(self, _v) -> None:
        pass

    @property
    def inbox_path(self):                          # type: ignore[override]
        return next((p.inbox_path for p in self.parts if p.inbox_path is not None), None)

    async def run(self, emit) -> None:
        await asyncio.gather(*(p.run(emit) for p in self.parts))


def make_source(cfg: Config) -> BridgeSource:
    b = cfg.bridge
    who = str(b.character or "")
    if who and not valid_file_character(who):
        raise ValueError("bridge.character %r: per-character files need 2-24 plain ASCII letters" % who)
    paths = candidate_paths(b.wow_dir, b.file_path, who)
    claiming = bool(b.get("claim")) and not who and bool(b.wow_dir) and not b.file_path        # the double-click flow: take a game no other copy has
    if b.source == "optical":
        return OpticalSource(b)
    if b.source == "file":
        return ClaimingFileSource(b.wow_dir, b.poll_hz) if claiming else FileSource(paths, b.poll_hz, who)
    if b.source == "sim":
        world = SimWorld(cfg.sim.world or None)
        if cfg.sim.character not in world.chars:
            world.add(cfg.sim.character)
        folder = Path(cfg.sim.folder) if cfg.sim.folder else cfg.home / "sim"
        return SimSource(SimGame(world, cfg.sim.character, cfg.sim.realm, folder,
                                 interval=float(cfg.roster.scan_interval_s), char_files=bool(who)), b.poll_hz, who)
    parts = []
    if claiming:
        parts.append(ClaimingFileSource(b.wow_dir, b.poll_hz))
    elif paths:
        parts.append(FileSource(paths, b.poll_hz, who))
    parts.append(OpticalSource(b))
    return parts[0] if len(parts) == 1 else AutoSource(parts)


def auto_checks(n: int, interval: float, sweep_s: float = 3.0, floor: int = 80, cap: int = 200) -> int:
    """Lookups per scan so that ``n`` names are all visited in about ``sweep_s`` seconds, within [floor, cap].

    The game also checks every nearby player on every scan and every never-seen name first, so this only sizes the
    round-robin over the unresolved majority: 80 per half second (the original fixed budget) up to 480 names, then
    growing with the roster to ``cap`` (200 per scan = 400 lookups a second).
    """
    want = -(-int(n) * float(interval) // float(sweep_s))               # ceil(n * interval / sweep)
    return int(max(floor, min(cap, want)))


class Page:
    """One connected browser page, with a bounded outgoing queue."""

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.q: asyncio.Queue = asyncio.Queue(maxsize=400)
        self.media = False
        self.role = ""              # "engine", "ui" or nothing (one page that does both)
        self.ua = ""
        self.webdriver = False
        self.task = asyncio.create_task(self._writer())

    async def _writer(self) -> None:
        try:
            while True:
                data = await self.q.get()
                if data is None:
                    break
                await self.ws.send_str(data)
        except (ConnectionError, RuntimeError):
            pass

    def put(self, obj: dict, droppable: bool = False) -> None:
        data = json.dumps(obj, separators=(",", ":"))
        try:
            self.q.put_nowait(data)
        except asyncio.QueueFull:
            if not droppable:
                log.warning("media page is not keeping up; dropping it")
                asyncio.ensure_future(self.ws.close())

    def close(self) -> None:
        try:
            self.q.put_nowait(None)
        except asyncio.QueueFull:
            self.task.cancel()


VOICE_PICTURES = ("voice-speaking.png", "voice-open.png", "voice-known.png", "voice-muted.png", "voice-deafened.png",      # the icons beside player names
                  "eqvoice-icon.png")                                                                                          # and the window's own (its taskbar button)


class App:
    def __init__(self, cfg: Config, source: Optional[BridgeSource] = None, *, headless: bool = False,
                 launch_browser: Optional[bool] = None, fake_media: bool = False, mute_audio: bool = False,
                 clock=time.monotonic, quit_on_window_close: bool = False):
        self.cfg, self.clock = cfg, clock
        ensure_logging(cfg.home, cfg.log_level)
        self.headless, self.fake_media, self.mute_audio = headless, fake_media, mute_audio
        self.quit_on_window_close = quit_on_window_close         # EqVoice.exe: closing the window quits the program
        self.window_grace_s = 8.0                                # a reload or a crash that comes back within this is not a quit
        self._window_task: Optional[asyncio.Task] = None
        # Tray-only (EqVoice.exe with a tray icon, `ui.tray_only`): the voice engine lives in a window nobody sees, so there is no taskbar button, and the
        # real window is opened from the tray and may be closed without ending the call. Closing it never quits; the engine going away restarts it.
        self.tray_only = False
        self._engine_task: Optional[asyncio.Task] = None
        self._engine_starts: list = []
        self._ui_launched = -100.0
        # On demand (the packaged program with its tray icon): the engine is a browser, about half a gigabyte, so it runs only while a game is
        # being played: started when the game reports a character, stopped when there has been no game for `engine_idle_s` seconds.
        self.engine_on_demand = False
        self.level_watch_until = -100.0               # the game's meter is showing: tell it the microphone's level until then
        self.engine_idle_s = 45.0
        self._engine_started_at = -100.0
        self._media_seen = 0.0                          # when the voice engine's page last said anything (it says hb every two seconds)
        self.engine_silent_s = 15.0                     # an engine page silent this long is frozen: it is ended and started again
        self._engine_idle_since: Optional[float] = None
        self._engine_stopping = False
        self._told_out_of_date = False
        self.report_note = None                         # (file name, until): the log zip just saved, told to the game for a while
        self._status_args = None                        # the last full status written: what the fast meter rewrite starts from
        self.notify_user: Optional[Callable[[str, str], None]] = None      # the tray's balloon, when there is a tray
        self.launch_browser = cfg.ui.open_browser if launch_browser is None else launch_browser
        self.source = source or make_source(cfg)
        self.sim: Optional[SimGame] = self.source.game if isinstance(self.source, SimSource) else None
        self._hook_trace(self.source)
        self.monitor = BridgeMonitor(cfg.bridge.stale_s, cfg.bridge.hold_s, clock)
        self.roster = Roster()
        self.ident: Optional[idmod.Identity] = None
        self.inbox: Optional[InboxWriter] = None
        self.ui_status: Optional[StatusWriter] = None       # what the in-game panel shows (eqvoice-st*.txt)
        self.ui_control: Optional[ControlReader] = None     # what the player clicked in the game (eqvoice-ctl*.txt)
        self.ui_commands = 0
        self.gate: tuple = (False, "waiting for the game")
        self.readings: dict = {}
        self.signalling = SignallingClient(cfg.signalling_url, cfg.join_code, self._on_sig_event, clock=clock)
        self.peers = PeerManager(self, PeerConfig.from_section(cfg.proximity), clock, ice_provider=self._ice)
        self.peers.cfg.near_gain = self._boost_gain()
        self.peers.media_ready = False
        self.peers.muted = {str(n).strip().lower() for n in (cfg.audio.get("mute_list") or []) if str(n).strip()}
        self.peers.log_ips = bool((cfg.get("diagnostics") or {}).get("log_ips"))
        self.token = cfg.ui.token or secrets.token_hex(12)
        self.pages: list = []
        self.devices: dict = {"inputs": [], "outputs": []}
        self.mic: dict = {"state": "starting", "label": "", "level": 0.0, "tx": False}
        self.ptt_down = False
        self.last_tx: Optional[bool] = None
        self.last_deaf: Optional[bool] = None
        self.last_muted: Optional[bool] = None
        self.last_state: Optional[tuple] = None         # (microphone closed, deafened) as last sent to the page
        self.key_note: tuple = ("", 0.0)                # (text, when): why the key the game offered was refused, shown in the panel for a few seconds
        self.started = time.time()
        self.port = 0
        self.url = ""                                   # the window's address, kept so the tray can open the window again
        self._tasks: list = []
        self._quit = asyncio.Event()
        self._proc = None
        self._runner: Optional[web.AppRunner] = None
        self._save_due = 0.0
        self._metrics_due = 0.0
        self._first_inbox = None
        self.fx_error = ""                              # why the page gave up on the audio chain (voices then play plain)
        # the microphone setup: idle | quiet | normal | loud while it listens, done once it has set things (quality says how well)
        self.cal = {"phase": "idle", "left": 0, "quality": "", "text": ""}
        self.cal_facts: dict = {}                       # the numbers behind the last setup (the log and the tests read them)
        self.gate_error = ""                            # why the page gave up on the keyboard filter (the raw microphone is sent)
        self._off_checked = -100.0                      # when the game's addon lists were last read for "EqVoice: disabled"
        self._off_for: list = []
        self.test_names =[n for n in (cfg.roster.get("test_names") or []) if isinstance(n, str) and n]   # extra names for the game
        self.test_readings: dict = {}                   # what the game says about them (they are not on the hub roster)
        self._traced_names: Optional[tuple] = None      # what the last "inbound_written" line said
        self._ack_pending: Optional[tuple] = None       # (seq, written at) waiting for the game to acknowledge
        self._acked_once = False
        self._traced_dist: dict = {}                    # name -> (yards, time) of the last "distance" line
        self._out_sig = None
        self._out_t = 0.0
        self._pipe_due = 0.0
        self.resource_meter = ResourceMeter()
        self._res_due = 0.0
        self._warn_due = 0.0
        self.last_resources: dict = {}
        self.addon_lines = 0
        self._mic_sig = None                            # what the last "mic" trace line said
        self._dev_sig = None
        self._warned: set = set()                       # media_warning problems already traced
        self.speaker_test: dict = {}                    # the page's answer to the last "Test speakers"
        self._log_cb = self._on_log_line
        if cfg.audio.ptt_code:
            k = vk_from_code(cfg.audio.ptt_code)
            if k:
                cfg.audio.ptt_vk = k[0]
        # the tail that keeps a push-to-talk word from being clipped would swallow a quick second press of the mute key, so open mic has none
        self.ptt = KeyPoller(lambda: int(cfg.audio.ptt_vk), self._on_ptt,
                             lambda: 0 if cfg.audio.mode == "open" else int(cfg.audio.ptt_release_ms))

    def _hook_trace(self, src) -> None:
        for part in getattr(src, "parts", None) or [src]:
            f = part if isinstance(part, FileSource) else getattr(part, "file", None)
            if isinstance(f, FileSource):
                f.on_trace = self._on_addon_trace

    def _on_addon_trace(self, seq: int, wall: int, text: str, state) -> None:
        self.addon_lines += 1
        addon_line(text)

    # ------------------------------------------------- PeerManager "host" API

    def signal(self, to: str, kind: str, ep: int, data: dict) -> bool:
        return self.signalling.send_signal(to, kind, ep, data)

    def media(self, cmd: dict) -> None:
        if cmd.get("cmd") == "gain":
            key = self.peers.by_sid.get(cmd.get("sid"))
            peer = self.peers.peers.get(key) if key else None
            trace("audio_gain", name=peer.name if peer else cmd.get("sid"), gain=round(float(cmd.get("value", 0)), 2),
                  hear="yes" if cmd.get("value", 0) > 0 else "no", transmit_to_them=bool(cmd.get("send")),
                  distance=(peer.dist if peer else None), ramp_ms=cmd.get("ms"))
        for p in self.pages:
            if p.media:
                p.put({"t": "cmd", "cmd": cmd})
                return

    def emit(self, event: str, **kw) -> None:
        pass            # the state machine already logs; this hook is for tests and future UI toasts

    # -------------------------------------------------------------- plumbing

    def _ice(self) -> list:
        if self.cfg.ice_servers:
            return list(self.cfg.ice_servers)
        return list(self.signalling.server_ice or DEFAULT_STUN)

    def _on_sig_event(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "_state":
            trace("hub_state", state=ev.get("state"), url=self.cfg.signalling_url, error=ev.get("error") or None)
            if "bad_code" in str(ev.get("error") or "") and not self._told_out_of_date and self.notify_user is not None:
                self._told_out_of_date = True                       # (the beta code was changed: only a newer installer carries the new one; retrying goes on quietly)
                self.notify_user("EqVoice", "This test version of EqVoice is out of date. Please download the newest installer and run it.")
        elif t == "_registered":
            if self.ident is not None:
                self.ident.token = ev.get("token", "") or self.ident.token
                idmod.save(self.cfg.home, self.ident)
            replaced, removed = self.roster.sync(ev.get("roster") or [])
            self.peers.roster_changed(replaced=replaced, removed_sids=removed)
            self.peers.on_signalling(True)
            trace("hub_registered", character=self.ident.character if self.ident else None, session=(ev.get("sid") or "")[:8],
                  lease_s=ev.get("lease"))
            trace("roster_received", total=len(self.roster), names=tnames(self.roster.names()))
        elif t == "_down":
            self.peers.on_signalling(False)
            trace("hub_down")
        elif t == "roster_update":
            adds, rems = ev.get("add") or [], ev.get("remove") or []
            gone = [s for s in rems if isinstance(s, str)] if isinstance(rems, list) else []
            gone_names = [self.roster.name_of(s) or s[:8] for s in gone]
            replaced = self.roster.add(adds) if isinstance(adds, list) else []
            self.roster.remove(gone)
            self.peers.roster_changed(replaced=replaced, removed_sids=gone)
            added = [a[0] for a in adds if isinstance(a, (list, tuple)) and a] if isinstance(adds, list) else []
            trace("roster_update", added=added, removed=gone_names, total=len(self.roster))
        elif t == "peer_available":
            if isinstance(ev.get("sid"), str):
                d = ev.get("distance")
                self.peers.on_link(ev["sid"], True, float(d) if isinstance(d, (int, float)) else None)
                trace("hub_link", name=ev.get("character") or self.roster.name_of(ev["sid"]), state="UP",
                      hub_distance=float(d) if isinstance(d, (int, float)) else None)
        elif t == "peer_out_of_range":
            if isinstance(ev.get("sid"), str):
                self.peers.on_link(ev["sid"], False)
                trace("hub_link", name=ev.get("character") or self.roster.name_of(ev["sid"]), state="DOWN")
        elif t in ("offer", "answer", "ice", "bye"):
            if isinstance(ev.get("from"), str) and isinstance(ev.get("ep"), int):
                data = ev.get("data")
                self.peers.on_signal(ev["from"], t, ev["ep"], data if isinstance(data, dict) else {})
        elif t == "error" and ev.get("code") != "not_linked":
            log.warning("signalling error: %s - %s", ev.get("code"), ev.get("msg"))
            trace("hub_error", code=ev.get("code"), msg=ev.get("msg"))

    def _on_frame(self, state) -> None:
        if not self.monitor.push(state):
            return
        now = self.clock()
        sig = (state.flags, state.ack, state.roster_size, state.character, tuple((r.name, int(r.dist)) for r in state.results))
        if (sig != self._out_sig and now - self._out_t >= 0.5) or now - self._out_t >= 10:
            if self._out_sig is None:
                trace("outbound_first_frame", source=self._bridge_path() or self.source.kind)
            self._out_sig, self._out_t = sig, now
            trace("outbound_read", frame=state.counter, character=state.character, realm=state.realm,
                  flags=self.flag_words(state.flags), roster_ack=state.ack, roster=state.roster_size,
                  checked=state.checked, found=state.loaded, not_available=state.misses, scan_ms=round(state.cost / 10.0, 1),
                  results=["%s:%.1f" % (r.name, r.dist) for r in state.results])

    def _bridge_path(self) -> Optional[str]:
        """The file the frames are coming from (or the source kind), for the first-frame trace line."""
        st = self.source.status or {}
        for part in [st] + list(st.get("parts") or []) + ([st["file"]] if isinstance(st.get("file"), dict) else []):
            if isinstance(part, dict) and part.get("path"):
                return str(part["path"])
        return None

    @staticmethod
    def flag_words(flags: int) -> list:
        names = [(1, "enabled"), (2, "file"), (4, "optical"), (8, "roster_fresh"), (16, "LOGOUT"), (32, "lookups_ok"),
                 (64, "ID_MISMATCH"), (128, "truncated")]
        return [n for bit, n in names if flags & bit]

    def _on_log_line(self, line: str) -> None:
        for p in self.pages:
            p.put({"t": "log", "line": line}, droppable=True)

    def _on_ptt(self, down: bool) -> None:
        self.ptt_down = down
        log.debug("ptt %s", "down" if down else "up")
        # Every press is a boundary of its own: in open-mic mode it changes nothing, so audio_mic alone would hide it.
        trace("ptt", down=down, source="button" if self.ptt.virtual else "key", mode=self.cfg.audio.mode,
              muted=bool(self.cfg.audio.muted), deafened=bool(self.cfg.audio.deafened), microphone_opens=self.tx_enabled)
        if down and self.cfg.audio.mode == "open" and not self.ptt.virtual:
            # With an open microphone the key has nothing to hold: each press mutes or unmutes (deafened stays deafened: unmute is not undeafen)
            self.cfg.audio.muted = not self.cfg.audio.muted
            trace("mute_toggle", muted=bool(self.cfg.audio.muted), by="key")
        self._apply_voice()

    # ----------------------------------------------------------------- voice

    @property
    def tx_enabled(self) -> bool:
        a = self.cfg.audio
        return (not a.muted) and (not a.deafened) and (a.mode == "open" or self.ptt_down)

    def _apply_voice(self) -> None:
        tx = self.tx_enabled
        if tx != self.last_tx:
            self.last_tx = tx
            trace("audio_mic", transmit=tx, mode=self.cfg.audio.mode, muted=bool(self.cfg.audio.muted),
                  deafened=bool(self.cfg.audio.deafened), ptt_key_down=self.ptt_down)
            self.media({"cmd": "tx", "enabled": tx})
        d = bool(self.cfg.audio.deafened)
        if d != self.last_deaf:
            if self.last_deaf is not None:
                self._cue("deafen" if d else "undeafen")
            self.last_deaf = d
            self.media({"cmd": "deafen", "value": d})
        st = (not tx, d)
        if st != self.last_state:
            self.last_state = st
            self.media({"cmd": "self_state", "mute": st[0], "deaf": st[1]})       # what the people you are connected to are told about you
        m = bool(self.cfg.audio.muted)
        if m != self.last_muted:
            if self.last_muted is not None and not d:
                self._cue("mute" if m else "unmute")                 # nothing but the deafen cue is played while deafened
            self.last_muted = m

    def _cue(self, kind: str) -> None:
        """A short sound that says the change happened (the game window may be full screen; the indicator is not always in sight)."""
        if self.cfg.audio.get("cues", True):
            self.media({"cmd": "cue", "kind": kind})

    # the boost option: how many times louder a voice is right next to you (within boost_radius, fading out by boost_fade_radius)
    BOOSTS = {"off": 1.0, "low": 1.4, "normal": 2.0, "high": 3.0}

    def _boost(self) -> str:
        b = self.cfg.audio.get("boost")
        return b if b in self.BOOSTS else "normal"

    def _boost_gain(self) -> float:
        return self.BOOSTS[self._boost()]

    def _gate_mode(self) -> str:
        return "manual" if self.cfg.audio.get("gate_mode") == "manual" else "auto"

    def _gate_db(self) -> float:
        try:
            return max(-100.0, min(0.0, float(self.cfg.audio.get("gate_db", -48.0))))
        except (TypeError, ValueError):
            return -48.0

    def _gate_label(self) -> str:
        """What the in-game panel calls the noise gate: off, auto or manual."""
        return self._gate_mode() if self.cfg.audio.get("keyboard_filter", True) else "off"

    def _input_gain(self) -> float:
        """How loud the microphone is before the gate and everything else: 0 .. 2.0 (1.0 = as it comes)."""
        try:
            return max(0.0, min(calibration.GAIN_MAX, float(self.cfg.audio.get("input_gain", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _bitrate_kbps(self) -> int:
        """0 = the browser's default; otherwise 8..64 (below 8 kbps speech stops being speech, above 64 is wasted on a voice)."""
        try:
            k = int(float(self.cfg.audio.get("max_bitrate_kbps", 24)))
        except (TypeError, ValueError):
            return 24
        return 0 if k <= 0 else max(8, min(64, k))

    def _send_config(self) -> None:
        a = self.cfg.audio
        self.media({"cmd": "config", "input": a.input, "output": a.output, "relay_only": bool(self.cfg.relay_only),
                    "processing": bool(a.processing), "volumes": dict(a.peer_volume), "master": float(a.master_volume),
                    "input_gain": self._input_gain(), "bitrate": self._bitrate_kbps(), "curve": self.peers.cfg.curve,
                    "keyboard": bool(a.get("keyboard_filter", True)), "auto_gain": bool(a.get("auto_gain", False)),
                    "auto_level": bool(a.get("auto_level", True)), "gate_mode": self._gate_mode(), "gate_db": self._gate_db()})
        self.last_tx = self.last_deaf = self.last_muted = self.last_state = None
        self._apply_voice()

    # -------------------------------------------------------------- identity

    def _wanted_game(self, view) -> Optional[tuple]:
        """(realm, character, login id) while the game is running a character we may speak for."""
        st = view.state
        if st is None or st.logout or not st.character or not st.realm:
            return None
        if view.age > self.cfg.bridge.ghost_s:
            return None                          # the game has been gone for a while: leave the roster
        return st.identity

    def _sync_identity(self, view) -> None:
        want = self._wanted_game(view)
        cur = self.ident.game if self.ident else None
        if want == cur:
            return
        if self.ident is not None:
            why = "logged out" if view.state is not None and view.state.logout else (
                "game gone" if want is None else "character changed")
            log.info("leaving the roster as %s (%s)", self.ident.character, why)
            if view.state is not None and view.state.logout:
                idmod.forget(self.cfg.home)
            trace("identity_left", character=self.ident.character, why=why)
            self.signalling.set_identity(None)
            self.peers.set_self(None)
            self.roster.clear()
            self.ident = None
            if self.inbox is not None:
                self.inbox.clear()
                self._first_inbox = None
        if want is not None:
            realm, character, login_id = want
            self.ident = idmod.load_or_create(self.cfg.home, realm, character, login_id)
            self.peers.set_self(self.ident.sid)
            self.signalling.set_identity(self.ident)
            self._first_inbox = None
            log.info("playing %s on %s (login %08x); session id %s", character, realm, login_id, self.ident.sid)
            trace("identity", source="bridge frame from the game", character=character, realm=realm,
                  login=login_id, session=self.ident.sid[:8])

    # ----------------------------------------------------------------- inbox

    def inbox_target(self) -> Optional[Path]:
        p = self.source.inbox_path
        if p is not None:
            return Path(p)
        wow = self.cfg.bridge.wow_dir
        return Path(wow) / "CustomData" / IN_NAME if wow else None

    def _sync_inbox(self) -> None:
        if self.ident is None:
            return
        target = self.inbox_target()
        if target is None:
            return
        if self.inbox is None or self.inbox.path != target:
            self.inbox = InboxWriter(target, refresh_s=float(self.cfg.roster.refresh_s))
        r = self.cfg.roster
        sig_ok = self.signalling.registered or self.peers.sig_down_since is None or \
            self.clock() - self.peers.sig_down_since < self.cfg.proximity.fail_closed_s
        names = self.roster.select(int(r.max_names), [p.name for p in self.peers.peers.values()]) if sig_ok else []
        for extra in self.test_names:                    # --test-roster: look these up too, as if they were on the roster
            if sig_ok and extra not in names and extra != self.ident.character:
                names.append(extra)
        mc = r.max_checks
        checks = int(mc) if isinstance(mc, (int, float)) and mc else auto_checks(
            len(names), float(r.scan_interval_s), float(r.get("sweep_s", 3.0)), 80, int(r.get("max_checks_cap", 200)))
        config = {"radius": self.cfg.proximity.say_radius, "interval": r.scan_interval_s,
                  "checks": checks, "report": r.report}
        errors_before = self.inbox.errors
        wrote = self.inbox.write(self.ident.character, names, config)
        if self.inbox.errors > errors_before:
            trace("inbound_write_failed", path=target, error=self.inbox.last_error)
        if wrote:
            if self._first_inbox is None:
                self._first_inbox = self.clock()
                log.info("roster file: %s (%d names)", target, len(names))
            key = tuple(sorted(names))
            if key != self._traced_names:
                self._traced_names = key
                self._ack_pending = (self.inbox.seq & 0xFFFF, self.clock())
                trace("inbound_written", path=target, seq=self.inbox.seq, character=self.ident.character, total=len(names),
                      names=tnames(sorted(names)))

    # ------------------------------------------------------- the in-game panel

    def _ui_file_paths(self) -> Optional[tuple]:
        """(status file, command file), named like the roster file so two game instances in one folder keep apart."""
        target = self.inbox_target()
        return (status_path(target), control_path(target)) if target is not None else None

    def _ui_peer_rows(self) -> list:
        rows = []
        for p in sorted(self.peers.peers.values(), key=lambda x: (x.dist is None, x.dist or 0.0, x.name)):
            if not (p.inside or p.state in (PS.CONNECTED, PS.OUT_OF_RANGE_GRACE, PS.CONNECTING)):
                continue
            path = "-"
            if p.path:
                path = "relay" if "relay" in str(p.path) else "direct"
            rows.append({"name": p.name, "dist": p.dist, "connected": p.state == PS.CONNECTED,
                         "speaking": bool(getattr(p, "audio_rx_on", False)), "path": path, "muted": self.peers.is_muted(p.name),
                         "state": self.peers.voice_state(p)})
        return rows

    # ------------------------------------------------------------ microphone setup (docs/11)

    def _start_calibration(self, plan=None) -> None:
        """Ask the page to listen for three short stretches: silence, a normal voice, a raised voice (the page times them and reports).
        ``plan`` is the three lengths in seconds; only the tests pass one, the page has its own."""
        if self.cal["phase"] in ("quiet", "normal", "loud"):
            return
        self.cal.update(phase="quiet", left=0, quality="", text="")
        trace("calibration_start")
        self.media({"cmd": "calibrate", "plan": plan} if plan else {"cmd": "calibrate"})

    def _cancel_calibration(self) -> None:
        if self.cal["phase"] in ("quiet", "normal", "loud"):
            self.media({"cmd": "calibrate_cancel"})
        self.cal.update(phase="idle", left=0, quality="", text="")

    def _on_calibration(self, m: dict) -> None:
        phase = m.get("phase")
        if m.get("live"):                                   # ten times a second: the level, for a window that is not the page measuring
            for p in self.pages:
                if not p.media:
                    p.put({"t": "cal_progress", "phase": phase, "left": m.get("left"), "level": m.get("level")}, droppable=True)
            return
        if phase in ("quiet", "normal", "loud"):
            try:
                left = max(0, int(-(-float(m.get("left", 0)) // 1)))          # seconds still to go, rounded up
            except (TypeError, ValueError):
                left = 0
            self.cal.update(phase=phase, left=left)
        elif phase == "done":
            self._finish_calibration(m)
        elif phase in ("failed", "cancelled"):
            text = "" if phase == "cancelled" else str(m.get("error") or "the microphone could not be measured")[:160]
            self.cal.update(phase="idle", left=0, quality="none" if text else "", text=text)
            trace("calibration_" + phase, error=text)
            self._tell_pages({"t": "cal_result", "ok": False, "quality": self.cal["quality"], "text": text})

    def _finish_calibration(self, m: dict) -> None:
        result = calibration.analyse(m.get("quiet"), m.get("normal"), m.get("loud"), m.get("peak"))
        a = self.cfg.audio
        if result.ok:
            a.input_gain = result.input_gain
            a.gate_mode = result.gate_mode
            if result.gate_db is not None:
                a.gate_db = result.gate_db
            a.calibrated = True
            self.media({"cmd": "input_gain", "value": self._input_gain()})
            self.media({"cmd": "gate", "mode": self._gate_mode(), "db": self._gate_db()})        # first: the page builds the filter with this bar
            if not bool(a.get("keyboard_filter", True)):
                a.keyboard_filter = True                                   # the setup is what the gate is for: it comes on with its new bar
                self.gate_error = ""
                self.media({"cmd": "keyboard", "value": True})
            self._save_soon()
        self.cal_facts = dict(result.facts)
        self.cal.update(phase="done" if result.ok else "idle", left=0, quality=result.quality, text=result.text)
        trace("calibration_done", ok=result.ok, quality=result.quality, **{k: v for k, v in result.facts.items() if v is not None})
        self._tell_pages({"t": "cal_result", "ok": result.ok, "quality": result.quality, "text": result.text,
                          "gate_db": result.gate_db, "input_gain": result.input_gain, "gate_mode": result.gate_mode})

    def _self_voice_level(self) -> int:
        """How I sound on my own microphone (0 nothing, 1 speaking, 2 a raised voice), steadied by the page; 0 while nobody can hear me."""
        if not self.tx_enabled:
            return 0
        try:
            return max(0, min(2, int(self.mic.get("vl") or 0)))
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------ the game's device pickers

    def _device_names(self, kind: str) -> list:
        """The names of the microphones ("inputs") or speakers ("outputs") Windows has, as the game lists them (at most 8)."""
        out = []
        for i, d in enumerate((self.devices or {}).get(kind) or []):
            out.append(str(d.get("label") or ("Microphone %d" if kind == "inputs" else "Speakers %d") % (i + 1)))
        return out[:uifiles.MAX_DEVICES]

    def _device_index(self, kind: str) -> int:
        """1.. for the chosen device's place in that list, 0 for the system default (or a device that is not there any more)."""
        chosen = self.cfg.audio.input if kind == "inputs" else self.cfg.audio.output
        if not chosen:
            return 0
        for i, d in enumerate(((self.devices or {}).get(kind) or [])[:uifiles.MAX_DEVICES], 1):
            if d.get("id") == chosen:
                return i
        return 0

    def _device_command(self, kind: str, name: str):
        """The window's own command for 'use the device with this name' (the name the game was shown), or None if there is none such."""
        want = " ".join(str(name or "").split()).lower()
        if want in ("", "default", "system default"):
            return {"cmd": "set_input" if kind == "inputs" else "set_output", "id": ""}
        for d, label in zip(((self.devices or {}).get(kind) or [])[:uifiles.MAX_DEVICES], self._device_names(kind)):
            if uifiles._device_name(label).lower() == want:
                return {"cmd": "set_input" if kind == "inputs" else "set_output", "id": str(d.get("id") or "")}
        return None

    def _watching_level(self) -> bool:
        return self.clock() < self.level_watch_until

    def _ui_fields(self, view, summary: dict) -> dict:
        a = self.cfg.audio
        talking = self.tx_enabled and float(self.mic.get("level") or 0.0) > 0.05
        return {"level": summary["level"], "mode": a.mode, "mute": int(bool(a.muted)), "deaf": int(bool(a.deafened)),
                "tx": int(self.tx_enabled), "talk": int(talking), "vl": self._self_voice_level(), "vol": int(round(float(a.master_volume) * 100)), "ing": int(round(self._input_gain() * 100)),
                "near": self.peers.nearby_count(), "conn": self.peers.counts()["CONNECTED"], "key": a.ptt_label or "?",
                "game": int(view.status == "live"), "hub": int(self.signalling.state == "registered"),
                "mic": self.mic.get("state") or "starting", "app": __version__, "boost": self._boost(), "gate": self._gate_label(),
                "gdb": int(round(self._gate_db())), "cues": int(bool(a.get("cues", True))),
                "cal": self.cal["phase"], "calt": int(self.cal["left"]), "calq": self.cal["quality"] or "-",
                "cald": int(bool(a.get("calibrated", False))),
                "din": self._device_index("inputs"), "dout": self._device_index("outputs"),
                "lvl": int(round(max(0.0, min(1.0, float(self.mic.get("level") or 0.0))) * 100)) if self._watching_level() else 0,
                **self._gate_meter(), **self._report_field()}

    def _report_field(self) -> dict:
        """``rpt``: the name of the log zip just saved, for the game to say so (about twenty-five seconds)."""
        n = self.report_note
        if n and self.clock() < n[1]:
            return {"rpt": n[0]}
        return {}

    def _gate_meter(self) -> dict:
        """What the panel's one bar draws, only while a meter is on screen: ``gl`` the level in dB of the very signal the noise gate judges (the
        keyboard filter's own measure: 200 Hz to 3.5 kHz, after the input volume, taken before the gate so it moves while the gate is shut),
        ``gt`` the bar the gate holds it against now and ``go`` 1 while the gate is open. With no gate running (the filter is off, or the page
        gave it up) ``gl`` is the microphone's broadband level and there is no ``gt`` or ``go``."""
        if not self._watching_level():
            return {}
        m = self.mic
        live = gate_on = None
        if m.get("gate") and isinstance(m.get("glevel"), (int, float)):
            live, gate_on = float(m["glevel"]), True
        elif isinstance(m.get("ldb"), (int, float)):
            live = float(m["ldb"])
        out = {}
        if live is not None:
            out["gl"] = int(round(max(-100.0, min(0.0, live))))
        if gate_on:
            if isinstance(m.get("gthr"), (int, float)):
                out["gt"] = int(round(max(-100.0, min(0.0, float(m["gthr"])))))
            out["go"] = int(bool(m.get("gopen")))
        return out

    def _sync_ui_files(self, view) -> None:
        """Publish the status the panel shows, and apply whatever the player clicked since the last tick."""
        if self.ident is None:
            return
        paths = self._ui_file_paths()
        if paths is None:
            return
        spath, cpath = paths
        if self.ui_status is None or self.ui_status.path != spath:
            self.ui_status = StatusWriter(spath)
        if self.ui_control is None or self.ui_control.path != cpath:
            self.ui_control = ControlReader(cpath, since=self.started)
        cmds = self.ui_control.poll()
        if cmds:
            self._apply_control(cmds)
        summary = self.status_summary(view)
        self.ui_status.min_interval_s = 0.2 if self._watching_level() else 0.4          # a meter wants five looks a second
        fields, peer_rows = self._ui_fields(view, summary), self._ui_peer_rows()
        devices = {"in": self._device_names("inputs"), "out": self._device_names("outputs")}
        self._status_args = (self.ident.character, fields, summary["text"], summary["more"], peer_rows, sorted(self.peers.muted), devices)
        self.ui_status.write(self.ident.character, fields, summary["text"], summary["more"], peer_rows, muted=sorted(self.peers.muted), devices=devices)

    METER_S = 0.05                                      # while a meter is on screen the game is told twenty times a second (the tick is four)

    async def _run_meter(self) -> None:
        while True:
            try:
                self._meter_push()
            except Exception:                           # noqa: BLE001
                log.exception("meter push failed")
            await asyncio.sleep(self.METER_S)

    def _meter_push(self) -> None:
        """The last full status again with only the meter's figures new: a bar that moves four times a second is a slideshow."""
        if self._status_args is None or self.ui_status is None or not self._watching_level():
            return
        character, fields, text, more, peer_rows, muted, devices = self._status_args
        if self.ident is None or character != self.ident.character:
            return
        fresh = {k: v for k, v in fields.items() if k not in ("gl", "gt", "go")}
        fresh["lvl"] = int(round(max(0.0, min(1.0, float(self.mic.get("level") or 0.0))) * 100))
        fresh.update(self._gate_meter())
        self.ui_status.min_interval_s = self.METER_S
        self.ui_status.write(character, fresh, text, more, peer_rows, muted=muted, devices=devices)

    def _apply_control(self, cmds) -> None:
        """The game's panel speaks the same vocabulary as the window: each command becomes the window's own message."""
        for cid, cmd, arg in cmds:
            msg = None
            if cmd == "mode" and arg in ("open", "ptt"):
                msg = {"cmd": "set_mode", "mode": arg}
            elif cmd == "gate" and arg in ("off", "auto", "manual"):
                msg = {"cmd": "gate", "value": arg}
            elif cmd == "gatedb":
                try:
                    msg = {"cmd": "gate_db", "value": float(arg)}
                except ValueError:
                    msg = None
            elif cmd == "pmute" and " " in arg.strip():
                who, _, flag = arg.strip().rpartition(" ")
                msg = {"cmd": "mute_player", "name": who, "value": flag == "1"} if flag in ("0", "1") else None
            elif cmd in ("mute", "deafen") and arg in ("0", "1"):
                msg = {"cmd": cmd, "value": arg == "1"}
            elif cmd == "volume":
                try:
                    msg = {"cmd": "master_volume", "value": max(0, min(100, int(float(arg)))) / 100.0}
                except ValueError:
                    msg = None
            elif cmd == "input":
                try:
                    msg = {"cmd": "input_gain", "value": max(0, min(200, int(float(arg)))) / 100.0}
                except ValueError:
                    msg = None
            elif cmd == "calibrate" and arg in ("0", "1"):
                msg = {"cmd": "calibrate" if arg == "1" else "calibrate_cancel"}
            elif cmd == "boost" and arg in self.BOOSTS:
                msg = {"cmd": "boost", "value": arg}
            elif cmd == "key" and arg:
                msg = {"cmd": "ptt_key", "wow": arg}
            elif cmd in ("micdev", "spkdev"):
                msg = self._device_command("inputs" if cmd == "micdev" else "outputs", arg)
            elif cmd == "watch":
                self.level_watch_until = self.clock() + 3.0 if arg == "1" else -100.0
            elif cmd == "test":
                msg = {"cmd": "test_speakers"}
            elif cmd == "report":                                               # the panel's "Save logs" button
                asyncio.ensure_future(self._save_report())
            self.ui_commands += 1
            trace("ingame_command", id=cid, command=cmd, argument=arg, applied=msg is not None)
            if msg is not None:
                self._on_ui(msg)

    # --------------------------------------------------------------- readings

    def _evaluate_gate(self, view) -> tuple:
        if self.ident is None:
            return False, view.reason or "waiting for a character from the game"
        if not view.usable:
            return False, view.reason or "the game is not reporting"
        if view.identity != self.ident.game:
            return False, "the character changed"
        return True, ""

    def _readings(self, view) -> dict:
        self.gate = self._evaluate_gate(view)
        if not self.gate[0]:
            return {}
        out, extra = {}, {}
        for r in view.results:
            if r.name in self.roster:
                out[r.name] = r.dist
            elif r.name in self.test_names:
                extra[r.name] = r.dist
        self.test_readings = extra
        return out

    # ------------------------------------------------------------------ tick

    def _tick(self) -> None:
        now = self.clock()
        view = self.monitor.view()
        self._sync_identity(view)
        self._manage_engine(now)
        self._watch_engine(now)
        self._sync_inbox()
        self._sync_ui_files(view)
        self.readings = self._readings(view)
        self._trace_ack(view, now)
        self._trace_distances(now)
        self.peers.update_readings(self.readings, self.roster)
        self.peers.tick()
        rr = float(self.cfg.proximity.report_radius)
        self.signalling.report_proximity([(n, d) for n, d in self.readings.items() if d <= rr])
        if now >= self._pipe_due:
            self._pipe_due = now + 5.0
            self._trace_pipeline(view)
        if now >= self._res_due:
            self._res_due = now + RESOURCES_EVERY
            self._trace_resources()
        if now >= self._warn_due:
            self._warn_due = now + 5.0
            self._check_media()
        if now >= self._metrics_due:
            self._metrics_due = now + METRICS_EVERY
            if self.ident is not None:
                m = self.metrics(view)
                log.info("metrics: roster=%d checked=%d loaded=%d missed=%d scan=%sms interval=%sms nearby=%d connected=%d prox_msgs=%d",
                         m["roster"], m["checked"], m["loaded"], m["missed"], m["scan_ms"], m["bridge_interval_ms"],
                         m["nearby"], m["connected"], m["proximity_msgs"])
        if self.pages:
            st = self.build_state(view)
            for p in self.pages:
                p.put(st, droppable=True)
        if self._save_due and now >= self._save_due:
            self._save_due = 0.0
            try:
                self.cfg.save()
            except OSError as e:
                log.warning("could not save config: %s", e)

    def _trace_ack(self, view, now: float) -> None:
        """The first frame whose roster_ack equals the sequence we wrote proves the inbound file reached the addon."""
        st = view.state
        if self._ack_pending is None or st is None or st.logout:
            return
        seq, t0 = self._ack_pending
        if st.ack == seq and st.roster_fresh:
            self._ack_pending = None
            self._acked_once = True
            trace("inbound_acked", seq=seq, by_the_game_after_s=round(now - t0, 1), game_roster_size=st.roster_size,
                  note="the addon read the roster file and says so in its frame")

    def _trace_distances(self, now: float) -> None:
        seen = set()
        for name, d in {**self.readings, **self.test_readings}.items():
            seen.add(name)
            prev = self._traced_dist.get(name)
            if prev is None or abs(prev[0] - d) >= 2.0 or now - prev[1] >= 5.0:
                self._traced_dist[name] = (d, now)
                trace("distance", name=name, yards=float(d), inside_radius=bool(d <= self.cfg.proximity.say_radius))
        for name in [n for n in self._traced_dist if n not in seen]:
            del self._traced_dist[name]
            trace("distance", name=name, yards="not reported", note="the game no longer sees them")

    def read_resources(self) -> dict:
        """CPU and memory of this client and of its media engine (the browser and its helpers), plus the mesh totals."""
        table = process_table()
        engine = tree_pids(self._proc.pid, table) if self._proc is not None else []
        mine = self.resource_meter.read("client", [os.getpid()])
        eng = self.resource_meter.read("engine", engine) if engine else {"cpu_pct": None, "rss_mb": 0.0, "private_mb": 0.0, "procs": 0}
        agg = self.peers.aggregate()
        self.last_resources = {"client": mine, "engine": eng, "mesh": agg}
        return self.last_resources

    def _trace_resources(self) -> None:
        try:
            r = self.read_resources()
        except Exception:                                                      # noqa: BLE001
            log.exception("could not read resource use")
            return
        if not r["mesh"]["active"] and not r["engine"]["procs"]:
            return                                    # nothing worth a line: no calls and no engine of ours
        m = r["mesh"]
        trace("resources", engine_page_silent_s=round(self.clock() - self._media_seen, 1) if any(p.media for p in self.pages) else None, peers_active=m["active"], peers_connected=m["connected"], in_kbps=m["in_kbps"], out_kbps=m["out_kbps"],
              client_cpu_pct=r["client"]["cpu_pct"], client_mb=r["client"]["private_mb"], engine_cpu_pct=r["engine"]["cpu_pct"],
              engine_mb=r["engine"]["private_mb"], engine_procs=r["engine"]["procs"], setup_ms_avg=m["setup_ms_avg"],
              setup_ms_max=m["setup_ms_max"])

    def _trace_pipeline(self, view) -> None:
        st = view.state
        trace("pipeline", character=self.ident.character if self.ident else None, hub=self.signalling.state,
              bridge=view.status, frames=view.frames, roster=len(self.roster),
              inbound_acked=self._acked_once, game_roster=(st.roster_size if st else None), game_found=(st.loaded if st else None),
              nearby=self.peers.nearby_count(), connected=self.peers.counts()["CONNECTED"],
              gate="open" if self.gate[0] else "closed: %s" % self.gate[1], addon_lines=self.addon_lines)

    def _save_soon(self) -> None:
        if not self._save_due:
            self._save_due = self.clock() + 1.0

    # --------------------------------------------------------------- reporting

    def metrics(self, view=None) -> dict:
        view = view or self.monitor.view()
        st = view.state
        counts = self.peers.counts()
        return {
            "roster": len(self.roster),
            "checked": st.checked if st else 0,           # names the game looked up in its last scan
            "loaded": st.loaded if st else 0,             # ... that it could see
            "missed": st.misses if st else 0,             # ... that were not loaded / failed
            "scan_ms": round(st.cost / 10.0, 1) if st else 0.0,
            "addon_interval_ms": st.interval_ms if st else 0,
            "bridge_interval_ms": round(view.interval_s * 1000) if view.interval_s else 0,
            "roster_file_writes": self.inbox.writes if self.inbox else 0,
            "roster_acked": bool(st and self.inbox and st.ack == (self.inbox.seq & 0xFFFF)),
            "reported": len(self.readings),
            "test_names": {n: round(d, 1) for n, d in self.test_readings.items()} if self.test_names else None,
            "nearby": self.peers.nearby_count(),
            "connected": counts["CONNECTED"],
            "states": counts,
            "proximity_msgs": self.signalling.prox_sent,
            "truncated": bool(st and st.truncated),
            "mesh": self.peers.aggregate(),
            "resources": self.last_resources,
        }

    def _waiting_for_addon(self) -> str:
        """No word from the addon yet. The commonest reason after a fresh install: the game lists EqVoice as disabled for this character."""
        now = self.clock()
        if now - self._off_checked >= 5.0 and self.cfg.bridge.wow_dir:
            self._off_checked = now
            self._off_for = installer.addon_off_for(Path(self.cfg.bridge.wow_dir))
        source = getattr(self, "source", None)
        src = (getattr(source, "parts", None) or [source])[0]
        taken = (getattr(src, "status", None) or {}).get("taken_by_others") or []
        if taken:
            return ("Every logged-in game already has an EqVoice (%s). This copy is waiting for another game window: log in a second "
                    "character, or close this copy." % ", ".join(taken[:3]))
        if self._off_for:
            names = ", ".join(self._off_for[:3]) + (" and %d more" % (len(self._off_for) - 3) if len(self._off_for) > 3 else "")
            return ("EqVoice is switched off in the game for %s. On the character screen click AddOns, tick EqVoice, and log in again."
                    % names)
        return "Waiting for the EqVoice addon (log in to the game; /ev status)."

    def bridge_hint(self, view, src: dict) -> str:
        if view.status == "lost":
            return "The bridge has been silent for too long: voice is off for safety until frames return."
        if view.status == "stale":
            return "No new frames from the game for %.0fs (minimised, covered, or frozen?). Holding the last readings briefly." % view.age
        if view.reason and view.status != "none":
            return view.reason[0].upper() + view.reason[1:] + "."
        if view.status == "none":
            parts = src.get("parts") or [src]
            for s in parts:
                if s.get("kind") == "optical":
                    w = s.get("window")
                    if w in ("not found", "minimised"):
                        return "WoW window %s. The optical bridge needs the game running and visible (windowed or borderless)." % w
                    if s.get("last_error") == "no_anchor":
                        return ("WoW found, but no EqVoice frame at the %s corner. In game: /ev status, /ev bridge optical; "
                                "Alt+Z hides the UI and the frame with it." % self.cfg.bridge.corner)
            return self._waiting_for_addon()
        if self.ident is not None and self.inbox_target() is None:
            return "Set the game folder (wow_dir) so the roster file can be written next to the addon's."
        st = view.state
        if (st is not None and not st.roster_fresh and self.inbox is not None and self.inbox.writes
                and self._first_inbox is not None and self.clock() - self._first_inbox > 8):
            return ("The game is not reading the roster file (%s). Try /reload in game, then /ev diag inbox." % IN_NAME)
        return ""

    @staticmethod
    def _is_headless(page) -> bool:
        return "HeadlessChrome" in page.ua

    @staticmethod
    def _fake_label(label) -> bool:
        return "fake" in str(label or "").lower()          # Chromium names its test devices "Fake Default Audio Input"

    def media_warnings(self) -> list:
        """(problem, text) for everything about the voice engine that would make a real test silently useless."""
        out = []
        page = next((p for p in self.pages if p.media), None)
        if page is not None and self._is_headless(page) and not self.headless:
            out.append(("hidden_engine", "The voice engine is a hidden test browser, not the EqVoice window, so you will not hear anyone. "
                                         "Close every EqVoice / Edge window left from an earlier run and restart this client."))
        if self._fake_label(self.mic.get("label")) and not self.fake_media:
            out.append(("fake_microphone", "The microphone is Chromium's FAKE test device (it plays a beep, not your voice). "
                                           "Something other than this client's own window is acting as the voice engine."))
        if self.mic.get("state") in ("denied", "no_device", "error", "unsupported"):
            out.append(("microphone_" + str(self.mic.get("state")), "The microphone is unavailable (%s %s): nobody can hear you." % (
                self.mic.get("state"), self.mic.get("label") or "")))
        if self.mute_audio and not self.headless:
            out.append(("muted_output", "This client was started with --mute-audio: you will hear nothing."))
        if self.gate_error:
            out.append(("keyboard_filter", "The keyboard filter is switched off (%s), so your microphone is sent as it is: keyboard and click "
                                           "noise will be heard. Choose the filter again to retry." % self.gate_error))
        if self.fx_error:
            out.append(("audio_chain", "The voice booster is switched off (%s), so voices play plain, without the boost up close. Nothing "
                                       "else is affected. Restart EqVoice to try it again." % self.fx_error))
        p = self.cfg.proximity
        lr = (self.signalling.limits or {}).get("link_radius")
        reach = max(float(p.say_radius), float(p.get("peak_radius", 0.0) or 0.0))
        if lr and reach + float(p.leave_hysteresis) > float(lr):
            out.append(("link_radius", "Your voice radius (%.0f yd, +%.0f to leave) is wider than the server will link two players "
                                       "(%.0f yd): people near the edge of your range will not connect. Lower the radius, or ask "
                                       "whoever runs the server to raise its link radius." % (reach, p.leave_hysteresis, float(lr))))
        return out

    def _check_media(self) -> None:
        for problem, text in self.media_warnings():
            if problem not in self._warned:
                self._warned.add(problem)
                log.warning("%s", text)
                trace("media_warning", problem=problem, detail=text)

    def status_summary(self, view=None) -> dict:
        """One headline for the top of the window: EqVoice is working, or the first thing to fix (and any others below it)."""
        view = view or self.monitor.view()
        sg = self.signalling
        problems: list = []                                    # (level, text), most important first
        if self.ident is None:
            hint = self.bridge_hint(view, self.source.status)
            problems.append(("warn", "Waiting for the game. " + (hint or "Start WoW, log in to a character and type /reload.")))
        elif view.status == "lost":
            problems.append(("bad", "The game stopped reporting, so voice is off for safety. Is WoW minimised, frozen or closed?"))
        elif view.status == "stale":
            problems.append(("warn", "No news from the game for %.0f s (minimised or frozen?). Holding the last positions briefly." % view.age))
        if sg.state != "registered":
            why = (": " + sg.last_error) if sg.last_error else ""
            problems.append(("bad" if sg.state == "backoff" else "warn",
                             ("Not connected to the voice server%s. Retrying." if sg.state == "backoff" else "Connecting to the voice server%s...") % why))
        mic = self.mic.get("state")
        if mic and mic not in ("ok", "starting"):
            problems.append(("bad", "Microphone problem (%s): nobody can hear you. Check the Microphone list and Windows' microphone privacy setting." % mic))
        elif mic == "starting":
            problems.append(("warn", "Starting the microphone..."))
        for _, text in self.media_warnings():
            if not any(text in t for _, t in problems):
                problems.append(("warn", text))
        if self.key_note[0] and self.clock() - self.key_note[1] < 8.0:
            problems.append(("warn", self.key_note[0]))
        if not any(p.media for p in self.pages):
            problems.append(("bad", "No voice window is connected."))
        for p in self.peers.peers.values():
            if p.err and p.inside and p.state in (PS.DISCONNECTED, PS.CONNECTING):
                problems.append(("warn", "Cannot connect to %s: %s" % (p.name, p.err)))
        if problems:
            worst = "bad" if any(l == "bad" for l, _ in problems) else "warn"
            head = next(t for l, t in problems if l == worst)
            return {"level": worst, "text": head, "more": [t for _, t in problems if t != head]}
        near, con = self.peers.nearby_count(), self.peers.counts()["CONNECTED"]
        who = "%s on %s" % (self.ident.character, self.ident.realm)
        text = "Ready: %s. " % who + ("Nobody in range." if not near else "%d player%s in range, %d connected." % (near, "" if near == 1 else "s", con))
        if self.tx_enabled:
            text += " You are transmitting."
        return {"level": "ok", "text": text, "more": []}

    def build_state(self, view=None) -> dict:
        view = view or self.monitor.view()
        src = self.source.status
        v = self.cfg.audio
        ident = self.ident
        return {
            "t": "state", "version": __version__, "sim": self.sim is not None,
            "identity": {"character": ident.character, "realm": ident.realm, "sid": ident.sid} if ident else None,
            "self_sid": ident.sid if ident else None,
            "signalling": self.signalling.status(),
            "gate": {"open": self.gate[0], "reason": self.gate[1]},
            "bridge": {"status": view.status, "age": round(view.age, 2), "frames": view.frames, "enabled": view.enabled,
                       "source_kind": self.source.kind, "source": src, "hint": self.bridge_hint(view, src),
                       "addon_flags": view.state.flags if view.state else 0},
            "metrics": self.metrics(view),
            "voice": {"mode": v.mode, "muted": bool(v.muted), "deafened": bool(v.deafened), "ptt_down": self.ptt_down,
                      "tx": self.tx_enabled, "ptt_label": v.ptt_label, "ptt_supported": self.ptt.supported,
                      "master": float(v.master_volume), "input_gain": self._input_gain(), "cal": dict(self.cal),
                      "calibrated": bool(v.get("calibrated", False)), "bitrate": self._bitrate_kbps(),
                      "keyboard": bool(v.get("keyboard_filter", True)), "boost": self._boost(),
                      "auto_level": bool(v.get("auto_level", True)), "gate_mode": self._gate_mode(), "gate_db": self._gate_db(),
                      "muted_players": sorted(self.peers.muted), "cues": bool(v.get("cues", True))},
            "audio": {"input": v.input, "output": v.output},
            "devices": self.devices, "mic": self.mic,
            "warnings": [t for _, t in self.media_warnings()], "speaker_test": self.speaker_test,
            "status": self.status_summary(view),
            "peers": self.peers.snapshot(),
            "proximity": {"say_radius": self.cfg.proximity.say_radius, "full_radius": self.cfg.proximity.full_radius,
                          "peak_radius": self.cfg.proximity.get("peak_radius", 0.0), "near_gain": self.peers.cfg.near_gain,
                          "curve": self.peers.cfg.curve},
            "sim_state": self._sim_state(),
        }

    def _sim_state(self) -> Optional[dict]:
        g = self.sim
        if g is None:
            return None
        w = g.world
        return {"character": g.character, "realm": g.realm, "frozen": g.frozen, "enabled": g.enabled, "lookups": g.lookups,
                "chars": {n: [round(p[0], 1), round(p[1], 1)] for n, p in w.chars.items()},
                "views": {"%s>%s" % k: v for k, v in w.views.items()}}

    def diag(self) -> dict:
        view = self.monitor.view()
        return {
            "version": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
            "uptime_s": round(time.time() - self.started, 1),
            "config": self.cfg.redacted(),
            "identity": {"character": self.ident.character, "realm": self.ident.realm} if self.ident else None,
            "gate": {"open": self.gate[0], "reason": self.gate[1]},
            "bridge": {"status": view.status, "age_s": round(view.age, 2), "frames": view.frames,
                       "enabled": view.enabled, "usable": view.usable, "reason": view.reason,
                       "flags": view.state.flags if view.state else None,
                       "readings": {n: round(d, 1) for n, d in self.readings.items()},
                       "source": self.source.status},
            "roster": {"size": len(self.roster), "inbox": str(self.inbox.path) if self.inbox else None,
                       "writes": self.inbox.writes if self.inbox else 0, "seq": self.inbox.seq if self.inbox else None,
                       "write_errors": self.inbox.errors if self.inbox else 0},
            "metrics": self.metrics(view),
            "signalling": self.signalling.status(),
            "ice_servers": [s.get("urls") for s in self._ice()],
            "voice": self.build_state(view)["voice"],
            "mic": self.mic, "devices": self.devices,
            "peers": self.peers.snapshot(),
            "pages": [{"ua": p.ua, "media": p.media, "automation": p.webdriver, "role": p.role} for p in self.pages],
            "warnings": [t for _, t in self.media_warnings()], "speaker_test": self.speaker_test,
            "log": RING.tail(200),
        }

    # ------------------------------------------------------------------ http

    def _authorised(self, request: web.Request) -> bool:
        tok = request.query.get("token") or request.headers.get("X-OV-Token", "")
        if not secrets.compare_digest(tok.encode(), self.token.encode()):
            return False
        origin = request.headers.get("Origin")
        if origin and origin not in ("http://127.0.0.1:%d" % self.port, "http://localhost:%d" % self.port):
            return False
        return True

    async def _index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def _static(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        if name not in ("app.js", "style.css", "voicemodel.js", "gate-worklet.js") and name not in VOICE_PICTURES:
            raise web.HTTPNotFound()
        return web.FileResponse(UI_DIR / name, headers={"Cache-Control": "no-store"})

    async def _api_state(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            raise web.HTTPForbidden()
        return web.json_response(self.build_state())

    async def _api_diag(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            raise web.HTTPForbidden()
        return web.json_response(self.diag())

    async def _api_sim(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            raise web.HTTPForbidden()
        if self.sim is None:
            raise web.HTTPConflict(text="not in simulation mode")
        d = await request.json()
        if not isinstance(d, dict) or not self._on_sim(d):
            raise web.HTTPBadRequest(text="unknown simulator command")
        return web.json_response({"ok": True})

    async def _api_ptt(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            raise web.HTTPForbidden()
        d = await request.json()
        self.ptt.virtual = bool(d.get("down"))
        self.ptt.sample()
        return web.json_response({"ok": True, "tx": self.tx_enabled})

    async def _ws_ui(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=256 * 1024)
        await ws.prepare(request)
        page = Page(ws)
        self.pages.append(page)
        self._window_back()
        try:
            async for m in ws:
                if m.type == WSMsgType.TEXT:
                    try:
                        self._on_page(page, json.loads(m.data))
                    except Exception:                                     # noqa: BLE001
                        log.exception("page message failed")
        finally:
            if page in self.pages:
                self.pages.remove(page)
            page.close()
            if page.media:
                self._elect_media_page()
            self._window_gone_maybe()
        return ws

    def _window_back(self) -> None:
        if self._window_task is not None:
            self._window_task.cancel()
            self._window_task = None

    def _window_gone_maybe(self) -> None:
        """The last window closed. When that means "quit" (the packaged exe), do it after a short grace period."""
        if self.tray_only or not self.quit_on_window_close or self.pages or self._quit.is_set():
            return                          # (tray-only: the engine is what must stay, and _engine_gone_maybe sees to it)
        self._window_back()
        self._window_task = asyncio.ensure_future(self._quit_if_still_gone())

    async def _quit_if_still_gone(self) -> None:
        try:
            await asyncio.sleep(self.window_grace_s)
        except asyncio.CancelledError:
            return
        if not self.pages:
            log.info("the EqVoice window was closed: quitting")
            trace("window_closed", quitting=True)
            self._quit.set()

    def _tell_pages(self, obj: dict) -> None:
        for p in self.pages:
            p.put(obj)

    async def _run_netcheck(self) -> None:
        """The window's "Check my connection": the same check as --netcheck, answered in the window."""
        from . import netcheck
        out = Path(self.cfg.home) / "logs" / "netcheck.txt"
        self._tell_pages({"t": "report", "kind": "netcheck", "text": "Checking your connection (about 10 seconds)..."})
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: netcheck.run(self.cfg.signalling_url, self.cfg.join_code, False, out))
            text = out.read_text(encoding="utf-8", errors="replace")
        except Exception as e:                                                  # noqa: BLE001
            log.exception("connection check failed")
            text = "The check could not run: %s" % e
        self._tell_pages({"t": "report", "kind": "netcheck", "text": text})

    async def _save_report(self) -> None:
        """The window's "Save report": a zip of logs on the Desktop, shown in Explorer."""
        from . import installer
        try:
            path = await asyncio.get_running_loop().run_in_executor(None, installer.make_report, self.cfg)
            text = "Saved: %s\nSend that one file to whoever asked for it." % path
            self.report_note = (Path(path).name, self.clock() + 25.0)
            if self.notify_user is not None:
                self.notify_user("EqVoice", "Saved %s. Drag that file into your Discord chat." % Path(path).name)
            if sys.platform == "win32" and not self.headless:
                import subprocess
                subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception as e:                                                  # noqa: BLE001
            log.exception("report failed")
            text = "Could not save the report: %s" % e
        self._tell_pages({"t": "report", "kind": "zip", "text": text})

    def _elect_media_page(self) -> None:
        owner = next((p for p in self.pages if p.media), None)
        if owner is None:
            self.peers.set_media_ready(False)
            self.mic = {"state": "no page", "label": "", "level": 0.0, "tx": False}
            self._engine_gone_maybe()

    # ------------------------------------------------------------ page input

    def _on_page(self, page: Page, m: dict) -> None:
        t = m.get("t")
        if page.media or t == "hello":
            self._media_seen = self.clock()
        if t == "hello":
            page.ua = str(m.get("ua", ""))[:200]
            page.webdriver = bool(m.get("webdriver"))
            page.role = str(m.get("role") or "")[:10]
            if page.role != "ui" and not any(p.media for p in self.pages):
                page.media = True
                log.info("media page connected (%s)", page.ua.split(" ")[-1] if page.ua else "?")
                trace("media_engine", browser=page.ua.split(" ")[-1] if page.ua else "?", headless=self._is_headless(page),
                      automation=page.webdriver, launched_by_this_client=self._proc is not None,
                      asked_for_fake_mic=self.fake_media, asked_for_mute=self.mute_audio)
                self.peers.set_media_ready(True)
                self._send_config()
                self._check_media()
            page.put(self.build_state(), droppable=True)
        elif t == "devices":
            self.devices = {"inputs": m.get("inputs", []), "outputs": m.get("outputs", [])}
            sig = (tuple(d.get("label", "") for d in self.devices["inputs"]), tuple(d.get("label", "") for d in self.devices["outputs"]))
            if sig != self._dev_sig:
                self._dev_sig = sig
                trace("audio_devices", microphones=[x or "?" for x in sig[0]], speakers=[x or "?" for x in sig[1]],
                      chosen_microphone=self.cfg.audio.input or "system default", chosen_speakers=self.cfg.audio.output or "system default")
        elif t == "mic":
            self.mic = {k: m.get(k) for k in ("state", "label", "level", "tx", "pcs", "gate", "gopen", "gfloor", "glevel", "gthr", "ldb", "hz", "vl")}
            sig = (self.mic.get("state"), self.mic.get("label"))
            if sig != self._mic_sig:
                self._mic_sig = sig
                trace("mic", state=sig[0], label=sig[1] or "-", fake=self._fake_label(sig[1]))
                self._check_media()
        elif t == "tone":
            self.speaker_test = {"ok": bool(m.get("ok")), "error": str(m.get("error") or "")[:120], "sink": str(m.get("sink") or "")[:80],
                                 "at": time.time()}
            trace("speaker_test", played=bool(m.get("ok")), speakers=m.get("sink") or "system default", error=self.speaker_test["error"] or None)
        elif t == "gate":
            if not m.get("ok"):
                self.gate_error = str(m.get("error") or "unknown")[:160]
                log.warning("the keyboard filter was switched off in the page: %s", self.gate_error)
                trace("keyboard_filter_failed", error=self.gate_error)
        elif t == "fx":
            if not m.get("ok"):
                self.fx_error = str(m.get("error") or "unknown")[:160]
                log.warning("the voice chain was switched off in the page: %s", self.fx_error)
                trace("voice_chain_failed", error=self.fx_error)
        elif t == "cal":
            self._on_calibration(m)
        elif t == "peer":
            sid, ep = m.get("sid"), m.get("ep")
            if isinstance(sid, str) and isinstance(ep, int):
                self.peers.on_media(sid, ep, m)
        elif t == "ui":
            self._on_ui(m)

    def _on_sim(self, m: dict) -> bool:
        """Simulator controls. Returns False for an unknown command."""
        g = self.sim
        cmd = m.get("cmd")
        if g is None:
            return False
        w = g.world
        name = str(m.get("name") or "")
        if cmd == "sim_add" and name:
            w.add(name, float(m.get("x", 0)), float(m.get("y", 0)))
        elif cmd == "sim_remove" and name:
            w.remove(name)
        elif cmd == "sim_move" and name:
            w.move(name, float(m.get("x", 0)), float(m.get("y", 0)))
        elif cmd == "sim_see" and m.get("target"):
            y = m.get("yards")
            w.see(g.character, str(m["target"]), None if y is None else float(y))
        elif cmd == "sim_freeze":
            g.frozen = bool(m.get("value"))
        elif cmd == "sim_enabled":
            g.enabled = bool(m.get("value"))
        elif cmd == "sim_lookups":
            g.lookups = bool(m.get("value"))
        elif cmd == "sim_logout":
            g.logout()
        elif cmd == "sim_login" and name:
            g.login(name, m.get("realm") or None)
            w.chars.setdefault(name, [0.0, 0.0])
        else:
            return False
        return True

    def _on_ui(self, m: dict) -> None:
        cmd, a = m.get("cmd"), self.cfg.audio
        if cmd == "set_input":
            a.input = str(m.get("id", ""))
            self.media({"cmd": "input", "id": a.input})
        elif cmd == "set_output":
            a.output = str(m.get("id", ""))
            self.media({"cmd": "output", "id": a.output})
        elif cmd == "set_mode" and m.get("mode") in ("ptt", "open"):
            a.mode = m["mode"]
        elif cmd == "mute":
            a.muted = bool(m.get("value"))
        elif cmd == "deafen":
            a.deafened = bool(m.get("value"))
        elif cmd == "ptt_key":
            if "code" in m:
                k = vk_from_code(str(m["code"]), m.get("key"))
                a.ptt_code = str(m["code"]) if k else a.ptt_code
            elif "wow" in m:                                     # the key as the game spells it: the in-game "change key" button
                k = vk_from_wow(str(m["wow"]))
                a.ptt_code = "" if k else a.ptt_code
            else:
                k = vk_from_mouse(int(m.get("button", 0)))
                a.ptt_code = "" if k else a.ptt_code
            trace("ptt_key", ok=bool(k), label=k[1] if k else "", source="game" if "wow" in m else "window")
            if k:
                a.ptt_vk, a.ptt_label = k
                log.info("push-to-talk key set to %s (virtual key 0x%02X)", k[1], k[0])
                self.key_note = ("", 0.0)
            elif "wow" in m:
                self.key_note = ("%s cannot be used as the key" % str(m["wow"])[:20], self.clock())
        elif cmd == "ptt_virtual":
            self.ptt.virtual = bool(m.get("down"))
            self.ptt.sample()
        elif cmd == "peer_volume":
            sid, val = m.get("sid"), float(m.get("value", 1.0))
            if isinstance(sid, str):
                a.peer_volume[sid] = max(0.0, min(1.5, val))
                self.media({"cmd": "volume", "sid": sid, "value": a.peer_volume[sid]})
        elif cmd == "master_volume":
            try:
                v = max(0.0, min(1.0, float(m.get("value", 1.0))))
            except (TypeError, ValueError):
                v = 1.0
            a.master_volume = v
            self.media({"cmd": "master", "value": v})
        elif cmd == "input_gain":
            try:
                a.input_gain = max(0.0, min(calibration.GAIN_MAX, float(m.get("value"))))
            except (TypeError, ValueError):
                pass
            trace("input_gain", value=self._input_gain())
            self.media({"cmd": "input_gain", "value": self._input_gain()})
        elif cmd == "calibrate":
            self._start_calibration()
        elif cmd == "calibrate_cancel":
            self._cancel_calibration()
        elif cmd == "boost" and m.get("value") in self.BOOSTS:
            a.boost = m["value"]
            self.peers.cfg.near_gain = self._boost_gain()
            trace("boost", value=a.boost, gain=self.peers.cfg.near_gain)
            self.media({"cmd": "curve", "value": self.peers.cfg.curve})
        elif cmd == "auto_level":
            a.auto_level = bool(m.get("value"))
            trace("auto_level", on=a.auto_level)
            self.media({"cmd": "level", "value": a.auto_level})
        elif cmd == "gate" and m.get("value") in ("off", "auto", "manual"):
            # the noise gate as one choice: off is the microphone as it is; auto and manual keep the keyboard filter on and pick the bar
            on = m["value"] != "off"
            if m["value"] != "off":
                self._on_ui({"cmd": "gate_mode", "value": m["value"]})
            if on != bool(a.get("keyboard_filter", True)):
                self._on_ui({"cmd": "keyboard_filter", "value": on})
        elif cmd == "gate_mode" and m.get("value") in ("auto", "manual"):
            a.gate_mode = m["value"]
            trace("gate_mode", mode=a.gate_mode, db=self._gate_db())
            self.media({"cmd": "gate", "mode": self._gate_mode(), "db": self._gate_db()})
        elif cmd == "gate_db":
            try:
                a.gate_db = max(-100.0, min(0.0, float(m.get("value"))))
            except (TypeError, ValueError):
                pass
            self.media({"cmd": "gate", "mode": self._gate_mode(), "db": self._gate_db()})
        elif cmd == "mute_player":
            name = str(m.get("name") or "").strip()
            if 1 <= len(name) <= 24 and not re.search(r"[\x00-\x20\x7f=|]", name):
                self.peers.set_muted(name, bool(m.get("value")))
                a.mute_list = sorted(self.peers.muted)
                trace("mute_player", name=name, muted=bool(m.get("value")), listed=len(self.peers.muted))
        elif cmd == "keyboard_filter":
            a.keyboard_filter = bool(m.get("value"))
            self.gate_error = ""                                 # choosing it again is a retry
            trace("keyboard_filter", on=a.keyboard_filter)
            self.media({"cmd": "keyboard", "value": a.keyboard_filter})
        elif cmd == "max_bitrate":
            try:
                a.max_bitrate_kbps = int(float(m.get("value")))
            except (TypeError, ValueError):
                pass
            trace("max_bitrate", kbps=self._bitrate_kbps())
            self.media({"cmd": "bitrate", "value": self._bitrate_kbps()})
        elif cmd == "netcheck":
            asyncio.ensure_future(self._run_netcheck())
        elif cmd == "save_report":
            asyncio.ensure_future(self._save_report())
        elif cmd == "test_speakers":
            self.speaker_test = {}
            trace("speaker_test_requested", speakers=a.output or "system default")
            self.media({"cmd": "tone"})
        elif cmd == "quit":
            log.info("quit requested from the UI")
            self._quit.set()
        elif isinstance(cmd, str) and cmd.startswith("sim_"):
            self._on_sim(m)
        self._apply_voice()
        self._save_soon()

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> str:
        self.cfg.audio.peer_volume = dict(self.cfg.audio.peer_volume)
        trace("start", version=__version__, hub=self.cfg.signalling_url, source=self.source.kind, wow_dir=self.cfg.bridge.wow_dir or None,
              files=("eqvoice-%s.txt" % self.cfg.bridge.character) if self.cfg.bridge.character else "eqvoice.txt",
              test_names=self.test_names or None, fake_media=self.fake_media, headless=self.headless, mute_audio=self.mute_audio)
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/static/{name}", self._static)
        app.router.add_get("/ws/ui", self._ws_ui)
        app.router.add_get("/api/state", self._api_state)
        app.router.add_get("/api/diag", self._api_diag)
        app.router.add_post("/api/sim", self._api_sim)
        app.router.add_post("/api/ptt", self._api_ptt)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", int(self.cfg.ui.port))
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        RING.listeners.append(self._log_cb)
        self._tasks = [
            asyncio.create_task(self._run_source(), name="bridge"),
            asyncio.create_task(self.signalling.run(), name="signalling"),
            asyncio.create_task(self._run_ticker(), name="ticker"),
            asyncio.create_task(self._run_meter(), name="meter"),
            asyncio.create_task(self.ptt.run(), name="ptt"),
        ]
        url = "http://127.0.0.1:%d/?token=%s" % (self.port, self.token)
        self.url = url
        if self.launch_browser and not self.engine_on_demand:
            self._engine_started_at = self.clock()
            await self._launch_window(url, role="engine" if self.tray_only else None)
        return url

    async def _launch_window(self, url: str, role: Optional[str] = None, fresh: bool = True) -> None:
        """Open a browser window on the page. ``role`` "engine" is the voice engine (kept out of sight), "ui" a window only; none is the page that is both.
        ``fresh``: first stop any leftover browser that holds the profile (not when the engine is running in it and this is only a window)."""
        exe = browser.find_browser(self.cfg.ui.browser)
        if exe is None:
            log.error("no Chromium-based browser found (Edge or Chrome). Open %s in one yourself.", url)
            return
        profile = self.cfg.home / "browser-profile"
        target = url + ("&role=" + role if role else "")
        stale = await asyncio.get_running_loop().run_in_executor(None, browser.kill_profile, profile) if fresh else 0
        if stale:
            log.warning("stopped %d leftover browser process(es) holding %s: a new window would have joined them "
                        "(with their old fake-microphone / muted-output flags)", stale, profile)
            trace("stale_browser_stopped", count=stale, profile=profile)
        proc = browser.launch(exe, target, profile, headless=self.headless, fake_media=self.fake_media, mute=self.mute_audio)
        if role != "ui":
            self._proc = proc                       # the engine's process: what shutting down ends (a window opened later only hands over to it)

    async def open_ui_window(self) -> None:
        """Tray-only: the window, on request (the tray's \"Open EqVoice\", and the first run). Asked twice within three seconds it opens once."""
        if not (self.launch_browser and getattr(self, "url", None)):
            return
        now = self.clock()
        if now - self._ui_launched < 3.0:
            return
        self._ui_launched = now
        await self._launch_window(self.url, role="ui", fresh=False)

    async def reopen_window(self) -> None:
        """The tray's \"Open EqVoice\" when there is no window to bring forward: a window again (tray-only) or the one page again (otherwise)."""
        if self.tray_only:
            await self.open_ui_window()
            return
        if self.launch_browser and getattr(self, "url", None):
            await self._launch_window(self.url)

    def _engine_gone_maybe(self) -> None:
        """Tray-only: the voice engine's page went away (a crash, or someone ended it). Start it again, out of sight; a few failures in a row
        and the player gets a window instead, which shows what is wrong."""
        if not self.tray_only or self._quit.is_set() or not self.launch_browser:
            return
        if self.engine_on_demand and self.ident is None:
            return                                          # nobody is playing: there is no reason for an engine
        if self._engine_task is not None:
            self._engine_task.cancel()
        self._engine_task = asyncio.ensure_future(self._relaunch_engine())

    async def _relaunch_engine(self) -> None:
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return
        if self._quit.is_set() or any(p.media for p in self.pages):
            return
        now = self.clock()
        self._engine_starts = [t for t in self._engine_starts if now - t < 120.0] + [now]
        if len(self._engine_starts) > 4:
            log.error("the voice engine keeps stopping: it is not started again")
            trace("engine_restart_gave_up", starts=len(self._engine_starts))
            if self.notify_user is not None:
                self.notify_user("EqVoice", "Voice stopped working and could not be restarted. Choose Restart EqVoice from the tray menu.")
            else:
                self._ui_launched = -100.0
                await self.open_ui_window()                 # a developer run: the window shows what is wrong
            return
        log.warning("the voice engine's window went away: starting it again")
        trace("engine_restart", starts=len(self._engine_starts))
        self._engine_started_at = now
        await self._launch_window(self.url, role="engine")

    # ------------------------------------------------------- the engine, on demand

    def _engine_running(self, now: float) -> bool:
        return any(p.media for p in self.pages) or now - self._engine_started_at < 20.0       # (a launch gets twenty seconds to show up)

    def _watch_engine(self, now: float) -> None:
        """The engine's page is connected but has said nothing for a long time (a frozen browser: it answers no offer, so no call can form).
        End it and start it again; the usual relaunch rules (and their limit) apply."""
        if not (self.tray_only and self.launch_browser) or self._quit.is_set() or self._engine_stopping:
            return
        if not any(p.media for p in self.pages):
            return
        silent = now - max(self._media_seen, self._engine_started_at)
        if silent < self.engine_silent_s:
            return
        self._media_seen = now                           # one restart at a time: the next look is a whole silence later
        asyncio.ensure_future(self._restart_silent_engine(silent))

    async def _restart_silent_engine(self, silent: float) -> None:
        log.warning("the voice engine has said nothing for %.0f s: ending it and starting it again", silent)
        trace("engine_unresponsive", silent_s=round(silent, 1))
        self._engine_stopping = True
        try:
            proc, self._proc = self._proc, None
            if proc is not None and proc.poll() is None:
                proc.terminate()
            await asyncio.get_running_loop().run_in_executor(None, browser.kill_profile, self.cfg.home / "browser-profile")
        finally:
            self._engine_stopping = False
        for p in list(self.pages):
            if p.media:
                await p.ws.close()                       # the page leaving starts the usual relaunch (the handler's cleanup)

    def _manage_engine(self, now: float) -> None:
        """Start the engine when a game reports a character; stop it when there has been no game for a while."""
        if not (self.engine_on_demand and self.launch_browser) or self._quit.is_set() or self._engine_stopping:
            return
        if self.ident is not None:
            self._engine_idle_since = None
            if not self._engine_running(now):
                self._engine_started_at = now
                asyncio.ensure_future(self._start_engine())
            return
        if self._engine_idle_since is None:
            self._engine_idle_since = now
            return
        if now - self._engine_idle_since >= self.engine_idle_s and (self._proc is not None or any(p.media for p in self.pages)):
            asyncio.ensure_future(self._stop_engine())

    async def _start_engine(self) -> None:
        log.info("a game is being played: starting the voice engine")
        trace("engine_start", why="a game is being played")
        await self._launch_window(self.url, role="engine")

    async def _stop_engine(self) -> None:
        self._engine_stopping = True
        try:
            log.info("no game for %.0f s: the voice engine is stopped until there is one", self.engine_idle_s)
            trace("engine_stop", idle_s=self.engine_idle_s)
            proc, self._proc = self._proc, None
            if proc is not None and proc.poll() is None:
                proc.terminate()
            await asyncio.get_running_loop().run_in_executor(None, browser.kill_profile, self.cfg.home / "browser-profile")
        finally:
            self._engine_stopping = False
            self._engine_started_at = -100.0

    async def _run_source(self) -> None:
        try:
            await self.source.run(self._on_frame)
        except asyncio.CancelledError:
            raise
        except Exception:                                                  # noqa: BLE001
            log.exception("bridge source crashed")

    async def _run_ticker(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:                                              # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(TICK)

    async def wait_closed(self) -> None:
        await self._quit.wait()

    def request_quit(self) -> None:
        self._quit.set()

    async def stop(self) -> None:
        log.info("shutting down")
        try:
            RING.listeners.remove(self._log_cb)
        except ValueError:
            pass
        self.peers.shutdown()
        await self.signalling.stop()            # unregisters first: our name leaves the roster at once
        if self._engine_task is not None:
            self._engine_task.cancel()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except BaseException:                                          # noqa: BLE001
                pass
        if self.inbox is not None:
            self.inbox.clear()                  # the game stops looking players up the moment we are gone
        if self.ui_status is not None:
            self.ui_status.clear()              # and its panel says the app is not running
        for p in self.pages:
            p.close()
        # Close the windows' connections ourselves: the server's own clean-up waits (a minute, by default) for every open connection to end, and a
        # window that is still open - the voice engine always is, in tray-only mode - never ends it until the browser is stopped, which comes after.
        await asyncio.gather(*(asyncio.wait_for(p.ws.close(), 2.0) for p in list(self.pages)), return_exceptions=True)
        if self._runner is not None:
            try:
                await asyncio.wait_for(self._runner.cleanup(), 10.0)
            except asyncio.TimeoutError:
                log.warning("the window server did not shut down in 10 s; going on without it")
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
            browser.kill_profile(self.cfg.home / "browser-profile")      # its helper processes, and any that got detached
        try:
            self.cfg.save()
        except OSError:
            pass
