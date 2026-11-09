"""The voice controls added with the boost / mute / key / noise gate / mute list / icons work: what the app does with each command, what it
tells the page and the game, and that it all survives a restart. Run against a real hub with a simulated game, like the other app tests."""
import json

from eqvoice_client.bridge.uifiles import status_path
from eqvoice_client.config import Config

from scene import Scene, pstate, run, until
from test_ingame_panel import read_status, send


def test_the_new_settings_have_defaults_and_old_files_get_them(tmp_path):
    cfg = Config.load(tmp_path / "fresh")
    a = cfg.audio
    assert a.boost == "normal" and a.auto_level is True and a.cues is True
    assert a.gate_mode == "auto" and a.gate_db == -48.0 and a.mute_list == []
    p = cfg.proximity
    assert (p.full_radius, p.loud_full_radius, p.peak_radius, p.curve_power, p.boost_radius, p.boost_fade_radius) == (30.0, 45.0, 85.0, 1.6, 0.0, 10.0)
    home = tmp_path / "old"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"config_version": 3, "audio": {"mode": "open", "muted": True}}))
    old = Config.load(home)
    assert old.audio.boost == "normal" and old.audio.gate_mode == "auto" and old.audio.muted is True and old.audio.mute_list == []


def test_with_an_open_microphone_the_key_mutes_and_unmutes_and_says_so(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), cfg={"audio": {"mode": "open"}})
            pa = sc.pages["Alice"]
            await until(lambda: pa.named("config"), 5, "the page is configured")
            assert a.cfg.audio.muted is False
            cues = lambda: [c["kind"] for c in pa.named("cue")]        # noqa: E731
            assert cues() == [], "no cue just for starting"
            a._on_ptt(True)                                              # a press of the key: open mic, so it toggles
            assert a.cfg.audio.muted is True
            await until(lambda: cues() == ["mute"], 5, "a falling blip tells you")
            await until(lambda: pa.named("tx")[-1]["enabled"] is False, 5, "the microphone closes")
            a._on_ptt(False)                                             # letting go does nothing
            assert a.cfg.audio.muted is True
            a._on_ptt(True)
            assert a.cfg.audio.muted is False
            await until(lambda: cues() == ["mute", "unmute"], 5, "a rising blip")
            await until(lambda: pa.named("tx")[-1]["enabled"] is True, 5, "and the microphone opens")
            a._on_ptt(False)
            # the page is told how you look to the others: microphone closed, not deafened
            await until(lambda: pa.named("self_state") and pa.named("self_state")[-1] == {"cmd": "self_state", "mute": False, "deaf": False}, 5, "self state")
            a._on_ptt(True)
            await until(lambda: pa.named("self_state")[-1]["mute"] is True and pa.named("self_state")[-1]["deaf"] is False, 5, "muted")
            a._on_ptt(False)
            # deafened: the key only mutes (it does not undeafen), and no other blip plays while deafened
            a._on_ui({"cmd": "deafen", "value": True})
            await until(lambda: cues()[-1] == "deafen", 5, "a blip for deafening")
            await until(lambda: pa.named("self_state")[-1] == {"cmd": "self_state", "mute": True, "deaf": True}, 5, "deafened and so not transmitting")
            n = len(cues())
            a._on_ptt(True)
            a._on_ptt(False)
            assert a.cfg.audio.deafened is True and len(cues()) == n, "no mute blip while deafened"
            a._on_ui({"cmd": "deafen", "value": False})
            await until(lambda: cues()[-1] == "undeafen", 5, "and one for undeafening")
            # blips can be switched off
            a.cfg.audio.cues = False
            n = len(cues())
            a._on_ptt(True)
            a._on_ptt(False)
            assert len(cues()) == n
    run(go(), 60)


