"""The real-WoW test, in one command. Starts the local Cloudflare hub, one EqVoice desktop client per character
(found automatically from the game's files), and a live checklist of the 20 boundaries.

    python tools/realtest.py doctor                 checks everything that can be checked without the game
    python tools/realtest.py run                    hub + clients + live checklist; log your characters in
    python tools/realtest.py run --test-roster Wipemon
                                                    one-player check: also ask the game to look up this name
    python tools/realtest.py report [RUN_DIR]       print the checklist for the last run again
    python tools/realtest.py hub                    just the hub (wrangler dev with TRACE on)
    python tools/realtest.py stop                   end a run that is still going (its hub, clients and voice windows)
    python tools/realtest.py run --tunnel           also reach the hub from the internet (Cloudflare quick tunnel, needs cloudflared),
                                                    build the friend's zip, and print live connection lines (two-PC test)
    python tools/realtest.py report --friend LOGS   add the friend's SEND-LOGS zip (or folder) to the checklist
                                                    (a test with three or more people: --friend once for each friend)

`run` waits for `CustomData\\eqvoice-<Character>.txt` files that the addon writes (a fresh one per logged-in
character, including two game instances sharing one folder) and starts a client for each. Everything is logged
under D:\\Octowow\\.eco-work\\realtest\\ (hub.log, home-<Character>\\logs\\eqvoice.log). Ctrl+C stops it all
and prints the final checklist.

The two clients share your microphone and speakers: use headphones, and note the push-to-talk keys differ
(F9, F10, ...), or use the "Hold to talk" button in each client's window.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "eqvoice-client"))
import pipeline_report as pr  # noqa: E402
import tunnel  # noqa: E402
import make_test_pack  # noqa: E402
from eqvoice_client import browser  # noqa: E402

WORK = Path(r"D:\Octowow\.eco-work")
NODE = WORK / "node"
RUNS = WORK / "realtest"
CF = ROOT / "eqvoice-signalling-cf"
DEFAULT_WOW = Path(r"D:\Octowow")
HUB_PORT = 8787
PTT_CODES = ["F9", "F10", "F11", "F12"]


# ------------------------------------------------------------------ helpers

def say(msg: str = "") -> None:
    print(msg, flush=True)


def node_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = str(NODE) + os.pathsep + env.get("PATH", "")
    env.update({"npm_config_cache": str(WORK / "npm-cache"), "TMP": str(WORK / "tmp"), "TEMP": str(WORK / "tmp"),
                "XDG_CONFIG_HOME": str(WORK / "xdg-config"), "XDG_CACHE_HOME": str(WORK / "xdg-cache"),
                "XDG_DATA_HOME": str(WORK / "xdg-data"), "WRANGLER_SEND_METRICS": "false",
                "WRANGLER_LOG_PATH": str(WORK / "wrangler-logs"), "NO_UPDATE_NOTIFIER": "1"})
    return env


def port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def kill_tree(p: subprocess.Popen) -> None:
    if p.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)


def stop_all() -> int:
    """End every EqVoice test process: other `realtest run`s first (so they stop respawning clients), then the clients,
    the Wrangler hub, and the voice windows. Returns how many processes it stopped."""
    def pids(pattern: str) -> list:
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.ProcessId -ne %d -and "
                  "$_.Name -match '^(python|node|workerd)\\.exe$' -and $_.CommandLine -match '%s' } | ForEach-Object { $_.ProcessId }" % (os.getpid(), pattern))
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30).stdout
        return [int(x) for x in out.split() if x.isdigit()]
    n = 0
    for pattern in (r"realtest\.py run", r"eqvoice_client.*realtest|eqvoice-signalling-cf"):
        for pid in pids(pattern):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
            n += 1
        time.sleep(0.5)
    return n + browser.kill_profile(RUNS)


def busy_ports() -> list:
    return [17421 + i for i in range(len(PTT_CODES)) if not port_free(17421 + i)]


def file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


def addon_status(wow: Path) -> tuple:
    """(ok, message): is the addon in the game folder the one in this repository?"""
    src = ROOT / "eqvoice-addon" / "EqVoice"
    dst = wow / "Interface" / "AddOns" / "EqVoice"
    if not dst.exists():
        return False, "not installed: powershell tools\\install-addon.ps1 -AddOns \"%s\"" % (wow / "Interface" / "AddOns")
    stale = [f.name for f in sorted(src.glob("*")) if f.is_file() and (not (dst / f.name).exists() or file_hash(f) != file_hash(dst / f.name))]
    if stale:
        return False, "STALE (differs in %s): run powershell tools\\install-addon.ps1 -AddOns \"%s\" then /reload in game" % (
            ", ".join(stale), wow / "Interface" / "AddOns")
    return True, "installed and identical to the repository (%d files)" % len(list(src.glob("*")))


def repair_addon(wow: Path, why: str) -> tuple:
    """The addon is missing or stale: put it in with the same installer EqVoice.exe uses (which also retires the addon's old name and switches
    it on for every character), unless the game is running: the game rewrites its addon lists when a character is left, so it must be closed."""
    from eqvoice_client import installer
    if not installer.is_wow_dir(wow):                      # only ever into a real game folder (the one with WoW.exe), never into whatever path was given
        return False, "%s. %s has no WoW.exe, so it is not a game folder: nothing was installed" % (why, wow)
    if installer.game_is_open(wow):
        return False, "%s. The game is running: close it and run this again, and the addon is installed (and switched on for every character) for you" % why
    try:
        res = installer.install_addon(wow)
        on = installer.enable_addon(wow)
    except installer.InstallError as e:
        return False, "%s. Could not install it: %s" % (why, e)
    ok, msg = addon_status(wow)
    return ok, "installed it for you: %s; %s. Now %s" % (res.words(), on.words(), msg)


def hub_up() -> bool:
    try:
        return urllib.request.urlopen("http://127.0.0.1:%d/health" % HUB_PORT, timeout=1).read().strip() == b"ok"
    except Exception:                                              # noqa: BLE001
        return False


def character_files(wow: Path, max_age: float = 20.0) -> dict:
    """{Character: path} for every eqvoice-<Character>.txt the addon wrote in the last ``max_age`` seconds."""
    out = {}
    data = wow / "CustomData"
    if not data.exists():
        return out
    for f in data.glob("eqvoice-*.txt"):
        m = re.fullmatch(r"eqvoice-([A-Za-z]{2,24})\.txt", f.name)
        if m and not f.name.startswith("eqvoice-in-") and time.time() - f.stat().st_mtime <= max_age and is_bridge_file(f):
            out[m.group(1)] = f
    return out


def is_bridge_file(path: Path) -> bool:
    """The addon's own file (OVB2 frames), not one of the app's: eqvoice-in.txt (roster, OVI1), eqvoice-st.txt (status, OVS1) and
    eqvoice-ctl.txt (commands, OVC1) have two-or-three-letter names that look just like a character called In, St or Ctl."""
    try:
        with open(path, "rb") as fh:
            return b"OVB2 " in fh.read(8192)
    except OSError:
        return False


# ------------------------------------------------------------------- doctor

def doctor(wow: Path) -> int:
    bad = 0

    def line(ok, what, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        say("  [%s] %s%s" % ("ok" if ok else "!!", what, ("  " + detail) if detail else ""))

    say("Pre-flight (nothing here needs the game running)")
    line((wow / "WoW.exe").exists(), "game folder %s" % wow, "" if (wow / "WoW.exe").exists() else "no WoW.exe here: pass --wow-dir")
    line((wow / "ClassicAPI.dll").exists(), "ClassicAPI.dll present (exact-name lookups)")
    ok, msg = addon_status(wow)
    line(ok, "addon", msg)
    toc = (wow / "Interface" / "AddOns" / "EqVoice" / "EqVoice.toc")
    line(toc.exists(), "EqVoice.toc in the game's AddOns folder")
    data = wow / "CustomData"
    ins = [f.name for f in data.glob("eqvoice-in*.txt")] if data.exists() else []
    plain = data / "eqvoice-in.txt"
    if plain.exists():
        say("      CustomData\\eqvoice-in.txt holds %r: the plain test text. `/ev diag inbox` should print it and say it is not a roster file." %
            plain.read_text(encoding="utf-8", errors="replace")[:40])
    fresh = character_files(wow, 3600)
    say("      per-character files seen in the last hour: %s" % (", ".join(sorted(fresh)) or "none (the new addon has not run yet)"))
    line((NODE / "node.exe").exists(), "Node at %s" % NODE)
    wrangler = CF / "node_modules" / "wrangler" / "bin" / "wrangler.js"
    line(wrangler.exists(), "Wrangler installed", "" if wrangler.exists() else "cd eqvoice-signalling-cf && npm install (with D:\\Octowow\\.eco-work\\node-env.sh sourced)")
    line(browser.find_browser() is not None, "Edge or Chrome found (the voice engine)")
    left = browser.profile_pids(RUNS)
    line(not left, "no leftover test browsers from an earlier run",
         ("%d process(es) still hold EqVoice profiles under %s: `run` stops them first (a leftover one can silently become "
          "the voice engine, with a fake microphone)" % (len(left), RUNS)) if left else "")
    for mod in ("aiohttp", "cryptography"):
        try:
            __import__(mod)
            line(True, "python module %s" % mod)
        except ImportError:
            line(False, "python module %s" % mod, "pip install %s" % mod)
    line(port_free(HUB_PORT) or hub_up(), "port %d is free (or the hub is already running)" % HUB_PORT)
    line(all(port_free(17421 + i) for i in range(4)), "client UI ports 17421-17424 are free")
    running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WoW.exe", "/NH"], capture_output=True, text=True).stdout
    n = running.lower().count("wow.exe")
    say("      WoW.exe instances running right now: %d" % n)
    say("")
    say("Not checkable here: that the game reads CustomData\\eqvoice-in-<Character>.txt, and the lookup by name at range.")
    say("`run` will tell you which of those two failed.")
    return 1 if bad else 0


# ---------------------------------------------------------------------- hub

def start_hub(run_dir: Path, python_hub: bool = False, join_code: str = "") -> subprocess.Popen:
    log = open(run_dir / "hub.log", "w", encoding="utf-8")
    if hub_up():
        if join_code:
            raise SystemExit("a hub is already answering on port %d, and I cannot give it a join code. End it first: python tools\\realtest.py stop" % HUB_PORT)
        say("a hub is already answering on port %d; using it (its log is not captured here)" % HUB_PORT)
        return None
    if python_hub:
        cmd = [sys.executable, "-m", "eqvoice_signalling", "--port", str(HUB_PORT), "--trace"] + (["--join-code", join_code] if join_code else [])
        p = subprocess.Popen(cmd, cwd=str(ROOT / "eqvoice-signalling"), stdout=log, stderr=subprocess.STDOUT)
    else:
        wrangler = CF / "node_modules" / "wrangler" / "bin" / "wrangler.js"
        if not wrangler.exists() or not (NODE / "node.exe").exists():
            raise SystemExit("Wrangler is not installed (run `python tools/realtest.py doctor`), or use --python-hub")
        cmd = [str(NODE / "node.exe"), str(wrangler), "dev", "--local", "--ip", "127.0.0.1", "--port", str(HUB_PORT), "--var", "TRACE:1"]
        if join_code:
            cmd += ["--var", "JOIN_CODE:" + join_code]
        p = subprocess.Popen(cmd, cwd=str(CF), env=node_env(), stdout=log, stderr=subprocess.STDOUT)
    end = time.time() + 90
    while time.time() < end:
        if hub_up():
            return p
        if p.poll() is not None:
            break
        time.sleep(0.5)
    raise SystemExit("the hub did not come up; read %s" % (run_dir / "hub.log"))


# ------------------------------------------------------------------ clients

def start_client(name: str, index: int, wow: Path, run_dir: Path, args, legacy: bool = False) -> subprocess.Popen:
    """One desktop client. ``legacy``: read the plain eqvoice.txt (no per-character file turned up).

    The window token is random per launch. A fixed one let a leftover test browser from an earlier run reconnect to
    the new client and become its voice engine (its fake microphone played a beep instead of a voice).
    """
    home = RUNS / ("home-" + name)
    token = secrets.token_hex(8)                  # letters and digits only: a leading '-' (urlsafe base64 has them) is read as an option
    cmd = [sys.executable, "-m", "eqvoice_client", "--home", str(home), "--port", str(17421 + index), "--token=" + token,
           "--signalling", "ws://127.0.0.1:%d/ws" % HUB_PORT, "--wow-dir", str(wow),
           "--ptt-code", PTT_CODES[index % len(PTT_CODES)], "--log-level", "INFO"]
    if not legacy:
        cmd += ["--character", name]
    if args.test_roster:
        cmd += ["--test-roster", args.test_roster]
    if getattr(args, "join_code", ""):
        cmd += ["--join-code", args.join_code]
    if args.fake_media:
        cmd += ["--fake-media"]
    if args.headless:
        cmd += ["--headless", "--fake-media", "--mute-audio"]
    log = open(run_dir / ("client-%s.out" % name), "w", encoding="utf-8")
    say("  starting the desktop client for %s: window at http://127.0.0.1:%d/?token=%s, push-to-talk key %s" % (
        name, 17421 + index, token, PTT_CODES[index % len(PTT_CODES)]))
    listed = run_dir / "clients.txt"
    if name not in (listed.read_text(encoding="utf-8").split() if listed.exists() else []):
        with open(listed, "a", encoding="utf-8") as f:
            f.write(name + "\n")
    return subprocess.Popen(cmd, cwd=str(ROOT / "eqvoice-client"), stdout=log, stderr=subprocess.STDOUT)


def friend_client(path, label: str = "friend") -> "pr.Client":
    """A friend's client from their SEND-LOGS zip, or a folder holding logs/eqvoice.log. ``label`` names it in the report."""
    p = Path(path)
    names = ("eqvoice.log", "octovoice.log")             # a friend still on the build from before the rename sends the old name
    if p.is_dir():
        logs = p / "logs"
        found = next((logs / n for n in names if (logs / n).exists()), None) if logs.exists() else None
        text = pr.read_log(found) if found else pr.read_log(p)
    else:
        with zipfile.ZipFile(p) as z:
            parts = []
            for n in sorted(x for x in z.namelist() if any(x.startswith("logs/" + base) for base in names)):
                parts.append(z.read(n).decode("utf-8", "replace"))
            text = "\n".join(reversed(parts))            # eqvoice.log.3 ... eqvoice.log: oldest first
    return pr.Client(label, pr.parse(text))


