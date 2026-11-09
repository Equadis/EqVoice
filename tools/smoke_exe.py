"""Start a built EqVoice.exe the way a test would and check it is whole: it must serve its window and every file the window needs,
carry the addon, and shut down cleanly. No game, no browser, no network.

    python tools/smoke_exe.py [dist/EqVoice.exe]

The packaged program is windowed, so there is no console: what it prints is read from <home>\\logs\\console.txt.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(url: str, timeout: float = 5.0) -> tuple:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:                      # a refusal is an answer, not a failure to connect
        return e.code, e.read()
    except Exception as e:                                                                     # noqa: BLE001
        return 0, str(e).encode()


def stop(proc: subprocess.Popen) -> None:
    """A one-file exe is a launcher plus the real program; the whole tree has to go."""
    if proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            pass


def smoke(exe: Path, timeout: float = 60.0) -> list:
    """A list of what is wrong (empty = fine)."""
    exe = Path(exe)
    problems: list = []
    if not exe.exists():
        return ["%s does not exist" % exe]
    if exe.stat().st_size < 5_000_000:
        problems.append("the exe is only %.1f MB: Python or aiohttp is probably missing from it" % (exe.stat().st_size / 1e6))
    home = Path(tempfile.mkdtemp(prefix="eqvoice-smoke-"))
    token = "smoketoken0123456789"
    out_path = home / "out.txt"
    sink = open(out_path, "w", encoding="utf-8")
    proc = subprocess.Popen([str(exe), "--home", str(home), "--source", "sim", "--sim-character", "SmokeTest", "--no-browser",
                             "--port", "0", "--token", token, "--signalling", "ws://127.0.0.1:9/ws"],
                            stdout=sink, stderr=subprocess.STDOUT, cwd=str(home))
    try:
        console = home / "logs" / "console.txt"
        url = None
        end = time.time() + timeout
        while time.time() < end and proc.poll() is None:
            for f in (out_path, console):
                if f.exists():
                    m = re.search(r"EqVoice UI: (http://127\.0\.0\.1:\d+)/", f.read_text(encoding="utf-8", errors="replace"))
                    if m:
                        url = m.group(1)
                        break
            if url:
                break
            time.sleep(0.3)
        if url is None:
            crash = home / "logs" / "crash.txt"
            return problems + ["the exe never reported its window address (exit code %s)%s" % (
                proc.poll(), (": " + crash.read_text(encoding="utf-8", errors="replace")[-400:]) if crash.exists() else "")]
        status, body = get(url + "/api/state?token=" + token)
        if status != 200:
            problems.append("/api/state answered %s" % status)
        else:
            state = json.loads(body)
            if "version" not in state or state.get("t") != "state":
                problems.append("/api/state is not the state document: %r" % list(state)[:5])
        for path in ("/", "/static/app.js", "/static/style.css", "/static/voicemodel.js", "/static/gate-worklet.js"):
            s, b = get(url + path + ("?token=" + token if path == "/" else ""))
            if s != 200 or len(b) < 200:
                problems.append("%s answered %s with %d bytes: the window's files are not inside the exe" % (path, s, len(b)))
        s, b = get(url + "/api/state")
        if s != 403:
            problems.append("/api/state without the token answered %s (should be 403)" % s)
    finally:
        stop(proc)
    return problems


def smoke_double_click(exe: Path, timeout: float = 90.0) -> list:
    """The exe with no arguments, as a double-click runs it, on a pretend PC: a fake game folder (saved in the settings, so no
    dialog can appear), hub.txt beside the exe, no browser. It must find the game, install the addon, read hub.txt and serve."""
    import os
    import shutil
    problems: list = []
    root = Path(tempfile.mkdtemp(prefix="eqvoice-dc-"))
    prog, home, game = root / "prog", root / "home", root / "Game"
    for d in (prog, home / "logs", game):
        d.mkdir(parents=True)
    (game / "WoW.exe").write_bytes(b"MZ")
    (game / "ClassicAPI.dll").write_bytes(b"MZ")
    shutil.copy2(exe, prog / Path(exe).name)
    nue = game / "WTF" / "Account" / "ACCT" / "N'Zoth" / "Nue" / "AddOns.txt"        # a character made before EqVoice: it lists it as disabled
    nue.parent.mkdir(parents=True)
    nue.write_bytes(b"BigWigs: enabled\r\nEqVoice: disabled\r\n")
    (prog / "hub.txt").write_text("ws://127.0.0.1:9/ws smokecode\n", encoding="utf-8")
    token = "dcsmoketoken0123456789"
    (home / "config.json").write_text(json.dumps({"bridge": {"wow_dir": str(game)}, "ux": {"welcomed": True}, "ui": {"token": token}}),
                                      encoding="utf-8")
    env = dict(os.environ, EQVOICE_HOME=str(home), EQVOICE_NO_BROWSER="1", EQVOICE_MUTEX=r"Local\EqVoice-smoke-%d" % os.getpid())   # (the player's own copy must not block this)
    proc = subprocess.Popen([str(prog / Path(exe).name)], env=env, cwd=str(prog), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        log = home / "logs" / "eqvoice.log"
        url = None
        end = time.time() + timeout
        while time.time() < end and proc.poll() is None and url is None:
            if log.exists():
                m = re.search(r"EqVoice window: (http://127\.0\.0\.1:\d+)/", log.read_text(encoding="utf-8", errors="replace"))
                url = m.group(1) if m else None
            time.sleep(0.3)
        if url is None:
            crash = home / "logs" / "crash.txt"
            return ["a double-click never got to its window (exit code %s)%s" % (
                proc.poll(), (": " + crash.read_text(encoding="utf-8", errors="replace")[-500:]) if crash.exists() else "")]
        addon = game / "Interface" / "AddOns" / "EqVoice"
        src = ROOT / "eqvoice-addon" / "EqVoice"
        missing = [f.name for f in src.iterdir() if not (addon / f.name).exists()]
        if missing:
            problems.append("the addon was not fully installed into the game folder: missing %s" % missing)
        elif any((addon / f.name).read_bytes() != f.read_bytes() for f in src.iterdir()):
            problems.append("the installed addon differs from the one in the repository (the exe carries a stale copy: rebuild it)")
        if nue.read_bytes() != b"BigWigs: enabled\r\nEqVoice: enabled\r\n":
            problems.append("the addon was not switched on for the existing character (AddOns.txt is %r)" % nue.read_bytes())
        status, body = get(url + "/api/state?token=" + token)
        if status != 200:
            problems.append("/api/state answered %s" % status)
        elif "ws://127.0.0.1:9/ws" not in body.decode("utf-8", "replace"):
            problems.append("the voice server address from hub.txt was not used")
        saved = json.loads((home / "config.json").read_text(encoding="utf-8"))
        if saved.get("bridge", {}).get("wow_dir") != str(game):
            problems.append("the game folder was not remembered")
        if saved.get("join_code") != "smokecode":
            problems.append("the join code from hub.txt was not applied")
    finally:
        stop(proc)
        shutil.rmtree(root, ignore_errors=True)
    return problems


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_two_copies(exe: Path, timeout: float = 90.0) -> list:
    """Two game windows on one PC: two double-clicks must give two copies, each serving a different character, each in its own data folder,
    and with a test setup (hub.txt on this PC, nobody running a server) the first copy hosts the voice server and the second does not."""
    import os
    import shutil
    root = Path(tempfile.mkdtemp(prefix="eqvoice-two-"))
    prog, home, game = root / "prog", root / "home", root / "Game"
    for d in (prog, home / "logs", game / "CustomData"):
        d.mkdir(parents=True)
    (game / "WoW.exe").write_bytes(b"MZ")
    (game / "ClassicAPI.dll").write_bytes(b"MZ")
    shutil.copy2(exe, prog / Path(exe).name)
    hub_port = _free_port()
    (prog / "hub.txt").write_text("ws://127.0.0.1:%d/ws smokecode\n" % hub_port, encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"bridge": {"wow_dir": str(game)}, "ux": {"welcomed": True}}), encoding="utf-8")
    files = [game / "CustomData" / ("eqvoice-%s.txt" % n) for n in ("Alice", "Bobby")]
    env = dict(os.environ, EQVOICE_HOME=str(home), EQVOICE_NO_BROWSER="1", EQVOICE_MUTEX=r"Local\EqVoice-smoke-two-%d" % os.getpid())
    procs, problems = [], []
    try:
        for i in range(2):
            for f in files:                                           # two games writing their files
                f.write_bytes(b"OVB2 0200BE00262F4930\n")
            procs.append(subprocess.Popen([str(prog / Path(exe).name)], env=env, cwd=str(prog), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(4.0)
        logs = [home / "logs" / "eqvoice.log", home / "copy-2" / "logs" / "eqvoice.log"]
        served = [None, None]
        end = time.time() + timeout
        while time.time() < end and None in served:
            for f in files:
                f.write_bytes(b"OVB2 0200BE00262F4930\n")             # keep the games' files fresh
            for i, lg in enumerate(logs):
                if lg.exists() and served[i] is None:
                    m = re.search(r"this EqVoice serves (\w+)", lg.read_text(encoding="utf-8", errors="replace"))
                    served[i] = m.group(1) if m else None
            time.sleep(0.5)
        if None in served:
            problems.append("two double-clicks did not give two copies that each took a game (copy 1 serves %s, copy 2 serves %s; second copy folder exists: %s)" % (
                served[0], served[1], (home / "copy-2").exists()))
        elif served[0] == served[1]:
            problems.append("both copies took the same character (%s)" % served[0])
        elif sorted(served) != ["Alice", "Bobby"]:
            problems.append("the copies took %r, not Alice and Bobby" % served)
        status, body = get("http://127.0.0.1:%d/health" % hub_port)
        if status != 200 or body.strip() != b"ok":
            problems.append("nobody was running a voice server on this PC, and the first copy did not host one (health answered %s)" % status)
        hosting = ["hosting the voice server" in (lg.read_text(encoding="utf-8", errors="replace") if lg.exists() else "") for lg in logs]
        if hosting != [True, False]:
            problems.append("exactly the first copy should host the voice server (copy 1: %s, copy 2: %s)" % tuple(hosting))
    finally:
        for p in procs:
            stop(p)
        shutil.rmtree(root, ignore_errors=True)
    return problems


def _dialogs_of(exe_path: str) -> list:
    """(handle, text) of every message box owned by a process whose program is ``exe_path``."""
    import ctypes
    from ctypes import wintypes
    user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    found = []
    buf = ctypes.create_unicode_buffer(1024)

    def program_of(pid: int) -> str:
        h = k32.OpenProcess(0x1000, False, pid)                       # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            n = wintypes.DWORD(1024)
            ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
            return buf.value if ok else ""
        finally:
            k32.CloseHandle(h)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770" and user32.IsWindowVisible(hwnd):    # the class of every standard message box
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if program_of(pid.value).lower() == exe_path.lower():
                texts = []

                @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                def child(ch, _):
                    t = ctypes.create_unicode_buffer(2048)
                    user32.GetWindowTextW(ch, t, 2048)
                    if t.value:
                        texts.append(t.value)
                    return True
                user32.EnumChildWindows(hwnd, child, 0)
                found.append((hwnd, " | ".join(texts)))
        return True
    user32.EnumWindows(each, 0)
    return found


def smoke_asks_first(exe: Path, timeout: float = 60.0) -> list:
    """One game window, one copy already serving it: a second double-click must ASK before opening another copy (not guess), and answering
    "No" must leave the first copy's character alone and end the second copy."""
    import os
    import shutil
    if sys.platform != "win32":
        return []
    import ctypes
    root = Path(tempfile.mkdtemp(prefix="eqvoice-asks-"))
    prog, home, game = root / "prog", root / "home", root / "Game"
    for d in (prog, home / "logs", game / "CustomData"):
        d.mkdir(parents=True)
    (game / "WoW.exe").write_bytes(b"MZ")
    (game / "ClassicAPI.dll").write_bytes(b"MZ")
    shutil.copy2(exe, prog / Path(exe).name)
    program = str(prog / Path(exe).name)
    (prog / "hub.txt").write_text("ws://127.0.0.1:%d/ws code\n" % _free_port(), encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"bridge": {"wow_dir": str(game)}, "ux": {"welcomed": True}}), encoding="utf-8")
    one_game = game / "CustomData" / "eqvoice-Alice.txt"
    env = dict(os.environ, EQVOICE_HOME=str(home), EQVOICE_NO_BROWSER="1", EQVOICE_MUTEX=r"Local\EqVoice-asks-%d" % os.getpid())
    problems, procs = [], []
    try:
        one_game.write_bytes(b"OVB2 0200BE00262F4930\n")
        procs.append(subprocess.Popen([program], env=env, cwd=str(prog), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        log1 = home / "logs" / "eqvoice.log"
        end = time.time() + timeout
        while time.time() < end and "this EqVoice serves Alice" not in (log1.read_text(encoding="utf-8", errors="replace") if log1.exists() else ""):
            one_game.write_bytes(b"OVB2 0200BE00262F4930\n")
            time.sleep(0.4)
        if "this EqVoice serves Alice" not in (log1.read_text(encoding="utf-8", errors="replace") if log1.exists() else ""):
            return ["the first copy never took the only logged-in character"]
        second = subprocess.Popen([program], env=env, cwd=str(prog), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(second)
        box = None
        end = time.time() + 30
        while time.time() < end and box is None:
            one_game.write_bytes(b"OVB2 0200BE00262F4930\n")
            boxes = _dialogs_of(program)
            box = boxes[0] if boxes else None
            time.sleep(0.4)
        if box is None:
            problems.append("the second copy did not ask before opening another copy (no dialog appeared)")
        else:
            hwnd, text = box
            if "already running" not in text or "Alice" not in text or "another copy" not in text.lower():
                problems.append("the question does not say what is happening: %r" % text)
            ctypes.windll.user32.PostMessageW(hwnd, 0x0111, 7, 0)       # WM_COMMAND, IDNO: "No"
            try:
                second.wait(20)
            except subprocess.TimeoutExpired:
                problems.append("answering No did not end the second copy")
            if "hosting the voice server" in ((home / "copy-2" / "logs" / "eqvoice.log").read_text(encoding="utf-8", errors="replace")
                                              if (home / "copy-2" / "logs" / "eqvoice.log").exists() else ""):
                problems.append("the second copy started anyway after No")
        if "this EqVoice serves Alice" not in log1.read_text(encoding="utf-8", errors="replace"):
            problems.append("the first copy lost its character")
    finally:
        for p in procs:
            stop(p)
        shutil.rmtree(root, ignore_errors=True)
    return problems


class _Copy:
    """One running EqVoice.exe in the voice test: its data folder, how to ask it questions, and what its log says."""

    def __init__(self, exe: Path, env: dict, cwd: Path, home: Path, token: str):
        self.exe, self.env, self.cwd, self.home, self.token = exe, env, cwd, home, token
        self.proc = None
        self.t0 = 0.0
        self.url = None
        self.offset = 0                  # where in the log this run of the copy begins: a restarted copy appends to the same file

    def start(self) -> "_Copy":
        self.t0 = time.time()
        self.url = None
        self.offset = len(self.log())
        self.proc = subprocess.Popen([str(self.exe)], env=self.env, cwd=str(self.cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self

    def kill(self) -> None:
        if self.proc is not None:
            stop(self.proc)

    @property
    def log_path(self) -> Path:
        return self.home / "logs" / "eqvoice.log"

    def log(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def run_log(self) -> str:
        """What this run of the copy has logged (not the runs before it)."""
        return self.log()[self.offset:]

    def state(self) -> dict:
        if self.url is None:
            m = re.findall(r"EqVoice window: (http://127\.0\.0\.1:\d+)/", self.run_log())
            self.url = m[-1] if m else None
        if self.url is None:
            return {}
        status, body = get(self.url + "/api/state?token=" + self.token, timeout=2.0)
        try:
            return json.loads(body) if status == 200 else {}
        except ValueError:
            return {}

    def character(self) -> str:
        return ((self.state().get("identity") or {}).get("character")) or ""


def smoke_voice(exe: Path, say=print, timeout: float = 150.0) -> list:
    """The real exe, twice, against two pretend game windows, with real Edge engines and real WebRTC audio (a fake microphone, muted).

    Checks, in order: copy 1 hosts the voice server and copy 2 does not; each copy takes a different character; microphones ready; both
    register; the two connect and audio flows; then copy 1 is killed: copy 2 must take over hosting and register again; then a new copy
    starts, takes the character that is free, and the two connect again, with no registration storm and no rate limit on the way."""
    import os
    import shutil
    exe = Path(exe)
    problems: list = []
    root = Path(tempfile.mkdtemp(prefix="eqvoice-voice-"))
    prog, base, game = root / "prog", root / "home", root / "Game"
    for d in (prog, base / "logs", base / "copy-2" / "logs", game / "CustomData"):
        d.mkdir(parents=True)
    (game / "WoW.exe").write_bytes(b"MZ")
    (game / "ClassicAPI.dll").write_bytes(b"MZ")
    shutil.copy2(exe, prog / exe.name)
    hub_port = _free_port()
    (prog / "hub.txt").write_text("ws://127.0.0.1:%d/ws voicecode\n" % hub_port, encoding="utf-8")
    toks = ("voicetoken-one-0123456789", "voicetoken-two-0123456789")
    for h, tok in ((base, toks[0]), (base / "copy-2", toks[1])):
        (h / "config.json").write_text(json.dumps({"bridge": {"wow_dir": str(game)}, "ux": {"welcomed": True}, "ui": {"token": tok}}), encoding="utf-8")
    env = dict(os.environ, EQVOICE_HOME=str(base), EQVOICE_HEADLESS="1", EQVOICE_FAKE_MEDIA="1", EQVOICE_MUTE_AUDIO="1",
               EQVOICE_MUTEX=r"Local\EqVoice-voice-%d" % os.getpid())
    # two pretend game windows 6 yards apart, writing the same files the addon writes
    sys.path.insert(0, str(ROOT / "eqvoice-client"))
    from eqvoice_client.bridge.sim import SimWorld
    world_file = root / "world.json"
    world = SimWorld(str(world_file))
    world.add("Alice", 0.0, 0.0)
    world.add("Bobby", 6.0, 0.0)
    games = subprocess.Popen([sys.executable, str(ROOT / "tools" / "sim_games.py"), "--dir", str(game), "--world", str(world_file), "Alice", "Bobby"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    one = _Copy(exe=prog / exe.name, env=env, cwd=prog, home=base, token=toks[0])
    two = _Copy(exe=prog / exe.name, env=env, cwd=prog, home=base / "copy-2", token=toks[1])
    times: dict = {}

    def wait(what: str, pred, limit: float) -> bool:
        end = time.time() + limit
        while time.time() < end:
            try:
                if pred():
                    return True
            except Exception:                                                                           # noqa: BLE001
                pass
            time.sleep(0.25)
        problems.append("timed out (%.0f s) waiting for: %s" % (limit, what))
        return False

    def connected(a: "_Copy", b_name: str) -> bool:
        s = a.state()
        return any(p.get("name") == b_name and p.get("state") == "CONNECTED" and ((p.get("stats") or {}).get("energy") or 0) > 0 for p in s.get("peers", []))

    def health_ok() -> bool:
        status, body = get("http://127.0.0.1:%d/health" % hub_port, timeout=1.0)
        return status == 200 and body.strip() == b"ok"

    try:
        t_start = time.time()
        one.start()
        if wait("copy 1's window", lambda: one.url or one.state(), 40):
            times["copy 1: window up"] = time.time() - one.t0
        wait("the voice server (hosted by copy 1)", health_ok, 15)
        two.start()
        wait("copy 2's window", lambda: two.url or two.state(), 40) and times.update({"copy 2: window up": time.time() - two.t0})
        wait("both copies to know their character", lambda: one.character() and two.character(), 60)
        a, b = one.character(), two.character()
        times["both characters taken"] = time.time() - t_start
        if not a or not b:
            return problems
        if a == b:
            problems.append("both copies took the same character (%s)" % a)
        wait("both microphones ready", lambda: one.state().get("mic", {}).get("state") == "ok" and two.state().get("mic", {}).get("state") == "ok", 30)
        times["microphones ready"] = time.time() - t_start
        wait("both registered with the voice server", lambda: one.state().get("signalling", {}).get("state") == "registered"
             and two.state().get("signalling", {}).get("state") == "registered", 30)
        times["both registered"] = time.time() - t_start
        wait("the two to connect and audio to flow", lambda: connected(one, b) and connected(two, a), 60)
        times["connected with audio"] = time.time() - t_start
        if "hosting the voice server" not in one.log():
            problems.append("copy 1 did not host the voice server")
        if "hosting the voice server" in two.log():
            problems.append("copy 2 tried to host a second voice server while copy 1 was hosting one")

        # --- the copy that hosts the server is closed: the other must take over
        kill_t = time.time()
        one.kill()
        wait("copy 2 to take over hosting the voice server", lambda: "hosting the voice server" in two.log() and health_ok(), 20)
        times["failover: hub back (s after the host closed)"] = time.time() - kill_t
        wait("copy 2 to be registered again", lambda: two.state().get("signalling", {}).get("state") == "registered", 40)
        times["failover: copy 2 registered again"] = time.time() - kill_t

        # --- a new copy starts (the double-click again): it takes the free character and the two connect again
        rec_t = time.time()
        one.start()
        wait("the new copy to take the free character", lambda: one.character() == a, 60)
        wait("the two to connect again with audio", lambda: connected(one, b) and connected(two, a), 60)
        times["recovery: new copy connected again"] = time.time() - rec_t
        if one.character() != a:
            problems.append("the new copy did not take the character that was free (%r, wanted %r)" % (one.character(), a))

        # --- no registration storm, no rate limit, no errors that matter
        for name, c in (("copy 1", one), ("copy 2", two)):
            text = c.log()
            registered = len(re.findall(r"hub_registered", text))
            if "ip_rate" in text:
                problems.append("%s met the voice server's rate limit" % name)
            if registered > 3:
                problems.append("%s registered %d times: that is flapping" % (name, registered))
        status, body = get("http://127.0.0.1:%d/stats" % hub_port, timeout=2.0)
        if status == 200:
            try:
                hs = json.loads(body)
                say("  hub (hosted by copy 2 now): %s" % json.dumps({k: hs[k] for k in hs if k in ("connections", "sessions", "links", "reserved", "totals")})[:300])
            except ValueError:
                pass
        for k, v in times.items():
            say("  %-48s %6.1f s" % (k, v))
    finally:
        one.kill()
        two.kill()
        stop(games)
        shutil.rmtree(root, ignore_errors=True)
    return problems


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    exe = Path(args[0]) if args else ROOT / "dist" / "EqVoice.exe"
    problems = smoke(exe) + smoke_double_click(exe) + smoke_two_copies(exe) + smoke_asks_first(exe)
    if "--voice" in sys.argv:
        problems += smoke_voice(exe)
    for p in problems:
        print("FAIL:", p)
    print("exe smoke test: %s" % ("FAILED" if problems else "passed"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
