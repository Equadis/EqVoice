"""The desktop app's half of the in-game panel: it publishes a status file the addon draws, and applies the commands the player
clicked in the game. Run against a real hub with a simulated game, like the other app tests."""
import json
import time

from eqvoice_client.bridge.uifiles import build_control, control_path, parse_status, status_path
from eqvoice_client.config import Config

from scene import Scene, pstate, run, until


def read_status(game):
    return parse_status(status_path(game.in_path).read_text(encoding="utf-8"))


def send(game, *commands, seq=[100]):
    """What the addon does: write the command file (ids come from the clock, as in Control.lua)."""
    base = int(time.time() * 100) + seq[0]
    seq[0] += len(commands) + 1
    control_path(game.in_path).write_text(build_control(seq[0], int(time.time()), "Alice",
                                                        [(base + i, c, a) for i, (c, a) in enumerate(commands)]), encoding="utf-8")


def test_the_app_publishes_a_status_the_game_can_draw(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(1000, 0))
            game = sc.games["Alice"]
            await until(lambda: status_path(game.in_path).exists(), 10, "a status file beside the roster file")
            await until(lambda: read_status(game).fields.get("hub") == "1", 10, "hub registered")
            await sc.pages["Alice"].ws.send_json({"t": "mic", "state": "ok", "label": "Test mic", "level": 0.0, "tx": False, "pcs": 0})
            await until(lambda: read_status(game).fields.get("mic") == "ok", 10, "the page reports a working microphone")
            st = read_status(game)
            assert st.character == "Alice", "written for the character the game reported"
            assert st.fields["mode"] == "ptt" and st.fields["key"] == "F9" and st.fields["vol"] == "100"
            assert st.fields["mute"] == "0" and st.fields["deaf"] == "0" and st.fields["game"] == "1"
            assert st.headline.startswith("Ready: Alice on"), st.headline
            assert st.peers == [], "nobody near yet"
            sc.walk_to("Alice", "Bobby", 20)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 15, "connected")
            await until(lambda: len(read_status(game).peers) == 1 and read_status(game).peers[0]["connected"], 10, "the player shows up")
            st = read_status(game)
            assert st.peers[0]["name"] == "Bobby" and 19 <= st.peers[0]["dist"] <= 21
            assert st.fields["near"] == "1" and st.fields["conn"] == "1"
            seq = st.seq
            await until(lambda: read_status(game).seq != seq, 5, "rewritten as a heartbeat so the game knows the app is alive")
            await a.stop()
            sc.apps.pop("Alice")
            assert not status_path(game.in_path).exists(), "removed with the app: the panel says it is not running"
    run(go(), 90)