def webrtc_report(homes: dict, extra: list = ()) -> str:
    clients = [pr.Client(name, pr.parse(pr.read_log(home))) for name, home in homes.items()] + list(extra)
    return pr.webrtc_block(clients) if clients else ""


def current_report(homes: dict, hub_log: Path, test_names: list, extra: list = ()) -> list:
    clients = [pr.Client(name, pr.parse(pr.read_log(home))) for name, home in homes.items()] + list(extra)
    hub = [e for e in pr.parse(pr.read_log(hub_log), since_start=False) if e.who == "hub"] if hub_log.exists() else []
    return pr.evaluate(clients, hub, test_names) if clients else []


def engine_warnings(homes: dict) -> list:
    """[(character, problem, detail)] from every client's media_warning trace lines."""
    out = []
    for name, home in homes.items():
        for e in pr.parse(pr.read_log(home)):
            if e.who == "desktop" and e.event == "media_warning":
                out.append((name, e.get("problem", "?"), e.get("detail", "")))
    return out


class Feed:
    """Prints the connection and audio lines of my clients as they are written, for watching a two-PC test live."""
    EVENTS = ("webrtc_gathered", "webrtc_connected", "webrtc_path", "webrtc_result", "webrtc_failed", "webrtc_ice_error", "webrtc_stats",
              "webrtc_closed", "audio_rx", "audio_tx", "hub_link", "media_warning", "identity", "mic")

    def __init__(self):
        self.pos: dict = {}

    def new_lines(self, homes: dict) -> list:
        out = []
        for name, home in homes.items():
            f = Path(home) / "logs" / "eqvoice.log"
            try:
                size = f.stat().st_size
            except OSError:
                continue
            at = self.pos.get(name)
            if at is None or size < at:                 # first look (skip the past) or the log rotated
                self.pos[name] = size if at is None else 0
                at = self.pos[name]
            if size <= at:
                continue
            with open(f, "rb") as fh:
                fh.seek(at)
                chunk = fh.read(size - at)
            self.pos[name] = size
            for raw in chunk.decode("utf-8", "replace").splitlines():
                i = raw.find("TRACE desktop ")
                if i < 0:
                    continue
                body = raw[i + len("TRACE desktop "):]
                ev = body.split(" ", 1)[0]
                if ev in self.EVENTS or (ev == "peer" and any(w in body for w in ("CONNECTED", "CONNECTING", "DISCONNECTED", "GRACE"))):
                    out.append("  [%s] %s %s" % (name, raw[11:19] if len(raw) > 19 else "", body[:300]))
        return out