def test_with_push_to_talk_the_key_is_held_and_does_not_mute(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa = sc.pages["Alice"]
            assert a.cfg.audio.mode == "ptt"
            await until(lambda: pa.named("config"), 5, "configured")
            a._on_ptt(True)
            assert a.cfg.audio.muted is False
            await until(lambda: pa.named("tx")[-1]["enabled"] is True, 5, "holding it transmits")
            await until(lambda: pa.named("self_state")[-1]["mute"] is False, 5, "and says the microphone is open")
            a._on_ptt(False)
            await until(lambda: pa.named("tx")[-1]["enabled"] is False, 5, "letting go stops")
            await until(lambda: pa.named("self_state")[-1]["mute"] is True, 5, "which looks muted to everyone else")
            assert pa.named("cue") == [], "no blips for the key in push to talk"
    run(go(), 60)


def test_the_game_changes_the_key_and_a_refused_key_is_explained(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            game = sc.games["Alice"]
            await until(lambda: status_path(game.in_path).exists() and a.ui_control is not None, 10, "the panel files")
            assert read_status(game).fields["key"] == "F9"
            send(game, ("key", "F10"))
            await until(lambda: a.cfg.audio.ptt_label == "F10" and a.cfg.audio.ptt_vk == 0x79, 5, "the key changed")
            await until(lambda: read_status(game).fields["key"] == "F10", 5, "and the panel is told")
            send(game, ("key", "Button4"))
            await until(lambda: a.cfg.audio.ptt_vk == 0x05, 5, "a mouse button works too")
            await until(lambda: read_status(game).fields["key"].startswith("Mouse_4"), 5, "named for the panel")
            send(game, ("key", "ESCAPE"))
            await until(lambda: "cannot be used as the key" in " ".join([read_status(game).headline] + read_status(game).more), 5, "a refused key says why")
            assert a.cfg.audio.ptt_vk == 0x05, "and the old key stays"
            send(game, ("key", "LeftButton"))
            await until(lambda: a.ui_commands >= 4, 5, "handled")
            assert a.cfg.audio.ptt_vk == 0x05, "a left click can never be the key"
    run(go(), 60)


def test_boost_and_the_noise_gate_reach_the_page_the_game_and_the_config(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa = sc.pages["Alice"]
            game = sc.games["Alice"]
            await until(lambda: status_path(game.in_path).exists() and a.ui_control is not None and pa.named("config"), 10, "ready")
            cfg = pa.named("config")[-1]
            assert cfg["curve"]["boost"] == 2.0 and cfg["auto_level"] is True and cfg["gate_mode"] == "auto" and cfg["gate_db"] == -48.0
            st = read_status(game).fields
            assert (st["boost"], st["gate"], st["gdb"]) == ("normal", "off", "-48"), "the test scene runs with the keyboard filter off"

            send(game, ("boost", "high"))
            await until(lambda: pa.named("curve") and pa.named("curve")[-1]["value"]["boost"] == 3.0, 5, "the page is given the new curve")
            assert a.cfg.audio.boost == "high" and a.peers.cfg.near_gain == 3.0 and a.build_state()["voice"]["boost"] == "high"
            await until(lambda: read_status(game).fields["boost"] == "high", 5, "the game is told")
            for given, want in (("off", 1.0), ("low", 1.4), ("normal", 2.0)):
                a._on_ui({"cmd": "boost", "value": given})
                await until(lambda: pa.named("curve")[-1]["value"]["boost"] == want, 5, "boost %s" % given)
            send(game, ("boost", "banana"), ("boost", "high"))
            await until(lambda: pa.named("curve")[-1]["value"]["boost"] == 3.0 and a.cfg.audio.boost == "high", 5, "back to high")
            assert [c["value"]["boost"] for c in pa.named("curve")][-2:] == [2.0, 3.0], "nonsense is not forwarded"

            send(game, ("gate", "manual"))
            await until(lambda: pa.named("keyboard") and pa.named("keyboard")[-1]["value"] is True, 5, "manual turns the filter on")
            await until(lambda: pa.named("gate") and pa.named("gate")[-1]["mode"] == "manual", 5, "and tells the page its mode")
            assert a.cfg.audio.keyboard_filter is True and a.cfg.audio.gate_mode == "manual"
            send(game, ("gatedb", "-52"))
            await until(lambda: pa.named("gate")[-1]["db"] == -52.0, 5, "the bar")
            assert a.cfg.audio.gate_db == -52.0
            await until(lambda: (read_status(game).fields["gate"], read_status(game).fields["gdb"]) == ("manual", "-52"), 5, "the game is told")
            send(game, ("gatedb", "-500"))
            await until(lambda: a.cfg.audio.gate_db == -100.0, 5, "the bar is kept in range")
            send(game, ("gatedb", "loud"), ("gate", "auto"))
            await until(lambda: pa.named("gate")[-1]["mode"] == "auto", 5, "auto")
            assert a.cfg.audio.gate_db == -100.0, "a bar that is not a number is ignored"
            n = len(pa.named("keyboard"))
            send(game, ("gate", "off"))
            await until(lambda: a.cfg.audio.keyboard_filter is False and pa.named("keyboard")[-1]["value"] is False, 5, "off turns the filter off")
            await until(lambda: read_status(game).fields["gate"] == "off", 5, "the game is told")
            before = a.ui_commands
            send(game, ("gate", "wat"), ("gate", "off"))
            await until(lambda: a.ui_commands >= before + 2, 5, "handled")
            assert len(pa.named("keyboard")) == n + 1, "switching off twice does not restart the microphone again"
            # the page's live measurements reach the window
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "m", "level": 0.1, "tx": False, "pcs": 0, "gate": True, "gopen": False,
                                   "gfloor": -70.5, "glevel": -61.2, "gthr": -58.0})
            await until(lambda: a.mic.get("glevel") == -61.2 and a.mic.get("gthr") == -58.0, 5, "the meter figures are kept")
        saved = json.loads((tmp_path / "Alice" / "config.json").read_text())
        assert saved["audio"]["boost"] == "high" and saved["audio"]["gate_mode"] == "auto" and saved["audio"]["keyboard_filter"] is False
    run(go(), 90)


def test_the_mute_list_from_the_game_the_window_and_a_restart(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(1000, 0))
            pa = sc.pages["Alice"]
            game = sc.games["Alice"]
            await until(lambda: status_path(game.in_path).exists() and a.ui_control is not None, 10, "the panel files")
            sc.walk_to("Alice", "Bobby", 20)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 15, "connected")
            await until(lambda: len(read_status(game).peers) == 1, 10, "listed")
            assert read_status(game).peers[0]["muted"] is False and read_status(game).muted == []
            send(game, ("pmute", "bobby 1"))
            await until(lambda: pa.named("peer_mute") and pa.named("peer_mute")[-1]["value"] is True, 5, "the page stops playing him")
            sid = pa.named("peer_mute")[-1]["sid"]
            assert sid == a.peers.peers["bobby"].sid
            assert a.cfg.audio.mute_list == ["bobby"] and a.peers.is_muted("Bobby")
            await until(lambda: read_status(game).muted == ["bobby"] and read_status(game).peers[0]["muted"] is True, 5, "the game is told, in the list and on his row")
            assert a.build_state()["voice"]["muted_players"] == ["bobby"]
            a._on_ui({"cmd": "mute_player", "name": "Carol", "value": True})
            await until(lambda: read_status(game).muted == ["bobby", "carol"], 5, "a player who is not near can be on the list too")
            send(game, ("pmute", "bobby 0"))
            await until(lambda: pa.named("peer_mute")[-1]["value"] is False, 5, "taken off")
            before = a.ui_commands
            send(game, ("pmute", "no spaces allowed 1"), ("pmute", "bobby maybe"), ("pmute", "x"))
            await until(lambda: a.ui_commands >= before + 3, 5, "handled")
            assert a.cfg.audio.mute_list == ["carol"], "nonsense is ignored: %r" % a.cfg.audio.mute_list
        saved = json.loads((tmp_path / "Alice" / "config.json").read_text())
        assert saved["audio"]["mute_list"] == ["carol"], "the list is saved"
        again = Config.load(tmp_path / "Alice")
        assert again.audio.mute_list == ["carol"]
    run(go(), 90)


