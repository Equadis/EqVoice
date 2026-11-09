"""What a double-click does, and the two app behaviours it relies on: closing the window quits, and the window's support buttons."""
import asyncio
import json
import sys
import zipfile
from pathlib import Path

import pytest

from eqvoice_client import installer, launcher
from eqvoice_client.config import Config

from scene import FakePage, Scene, run, until
from test_installer import make_addon, make_game, put_list


RealSingleInstance = launcher.SingleInstance


class NotRunning:
    already = False
    slot = 1

    def release(self):
        pass


class SecondCopy(NotRunning):
    slot = 2


class AlreadyRunning:
    already = True
    slot = 0

    def release(self):
        pass


@pytest.fixture(autouse=True)
def no_real_mutex(monkeypatch):
    """Every test gets a pretend "nobody else is running" unless it says otherwise: a real mutex would leak between tests."""
    monkeypatch.setattr(launcher, "SingleInstance", lambda: NotRunning())


class Dialogs:
    """Stands in for the native message boxes and records what the player would have read."""

    def __init__(self, monkeypatch, yes=False):
        self.messages, self.questions, self.yes = [], [], yes
        monkeypatch.setattr(installer, "message", lambda title, text, kind="info": self.messages.append((kind, text)))
        monkeypatch.setattr(installer, "ask_yes_no", lambda title, text: self.questions.append(text) or self.yes)

    def kinds(self):
        return [k for k, _ in self.messages]


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """A fake PC: a game folder, an addon to copy, a hub.txt, and no real dialogs."""
    wow = make_game(tmp_path / "Game")
    (wow / "ClassicAPI.dll").write_bytes(b"MZ")
    (wow / "nampower.dll").write_bytes(b"MZ")
    src = make_addon(tmp_path / "src")
    monkeypatch.setenv("EQVOICE_ADDON_SRC", str(src))
    prog = tmp_path / "prog"
    prog.mkdir()
    (prog / "hub.txt").write_text("wss://voice.example/ws code123", encoding="utf-8")
    monkeypatch.setattr(installer, "app_dir", lambda: prog)
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [wow])
    return {"wow": wow, "prog": prog, "src": src}


# ------------------------------------------------------------ command-line runs

def test_command_line_runs_get_the_server_address_from_hub_txt(setup_env):
    assert launcher.with_hub(["--no-browser"]) == ["--no-browser", "--signalling", "wss://voice.example/ws", "--join-code", "code123"]
    assert launcher.with_hub(["--signalling", "ws://x/ws"]) == ["--signalling", "ws://x/ws"], "a named server is never overridden"
    assert launcher.with_hub(["--signalling=ws://x/ws"]) == ["--signalling=ws://x/ws"]
    got = launcher.with_hub(["--join-code", "mine"])
    assert got.count("--join-code") == 1 and got[got.index("--join-code") + 1] == "mine"


def test_without_hub_txt_arguments_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "app_dir", lambda: tmp_path)
    assert launcher.with_hub(["--netcheck"]) == ["--netcheck"]


def test_a_windowed_exe_gets_real_output_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launcher.fix_std_streams(tmp_path)
    print("this would have crashed a windowed exe", flush=True)
    sys.stderr.write("so would this\n")
    assert sys.stdout is not None and sys.stderr is not None


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows mutex")
def test_each_copy_takes_the_next_slot_until_there_are_none():
    name = r"Local\EqVoice-test-%d" % id(object())
    copies = [RealSingleInstance(name) for _ in range(RealSingleInstance.MAX)]
    assert [c.slot for c in copies] == [1, 2, 3, 4] and not any(c.already for c in copies), "a second game window gets a second copy"
    full = RealSingleInstance(name)
    assert full.already is True and full.slot == 0, "the fifth finds no room"
    copies[1].release()
    again = RealSingleInstance(name)
    assert again.slot == 2 and not again.already, "a slot given back is the next one handed out"
    for c in copies + [again, full]:
        c.release()
    last = RealSingleInstance(name)
    assert last.slot == 1
    last.release()


# ----------------------------------------------------------------- game + addon

def test_setup_installs_the_addon_and_remembers_the_game(tmp_path, monkeypatch, setup_env):
    d = Dialogs(monkeypatch)
    cfg = Config.load(tmp_path / "home")
    wow = launcher.ensure_game(cfg)
    assert wow == setup_env["wow"] and cfg.bridge.wow_dir == str(wow)
    assert (wow / "Interface" / "AddOns" / "EqVoice" / "Core.lua").exists()
    assert d.messages == [] and d.questions == [], "a normal first run asks nothing"