def test_what_the_player_clicks_in_the_game_happens(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa = sc.pages["Alice"]
            game = sc.games["Alice"]
            await until(lambda: status_path(game.in_path).exists() and a.ui_control is not None, 10, "the panel files")
            tx = lambda: [c["enabled"] for c in pa.cmds if c["cmd"] == "tx"]       # noqa: E731
            assert a.cfg.audio.mode == "ptt" and tx()[-1] is False

            send(game, ("mode", "open"))
            await until(lambda: a.cfg.audio.mode == "open", 5, "mode open")
            await until(lambda: tx()[-1] is True, 5, "an open mic transmits")
            await until(lambda: read_status(game).fields["mode"] == "open" and read_status(game).fields["tx"] == "1", 5, "and the status says so")

            send(game, ("mute", "1"))
            await until(lambda: a.cfg.audio.muted is True and tx()[-1] is False, 5, "mute silences the mic")
            await until(lambda: read_status(game).fields["mute"] == "1" and read_status(game).fields["tx"] == "0", 5, "status follows")
            send(game, ("mute", "0"))
            await until(lambda: a.cfg.audio.muted is False and tx()[-1] is True, 5, "unmute")

            send(game, ("deafen", "1"))
            await until(lambda: a.cfg.audio.deafened is True and pa.named("deafen")[-1]["value"] is True, 5, "deafen")
            send(game, ("deafen", "0"))
            await until(lambda: a.cfg.audio.deafened is False, 5, "undeafen")

            send(game, ("volume", "40"))
            await until(lambda: pa.named("master") and pa.named("master")[-1]["value"] == 0.4, 5, "the page is told the master volume")
            assert a.cfg.audio.master_volume == 0.4
            await until(lambda: read_status(game).fields["vol"] == "40", 5, "status follows")

            send(game, ("test", "1"))
            await until(lambda: pa.named("tone"), 5, "the speaker test is played")

            # two commands in one file are both applied, in order
            send(game, ("mode", "ptt"), ("mute", "1"))
            await until(lambda: a.cfg.audio.mode == "ptt" and a.cfg.audio.muted is True, 5, "both applied")
            send(game, ("mute", "0"))
            await until(lambda: a.cfg.audio.muted is False, 5, "back")

            # nonsense is ignored, not obeyed or fatal
            before = (a.cfg.audio.mode, a.cfg.audio.muted, a.cfg.audio.master_volume)
            send(game, ("mode", "banana"), ("mute", "maybe"), ("volume", "loud"), ("selfdestruct", "1"), ("volume", "250"))
            await until(lambda: a.cfg.audio.master_volume == 1.0, 5, "250 is clamped to 100")
            assert (a.cfg.audio.mode, a.cfg.audio.muted) == before[:2]
            assert a.ui_commands >= 9
        saved = json.loads((tmp_path / "Alice" / "config.json").read_text())
        assert saved["audio"]["master_volume"] == 1.0, "settings made in the game persist"
    run(go(), 90)


def test_the_page_is_told_the_master_volume_when_it_connects(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False, cfg={"audio": {"master_volume": 0.6, "mode": "open"}})
            from scene import FakePage
            pa = FakePage(a)
            await pa.connect(sc.session)
            await until(lambda: pa.named("config"), 5, "config")
            assert pa.named("config")[0]["master"] == 0.6
            assert a.build_state()["voice"]["master"] == 0.6
    run(go(), 60)


def test_the_input_volume_and_the_bitrate_cap_reach_the_page_and_the_game(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa = sc.pages["Alice"]
            game = sc.games["Alice"]
            await until(lambda: pa.named("config") and status_path(game.in_path).exists() and a.ui_control is not None, 10, "config and panel files")
            cfg = pa.named("config")[-1]
            assert cfg["input_gain"] == 1.0 and cfg["bitrate"] == 24 and "fx" not in cfg, "the microphone as it comes and a 24 kbps cap unless chosen otherwise"
            await until(lambda: read_status(game).fields.get("ing") == "100", 10, "the game is told")
            assert a.build_state()["voice"]["input_gain"] == 1.0 and a.build_state()["voice"]["bitrate"] == 24

            send(game, ("input", "150"))
            await until(lambda: a.cfg.audio.input_gain == 1.5 and pa.named("input_gain") and pa.named("input_gain")[-1]["value"] == 1.5, 5, "the page is told")
            await until(lambda: read_status(game).fields["ing"] == "150", 5, "and the status follows")
            send(game, ("input", "banana"))
            send(game, ("input", "999"))
            await until(lambda: a.cfg.audio.input_gain == 2.0, 5, "the top is 200%")
            await until(lambda: [c["value"] for c in pa.named("input_gain")] == [1.5, 2.0], 5, "the page hears 150%, then 200%: nonsense is not forwarded")

            for n, (given, want) in enumerate(((16, 16), (1, 8), (999, 64), (0, 0), ("x", 0)), 1):
                a._on_ui({"cmd": "max_bitrate", "value": given})
                await until(lambda: len(pa.named("bitrate")) == n and pa.named("bitrate")[-1]["value"] == want, 5, "cap %r -> %r" % (given, want))
            a.cfg.audio.input_gain = "loud"
            a.cfg.audio.max_bitrate_kbps = "lots"
            a._send_config()
            await until(lambda: pa.named("config")[-1]["input_gain"] == 1.0 and pa.named("config")[-1]["bitrate"] == 24, 5,
                        "a damaged setting is replaced by the default, not passed on to the page")
        saved = json.loads((tmp_path / "Alice" / "config.json").read_text())
        assert "input_gain" in saved["audio"] and "max_bitrate_kbps" in saved["audio"] and "voice_fx" not in saved["audio"]
    run(go(), 60)


def test_a_page_that_gave_up_on_the_voice_chain_is_shown(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa = sc.pages["Alice"]
            assert a.fx_error == ""
            await pa.ws.send_json({"t": "fx", "ok": False, "error": "the effect produced silence while a voice was arriving"})
            await until(lambda: a.fx_error, 5, "the app learns of it")
            warn = " ".join(t for _, t in a.media_warnings())
            assert "voice booster is switched off" in warn and "play plain" in warn and "Restart EqVoice" in warn
            assert "voice booster" in " ".join([a.status_summary()["text"]] + list(a.status_summary()["more"]))
    run(go(), 60)


def test_the_new_audio_settings_default_and_survive_an_old_config(tmp_path):
    cfg = Config.load(tmp_path / "fresh")
    assert cfg.audio.input_gain == 1.0 and cfg.audio.calibrated is False and cfg.audio.max_bitrate_kbps == 24 and "voice_fx" not in cfg.audio
    home = tmp_path / "old"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"config_version": 2, "audio": {"mode": "open", "master_volume": 0.5, "voice_fx": "phone"}}))
    old = Config.load(home)
    assert old.audio.input_gain == 1.0 and old.audio.max_bitrate_kbps == 24 and old.audio.master_volume == 0.5
    assert "voice_fx" not in old.audio, "the telephone effect setting was taken out: an old file loses it"