def test_what_the_other_player_is_doing_reaches_the_game(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(1000, 0))
            game = sc.games["Alice"]
            await until(lambda: status_path(game.in_path).exists() and a.ui_control is not None, 10, "the panel files")
            sc.walk_to("Alice", "Bobby", 20)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 15, "connected")
            p = a.peers.peers["bobby"]
            await until(lambda: len(read_status(game).peers) == 1, 10, "listed")
            assert read_status(game).peers[0]["state"] == "-", "no word from Bobby's page yet"
            await sc.pages["Alice"].ws.send_json({"t": "peer", "sid": p.sid, "ep": p.ep, "type": "vstate", "mute": False, "deaf": False})
            await until(lambda: read_status(game).peers[0]["state"] == "o", 5, "his microphone is open")
            await sc.pages["Alice"].ws.send_json({"t": "peer", "sid": p.sid, "ep": p.ep, "type": "vstate", "mute": True, "deaf": False})
            await until(lambda: read_status(game).peers[0]["state"] == "m", 5, "muted")
            await sc.pages["Alice"].ws.send_json({"t": "peer", "sid": p.sid, "ep": p.ep, "type": "vstate", "mute": True, "deaf": True})
            await until(lambda: read_status(game).peers[0]["state"] == "d", 5, "deafened")
            assert a.build_state()["peers"][0]["vstate"] == "d"
    run(go(), 90)


