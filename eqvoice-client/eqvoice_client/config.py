"""Configuration: a JSON file with defaults, plus attribute access (cfg.bridge.corner)."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

DEFAULTS = {
    # Where to find the signalling server. Replace with your community's server.
    "signalling_url": "ws://127.0.0.1:8787/ws",
    "join_code": "",
    # Privacy mode: only use TURN relays, never reveal this PC's address to peers.
    # Needs a TURN server in ice_servers (or offered by the signalling server).
    "relay_only": False,
    "ice_servers": None,          # None = use what the signalling server offers
    "bridge": {
        "source": "auto",         # auto | optical | file | sim
        "corner": "TOPLEFT",      # must match /ev bridge corner in game
        "wow_dir": "",            # game folder: CustomData\ (and Imports\) hold the bridge files
        "file_path": "",          # explicit path to eqvoice.txt, overrides wow_dir
        "claim": False,           # (EqVoice.exe sets it) take whichever logged-in character no other copy of EqVoice serves, for a copy started without --character
        "character": "",          # only when two game instances share one folder: use eqvoice-<Character>.txt / eqvoice-in-<Character>.txt
        "region": [768, 288],     # pixels captured in the chosen corner
        "poll_hz": 10,
        "window_classes": ["GxWindowClassD3d", "GxWindowClassOpenGl", "GxWindowClass"],
        "window_title": "World of Warcraft",
        "screen_region": None,    # [x, y, w, h] to capture a fixed screen area instead of the window
        "stale_s": 4.0,           # no new frame for this long = stale (last readings kept)
        "hold_s": 8.0,            # ... and after this long nobody is audible (fail closed)
        "ghost_s": 30.0,          # ... and after this long the character is taken off the roster (game gone)
    },
    "audio": {
        "input": "",
        "output": "",
        "mode": "open",           # open | ptt: open mic is the default (switch in the game with /eqvc, or in this window)
        "ptt_code": "F9",         # physical key (KeyboardEvent.code); resolved through your keyboard layout
        "ptt_vk": 0,              # Windows virtual key; derived from ptt_code at start, or a mouse button
        "ptt_label": "F9",
        "ptt_release_ms": 200,
        "processing": True,       # echo cancellation, noise suppression, auto gain (turn off to debug)
        "muted": False,
        "deafened": False,
        "master_volume": 1.0,     # 0..1 on top of the per-player volumes; set from the game panel or this window
        "input_gain": 1.0,        # how loud your microphone is before anything else touches it: 0 .. 2.0 (1.0 = as the microphone gives it); the setup may move it
        "calibrated": False,      # the microphone setup (silence, a normal voice, a raised voice) has been run and has set the noise gate
        "max_bitrate_kbps": 24,   # cap on the voice you send and ask for, 8..64 (0 = the browser's own default, about 32)
        "keyboard_filter": True,  # keep keyboard clacks, mouse clicks and other short noises off your microphone (a gate with a short look-ahead)
        "auto_gain": False,       # the browser's automatic microphone level: off, so that talking louder is heard as louder (and reaches further)
        "boost": "normal",        # how much louder a voice is right next to you, easing away smoothly to nothing at 10 yd: off | low (x1.4) | normal (x2) | high (x3)
        "auto_level": True,       # lift quiet microphones and ease hot ones towards the same level on your side, so every voice is loud and clear
        "gate_mode": "auto",      # the noise gate (it is the keyboard filter, with a bar): auto follows the room's noise, manual uses gate_db
        "gate_db": -48.0,         # manual mode: the level (dB, in the speech band) a voice must reach to open the gate; -80 (very sensitive) .. -20
        "mute_list": [],          # players whose voice you never hear (lower-case character names)
        "block_list": [],         # players who never hear YOU: your microphone is not sent to them (lower-case character names)
        "player_volume": {},      # lower-case character name -> 0.0 .. 2.0: how loud that one player is for you (1.0 = as everyone else)
        "spatial": "headphones",  # where voices are heard from: mono | speakers | headphones
        "deaf_keep_mic": False,   # deafened, but your microphone stays open: you hear nobody, they still hear you
        "cues": True,             # a short blip when you mute, unmute, deafen or undeafen (including with the key, in the game)
        "peer_volume": {},
    },
    "proximity": {
        "say_radius": 41.0,       # yards: how far an ordinary voice carries. Measured /say range on this server: 40.8 to 41.2 (docs/08)
        "peak_radius": 85.0,      # yards: where the volume curve ends: silent from here. Calls are kept up to here
        "full_radius": 30.0,      # yards: a voice is at its full volume up to here (30 yd is still 100%), then falls away to nothing at peak_radius
        "loud_full_radius": 45.0, # yards: ... and a raised voice (shouting) stays at full volume up to here
        "curve_power": 1.6,       # how the volume falls between full_radius and peak_radius: (1 - t) ** power, so about 12% of full at 70 yd
        "boost_radius": 0.0,      # yards of full boost held before it starts to ease (0: it eases away from the first inch) ...
        "boost_fade_radius": 10.0,# ... and it has eased away to nothing by here (a smooth curve, no step)
        "leave_hysteresis": 3.0,  # yards beyond the radius before a player counts as having left
        "report_radius": 95.0,    # yards: players reported to the signalling server as 'seen' (at least peak_radius + leave_hysteresis)
        "debounce_s": 0.8,        # inside the radius this long before a connection is attempted
        "leave_grace_s": 1.5,     # out of range / unseen this long before the voice is faded out
        "teardown_s": 8.0,        # ... and faded out this long before the peer connection is closed
        "connect_timeout_s": 20.0,
        "max_peers": 8,
        "fade_in_ms": 300,
        "fade_out_ms": 500,
        "retry_backoff_s": [3, 6, 12, 24, 48],
        "forget_s": 30.0,
        "fail_closed_s": 8.0,     # signalling down this long = everyone fades out
    },
    "roster": {
        "refresh_s": 4.0,         # rewrite the roster file at least this often (the game treats 15 s of silence as 'gone')
        "scan_interval_s": 0.5,   # how often the game looks the roster up
        "max_checks": "auto",     # names the game checks per scan: a number, or "auto" = enough to sweep the whole roster in sweep_s
        "sweep_s": 3.0,           # auto: aim for a full sweep of the roster in about this many seconds (never below 80 or above max_checks_cap per scan)
        "max_checks_cap": 200,    # auto: the most lookups the game is ever asked to do in one scan
        "report": 100.0,          # yards: the game reports lookups out to here (at least report_radius)
        "max_names": 2000,        # names handed to the game; beyond this a rotating window is used
        "test_names": [],         # extra names for the game to look up that are not on the hub roster (--test-roster, one-player checks)
    },
    "sim": {                      # simulation mode (--source sim): a pretend game, see bridge/sim.py
        "character": "Alice",
        "realm": "Testrealm",
        "world": "",              # JSON file shared by several simulated clients; blank = private world
        "folder": "",             # the pretend game folder (default: <home>\\sim); share one to rehearse two instances in one folder
    },
    "ui": {
        "port": 17420,
        "open_browser": True,
        "browser": "",            # path to msedge/chrome, blank = auto-detect
        "token": "",              # blank = random per launch
        "tray": True,             # EqVoice.exe puts its icon in the system tray (double-click: the window; right-click: open or quit)
        "developer": False,       # a developer build: the window (settings, diagnostics) can be opened from the tray menu. The public build has no window at all
        "engine_on_demand": True, # tray mode: the voice engine (a browser) runs only while a game is being played; false: from the moment the program starts
        "tray_only": True,        # ... and, with that icon, has no taskbar button: the voice engine runs in a window nobody sees, and the real window opens from the tray (false: one window, with its button, as before)
    },
    "diagnostics": {
        "log_ips": False,         # put real IP addresses in the logs (default: only their kind: private, public, cgnat ...)
    },
    "ux": {
        "welcomed": False,        # the one-time "here is what to do in the game" message of EqVoice.exe has been shown
    },
    "log_level": "INFO",
    "config_version": 5,
}

# Version 2 made open mic the default and F9 the push-to-talk key. A saved file from before that still says "ptt" with the
# old default key (a backtick); nobody chose that, so it moves to the new defaults once. A different key means the player
# set it up on purpose and the file is left alone.
_OLD_DEFAULT_PTT = ("Backquote", "")


# Version 3 widened the range (a raised voice carries to 85 yd, a voice is doubled inside 15) and the lookups that feed it. Every saved
# file holds the old defaults (the app writes them all out), so a value that still equals the old default moves to the new one;
# a value the player changed is left alone.
_V3_CHANGES = (("proximity", "full_radius", 10.0, 15.0), ("proximity", "report_radius", 50.0, 95.0), ("roster", "report", 60.0, 100.0))

# Version 4 changed the volume curve: a voice is at 100% to 30 yd (it was doubled inside 15 and falling after that), quiet at 70, and the
# doubling became the "boost" option for the people right next to you. The old doubled-inside-15 radius moves to the new 30; a value the
# player changed is left alone.
_V4_CHANGES = (("proximity", "full_radius", 15.0, 30.0),)


# Version 5 took the "voice effect" (a telephone-style band-limit on what you hear) out: it changed nothing anyone could hear, and a setting
# that does nothing is a setting to get wrong. A saved file still holding it loses it.


def _migrate(data: dict, saved_version: int) -> None:
    if saved_version < 2:
        a = data.get("audio", {})
        if a.get("mode") == "ptt" and a.get("ptt_code") in _OLD_DEFAULT_PTT:
            a["mode"], a["ptt_code"], a["ptt_label"] = "open", "F9", "F9"
    if saved_version < 3:
        for section, key, old, new in _V3_CHANGES:
            sec = data.get(section)
            if isinstance(sec, dict) and sec.get(key) == old:
                sec[key] = new
    if saved_version < 4:
        for section, key, old, new in _V4_CHANGES:
            sec = data.get(section)
            if isinstance(sec, dict) and sec.get(key) == old:
                sec[key] = new
    if saved_version < 5:
        a = data.get("audio")
        if isinstance(a, dict):
            a.pop("voice_fx", None)
    data["config_version"] = DEFAULTS["config_version"]


class Section(dict):
    """dict with attribute access; nested dicts are Sections too."""

    def __getattr__(self, name):
        try:
            v = self[name]
        except KeyError:
            raise AttributeError(name) from None
        return v

    def __setattr__(self, name, value):
        self[name] = value


def _wrap(d):
    if isinstance(d, dict):
        return Section({k: _wrap(v) for k, v in d.items()})
    return d


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k not in ("peer_volume", "player_volume"):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


# The product was called OctoVoice before it was EqVoice. Its data folder (settings, the voice server's address, this PC's identity, logs) is
# carried over to the new name once, so nobody has to set up again.
LEGACY_HOME_NAME = "OctoVoice"


def default_home() -> Path:
    env = os.environ.get("EQVOICE_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base, new, old = Path(os.environ.get("APPDATA", str(Path.home()))), "EqVoice", LEGACY_HOME_NAME
    else:
        base, new, old = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))), "eqvoice", LEGACY_HOME_NAME.lower()
    home = base / new
    migrate_legacy_home(base / old, home)
    return home


def migrate_legacy_home(old: Path, new: Path) -> bool:
    """Move the folder of the old name to the new one if there is nothing at the new one yet. True if it was moved or copied."""
    try:
        if new.exists() or not old.is_dir():
            return False
        try:
            old.rename(new)
        except OSError:
            import shutil
            shutil.copytree(old, new)
        return True
    except OSError:
        return False


class Config(Section):
    path: Path

    @classmethod
    def load(cls, home: Path, overrides: dict | None = None) -> "Config":
        home = Path(home)
        home.mkdir(parents=True, exist_ok=True)
        data = copy.deepcopy(DEFAULTS)
        p = home / "config.json"
        if p.exists():
            try:
                saved = json.loads(p.read_text(encoding="utf-8"))
                saved_version = int(saved.get("config_version", 1)) if isinstance(saved, dict) else 1
                _merge(data, saved)
                _migrate(data, saved_version)
            except (OSError, ValueError, TypeError):
                (home / "config.json.bad").write_bytes(p.read_bytes())
                data = copy.deepcopy(DEFAULTS)
        if overrides:
            _merge(data, overrides)
        cfg = cls(_wrap(data))
        object.__setattr__(cfg, "path", p)
        object.__setattr__(cfg, "home", home)
        return cfg

    def save(self) -> None:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(self.home), suffix=".tmp", encoding="utf-8")
        try:
            json.dump(self, tmp, indent=2, sort_keys=True)
            tmp.close()
            os.replace(tmp.name, self.path)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def redacted(self) -> dict:
        d = json.loads(json.dumps(self))
        if d.get("join_code"):
            d["join_code"] = "***"
        if d["ui"].get("token"):
            d["ui"]["token"] = "***"
        return d