def run(args) -> int:
    wows = [Path(w) for w in args.wow_dir]
    bad = False
    for wow in wows:
        ok, msg = addon_status(wow)
        if not ok:
            ok, msg = repair_addon(wow, msg)
        say("addon in %s: %s" % (wow, msg))
        bad = bad or not ok
    if bad and not args.force:
        say("fix that first (or pass --force)")
        return 2
    busy = busy_ports()
    if busy:
        say("NOT STARTING: port(s) %s are in use, so an earlier run (or its clients) is still going. Two runs on top of each" % ", ".join(map(str, busy)))
        say("other crash-loop their clients and tear down each other's voice windows. Press Ctrl+C in the terminal running the old")
        say("one, or end it with:   python tools\\realtest.py stop")
        return 2
    RUNS.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS / time.strftime("run-%Y%m%d-%H%M%S")
    run_dir.mkdir()
    (RUNS / "latest.txt").write_text(str(run_dir), encoding="utf-8")
    procs, homes = [], {}
    test_names = [n for n in (args.test_roster or "").split(",") if n.strip()]
    stale = browser.kill_profile(RUNS)
    if stale:
        say("stopped %d leftover test browser process(es) from an earlier run (they would have hijacked the voice engine)" % stale)
    warned = set()
    only = {n.strip().lower() for n in (args.only or "").split(",") if n.strip()}
    feed = Feed() if args.tunnel else None
    try:
        say("starting the hub (wrangler dev, tracing on%s) ..." % (", join code on" if args.join_code else ""))
        hub = start_hub(run_dir, args.python_hub, args.join_code)
        if hub:
            procs.append(hub)
        say("hub is up at ws://127.0.0.1:%d/ws; its trace goes to %s" % (HUB_PORT, run_dir / "hub.log"))
        if args.tunnel:
            exe = tunnel.find_cloudflared()
            if exe is None:
                say("")
                say("NO TUNNEL: cloudflared was not found (looked at $CLOUDFLARED, %s, PATH)." % (tunnel.WORK / "cloudflared" / "cloudflared.exe"))
                say("Without it only this PC can reach the hub. See docs/07-internet-test.md for the options. Continuing locally.")
            else:
                tp, wss = tunnel.start_quick_tunnel(exe, HUB_PORT, run_dir / "tunnel.log")
                procs.append(tp)
                zpath = make_test_pack.build(wss, args.join_code, ROOT / "dist")
                (run_dir / "friend.txt").write_text("%s %s\n" % (wss, args.join_code), encoding="utf-8")
                say("")
                say("TUNNEL UP: the hub is reachable from the internet at %s" % wss)
                say("Friend's pack: %s   (send them this one file; the address and join code are inside)" % zpath)
                say("It stays valid only while this window is open.")
        say("")
        say("Now log in your character(s). I look for CustomData\\eqvoice-<Character>.txt (written by the addon every second) in: %s" % ", ".join(map(str, wows)))
        say("When one appears I start a desktop client for it. Ctrl+C to finish.")
        say("")
        started, waited, last_print, shown = {}, 0.0, "", {}
        gave_up = {}                                    # name -> when its client died; not restarted for a minute
        slots = {}                                      # name -> its fixed slot (UI port and push-to-talk key), kept across restarts
        t0 = time.time()
        while True:
            for wow in wows:
                for name in character_files(wow):
                    if only and name.lower() not in only:
                        continue
                    if name not in started and len(started) < len(PTT_CODES) and time.time() - gave_up.get(name, -1e9) > 60:
                        idx = slots.setdefault(name, len(slots))
                        started[name] = start_client(name, idx, wow, run_dir, args)
                        procs.append(started[name])
                        homes[name] = RUNS / ("home-" + name)
            if not started and time.time() - t0 > 25:
                for wow in wows:
                    plain = wow / "CustomData" / "eqvoice.txt"
                    if plain.exists() and time.time() - plain.stat().st_mtime <= 20:
                        say("  only the plain eqvoice.txt is being written (no per-character file): the game may not accept that file name.")
                        say("  starting one client on the plain files (fine for one game; two games in one folder need the per-character files)")
                        started["game"] = start_client("game", 0, wow, run_dir, args, legacy=True)
                        procs.append(started["game"])
                        homes["game"] = RUNS / "home-game"
                        break
            if not started and time.time() - t0 > 60 and int(time.time() - t0) % 30 == 0:
                say("  ... no fresh eqvoice file after %d s. Is the NEW addon loaded (/reload) and the character in the world?" % (time.time() - t0))
                time.sleep(1.1)
            time.sleep(2.0)
            steps = current_report(homes, run_dir / "hub.log", test_names)
            for s in steps:
                key = s.status
                if shown.get(s.n) != key and s.status in (pr.PASS, pr.FAIL):
                    shown[s.n] = key
                    say("  step %2d %-8s %s%s" % (s.n, s.status, s.title, ("  [" + s.evidence + "]") if s.evidence and s.status == pr.PASS else ""))
            for name, problem, detail in engine_warnings(homes):
                if (name, problem) not in warned:
                    warned.add((name, problem))
                    say("  !! %s: %s" % (name, detail))
            if feed is not None:
                for ln in feed.new_lines(homes):
                    say(ln)
            if steps:
                nxt = next((s for s in steps if s.status in (pr.NOT_YET, pr.FAIL)), None)
                msg = ("waiting at step %d: %s" % (nxt.n, nxt.title)) if nxt else "all boundaries passed"
                if msg != last_print:
                    last_print = msg
                    say("  >> " + msg)
            for name, p in list(started.items()):
                if p.poll() is not None:
                    say("  !! the client for %s exited (%s); I will not restart it for a minute. Its last words:" % (name, p.returncode))
                    try:
                        for ln in (run_dir / ("client-%s.out" % name)).read_text(encoding="utf-8", errors="replace").splitlines()[-4:]:
                            say("       " + ln[:200])
                    except OSError:
                        pass
                    gave_up[name] = time.time()
                    del started[name]
    except KeyboardInterrupt:
        pass
    finally:
        steps = current_report(homes, run_dir / "hub.log", test_names)
        report = pr.render(steps, "FINAL CHECKLIST") if steps else "no client was started"
        if steps:
            report += "\n\n" + webrtc_report(homes)
        for p in procs:
            kill_tree(p)
        browser.kill_profile(RUNS)                  # the media engines: a taskkill of the client does not always reach them
        (run_dir / "report.txt").write_text(report + "\n", encoding="utf-8")
        say("")
        say(report)
        say("")
        say("saved to %s" % (run_dir / "report.txt"))
    return 0


