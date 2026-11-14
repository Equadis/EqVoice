"""0.4.0: one player's volume and block, the two deafens, the microphone test, the sound mode, the direction of each voice. What the app does with
each command, what it tells the page and the game, and that it survives a restart. A real hub and a simulated game, like the other app tests."""
import asyncio
import json
import time

from eqvoice_client.bridge.uifiles import direction_path, status_path
from eqvoice_client.config import Config

from scene import Scene, pstate, run, until
from test_ingame_panel import read_status, send


async def near_pair(sc):
    a = await sc.add("Alice", at=(0, 0))
    await sc.add("Bobby", at=(1000, 0))
    game = sc.games["Alice"]
    await until(lambda: status_path(game.in_path).exists() and a.ui_control is not None, 10, "the panel files")
    sc.walk_to("Alice", "Bobby", 20)
    await until(lambda: pstate(a, "Bobby") == "CONNECTED", 15, "connected")
    await until(lambda: len(read_status(game).peers) == 1, 10, "listed")
    return a, sc.pages["Alice"], game


def test_one_players_volume_reaches_only_their_stream_goes_up_to_200_percent_and_is_kept(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, game = await near_pair(sc)
            sid = a.peers.peers["bobby"].sid
            send(game, ("pvol", "bobby 150"))
            await until(lambda: pa.named("volume") and pa.named("volume")[-1]["value"] == 1.5, 5, "the page is told Bobby's own volume")
            assert pa.named("volume")[-1]["sid"] == sid, "for his stream only"
            assert a.cfg.audio.master_volume == 1.0, "the everyone-volume is not touched"
            await until(lambda: read_status(game).peers[0]["vol"] == 150, 5, "the game is told, on his row")
            send(game, ("pvol", "bobby 999"))
            await until(lambda: pa.named("volume")[-1]["value"] == 2.0, 5, "200% is the most")
            send(game, ("pvol", "bobby 100"))
            await until(lambda: pa.named("volume")[-1]["value"] == 1.0 and "bobby" not in a.cfg.audio.player_volume, 5, "100% is the default: nothing to remember")
            send(game, ("pvol", "bobby 30"))
            await until(lambda: a.cfg.audio.player_volume.get("bobby") == 0.3, 5, "kept by name")
        saved = json.loads((tmp_path / "Alice" / "config.json").read_text())
        assert saved["audio"]["player_volume"] == {"bobby": 0.3}
        again = Config.load(tmp_path / "Alice")
        assert again.audio.player_volume == {"bobby": 0.3}
    run(go(), 120)


def test_a_new_call_starts_at_the_players_saved_volume_and_a_muted_player_is_not_blocked(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, game = await near_pair(sc)
            send(game, ("pvol", "bobby 160"), ("pmute", "bobby 1"))
            await until(lambda: a.cfg.audio.player_volume.get("bobby") == 1.6 and a.peers.is_muted("Bobby"), 5, "set")
            assert not a.peers.is_blocked("Bobby"), "muting is only about what you hear"
            n = len(pa.named("volume"))
            p = a.peers.peers["bobby"]
            a.peers._peer_prefs(p)                                   # what happens right after a call is opened (a reconnect)
            await until(lambda: len(pa.named("volume")) > n and pa.named("volume")[-1]["value"] == 1.6, 5, "the page is handed the saved volume for the new call")
    run(go(), 120)


def test_block_stops_your_microphone_going_to_that_player_and_unblock_gives_it_back(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, game = await near_pair(sc)
            await until(lambda: pa.named("gain") and pa.named("gain")[-1].get("send") is True, 10, "Bobby is being sent your voice")
            send(game, ("pblock", "bobby 1"))
            await until(lambda: a.peers.is_blocked("Bobby"), 5, "blocked")
            await until(lambda: pa.named("gain") and pa.named("gain")[-1].get("send") is False, 5, "the page is told to send him nothing")
            assert a.cfg.audio.block_list == ["bobby"]
            assert not a.peers.is_muted("Bobby"), "you still hear him: block is about what he hears"
            await until(lambda: read_status(game).blocked == ["bobby"] and read_status(game).peers[0]["blocked"] is True, 5, "the game is told")
            await asyncio.sleep(1.0)
            assert pa.named("gain")[-1].get("send") is False, "it stays blocked while the distance updates keep coming"
            send(game, ("pblock", "bobby 0"))
            await until(lambda: pa.named("gain")[-1].get("send") is True, 5, "unblocked: sent again")
            send(game, ("pblock", "bobby 1"))
            await until(lambda: a.cfg.audio.block_list == ["bobby"], 5, "blocked again")
        again = Config.load(tmp_path / "Alice")
        assert again.audio.block_list == ["bobby"], "the block list is saved"
    run(go(), 120)


def test_deafen_closes_the_microphone_too_and_gives_back_your_own_mute_the_other_deafen_keeps_it_open(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            a._on_ui({"cmd": "set_mode", "mode": "open"})
            await until(lambda: a.tx_enabled, 5, "transmitting")
            send(game, ("deafen", "1"))
            await until(lambda: a.cfg.audio.deafened and not a.tx_enabled, 5, "deafened: the microphone is closed with it")
            await until(lambda: pa.named("tx") and pa.named("tx")[-1]["enabled"] is False and pa.named("deafen")[-1]["value"] is True, 5, "the page is told: line closed, hearing off")
            await until(lambda: read_status(game).fields["deaf"] == "1" and read_status(game).fields["dk"] == "0", 5, "the game is told: normal deafen")
            send(game, ("deafen", "0"))
            await until(lambda: not a.cfg.audio.deafened and a.tx_enabled, 5, "undeafened: transmitting again")
            send(game, ("deafen", "2"))
            await until(lambda: a.cfg.audio.deafened and a.cfg.audio.deaf_keep_mic, 5, "the other deafen")
            assert a.tx_enabled, "the microphone stays open: they still hear you"
            await until(lambda: pa.named("deafen")[-1]["value"] is True and pa.named("tx")[-1]["enabled"] is True, 5, "you hear nobody, and the line stays open")
            await until(lambda: read_status(game).fields["deaf"] == "1" and read_status(game).fields["dk"] == "1", 5, "the game is told which deafen")
            send(game, ("deafen", "1"))
            await until(lambda: not a.cfg.audio.deaf_keep_mic and not a.tx_enabled, 5, "switching to the normal deafen closes the microphone")
            send(game, ("deafen", "0"))
            # a microphone you had muted yourself stays muted through a deafen and after it
            send(game, ("mute", "1"))
            await until(lambda: a.cfg.audio.muted and not a.tx_enabled, 5, "muted")
            send(game, ("deafen", "2"))
            await until(lambda: a.cfg.audio.deafened, 5, "deafened, microphone open")
            assert not a.tx_enabled, "but it was muted already: the deafen does not open it"
            send(game, ("deafen", "0"))
            await until(lambda: not a.cfg.audio.deafened, 5, "undeafened")
            assert a.cfg.audio.muted and not a.tx_enabled, "your own mute is exactly as you left it"
    run(go(), 90)


def test_the_microphone_test_closes_the_line_to_everyone_never_unmutes_and_goes_on_until_stopped(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            a._on_ui({"cmd": "set_mode", "mode": "open"})
            await until(lambda: a.tx_enabled, 5, "transmitting")
            send(game, ("mictest", "1"))
            await until(lambda: a.mic_test and not a.tx_enabled, 5, "the test: nothing is transmitted")
            await until(lambda: pa.named("tx") and pa.named("tx")[-1]["enabled"] is False, 5, "the page is told the line is closed ...")
            await until(lambda: pa.named("mic_test") and pa.named("mic_test")[-1]["on"] is True, 5, "... and to play the microphone back to you")
            await until(lambda: read_status(game).fields["mt"] == "1", 5, "the game shows the button as running")
            a._on_ui({"cmd": "set_mode", "mode": "ptt"})
            a.ptt_down = True
            assert not a.tx_enabled, "not even push to talk sends during the test"
            a.ptt_down = False
            a._on_ui({"cmd": "set_mode", "mode": "open"})
            await asyncio.sleep(0.6)                                   # the panel is closed in the game: nothing here says the test must stop
            assert a.mic_test and not a.tx_enabled, "it goes on until the button is pressed again"
            # a new engine page (a restart of the engine) is told the test is still on
            n = len(pa.named("mic_test"))
            a._send_config()
            await until(lambda: len(pa.named("mic_test")) > n and pa.named("mic_test")[-1]["on"] is True, 5, "the new engine goes on playing it back")
            send(game, ("mictest", "0"))
            await until(lambda: not a.mic_test and a.tx_enabled, 5, "stopped: transmitting again, as before")
            await until(lambda: pa.named("mic_test")[-1]["on"] is False, 5, "and the playback is stopped")
            # a microphone that was muted before the test is still muted after it
            send(game, ("mute", "1"))
            await until(lambda: not a.tx_enabled, 5, "muted")
            send(game, ("mictest", "1"))
            await until(lambda: a.mic_test, 5, "testing while muted")
            send(game, ("mictest", "0"))
            await until(lambda: not a.mic_test, 5, "stopped")
            assert a.cfg.audio.muted and not a.tx_enabled, "the test never unmutes a microphone"
            # an engine that cannot play it back ends the test, and says so
            send(game, ("mictest", "1"))
            await until(lambda: a.mic_test, 5, "again")
            await pa.ws.send_json({"t": "mictest", "on": False, "ok": False, "error": "no audio in this browser"})
            await until(lambda: not a.mic_test, 5, "the test could not start: it is off again, not stuck")
    run(go(), 90)


def test_the_sound_mode_is_kept_sent_to_the_page_and_an_unknown_one_is_refused(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            assert a.cfg.audio.spatial == "headphones", "headphones unless the player says otherwise"
            send(game, ("spatial", "speakers"))
            await until(lambda: pa.named("spatial") and pa.named("spatial")[-1]["value"] == "speakers", 5, "the page is told")
            await until(lambda: read_status(game).fields["sp"] == "speakers", 5, "and the game")
            before = a.ui_commands
            send(game, ("spatial", "surround"))
            await until(lambda: a.ui_commands > before, 5, "handled")
            assert a.cfg.audio.spatial == "speakers", "an unknown mode changes nothing"
            n = len(pa.named("config"))
            a._send_config()
            await until(lambda: len(pa.named("config")) > n and pa.named("config")[-1]["spatial"] == "speakers", 5, "a new engine is handed it")
        assert Config.load(tmp_path / "Alice").audio.spatial == "speakers", "kept"
    run(go(), 90)


def test_the_direction_file_from_the_game_is_handed_to_the_page_as_it_changes_and_stale_files_say_nothing(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, game = await near_pair(sc)
            sid = a.peers.peers["bobby"].sid
            path = direction_path(game.in_path)
            path.write_text("OVD1 1\nBobby 90\n", encoding="utf-8")
            await until(lambda: pa.named("dir") and pa.named("dir")[-1]["sid"] == sid and pa.named("dir")[-1]["az"] == 90.0, 5, "Bobby is on your right")
            n = len(pa.named("dir"))
            path.write_text("OVD1 2\nBobby 91\n", encoding="utf-8")
            await asyncio.sleep(0.6)
            assert len(pa.named("dir")) == n, "a degree is not worth telling the page"
            path.write_text("OVD1 3\nBobby -90\n", encoding="utf-8")
            await until(lambda: pa.named("dir")[-1]["az"] == -90.0, 5, "he walks round to your left")
            path.write_text("OVD1 4\n", encoding="utf-8")
            await until(lambda: pa.named("dir")[-1]["az"] is None, 5, "the game cannot say where he is: centred")
            path.write_text("OVD1 5\nBobby 45\n", encoding="utf-8")
            await until(lambda: pa.named("dir")[-1]["az"] == 45.0, 5, "back")
            old = time.time() - 60
            import os
            os.utime(path, (old, old))                                  # the game stopped writing a minute ago
            await until(lambda: pa.named("dir")[-1]["az"] is None, 5, "an old file says nothing: a voice is centred, not stuck at an old angle")
    run(go(), 120)


def test_the_new_settings_have_defaults_and_old_config_files_get_them(tmp_path):
    cfg = Config.load(tmp_path / "x")
    assert cfg.audio.spatial == "headphones" and cfg.audio.block_list == [] and cfg.audio.player_volume == {} and cfg.audio.deaf_keep_mic is False
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "config.json").write_text(json.dumps({"audio": {"mode": "ptt", "peer_volume": {"a": 1.0}}}), encoding="utf-8")
    old = Config.load(tmp_path / "old")
    assert old.audio.mode == "ptt" and old.audio.spatial == "headphones" and old.audio.player_volume == {}