def test_a_missing_classicapi_is_explained_but_setup_continues(tmp_path, monkeypatch, setup_env):
    (setup_env["wow"] / "ClassicAPI.dll").unlink()
    d = Dialogs(monkeypatch)
    cfg = Config.load(tmp_path / "home")
    assert launcher.ensure_game(cfg) == setup_env["wow"]
    assert d.kinds() == ["warn"] and "ClassicAPI.dll" in d.messages[0][1] and "OctoWoW" in d.messages[0][1]


def test_setup_switches_the_addon_on_for_every_character_when_the_game_is_closed(tmp_path, monkeypatch, setup_env):
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    wow = setup_env["wow"]
    nue = put_list(wow, "N'Zoth", "Nue", b"EqVoice: disabled\r\n")
    d = Dialogs(monkeypatch)
    cfg = Config.load(tmp_path / "home")
    cfg.bridge.wow_dir = str(wow)
    assert launcher.ensure_game(cfg) == wow
    assert nue.read_bytes() == b"EqVoice: enabled\r\n"
    assert d.messages == [], "done silently: that is the point of one click"


def test_with_the_game_open_the_player_is_told_exactly_what_to_tick(tmp_path, monkeypatch, setup_env):
    wow = setup_env["wow"]                                    # setup_env pretends the game is running
    nue = put_list(wow, "N'Zoth", "Nue", b"EqVoice: disabled\r\n")
    d = Dialogs(monkeypatch)
    assert launcher.ensure_game(Config.load(tmp_path / "home")) == wow, "setup carries on"
    assert nue.read_bytes() == b"EqVoice: disabled\r\n", "the game would undo a change made under it"
    assert d.kinds() == ["warn"]
    text = d.messages[0][1]
    for must in ("Nue (N'Zoth)", "AddOns", "tick EqVoice", "Log Out", "/eqvc"):
        assert must in text, must


def test_a_long_list_of_characters_is_shortened_in_the_message():
    names = ["Char%d (Realm)" % i for i in range(9)]
    text = launcher.switched_off_text(names)
    assert "Char0 (Realm)" in text and "Char5 (Realm)" in text and "Char6 (Realm)" not in text and "and 3 more" in text


def test_the_window_names_the_characters_the_addon_is_off_for(tmp_path):
    from types import SimpleNamespace
    from eqvoice_client.app import App
    wow = make_game(tmp_path / "Game")
    put_list(wow, "N'Zoth", "Nue", b"EqVoice: disabled\r\n")
    t = [0.0]
    me = SimpleNamespace(clock=lambda: t[0], cfg=SimpleNamespace(bridge=SimpleNamespace(wow_dir=str(wow))), _off_checked=-100.0, _off_for=[])
    text = App._waiting_for_addon(me)
    assert "switched off in the game for Nue (N'Zoth)" in text and "AddOns" in text
    put_list(wow, "N'Zoth", "Nue", b"EqVoice: enabled\r\n")
    t[0] = 2.0
    assert "switched off" in App._waiting_for_addon(me), "re-read at most every 5 s"
    t[0] = 6.0
    assert App._waiting_for_addon(me).startswith("Waiting for the EqVoice addon"), "fixed: back to the ordinary hint"
    me.cfg.bridge.wow_dir = ""
    me._off_checked, me._off_for = -100.0, []
    assert App._waiting_for_addon(me).startswith("Waiting for the EqVoice addon"), "no game folder: nothing to read, no crash"


def test_a_game_that_cannot_be_found_or_chosen_ends_setup_politely(tmp_path, monkeypatch, setup_env):
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    monkeypatch.setattr(installer, "registry_wow_dirs", lambda: [])
    monkeypatch.setattr(installer, "common_wow_dirs", lambda: [])
    monkeypatch.setattr(launcher, "_ask_for_game", lambda: None)
    assert launcher.ensure_game(Config.load(tmp_path / "home")) is None


