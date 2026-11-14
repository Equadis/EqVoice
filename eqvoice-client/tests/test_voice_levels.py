"""How you sound, as far as the app and the game are concerned: the page steadies the microphone's level into 0 (nothing), 1 (speaking) and 2 (a raised
voice) and reports it; the app tells the game only while someone can actually hear you."""
import asyncio

from scene import Scene, run, until
from test_ingame_panel import read_status, send


def mic(level, vl):
    return {"t": "mic", "state": "ok", "label": "Test mic", "level": level, "tx": True, "pcs": 0, "vl": vl}


def test_how_you_sound_reaches_the_game_only_while_you_can_be_heard(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None and read_status(game).fields.get("vl") == "0", 10, "status with vl")
            a._on_ui({"cmd": "set_mode", "mode": "open"})
            await until(lambda: a.tx_enabled, 5, "open microphone")
            await pa.ws.send_json(mic(0.4, 1))
            await until(lambda: read_status(game).fields["vl"] == "1", 5, "speaking")
            await pa.ws.send_json(mic(0.9, 2))
            await until(lambda: read_status(game).fields["vl"] == "2", 5, "a raised voice")
            a._on_ui({"cmd": "mute", "value": True})
            await until(lambda: read_status(game).fields["vl"] == "0", 5, "muted: nobody hears you, so the icon does not say you are shouting")
            a._on_ui({"cmd": "mute", "value": False})
            await until(lambda: read_status(game).fields["vl"] == "2", 5, "unmuted: back")
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Test mic", "level": 0.0, "tx": True, "pcs": 0, "vl": "nonsense"})
            await until(lambda: read_status(game).fields["vl"] == "0", 5, "a damaged value is nothing, not an error")
    run(go(), 90)


DEVICES = {"t": "devices",
           "inputs": [{"id": "default", "label": "Default - Microphone (Microphone A)"}, {"id": "idA", "label": "Microphone (Microphone A)"},
                      {"id": "idB", "label": "Microphone (Headset Mic (USB))"}],
           "outputs": [{"id": "default", "label": "Default - Speakers (Realtek(R) Audio)"}, {"id": "idR", "label": "Speakers (Realtek(R) Audio)"},
                       {"id": "idS", "label": "Monitor (HDMI)"}]}