def report(run_dir_arg, friends=None) -> int:
    """``friends``: one SEND-LOGS zip or folder per friend (a test with three or more people has two or more of them)."""
    if isinstance(friends, (str, Path)):
        friends = [friends]
    friends = list(friends or [])
    run_dir = Path(run_dir_arg) if run_dir_arg else Path((RUNS / "latest.txt").read_text(encoding="utf-8").strip())
    listed = run_dir / "clients.txt"
    names = [n for n in listed.read_text(encoding="utf-8").split() if n] if listed.exists() else []
    homes = {n: RUNS / ("home-" + n) for n in names} or {p.name[5:]: p for p in RUNS.glob("home-*")}
    extra = [friend_client(f, "friend" if i == 0 else "friend%d" % (i + 1)) for i, f in enumerate(friends)]
    steps = current_report(homes, run_dir / "hub.log", [], extra)
    with_logs = "" if not friends else (" (with the friend's logs)" if len(friends) == 1 else " (with %d friends' logs)" % len(friends))
    say(pr.render(steps, "checklist for %s%s" % (run_dir, with_logs)) if steps else "no clients found")
    if steps:
        say("")
        say(webrtc_report(homes, extra))
    return 0 if steps and all(s.status in (pr.PASS, pr.NA) for s in steps) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["doctor", "run", "report", "hub", "stop"])
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--wow-dir", action="append", default=None,
                    help="the game folder (repeat it if your two games run from two folders); default %s" % DEFAULT_WOW)
    ap.add_argument("--test-roster", default=None, help="NAME,NAME also looked up by the game (one-player check of steps 7-10)")
    ap.add_argument("--fake-media", action="store_true", help="Chromium's fake microphone instead of yours")
    ap.add_argument("--python-hub", action="store_true", help="the Python hub instead of Wrangler")
    ap.add_argument("--headless", action="store_true", help="rehearsal: hidden media engines with a fake microphone (no windows, no sound)")
    ap.add_argument("--force", action="store_true", help="run even if the installed addon differs from the repository")
    ap.add_argument("--join-code", default="", help="require this code from every client (the hub and my clients use it)")
    ap.add_argument("--tunnel", action="store_true",
                    help="two-PC test: reach the hub from the internet through a Cloudflare quick tunnel (needs cloudflared; adds a "
                         "join code), build dist/EqVoice-test-pack.zip for the friend, print live connection lines")
    ap.add_argument("--only", default="", help="NAME,NAME: start clients only for these characters (the others stay offline)")
    ap.add_argument("--friend", action="append", default=None, help="report: a friend's SEND-LOGS zip or folder (give it once for each friend)")
    a = ap.parse_args(argv)
    if a.tunnel and not a.join_code:
        a.join_code = secrets.token_hex(4)
    a.wow_dir = a.wow_dir or [str(DEFAULT_WOW)]
    if hasattr(signal, "SIGBREAK"):                      # let a CTRL_BREAK (a parent stopping us) end the run cleanly too
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if a.command == "doctor":
        return max(doctor(Path(w)) for w in a.wow_dir)
    if a.command == "report":
        return report(a.run_dir, a.friend)
    if a.command == "stop":
        say("stopped %d process(es)" % stop_all())
        return 0
    if a.command == "hub":
        RUNS.mkdir(parents=True, exist_ok=True)
        d = RUNS / time.strftime("hub-%Y%m%d-%H%M%S")
        d.mkdir()
        p = start_hub(d, a.python_hub, a.join_code)
        say("hub running%s; log %s; Ctrl+C to stop" % (" (join code on)" if a.join_code else "", d / "hub.log"))
        try:
            while p is None or p.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if p:
                kill_tree(p)
        return 0
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