def test_a_protected_game_folder_offers_administrator_rights(tmp_path, monkeypatch, setup_env):
    def denied(wow, src=None):
        raise installer.InstallError("Windows would not let EqVoice write", permission=True)
    monkeypatch.setattr(installer, "install_addon", denied)
    monkeypatch.setattr(installer, "is_admin", lambda: False)
    relaunched = []
    monkeypatch.setattr(installer, "relaunch_as_admin", lambda args=None: relaunched.append(1) or True)
    d = Dialogs(monkeypatch, yes=True)
    with pytest.raises(SystemExit):
        launcher.ensure_game(Config.load(tmp_path / "home"))
    assert relaunched == [1] and "administrator" in d.questions[0], "yes: this copy hands over to an elevated one and exits"
    d2 = Dialogs(monkeypatch, yes=False)
    assert launcher.ensure_game(Config.load(tmp_path / "home2")) is None and d2.messages == [], "no: setup stops quietly"


def test_any_other_install_failure_is_shown_in_words(tmp_path, monkeypatch, setup_env):
    monkeypatch.setattr(installer, "install_addon", lambda wow, src=None: (_ for _ in ()).throw(installer.InstallError("The disk is full.")))
    d = Dialogs(monkeypatch)
    assert launcher.ensure_game(Config.load(tmp_path / "home")) is None
    assert d.messages == [("error", "The disk is full.")]


# ------------------------------------------------------------ the whole double-click

@pytest.fixture
def double_click_env(tmp_path, monkeypatch, setup_env):
    seen = {}

    async def fake_run_app(cfg, welcome):
        seen["cfg"], seen["welcome"] = cfg, welcome
        return 0

    monkeypatch.setattr(launcher, "run_app", fake_run_app)
    d = Dialogs(monkeypatch)
    return seen, d, tmp_path / "home"


def test_a_first_double_click_sets_everything_up_and_runs(double_click_env, setup_env):
    seen, d, home = double_click_env
    assert launcher.double_click(home) == 0
    cfg = seen["cfg"]
    assert cfg.signalling_url == "wss://voice.example/ws" and cfg.join_code == "code123", "the address from hub.txt"
    assert cfg.bridge.wow_dir == str(setup_env["wow"]) and cfg.ui.port == 0
    assert seen["welcome"] is True, "the first run explains what to do in the game"
    assert cfg.audio.mode == "open", "open mic out of the box"
    saved = json.loads((home / "config.json").read_text())
    assert saved["bridge"]["wow_dir"] == str(setup_env["wow"]) and saved["ux"]["welcomed"] is True
    assert (setup_env["wow"] / "Interface" / "AddOns" / "EqVoice" / "Core.lua").exists()


def test_the_second_double_click_is_quiet(double_click_env):
    seen, d, home = double_click_env
    launcher.double_click(home)
    launcher.double_click(home)
    assert seen["welcome"] is False and d.messages == []


def test_a_copy_without_a_server_address_says_so(tmp_path, monkeypatch, setup_env):
    (setup_env["prog"] / "hub.txt").unlink()
    d = Dialogs(monkeypatch)
    ran = []
    monkeypatch.setattr(launcher, "run_app", lambda cfg, w: ran.append(1))
    assert launcher.double_click(tmp_path / "home") == 1
    assert d.kinds() == ["error"] and "voice server" in d.messages[0][1] and ran == []


def test_when_the_game_cannot_be_found_nothing_runs(tmp_path, monkeypatch, setup_env):
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    for name in ("registry_wow_dirs", "common_wow_dirs"):
        monkeypatch.setattr(installer, name, lambda: [])
    monkeypatch.setattr(launcher, "_ask_for_game", lambda: None)
    d = Dialogs(monkeypatch)
    ran = []
    monkeypatch.setattr(launcher, "run_app", lambda cfg, w: ran.append(1))
    assert launcher.double_click(tmp_path / "home") == 1 and ran == []
    assert d.kinds() == ["warn"] and "game folder" in d.messages[0][1]


def test_a_copy_already_running_is_not_started_twice(tmp_path, monkeypatch, setup_env):
    monkeypatch.setattr(launcher, "SingleInstance", lambda: AlreadyRunning())
    d = Dialogs(monkeypatch)
    ran = []
    monkeypatch.setattr(launcher, "run_app", lambda cfg, w: ran.append(1))
    assert launcher.double_click(tmp_path / "home") == 0 and ran == []
    assert "already running 4 times" in d.messages[0][1], "every slot is taken"