def test_two_quick_presses_of_the_key_with_an_open_microphone_are_two_toggles(tmp_path):
    """Push to talk keeps transmitting for a moment after the key is released, so word ends are not clipped. That tail must not swallow a
    second press of the mute key: tapping it twice quickly mutes and unmutes."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), cfg={"audio": {"mode": "open", "ptt_vk": 0x78}})
            now, down = [100.0], [False]
            a.ptt.clock = lambda: now[0]
            a.ptt.sampler = lambda vk: down[0]
            a.ptt.virtual = False

            def step(is_down, dt=0.03):
                down[0] = is_down
                now[0] += dt
                a.ptt.sample()

            assert a.cfg.audio.muted is False
            step(True); step(False, 0.05)                  # a tap
            assert a.cfg.audio.muted is True
            step(True, 0.06); step(False, 0.05)            # the second tap, 110 ms after the first press ended
            assert a.cfg.audio.muted is False, "the second tap is a second toggle"
            step(True); step(True); step(True)             # holding it down does not toggle again and again
            assert a.cfg.audio.muted is True
            step(False)
            a.cfg.audio.mode = "ptt"
            step(True)
            step(False, 0.05)
            assert a.cfg.audio.muted is True, "in push to talk the key never mutes"
            assert a.ptt.down is True, "and the tail is still there: the key was let go 50 ms ago"
    run(go(), 60)


def test_the_app_serves_the_icon_pictures_to_the_window(tmp_path):
    import aiohttp

    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            base = "http://127.0.0.1:%d/static/" % a.port
            async with aiohttp.ClientSession() as s:
                for name in ("voice-speaking.png", "voice-open.png", "voice-known.png", "voice-muted.png", "voice-deafened.png", "eqvoice-icon.png"):
                    async with s.get(base + name) as r:
                        body = await r.read()
                        assert r.status == 200 and r.headers["Content-Type"] == "image/png" and body[:4] == b"\x89PNG", name
                for name in ("voice-other.png", "voice-speaking.png/../app.py", "config.json", "index.html"):
                    async with s.get(base + name) as r:
                        assert r.status == 404, name
    run(go(), 60)
