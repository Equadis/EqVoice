"""Running from the tray with no taskbar button. The voice engine lives in a window nobody sees (page role "engine"); the real window (role "ui")
is opened from the tray on request and may be closed without ending the call; the engine going away starts it again. The rules here are the app's:
who becomes the voice engine, what closing a window does, what the tray's Open does, and the live setup level reaching a window that is not
measuring. (The real windows, real hiding and the real exe are in test_tray.py and tools/smoke_tray.py.)"""
import asyncio
import time

import aiohttp

from eqvoice_client.config import Config

from scene import FakePage, Scene, run, until


class Launches:
    """Stands in for opening a browser window: records what was asked for."""

    def __init__(self, app):
        self.calls = []
        app.launch_browser = True
        app.url = "http://127.0.0.1:1/?token=t"

        async def fake(url, role=None, fresh=True):
            self.calls.append((role, fresh))
        app._launch_window = fake

    def roles(self):
        return [c[0] for c in self.calls]


async def add_pages(sc, app, *roles):
    pages = []
    for r in roles:
        p = FakePage(app, role=r)
        await p.connect(sc.session)
        pages.append(p)
        await asyncio.sleep(0.15)
    return pages


def test_a_window_that_is_only_a_window_never_becomes_the_voice_engine(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            ui, engine = await add_pages(sc, a, "ui", "engine")
            assert [p.media for p in a.pages] == [False, True], "the window opened first, and still the engine is the engine"
            a.media({"cmd": "tone"})
            await until(lambda: engine.named("tone"), 5, "commands go to the engine")
            assert ui.named("tone") == [] and not ui.cmds, "and never to the window"
            assert sorted(p["role"] for p in a.diag()["pages"]) == ["engine", "ui"], "the diagnostics say which page is which"
    run(go(), 60)


def test_a_page_with_no_role_is_both_as_it_always_was(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            (only,) = await add_pages(sc, a, "")
            assert a.pages[0].media is True, "one page that does everything"
            second, = await add_pages(sc, a, "")
            assert [p.media for p in a.pages] == [True, False], "a second one is a window"
    run(go(), 60)


def test_closing_the_window_never_ends_the_program_in_tray_only_mode(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only, a.quit_on_window_close, a.window_grace_s = True, True, 0.2
            launches = Launches(a)
            engine, ui = await add_pages(sc, a, "engine", "ui")
            await ui.ws.close()
            await asyncio.sleep(0.8)
            assert not a._quit.is_set(), "the window was closed: voice goes on"
            assert [p.media for p in a.pages] == [True], "the engine is still there"
            assert launches.calls == [], "and nothing was restarted"
            await engine.ws.close()                       # now the engine goes too: that is the one thing that is restarted, not a quit
            await until(lambda: launches.calls, 5, "the engine is started again")
            assert not a._quit.is_set()
            assert launches.calls == [("engine", True)], "out of sight (an engine), and clearing out whatever held the profile"
    run(go(), 60)


def test_without_tray_only_closing_the_last_window_still_quits_after_the_grace_period(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only, a.quit_on_window_close, a.window_grace_s = False, True, 0.3
            launches = Launches(a)
            (only,) = await add_pages(sc, a, "")
            await only.ws.close()
            await until(lambda: a._quit.is_set(), 5, "no tray: the window is the program")
            assert launches.calls == []
    run(go(), 60)


def test_a_reload_of_the_engine_page_is_not_a_restart(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only = True
            launches = Launches(a)
            (engine,) = await add_pages(sc, a, "engine")
            await engine.ws.close()
            await asyncio.sleep(0.3)                     # a page reload is back well inside a second
            (again,) = await add_pages(sc, a, "engine")
            await asyncio.sleep(1.5)
            assert launches.calls == [], "the engine came back by itself: nothing to start"
            assert a.pages[0].media is True
    run(go(), 60)


def test_an_engine_that_keeps_stopping_is_given_up_on_and_the_player_gets_a_window(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only = True
            launches = Launches(a)
            a._engine_starts = [a.clock()] * 4           # four starts in the last two minutes already
            (engine,) = await add_pages(sc, a, "engine")
            await engine.ws.close()
            await until(lambda: launches.calls, 5, "something is launched")
            assert launches.calls == [("ui", False)], "a window to show what is wrong, not a fifth engine: %r" % launches.calls
    run(go(), 60)


def test_the_trays_open_gives_one_window_even_when_asked_twice(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only = True
            launches = Launches(a)
            await a.reopen_window()
            await a.reopen_window()
            assert launches.calls == [("ui", False)], "one window, and the browser that holds the engine is left alone (no clearing out)"
            a._ui_launched = a.clock() - 5
            await a.reopen_window()
            assert launches.roles() == ["ui", "ui"], "a few seconds later it is a new request"
    run(go(), 60)


def test_without_tray_only_the_tray_reopens_the_one_page_as_before(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            launches = Launches(a)
            await a.reopen_window()
            assert launches.calls == [(None, True)]
    run(go(), 60)


def test_the_setups_live_level_reaches_a_window_that_is_not_measuring(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            engine, ui = await add_pages(sc, a, "engine", "ui")
            a.cal.update(phase="normal", left=5)
            await engine.ws.send_json({"t": "cal", "phase": "normal", "left": 4.2, "level": -41.5, "live": True})
            await until(lambda: ui.cal_progress, 5, "the window is told")
            assert ui.cal_progress[-1]["level"] == -41.5 and ui.cal_progress[-1]["phase"] == "normal" and ui.cal_progress[-1]["left"] == 4.2
            assert engine.cal_progress == [], "the page that measures already has it"
            assert a.cal["phase"] == "normal" and a.cal["left"] == 5, "a live reading is only a picture: the state is what the once-a-second reports say"
    run(go(), 60)


def test_the_pages_pulse_is_kept_so_a_slowed_down_hidden_page_can_be_seen(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            (engine,) = await add_pages(sc, a, "engine")
            await engine.ws.send_json({"t": "mic", "state": "ok", "label": "x", "level": 0, "tx": False, "pcs": 0, "hz": 50})
            await until(lambda: a.mic.get("hz") == 50, 5, "the pulse")
    run(go(), 60)


def test_tray_only_is_the_default_and_can_be_switched_off(tmp_path):
    from eqvoice_client import launcher
    cfg = Config.load(tmp_path / "fresh")
    assert cfg.ui.tray_only is True and launcher.cfg_wants_tray_only(cfg)
    off = Config.load(tmp_path / "off", {"ui": {"tray_only": False}})
    assert not launcher.cfg_wants_tray_only(off)


def test_stopping_the_app_does_not_wait_for_a_window_that_is_still_open(tmp_path):
    """Quit from the tray with a window open used to wait out the server's minute for its connection to end (the browser was only stopped after
    that). In tray-only mode the voice engine's window is always open, so this is every Quit."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            engine, ui = await add_pages(sc, a, "engine", "ui")
            sc.apps.pop("Alice")                          # stopped here, by the test, with both windows still connected
            began = time.monotonic()
            await asyncio.wait_for(a.stop(), 25)
            took = time.monotonic() - began
            assert took < 10, "stopping took %.1f s with two windows open" % took
            for p in (engine, ui):
                await p.close()
    run(go(), 90)


class FakeProc:
    """Stands in for the browser's process: alive until told to end."""

    def __init__(self):
        self.ended = False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True


def test_the_engine_is_started_when_a_game_reports_a_character_and_stopped_when_there_has_been_no_game_for_a_while(tmp_path, monkeypatch):
    killed = []
    from eqvoice_client import app as app_module
    monkeypatch.setattr(app_module.browser, "kill_profile", lambda profile: killed.append(str(profile)) or 0)

    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            game = sc.games["Alice"]
            game.logout()                                        # start with nobody playing
            await until(lambda: a.ident is None, 10, "no character")
            a.tray_only, a.engine_on_demand, a.engine_idle_s = True, True, 1.0
            launches = Launches(a)
            await asyncio.sleep(1.0)
            assert launches.calls == [], "nobody is playing: no engine (a browser of half a gigabyte) for nobody"
            game.login("Alice", "Testrealm")
            await until(lambda: launches.calls, 10, "a character appeared: the engine is started")
            assert launches.calls == [("engine", True)], launches.calls
            await asyncio.sleep(1.0)
            assert len(launches.calls) == 1, "and started once, not on every tick while its page is loading"
            proc = a._proc = FakeProc()                          # (the real launch would have set this)
            (engine,) = await add_pages(sc, a, "engine")
            game.logout()
            await until(lambda: a.ident is None, 10, "the game is gone")
            await asyncio.sleep(0.5)
            assert not proc.ended, "gone for under the idle time: kept (a /reload or a relog comes back at once)"
            await until(lambda: proc.ended, 8, "the engine is stopped once there has been no game for long enough")
            assert killed and killed[-1].endswith("browser-profile"), "and every process of its profile with it: %r" % killed
            await engine.ws.close()
            await asyncio.sleep(1.8)
            assert len(launches.calls) == 1, "its page going away is not a reason to start another while nobody plays"
            game.login("Alice", "Testrealm")
            await until(lambda: len(launches.calls) == 2, 10, "the next game starts it again")
    run(go(), 120)


def test_a_game_that_comes_back_before_the_idle_time_keeps_the_same_engine(tmp_path, monkeypatch):
    from eqvoice_client import app as app_module
    monkeypatch.setattr(app_module.browser, "kill_profile", lambda profile: 0)

    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            game = sc.games["Alice"]
            a.tray_only, a.engine_on_demand, a.engine_idle_s = True, True, 4.0
            launches = Launches(a)
            await until(lambda: launches.calls, 10, "started")
            proc = a._proc = FakeProc()
            (engine,) = await add_pages(sc, a, "engine")
            game.logout()
            await until(lambda: a.ident is None, 10, "gone")
            await asyncio.sleep(1.5)
            game.login("Alice", "Testrealm")
            await asyncio.sleep(5.0)
            assert not proc.ended and len(launches.calls) == 1, "a relog inside the idle time costs nothing: same engine, nothing started"
    run(go(), 120)


def test_without_on_demand_the_engine_starts_with_the_program_as_it_did(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only, a.engine_on_demand = True, False
            launches = Launches(a)
            await until(lambda: a.ident is not None, 10, "a game is being played")
            await asyncio.sleep(1.0)
            assert launches.calls == [], "the tick starts nothing unless the program runs the engine on demand (it started it itself, at launch)"
    run(go(), 60)


def test_when_the_engine_cannot_be_kept_up_the_player_is_told_in_words_and_no_window_opens(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            a.tray_only = True
            told = []
            a.notify_user = lambda title, text: told.append((title, text))
            launches = Launches(a)
            a._engine_starts = [a.clock()] * 4
            (engine,) = await add_pages(sc, a, "engine")
            await engine.ws.close()
            await until(lambda: told, 5, "the player is told")
            assert launches.calls == [], "no window (the public build has none) and no fifth engine"
            assert "Restart EqVoice" in told[0][1] and "tray" in told[0][1], told
    run(go(), 60)


def test_a_refused_beta_code_tells_the_player_once_to_get_the_newest_installer(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            told = []
            a.notify_user = lambda title, text: told.append(text)
            for _ in range(3):
                a._on_sig_event({"t": "_state", "state": "backoff", "error": "RuntimeError: server refused: bad_code (wrong join code)"})
            assert len(told) == 1 and "newest installer" in told[0] and "code" not in told[0].lower().replace("installer", ""), told
    run(go(), 60)