def test_commands_from_an_old_session_are_not_replayed(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            # a stale command file is already lying in the game folder when the app starts
            game_dir = tmp_path / "Alice" / "game" / "CustomData"
            game_dir.mkdir(parents=True)
            old = int((time.time() - 7200) * 100)
            control_path(game_dir / "eqvoice-in-Alice.txt").write_text(build_control(1, 1, "Alice", [(old, "mute", "1")]), encoding="utf-8")
            a = await sc.add("Alice", at=(0, 0))
            game = sc.games["Alice"]
            assert game.in_path.parent == game_dir
            await until(lambda: a.ui_control is not None, 10, "the reader started")
            await until(lambda: status_path(game.in_path).exists(), 10, "status")
            time.sleep(0.6)
            assert a.cfg.audio.muted is False and a.ui_commands == 0, "an hour-old mute does not mute you"
    run(go(), 60)


def test_open_mic_is_the_default_and_f9_the_key(tmp_path):
    cfg = Config.load(tmp_path / "fresh")
    assert cfg.audio.mode == "open" and cfg.audio.ptt_code == "F9" and cfg.audio.ptt_label == "F9"
    assert cfg.audio.master_volume == 1.0 and cfg.config_version == 5


def test_an_old_config_that_never_chose_push_to_talk_moves_to_the_new_defaults(tmp_path):
    home = tmp_path / "old"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"audio": {"mode": "ptt", "ptt_code": "Backquote", "ptt_label": "`", "muted": True}}))
    cfg = Config.load(home)
    assert cfg.audio.mode == "open" and cfg.audio.ptt_code == "F9" and cfg.audio.ptt_label == "F9"
    assert cfg.audio.muted is True, "everything else is kept"
    cfg.save()
    assert json.loads((home / "config.json").read_text())["config_version"] == 5


def test_a_player_who_chose_their_own_key_keeps_push_to_talk(tmp_path):
    home = tmp_path / "mine"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"audio": {"mode": "ptt", "ptt_code": "KeyV", "ptt_label": "V"}}))
    cfg = Config.load(home)
    assert cfg.audio.mode == "ptt" and cfg.audio.ptt_code == "KeyV"


def test_an_old_config_that_already_said_open_stays_open_and_a_new_one_is_not_touched(tmp_path):
    home = tmp_path / "open"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"audio": {"mode": "open", "ptt_code": "Backquote"}}))
    assert Config.load(home).audio.ptt_code == "Backquote", "only a push-to-talk setting was migrated"
    home2 = tmp_path / "v2"
    home2.mkdir()
    (home2 / "config.json").write_text(json.dumps({"config_version": 2, "audio": {"mode": "ptt", "ptt_code": "Backquote"}}))
    cfg = Config.load(home2)
    assert cfg.audio.mode == "ptt", "a version-2 file that chose push to talk is respected"