def test_the_game_is_told_which_microphones_and_speakers_there_are_and_picks_one_by_name(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            await pa.ws.send_json(DEVICES)
            await until(lambda: read_status(game).devices["in"] == ["Microphone A", "Headset Mic (USB)"], 10, "the game is told the microphones, by their own names, without the system aliases")
            st = read_status(game)
            assert st.devices["out"] == ["Realtek(R) Audio", "Monitor (HDMI)"]
            assert st.defaults == {"in": "Microphone A", "out": "Realtek(R) Audio"}, "and told what the system default is now"
            assert st.fields["din"] == "0" and st.fields["dout"] == "0", "nothing chosen: the system's own"

            send(game, ("micdev", "Headset Mic (USB)"))
            await until(lambda: a.cfg.audio.input == "idB" and pa.named("input") and pa.named("input")[-1]["id"] == "idB", 5, "the app and the page use it")
            await until(lambda: read_status(game).fields["din"] == "2", 5, "and the game is told which one is in use")

            send(game, ("spkdev", "Monitor (HDMI)"))
            await until(lambda: a.cfg.audio.output == "idS" and pa.named("output") and pa.named("output")[-1]["id"] == "idS", 5, "the speakers too")
            await until(lambda: read_status(game).fields["dout"] == "2", 5, "")

            send(game, ("micdev", "No such microphone"))
            send(game, ("spkdev", ""))
            await until(lambda: a.cfg.audio.output == "", 5, "an empty name is the system default")
            assert a.cfg.audio.input == "idB", "a name the PC does not have changes nothing"
            send(game, ("micdev", "default"))
            await until(lambda: a.cfg.audio.input == "", 5, "'default' is the system's own")
            await until(lambda: read_status(game).fields["din"] == "0", 5, "")
            send(game, ("micdev", "headset mic (usb)"))
            await until(lambda: a.cfg.audio.input == "idB", 5, "the name is matched without regard to case")
    run(go(), 90)


def test_the_game_gets_the_microphones_level_only_while_it_is_watching(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Test mic", "level": 0.5, "tx": True, "pcs": 0})
            await until(lambda: a.mic.get("level") == 0.5, 5, "the page's level")
            await asyncio.sleep(0.8)
            assert read_status(game).fields["lvl"] == "0", "nobody is looking at a meter: the level is not sent (the file is not rewritten ten times a second)"
            send(game, ("watch", "1"))
            await until(lambda: read_status(game).fields["lvl"] == "50", 5, "the meter is on: the level is sent")
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Test mic", "level": 0.8, "tx": True, "pcs": 0})
            await until(lambda: read_status(game).fields["lvl"] == "80", 5, "and follows it")
            await until(lambda: read_status(game).fields["lvl"] == "0", 8, "and stops by itself when the game stops asking (a closed window cannot say so)")
            send(game, ("watch", "1"))
            await until(lambda: read_status(game).fields["lvl"] == "80", 5, "asked again")
            send(game, ("watch", "0"))
            await until(lambda: read_status(game).fields["lvl"] == "0", 5, "and told to stop")
    run(go(), 90)


def gate_mic(**kw):
    m = {"t": "mic", "state": "ok", "label": "Test mic", "level": 0.4, "tx": True, "pcs": 0, "vl": 1, "gate": True,
         "glevel": -31.4, "gthr": -52.0, "gopen": True, "ldb": -45}
    m.update(kw)
    return m


def test_the_game_gets_the_level_of_the_signal_the_gate_judges_with_the_bar_and_whether_it_is_open(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            await pa.ws.send_json(gate_mic())
            await until(lambda: a.mic.get("glevel") == -31.4, 5, "the page's gate level")
            await asyncio.sleep(0.8)
            f = read_status(game).fields
            assert "gl" not in f and "gt" not in f and "go" not in f, "nobody is looking at the meter: none of it is sent"
            send(game, ("watch", "1"))
            await until(lambda: read_status(game).fields.get("gl") == "-31", 5, "the meter is on: the gate's own level (dB), not the microphone's")
            f = read_status(game).fields
            assert f["gt"] == "-52" and f["go"] == "1", f
            await pa.ws.send_json(gate_mic(glevel=-72.0, gopen=False))
            await until(lambda: read_status(game).fields.get("gl") == "-72", 5, "the level keeps moving while the gate is shut: that is what the bar compares")
            assert read_status(game).fields["go"] == "0"
            await pa.ws.send_json(gate_mic(gate=False, glevel=None, gthr=None, gopen=None, ldb=-40))
            await until(lambda: read_status(game).fields.get("gl") == "-40", 5, "no gate running: the microphone's own level, on the same track")
            f = read_status(game).fields
            assert "gt" not in f and "go" not in f, "and no bar and no open/shut"
            await pa.ws.send_json(gate_mic(glevel=5.0))
            await until(lambda: read_status(game).fields.get("gl") == "0", 5, "levels are kept on the track (never above 0 dB)")
    run(go(), 90)


def test_a_meter_on_screen_gets_the_level_many_times_a_second_not_at_the_tick(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            pa, game = sc.pages["Alice"], sc.games["Alice"]
            await until(lambda: a.ui_control is not None, 10, "panel files")
            send(game, ("watch", "1"))
            await pa.ws.send_json(gate_mic(glevel=-50.0))
            await until(lambda: read_status(game).fields.get("gl") == "-50", 5, "the meter is on")
            seen = set()
            for i in range(14):
                await pa.ws.send_json(gate_mic(glevel=-60.0 + i))
                await asyncio.sleep(0.05)
                seen.add(read_status(game).fields.get("gl"))
            print("\n[meter] distinct levels the game saw in 0.7 s: %d" % len(seen))
            assert len(seen) >= 8, "the file follows the page about every 50 ms (the 0.25 s tick alone would show at most 3): %r" % sorted(seen)
    run(go(), 90)


def test_the_save_logs_button_makes_the_zip_and_the_game_is_told_its_name(tmp_path, monkeypatch):
    from eqvoice_client import installer
    made = []

    def fake_report(cfg, dest_dir=None):
        p = tmp_path / "EqVoice-logs-20261108-101010.zip"
        p.write_bytes(b"PK")
        made.append(p)
        return p
    monkeypatch.setattr(installer, "make_report", fake_report)

    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            game = sc.games["Alice"]
            told = []
            a.notify_user = lambda title, text: told.append(text)
            await until(lambda: a.ui_control is not None, 10, "panel files")
            assert "rpt" not in read_status(game).fields
            send(game, ("report", "1"))
            await until(lambda: read_status(game).fields.get("rpt") == "EqVoice-logs-20261108-101010.zip", 5, "the game is told the file's name")
            assert made and told and "Discord" in told[0] and "EqVoice-logs-20261108-101010.zip" in told[0], told
    run(go(), 90)