def test_a_crash_is_written_down_and_shown_in_words(tmp_path, monkeypatch, setup_env):
    d = Dialogs(monkeypatch)
    monkeypatch.setenv("EQVOICE_HOME", str(tmp_path / "crash-home"))             # a test must never write into the player's real data folder
    monkeypatch.setattr(launcher, "double_click", lambda home=None: 1 / 0)
    assert launcher.main([]) == 2
    assert "ZeroDivisionError" in (tmp_path / "crash-home" / "logs" / "crash.txt").read_text(encoding="utf-8")
    assert d.kinds() and d.kinds()[-1] == "error" and "ZeroDivisionError" in d.messages[-1][1]


def test_the_welcome_text_names_the_in_game_commands():
    assert "/eqvc" in launcher.WELCOME and "right-click" in launcher.WELCOME and "EqVoice window" in launcher.WELCOME


# ------------------------------------------------- closing the window quits (the exe)

def test_closing_the_window_quits_after_a_short_grace(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            a.quit_on_window_close, a.window_grace_s = True, 0.5
            pa = FakePage(a)
            await pa.connect(sc.session)
            await until(lambda: a.pages, 3, "window connected")
            await pa.close()
            await asyncio.wait_for(a.wait_closed(), 5)          # the window is gone and stays gone: quit
    run(go(), 60)


def test_a_window_that_comes_straight_back_does_not_quit(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            a.quit_on_window_close, a.window_grace_s = True, 0.8
            p1 = FakePage(a)
            await p1.connect(sc.session)
            await until(lambda: a.pages, 3, "window connected")
            await p1.close()
            p2 = FakePage(a)                                     # a reload
            await p2.connect(sc.session)
            await asyncio.sleep(1.4)
            assert not a._quit.is_set(), "a reload is not a quit"
            await p2.close()
            await asyncio.wait_for(a.wait_closed(), 5)
    run(go(), 60)


def test_without_the_flag_closing_the_window_changes_nothing(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            a.window_grace_s = 0.3
            pa = FakePage(a)
            await pa.connect(sc.session)
            await until(lambda: a.pages, 3, "window connected")
            await pa.close()
            await asyncio.sleep(0.8)
            assert not a._quit.is_set()
    run(go(), 60)


# ------------------------------------------------------- the window's support buttons

def test_save_report_button_makes_a_zip_and_says_where(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    (tmp_path / "user" / "Desktop").mkdir(parents=True)

    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            pa = FakePage(a)
            await pa.connect(sc.session)
            await pa.ws.send_json({"t": "ui", "cmd": "save_report"})
            await until(lambda: pa.reports, 10, "the answer")
            text = pa.reports[-1]["text"]
            assert text.startswith("Saved: ") and "Send that one file" in text
            zips = list((tmp_path / "user" / "Desktop").glob("EqVoice-logs-*.zip"))
            assert len(zips) == 1
            with zipfile.ZipFile(zips[0]) as z:
                assert "system.txt" in z.namelist() and "config.json" in z.namelist()
            await pa.close()
    run(go(), 60)


def test_check_my_connection_button_answers_in_the_window(tmp_path, monkeypatch):
    from eqvoice_client import netcheck

    def fake_run(url, code, show_ips, report):
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text("hub OK\nSTUN OK\nready for a voice test\n", encoding="utf-8")
        return 0
    monkeypatch.setattr(netcheck, "run", fake_run)

    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            pa = FakePage(a)
            await pa.connect(sc.session)
            await pa.ws.send_json({"t": "ui", "cmd": "netcheck"})
            await until(lambda: len(pa.reports) >= 2, 10, "a progress line and then the result")
            assert "Checking" in pa.reports[0]["text"]
            assert "ready for a voice test" in pa.reports[-1]["text"]
            await pa.close()
    run(go(), 60)


# ---------------------------------------------------- one copy per game window

def test_a_second_double_click_takes_the_next_slot_and_its_own_data_folder(tmp_path, monkeypatch, setup_env):
    assert launcher.slot_home(tmp_path / "base", 1) == tmp_path / "base"
    assert launcher.slot_home(tmp_path / "base", 3) == tmp_path / "base" / "copy-3"
    from eqvoice_client.bridge.claims import Claims
    monkeypatch.setattr("eqvoice_client.bridge.claims.CLAIMS", Claims("Local\\EqVoice-test-%d-" % id(object())))
    monkeypatch.setattr("eqvoice_client.bridge.filesource.CLAIMS", __import__("eqvoice_client.bridge.claims", fromlist=["CLAIMS"]).CLAIMS)
    monkeypatch.setattr(launcher, "SingleInstance", lambda: SecondCopy())
    d = Dialogs(monkeypatch, yes=True)
    ran = []

    async def fake_run_app(cfg, welcome):
        ran.append((cfg.home, welcome, cfg.bridge.claim))
        return 0
    monkeypatch.setattr(launcher, "run_app", fake_run_app)
    wow = setup_env["wow"]
    data = wow / "CustomData"
    data.mkdir()
    (data / "eqvoice-Nue.txt").write_text("OVB2 00\n")                       # a game is logged in and nobody serves it: no question
    base = tmp_path / "home"
    (base).mkdir()
    (base / "config.json").write_text('{"bridge": {"wow_dir": "%s"}}' % str(wow).replace("\\", "/"))      # the first copy already found the game
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    monkeypatch.setattr(installer, "registry_wow_dirs", lambda: [])
    monkeypatch.setattr(installer, "common_wow_dirs", lambda: [])
    assert launcher.double_click(base) == 0
    assert d.questions == [] and d.messages == [], "no search, no folder chooser, no question"
    assert ran and ran[0][0] == base / "copy-2" and ran[0][1] is False and ran[0][2] is True, ran
    assert (base / "copy-2" / "config.json").exists() and "copy-2" not in (base / "config.json").read_text(), "its settings are its own: the first copy's file is not touched"


def test_a_second_double_click_with_every_game_served_asks_first(tmp_path, monkeypatch, setup_env):
    from eqvoice_client.bridge import claims as claimsmod
    from eqvoice_client.bridge.claims import Claims
    mine = Claims("Local\\EqVoice-test-%d-" % id(object()))
    monkeypatch.setattr(claimsmod, "CLAIMS", mine)
    monkeypatch.setattr("eqvoice_client.bridge.filesource.CLAIMS", mine)
    other = Claims(mine.prefix)                       # another copy of EqVoice, serving Nue
    assert other.try_claim("Nue")
    monkeypatch.setattr(launcher, "SingleInstance", lambda: SecondCopy())
    wow = setup_env["wow"]
    (wow / "CustomData").mkdir()
    (wow / "CustomData" / "eqvoice-Nue.txt").write_text("OVB2 00\n")
    ran = []

    async def fake_run_app(cfg, welcome):
        ran.append(1)
        return 0
    monkeypatch.setattr(launcher, "run_app", fake_run_app)
    d = Dialogs(monkeypatch, yes=False)
    assert launcher.double_click(tmp_path / "home") == 0 and ran == []
    assert len(d.questions) == 1 and "already running" in d.questions[0] and "Nue" in d.questions[0], "it says what is being served and asks"
    d2 = Dialogs(monkeypatch, yes=True)
    assert launcher.double_click(tmp_path / "home2") == 0 and ran == [1] and len(d2.questions) == 1, "and goes ahead if the player says yes"
    other.release_all()


# ------------------------------------------------ a test setup hosts its own voice server

def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_local_address_nobody_answers_gets_a_hub_from_the_first_copy_and_a_second_copy_just_connects(tmp_path):
    import aiohttp
    port = _free_port()
    url = "ws://127.0.0.1:%d/ws" % port

    async def health():
        async with aiohttp.ClientSession() as s:
            async with s.get("http://127.0.0.1:%d/health" % port) as r:
                return r.status, (await r.text()).strip()

    async def go():
        a, b = launcher.LocalHub(url, "code1"), launcher.LocalHub(url, "code1")
        assert a.enabled and b.enabled
        assert await a.ensure() is True, "nobody is listening: this copy hosts"
        assert await health() == (200, "ok")
        assert await b.ensure() is False, "the second copy finds it already there and does not start another"
        await a.stop()
        for _ in range(20):                                       # the hosting copy was closed: the other takes over
            if await b.ensure():
                break
            await asyncio.sleep(0.1)
        assert b.runner is not None and await health() == (200, "ok"), "another copy hosts it now"
        await b.stop()
    run(go(), 40)


def test_a_server_somewhere_else_is_never_hosted_from_here():
    for url in ("wss://voice.example.workers.dev/ws", "ws://192.168.1.20:8787/ws", "ws://10.0.0.5:8787/ws"):
        hub = launcher.LocalHub(url)
        assert hub.enabled is False
        assert asyncio.run(hub.ensure()) is False and hub.runner is None, url