def test_a_damaged_config_falls_back_to_the_defaults(tmp_path):
    home = tmp_path / "bad"
    home.mkdir()
    (home / "config.json").write_text("{not json")
    cfg = Config.load(home)
    assert cfg.audio.mode == "open" and (home / "config.json.bad").exists()


def test_an_old_config_gets_the_wider_range_unless_the_player_chose_their_own(tmp_path):
    home = tmp_path / "v2"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"config_version": 2, "proximity": {"say_radius": 41.0, "full_radius": 10.0, "report_radius": 50.0},
                                                  "roster": {"report": 60.0}}))
    cfg = Config.load(home)
    assert (cfg.proximity.full_radius, cfg.proximity.report_radius, cfg.roster.report) == (30.0, 95.0, 100.0), "the saved old defaults move on"
    assert cfg.proximity.peak_radius == 85.0 and cfg.audio.boost == "normal" and cfg.config_version == 5
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    (chosen / "config.json").write_text(json.dumps({"config_version": 2, "proximity": {"full_radius": 12.0, "report_radius": 70.0}, "roster": {"report": 80.0}}))
    mine = Config.load(chosen)
    assert (mine.proximity.full_radius, mine.proximity.report_radius, mine.roster.report) == (12.0, 70.0, 80.0), "values the player changed are left alone"
    again = tmp_path / "v3"
    again.mkdir()
    (again / "config.json").write_text(json.dumps({"config_version": 3, "proximity": {"full_radius": 10.0}}))
    assert Config.load(again).proximity.full_radius == 10.0, "a version-3 file that says 10 means 10"
    v3 = tmp_path / "v3old"
    v3.mkdir()
    (v3 / "config.json").write_text(json.dumps({"config_version": 3, "proximity": {"full_radius": 15.0}}))
    assert Config.load(v3).proximity.full_radius == 30.0, "the version-3 default (doubled inside 15) becomes the version-4 one (full volume to 30)"
    v4 = tmp_path / "v4"
    v4.mkdir()
    (v4 / "config.json").write_text(json.dumps({"config_version": 4, "proximity": {"full_radius": 15.0}}))
    assert Config.load(v4).proximity.full_radius == 15.0, "a version-4 file that says 15 means 15"


def test_a_fake_microphone_is_not_run_through_the_keyboard_filter():
    from eqvoice_client.__main__ import overrides, parse
    assert overrides(parse(["--fake-media"]))["audio"]["keyboard_filter"] is False, "its beeps are exactly what the filter drops"
    assert "audio" not in overrides(parse([])), "a real microphone is left to the player's setting (on by default)"


def test_the_data_folder_of_the_old_name_is_carried_over_once(tmp_path, monkeypatch):
    from eqvoice_client import config as cfgmod
    monkeypatch.delenv("EQVOICE_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(cfgmod.sys, "platform", "win32")
    old = tmp_path / "OctoVoice"
    (old / "logs").mkdir(parents=True)
    (old / "config.json").write_text('{"join_code": "abc"}')
    (old / "identity.json").write_text("key")
    home = cfgmod.default_home()
    assert home == tmp_path / "EqVoice" and not old.exists()
    assert (home / "config.json").read_text() == '{"join_code": "abc"}' and (home / "identity.json").read_text() == "key", "settings and identity come with it"
    assert cfgmod.default_home() == home, "and a second look changes nothing"
    # both exist (the player already ran the new one): nothing is moved over anything
    (tmp_path / "OctoVoice").mkdir()
    (tmp_path / "OctoVoice" / "config.json").write_text("{}")
    assert cfgmod.default_home() == home and (tmp_path / "OctoVoice" / "config.json").exists()
    assert cfgmod.default_home() == home and (home / "config.json").read_text() == '{"join_code": "abc"}'
