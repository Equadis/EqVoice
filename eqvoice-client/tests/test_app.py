"""The whole client against a real hub over real sockets, with a simulated game and a scripted media page.

Every scenario here walks the full pipeline: game -> bridge file -> client -> hub -> peers -> media commands.
"""
import asyncio
import json
import time

import aiohttp
import pytest

from eqvoice_client.app import App
from eqvoice_client.bridge.inbox import parse_text
from eqvoice_client.bridge.sim import SimGame, SimSource
from eqvoice_client.peers import voice_gain
from eqvoice_signalling.hub import Hub, Limits
from eqvoice_signalling.server import make_app
from aiohttp.test_utils import TestServer

from scene import FAR, REALM, TOKEN, FakePage, Scene, make_cfg, phantoms, pstate, run, until


# ---------------------------------------------------------------------- http

def test_auth_and_origin(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            app = await sc.add("Alice", page=False)
            base = "http://127.0.0.1:%d" % app.port
            async with aiohttp.ClientSession() as s:
                async with s.get(base + "/") as r:
                    assert r.status == 200 and "EqVoice" in await r.text()
                async with s.get(base + "/static/app.js") as r:
                    assert r.status == 200
                async with s.get(base + "/static/../config.json") as r:
                    assert r.status == 404
                async with s.get(base + "/api/state") as r:
                    assert r.status == 403, "no token, no state"
                async with s.get(base + "/api/diag?token=wrong") as r:
                    assert r.status == 403
                async with s.get(base + "/api/state?token=" + TOKEN, headers={"Origin": "http://evil.example"}) as r:
                    assert r.status == 403, "a web page on another origin cannot read it"
                async with s.get(base + "/api/state?token=" + TOKEN) as r:
                    assert r.status == 200
                async with s.post(base + "/api/ptt", json={"down": True}) as r:
                    assert r.status == 403
                async with s.post(base + "/api/sim", json={"cmd": "sim_add", "name": "Bobby"}) as r:
                    assert r.status == 403
                try:
                    await s.ws_connect(base + "/ws/ui?token=nope")
                    assert False, "ws without token must be refused"
                except aiohttp.WSServerHandshakeError as e:
                    assert e.status == 403
    run(go())


# ------------------------------------------------------------ the main journey

def test_unrelated_characters_register_meet_talk_and_part(tmp_path):
    """The real-WoW test path, in simulation: register, stand apart, walk close, talk, walk away."""
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice"), await sc.add("Bobby")
            pa, pb = sc.pages["Alice"], sc.pages["Bobby"]

            # 1. both register, as the characters the GAME reported, and learn of each other
            await until(lambda: sc.hub.stats()["sessions"] == 2, 10, "both registered")
            await until(lambda: "Bobby" in a.roster and "Alice" in b.roster, 5, "rosters")
            assert {s.character for s in sc.hub.by_sid.values()} == {"Alice", "Bobby"}
            assert a.ident.character == "Alice" and a.ident.realm == REALM

            # 2. the roster reaches the game through the file; the game answers; nobody is near
            await until(lambda: sc.games["Alice"].roster == ["Bobby"], 5, "game read the roster file")
            await until(lambda: a.monitor.view().state and a.monitor.view().state.roster_fresh, 5, "game says roster fresh")
            assert a.peers.peers == {} and not pa.named("open"), "300 yards apart: nothing at all"

            # 3. walk within range: both clients report mutual proximity, voice connects
            sc.walk_to("Alice", "Bobby", 30)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 15, "connected")
            open_a, open_b = pa.named("open")[0], pb.named("open")[0]
            assert open_a["initiator"] != open_b["initiator"] and open_a["ice"] == [{"urls": ["stun:example.org"]}]
            assert open_a["ep"] == open_b["ep"]
            assert len(sc.hub.links) == 1, "the hub confirmed the pair"
            await until(lambda: pa.last_gain()["send"] is True, 3, "the page is told to play and transmit")
            prox = a.cfg.proximity
            curve = a.peers.cfg.curve
            assert pa.last_gain()["value"] == pytest.approx(voice_gain(30, curve), abs=0.02) and pa.last_gain()["value"] == pytest.approx(1.0, abs=0.02), "30 yd is full volume"

            # 4. distance controls volume, in both directions
            sc.walk_to("Alice", "Bobby", 2)
            want2 = voice_gain(2, curve)
            await until(lambda: abs(pa.last_gain()["value"] - want2) < 0.02 and abs(pb.last_gain()["value"] - want2) < 0.02, 6,
                        "boosted right next to each other (%.2f times a voice's own loudness at 2 yd)" % want2)
            assert 1.7 < want2 < 2.0
            sc.walk_to("Alice", "Bobby", 6)
            want6 = voice_gain(6, curve)
            await until(lambda: abs(pa.last_gain()["value"] - want6) < 0.02, 6, "and less so at 6 yd (%.2f): it scales with the distance" % want6)
            assert 1.0 < want6 < want2
            sc.walk_to("Alice", "Bobby", 65)
            want65 = voice_gain(65, curve)
            await until(lambda: abs(pa.last_gain()["value"] - want65) < 0.05, 6, "quiet at 65 yards (%.2f)" % want65)
            assert want65 < 0.25
            st = pa.states[-1] if pa.states else a.build_state()
            assert st["identity"]["character"] == "Alice"

            # 5. walk away: fade out first, close later, hub link removed
            pa.cmds.clear(); pb.cmds.clear()
            sc.vanish("Bobby")
            await until(lambda: pstate(a, "Bobby") == "OUT_OF_RANGE_GRACE", 6, "A: out of range")
            await until(lambda: pa.last_gain()["value"] == 0.0 and pa.last_gain()["send"] is False, 3, "faded out, not transmitting")
            assert not pa.named("close"), "the connection is kept for a while"
            await until(lambda: pstate(a, "Bobby") in ("DISCONNECTED", None) and pa.named("close"), 8, "A: torn down later")
            await until(lambda: pb.named("close"), 8, "B: torn down too")
            assert len(sc.hub.links) == 0

            # 6. come back: a fresh connection
            ep_old = open_a["ep"]
            sc.walk_to("Alice", "Bobby", 12)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "reconnected")
            assert pa.named("open")[-1]["ep"] > ep_old or pb.named("open")[-1]["ep"] > ep_old
    run(go(), 120)


def test_walking_back_quickly_reuses_the_connection(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            pa = sc.pages["Alice"]
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "connected")
            opens = len(pa.named("open"))
            sc.vanish("Bobby")
            await until(lambda: pstate(a, "Bobby") == "OUT_OF_RANGE_GRACE", 6, "faded")
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 8, "back")
            assert len(pa.named("open")) == opens and not pa.named("close"), "no new WebRTC negotiation for a short absence"
    run(go(), 90)


def test_alice_18_bob_19_connects_and_one_side_60_disconnects(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(100, 0))
            sc.world.pair("Alice", "Bobby", 18, 19)         # Alice sees Bob at 18, Bob sees Alice at 19
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "connected")
            link = next(iter(sc.hub.links.values()))
            assert link.dist == pytest.approx(18.5)
            sc.world.see("Alice", "Bobby", 60)               # Alice's game now says 60; Bob's still says 19
            await until(lambda: len(sc.hub.links) == 0, 10, "the hub ends the link: the two reports disagree")
            await until(lambda: pstate(b, "Alice") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED", None), 6,
                        "Bob fades Alice out although his own game still sees her at 19")
            await until(lambda: pstate(a, "Bobby") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED", None), 6, "and Alice fades Bob")
            sc.world.see("Alice", "Bobby", 17)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "agreement restores it")
    run(go(), 120)


def test_one_sided_proximity_never_connects(tmp_path):
    """Alice's game can see Bob, Bob's cannot see Alice (say, Alice is hidden behind a loading screen)."""
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            sc.world.see("Bobby", "Alice", 150)             # not loaded for Bob
            await until(lambda: pstate(a, "Bobby") == "NEARBY_PENDING", 10, "Alice sees Bob")
            await asyncio.sleep(3.0)
            assert pstate(a, "Bobby") == "NEARBY_PENDING" and pstate(b, "Alice") is None
            assert len(sc.hub.links) == 0 and not sc.pages["Alice"].named("open")
            assert not sc.pages["Alice"].named("gain")
    run(go(), 60)


def test_a_known_name_alone_is_not_enough(tmp_path):
    """Everyone on the roster is known by name; only mutual proximity opens a connection."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(FAR, 0)); await sc.add("Carol", at=(2 * FAR, 0))
            await until(lambda: len(a.roster) == 2, 10, "roster")
            await asyncio.sleep(2.0)
            assert len(sc.hub.links) == 0 and a.peers.peers == {}
            assert sorted(a.roster.names()) == ["Bobby", "Carol"]
    run(go(), 60)


# ------------------------------------------------------------- fail closed

def test_frozen_game_fails_closed_and_recovers(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            pa = sc.pages["Alice"]
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "connected")
            sc.games["Alice"].frozen = True                      # WoW minimised / hung on Alice's PC
            await until(lambda: a.monitor.view().status == "stale", 4, "stale")
            assert pstate(a, "Bobby") == "CONNECTED", "an alt-tab does not cut the call"
            assert "No new frames" in a.build_state()["bridge"]["hint"]
            await until(lambda: a.monitor.view().status == "lost", 6, "lost")
            await until(lambda: pstate(a, "Bobby") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED"), 6, "Alice fades out")
            assert "safety" in a.build_state()["bridge"]["hint"]
            assert a.gate[0] is False
            await until(lambda: pstate(b, "Alice") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED"), 8,
                        "Bob also fades: Alice stopped vouching for the pair")
            sc.games["Alice"].frozen = False
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 25, "recovers")
    run(go(), 120)


def test_a_game_that_stays_gone_leaves_the_roster(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(FAR, 0))
            await until(lambda: sc.hub.stats()["sessions"] == 2 and "Alice" in b.roster, 10, "both online")
            sc.games["Alice"].frozen = True                      # the game crashed
            await until(lambda: a.ident is None, 15, "the client stops speaking for a character that is gone")
            await until(lambda: "Alice" not in b.roster, 5, "and the hub drops her at once")
            assert sc.hub.stats()["sessions"] == 1
            assert not sc.games["Alice"].in_path.exists(), "the roster file is removed so a restarted game starts clean"
            sc.games["Alice"].frozen = False
            await until(lambda: a.ident is not None and "Alice" in b.roster, 15, "she is back when the game is")
    run(go(), 90)


def test_disabled_addon_or_missing_lookups_means_silence(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            sc.games["Alice"].enabled = False                    # /ev off
            await until(lambda: pstate(a, "Bobby") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED"), 8, "fades")
            assert "switched off" in a.build_state()["bridge"]["hint"]
            assert a.ident is not None, "she stays online (she may switch it back on)"
            sc.games["Alice"].enabled = True
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 25, "back on")
            sc.games["Alice"].lookups = False
            await until(lambda: pstate(a, "Bobby") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED"), 8, "no lookups: fades")
            assert "ClassicAPI" in a.build_state()["bridge"]["hint"]
    run(go(), 120)


def test_losing_the_signalling_server_fails_closed(tmp_path):
    async def go():
        sc = Scene(tmp_path)
        await sc.__aenter__()
        try:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            pa = sc.pages["Alice"]
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "connected")
            await sc.server.close()                              # the hub dies
            await until(lambda: a.signalling.state != "registered", 5, "client notices")
            await asyncio.sleep(1.5)
            assert pstate(a, "Bobby") == "CONNECTED", "a blip must not cut the call"
            await until(lambda: pstate(a, "Bobby") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED"), 10,
                        "after fail_closed_s everyone fades out")
            await until(lambda: pa.last_gain()["value"] == 0.0, 3, "volume at zero")
            names = parse_text(sc.games["Alice"].in_path.read_text()).names
            assert names == [], "and the game is told to look nobody up while we cannot vouch for anyone"
        finally:
            sc.server = TestServer(make_app(Hub()))              # so __aexit__ can close something
            await sc.server.start_server()
            await sc.__aexit__(None, None, None)
    run(go(), 90)


# ------------------------------------------------- identity: logout, switch, dup

def test_logout_removes_the_character_and_login_replaces_it(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            pb = sc.pages["Bobby"]
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 20, "connected")
            sc.games["Alice"].logout()
            await until(lambda: "Alice" not in b.roster, 5, "gone from the roster at once")
            assert sc.hub.stats()["sessions"] == 1 and a.ident is None
            await until(lambda: pstate(b, "Alice") is None and pb.named("close"), 5, "Bobby's call with her is closed")
            # she logs in with another character, standing at the same spot
            sc.world.add("Mage", 10, 0)
            sc.vanish("Alice")
            sc.games["Alice"].login("Mage")
            await until(lambda: "Mage" in b.roster and a.ident and a.ident.character == "Mage", 10, "registered as Mage")
            assert sorted(s.character for s in sc.hub.by_sid.values()) == ["Bobby", "Mage"]
            await until(lambda: pstate(b, "Mage") == "CONNECTED", 20, "and talks as Mage")
    run(go(), 120)


def test_switching_character_without_logout_still_leaves_the_old_name(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(FAR, 0))
            await until(lambda: "Alice" in b.roster, 10, "online")
            old_sid = a.ident.sid
            sc.world.add("Mage", 0, 0)
            sc.games["Alice"].login("Mage")
            await until(lambda: "Mage" in b.roster and "Alice" not in b.roster, 10, "swapped on the roster")
            assert a.ident.sid != old_sid and a.ident.character == "Mage"
    run(go(), 60)


def test_two_clients_claiming_one_character_cannot_both_be_online(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await until(lambda: a.signalling.registered, 10, "registered")
            # a second PC (new home, new key) running the same character
            sc.world.add("Alice2", FAR, 0)
            game = SimGame(sc.world, "Alice", REALM, tmp_path / "second" / "game", login_id=999, interval=0.1)
            second = App(make_cfg(tmp_path / "second", sc.url, "Alice"), SimSource(game, poll_hz=20), launch_browser=False)
            await second.start()
            try:
                await until(lambda: "name_in_use" in second.signalling.last_error, 10, "the second is refused")
                assert a.signalling.registered and sc.hub.stats()["sessions"] == 1, "the first holder is untouched"
                assert not second.signalling.registered
            finally:
                await second.stop()
    run(go(), 60)


# ------------------------------------------------------------- the roster file

def test_roster_file_is_bound_to_the_character_and_removed_on_exit(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(FAR, 0)); await sc.add("Carol", at=(2 * FAR, 0))
            game = sc.games["Alice"]
            await until(lambda: game.in_path.exists() and parse_text(game.in_path.read_text()).names == ["Bobby", "Carol"], 10,
                        "the roster file lists the other online characters")
            got = parse_text(game.in_path.read_text())
            assert got.character == "Alice", "written FOR the character the game reported"
            assert got.config["checks"] == 80 and got.config["radius"] == 41
            seq = got.seq
            await until(lambda: parse_text(game.in_path.read_text()).seq != seq, 4, "rewritten regularly even when unchanged")
            await a.stop()
            sc.apps.pop("Alice")
            assert not game.in_path.exists(), "gone with the client: the game stops looking people up"
    run(go(), 60)


def test_the_roster_carries_names_only(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(FAR, 0))
            await until(lambda: "Bobby" in a.roster, 10, "roster")
            blob = json.dumps({"roster": a.roster.by_key, "state": a.build_state()})
            assert "127.0.0.1" not in blob.replace(a.signalling.url, ""), "no addresses of other players anywhere"
            assert "10.0." not in blob
    run(go(), 60)


# --------------------------------------------------------------- scale/metrics


def test_a_crowded_realm_is_handled_with_a_bounded_budget(tmp_path):
    async def go():
        limits = Limits(leave_grace=1.0, registers_per_ip_per_minute=100, max_conns_per_ip=100)
        async with Scene(tmp_path, limits=limits) as sc:
            phantoms(sc.hub, 399)
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(10, 0))
            await until(lambda: len(a.roster) == 400, 15, "400 other characters on the roster")
            game = sc.games["Alice"]
            await until(lambda: game.in_path.exists() and len(parse_text(game.in_path.read_text()).names) == 400, 10,
                        "the whole roster is handed to the game")
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 25, "and the one real neighbour is still found and connected")
            m = a.metrics()
            assert m["roster"] == 400 and m["checked"] <= 81, "the game checks a bounded number of names per scan"
            assert m["loaded"] >= 1 and m["nearby"] == 1 and m["connected"] == 1
            t0 = time.perf_counter()
            for _ in range(20):
                a._tick()
            per_tick = (time.perf_counter() - t0) / 20
            print("[perf] a client tick with a 400-name roster: %.2f ms" % (per_tick * 1000))
            assert per_tick < 0.05, "a tick with a 400-name roster took %.1f ms" % (per_tick * 1000)
    run(go(), 120)


def test_metrics_and_state_describe_the_pipeline(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(15, 0))
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            await until(lambda: a.metrics()["roster_acked"], 5, "the game acknowledges the roster file")
            st = a.build_state()
            m = st["metrics"]
            assert m["roster"] == 1 and m["loaded"] == 1 and m["missed"] == 0 and m["nearby"] == 1 and m["connected"] == 1
            assert m["addon_interval_ms"] == 100 and m["bridge_interval_ms"] > 0 and m["proximity_msgs"] >= 1
            assert st["gate"] == {"open": True, "reason": ""} and st["identity"]["character"] == "Alice"
            peer = st["peers"][0]
            assert peer["name"] == "Bobby" and peer["dist"] == 15.0 and peer["linked"] and peer["state"] == "CONNECTED"
            assert st["proximity"] == {"say_radius": 41.0, "full_radius": 30.0, "peak_radius": 85.0, "near_gain": 2.0,
                                       "curve": {"boost": 2.0, "boost_r": 0.0, "boost_fade": 10.0, "full": 30.0, "full_loud": 45.0, "end": 85.0, "power": 1.6}}
            assert st["voice"]["boost"] == "normal" and st["voice"]["auto_level"] is True
    run(go(), 60)


# ------------------------------------------------------------ voice controls

def test_voice_controls_and_ptt(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            pa = FakePage(a, auto=False)
            await pa.connect(sc.session)
            await until(lambda: pa.cmds, 3, "config")
            cfgcmd = pa.named("config")[0]
            assert cfgcmd["relay_only"] is False and cfgcmd["input"] == ""
            tx = lambda: [c["enabled"] for c in pa.cmds if c["cmd"] == "tx"]       # noqa: E731
            assert tx()[-1] is False, "push-to-talk starts silent"
            base = "http://127.0.0.1:%d" % a.port
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": True})
            await until(lambda: tx()[-1] is True, 3, "tx on while the key is down")
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": False})
            await asyncio.sleep(0.1)
            assert tx()[-1] is True, "release delay keeps the end of the word"
            await until(lambda: tx()[-1] is False, 3, "tx off after release")
            await pa.ws.send_json({"t": "ui", "cmd": "mute", "value": True})
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": True})
            await asyncio.sleep(0.3)
            assert tx()[-1] is False, "muted: key does nothing"
            await pa.ws.send_json({"t": "ui", "cmd": "mute", "value": False})
            await until(lambda: tx()[-1] is True, 3, "unmute with key held")
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": False})
            await until(lambda: tx()[-1] is False, 3, "off")
            await pa.ws.send_json({"t": "ui", "cmd": "set_mode", "mode": "open"})
            await until(lambda: tx()[-1] is True, 3, "open mic transmits")
            await pa.ws.send_json({"t": "ui", "cmd": "deafen", "value": True})
            await until(lambda: tx()[-1] is False, 3, "deafen silences the mic")
            assert pa.named("deafen")[-1]["value"] is True
            await pa.ws.send_json({"t": "ui", "cmd": "ptt_key", "code": "KeyV"})
            await pa.ws.send_json({"t": "ui", "cmd": "ptt_key", "button": 3})
            await pa.ws.send_json({"t": "ui", "cmd": "ptt_key", "button": 0})        # left click is refused
            await asyncio.sleep(0.2)
            assert a.cfg.audio.ptt_vk == 0x05 and "Mouse 4" in a.cfg.audio.ptt_label
            await pa.ws.send_json({"t": "devices", "inputs": [{"id": "m1", "label": "Mic"}], "outputs": [{"id": "s1", "label": "Spk"}]})
            await pa.ws.send_json({"t": "ui", "cmd": "set_input", "id": "m1"})
            await pa.ws.send_json({"t": "ui", "cmd": "set_output", "id": "s1"})
            await pa.ws.send_json({"t": "ui", "cmd": "peer_volume", "sid": "BBBBBBBB", "value": 9})
            await until(lambda: pa.named("volume"), 3, "volume forwarded")
            assert pa.named("volume")[-1]["value"] == 1.5, "volume is clamped"
            assert a.cfg.audio.input == "m1" and a.cfg.audio.output == "s1"
            await until(lambda: a.build_state()["devices"]["inputs"], 3, "devices stored")
            await pa.close()
        saved = json.loads((tmp_path / "Alice" / "config.json").read_text())
        assert saved["audio"]["input"] == "m1" and saved["audio"]["ptt_vk"] == 5, "settings persist"
    run(go(), 60)


def test_page_disconnect_drops_calls_and_reconnect_restores(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(10, 0))
            pa = sc.pages["Alice"]
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            await pa.close()                                     # the browser window was closed
            await until(lambda: pstate(a, "Bobby") in ("DISCONNECTED", "NEARBY_PENDING"), 5, "A: calls dropped")
            assert not a.peers.media_ready
            opens = len(pa.named("open"))
            await asyncio.sleep(2.5)
            assert len(pa.named("open")) == opens, "no new attempts without a media engine"
            pa2 = FakePage(a)
            await pa2.connect(sc.session)
            sc.pages["Alice"] = pa2
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 25, "reconnects through the new page")
    run(go(), 90)


def test_sim_api_and_diag(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            base = "http://127.0.0.1:%d" % a.port
            await until(lambda: a.ident is not None, 5, "the client knows who it is")
            r = await sc.session.post(base + "/api/sim?token=" + TOKEN, json={"cmd": "sim_add", "name": "Bobby", "x": 9, "y": 0})
            assert r.status == 200 and "Bobby" in sc.world.chars
            r = await sc.session.post(base + "/api/sim?token=" + TOKEN, json={"cmd": "sim_see", "target": "Bobby", "yards": 22})
            assert r.status == 200 and sc.world.distance("Alice", "Bobby") == 22.0
            r = await sc.session.post(base + "/api/sim?token=" + TOKEN, json={"cmd": "nonsense"})
            assert r.status == 400
            r = await sc.session.post(base + "/api/sim?token=" + TOKEN, json={"cmd": "sim_freeze", "value": True})
            assert sc.games["Alice"].frozen is True
            d = await (await sc.session.get(base + "/api/diag?token=" + TOKEN)).json()
            for k in ("version", "config", "bridge", "roster", "signalling", "peers", "voice", "log", "ice_servers", "pages", "metrics", "gate"):
                assert k in d, k
            assert d["config"]["ui"]["token"] == "***", "secrets are redacted from diagnostics"
            assert TOKEN not in json.dumps(d)
            assert d["log"], "recent log lines are included"
            assert d["identity"]["character"] == "Alice"
    run(go(), 60)


# ------------------------------------------------------------ the voice engine is what it should be

NORMAL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
HIDDEN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 HeadlessChrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"


def _log():
    from eqvoice_client.logbuf import RING
    return "\n".join(RING.tail(600))


def _clear_log():
    from eqvoice_client.logbuf import RING
    RING.records.clear()


def test_small_numbers_stay_readable_in_trace_lines():
    from eqvoice_client.trace import fmt
    assert fmt(0.004) == "0.004" and fmt(0.0) == "0.0" and fmt(12.34) == "12.3" and fmt(0.75) == "0.75" and fmt(-0.02) == "-0.02"


def test_a_hidden_test_browser_or_fake_microphone_is_called_out(tmp_path):
    """The real-game test once heard a beep instead of a voice: a leftover test browser (headless, fake microphone) had become the
    voice engine. Nothing said so. Now the client names both facts the moment the page says hello and reports its microphone."""
    async def go():
        async with Scene(tmp_path) as sc:
            _clear_log()
            a = await sc.add("Alice", page=False)
            pa = FakePage(a, auto=False, ua=HIDDEN_UA)
            await pa.connect(sc.session)
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Fake Default Audio Input", "level": 0, "tx": False, "pcs": 0})
            await until(lambda: len(a.build_state()["warnings"]) == 2, 3, "both problems named")
            text = " ".join(a.build_state()["warnings"])
            assert "hidden test browser" in text and "FAKE" in text
            assert a.diag()["warnings"] == a.build_state()["warnings"]
            log = _log()
            assert "TRACE desktop media_engine" in log and "headless=yes" in log and "launched_by_this_client=no" in log
            assert "TRACE desktop media_warning problem=hidden_engine" in log and "TRACE desktop media_warning problem=fake_microphone" in log
            assert 'TRACE desktop mic state=ok label="Fake Default Audio Input" fake=yes' in log
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Fake Default Audio Input", "level": 0.2, "tx": True, "pcs": 1})
            await asyncio.sleep(0.2)
            assert _log().count("media_warning problem=fake_microphone") == 1, "each problem is traced once"
            await pa.close()
    run(go(), 30)


def test_a_test_engine_that_was_asked_for_is_not_a_problem(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            a.fake_media = a.headless = True                       # `--headless --fake-media`: a rehearsal on purpose
            pa = FakePage(a, auto=False, ua=HIDDEN_UA)
            await pa.connect(sc.session)
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Fake Default Audio Input", "level": 0, "tx": False, "pcs": 0})
            await until(lambda: a.mic.get("label"), 3, "mic reported")
            assert a.build_state()["warnings"] == []
            await pa.close()
    run(go(), 30)


def test_a_missing_microphone_is_a_warning_and_speakers_can_be_tested(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            _clear_log()
            a = await sc.add("Alice", page=False)
            pa = FakePage(a, auto=False, ua=NORMAL_UA)
            await pa.connect(sc.session)
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Microphone (USB)", "level": 0, "tx": False, "pcs": 0})
            await until(lambda: a.mic.get("label"), 3, "mic reported")
            assert a.build_state()["warnings"] == [], "a normal engine and a real microphone raise nothing"
            await pa.ws.send_json({"t": "ui", "cmd": "test_speakers"})
            await until(lambda: pa.named("tone"), 3, "the page is told to play the tone")
            await pa.ws.send_json({"t": "tone", "ok": True, "error": "", "sink": "Speakers (Realtek)"})
            await until(lambda: a.build_state()["speaker_test"].get("ok") is True, 3, "the page answers")
            assert 'TRACE desktop speaker_test played=yes speakers="Speakers (Realtek)"' in _log()
            await pa.ws.send_json({"t": "tone", "ok": False, "error": "NotAllowedError", "sink": ""})
            await until(lambda: a.build_state()["speaker_test"].get("ok") is False, 3, "a failure is reported too")
            assert "played=no" in _log() and "NotAllowedError" in _log()
            await pa.ws.send_json({"t": "mic", "state": "no_device", "label": "Requested device not found", "level": 0, "tx": False, "pcs": 0})
            await until(lambda: a.build_state()["warnings"], 3, "no microphone is a warning")
            assert "nobody can hear you" in a.build_state()["warnings"][0]
            await pa.close()
    run(go(), 30)


def test_the_audio_line_says_what_the_speakers_were_doing(tmp_path):
    """RECEIVING alone does not prove you heard anything: the line also carries the playback facts."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(10, 0))
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            _clear_log()
            peer = next(p for p in a.build_state()["peers"] if p["name"] == "Bobby")
            ep = a.peers.peers[a.peers.by_sid[peer["sid"]]].ep
            base = {"t": "peer", "sid": peer["sid"], "ep": ep, "type": "stats", "rx": 1000, "tx": 0, "level": 0.0,
                    "playing": True, "muted": False, "vol": 0.75, "sink": "Speakers"}
            pa = sc.pages["Alice"]
            await pa.ws.send_json(dict(base, energy=0.0))
            await pa.ws.send_json(dict(base, energy=0.05, rx=2000))
            await until(lambda: "audio_rx name=Bobby state=RECEIVING" in _log(), 3, "the audio line")
            line = next(l for l in _log().splitlines() if "audio_rx name=Bobby state=RECEIVING" in l)
            for want in ("playing=yes", "muted=no", "element_volume=0.75", "speakers=Speakers", "energy_gain=0.05"):
                assert want in line, (want, line)
            assert a.build_state()["peers"][0]["stats"]["playing"] is True
    run(go(), 60)


def test_sending_is_only_narrated_while_the_microphone_is_open(tmp_path):
    """A new connection sends setup packets with nobody talking; that must not read as 'your voice reaches them'."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(10, 0))
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            _clear_log()
            peer = next(p for p in a.build_state()["peers"] if p["name"] == "Bobby")
            ep = a.peers.peers[a.peers.by_sid[peer["sid"]]].ep
            stat = lambda tx: {"t": "peer", "sid": peer["sid"], "ep": ep, "type": "stats", "rx": 0, "tx": tx, "energy": 0.0, "level": 0.0}   # noqa: E731
            pa = sc.pages["Alice"]
            await pa.ws.send_json(stat(0))
            await pa.ws.send_json(stat(5000))                      # a burst while push-to-talk is up
            await asyncio.sleep(0.4)
            assert "audio_tx name=Bobby state=SENDING" not in _log()
            base = "http://127.0.0.1:%d" % a.port
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": True})
            await until(lambda: a.tx_enabled, 3, "push-to-talk down")
            await pa.ws.send_json(stat(10000))
            await until(lambda: "audio_tx name=Bobby state=SENDING" in _log(), 3, "the line, with the microphone open")
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": False})
    run(go(), 60)


def test_every_push_to_talk_press_is_narrated_even_with_an_open_mic(tmp_path):
    """The 2026-10-17 run could not tell 'never pressed' from 'pressed in the other window': open mic hides presses."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", page=False)
            pa = FakePage(a, auto=False, ua=NORMAL_UA)
            await pa.connect(sc.session)
            await until(lambda: pa.cmds, 3, "config")
            _clear_log()
            base = "http://127.0.0.1:%d" % a.port
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": True})
            await until(lambda: "TRACE desktop ptt down=yes source=button mode=ptt" in _log(), 3, "the press")
            assert "microphone_opens=yes" in _log()
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": False})
            await until(lambda: "TRACE desktop ptt down=no" in _log(), 3, "the release (after its short tail)")
            await pa.ws.send_json({"t": "ui", "cmd": "set_mode", "mode": "open"})
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": True})
            await until(lambda: "ptt down=yes source=button mode=open" in _log(), 3, "a press in open-mic mode is still narrated")
            await sc.session.post(base + "/api/ptt?token=" + TOKEN, json={"down": False})
            await pa.close()
    run(go(), 30)


def test_packets_with_almost_no_sound_are_told_apart_from_no_packets(tmp_path):
    """2026-10-17: one direction logged nothing at all. 'Nothing arrived' and 'it arrived near-silent' need different words."""
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0))
            await sc.add("Bobby", at=(10, 0))
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            _clear_log()
            peer = next(p for p in a.build_state()["peers"] if p["name"] == "Bobby")
            ep = a.peers.peers[a.peers.by_sid[peer["sid"]]].ep
            stat = lambda rx, energy: {"t": "peer", "sid": peer["sid"], "ep": ep, "type": "stats", "rx": rx, "tx": 0,   # noqa: E731
                                       "energy": energy, "level": 0.0, "playing": True, "muted": False, "vol": 1.0, "sink": ""}
            pa = sc.pages["Alice"]
            await pa.ws.send_json(stat(0, 0.0))
            await pa.ws.send_json(stat(2000, 0.00001))                     # bytes flow, no sound
            await until(lambda: "audio_rx name=Bobby state=ARRIVING_QUIET" in _log(), 3, "the quiet-packets line")
            assert "state=RECEIVING" not in _log()
            await pa.ws.send_json(stat(4000, 0.0008))                      # a faint voice
            await until(lambda: "audio_rx name=Bobby state=RECEIVING" in _log(), 3, "faint speech now counts")
            assert "TRACE desktop audio_flow name=Bobby" in _log(), "a running total is logged too"
            assert _log().count("state=ARRIVING_QUIET") == 1
    run(go(), 60)


# ------------------------------------------------------------ the network path is narrated

HOSTC = "candidate:1 1 udp 2113937151 192.168.1.5 54400 typ host generation 0"
SRFLXC = "candidate:2 1 udp 1677729535 93.184.216.34 54400 typ srflx raddr 192.168.1.5 rport 54400 generation 0"


async def _open_peer(sc, page_auto=False):
    """Alice (scripted page that does nothing by itself) next to an ordinary Bobby; returns (alice, her page, the open command)."""
    a = await sc.add("Alice", at=(0, 0), page=False)
    pa = FakePage(a, auto=page_auto, ua=NORMAL_UA)
    await pa.connect(sc.session)
    await sc.add("Bobby", at=(10, 0))
    cmd = await until(lambda: pa.named("open"), 20, "a call to open")
    return a, pa, cmd[-1]


def test_candidates_gathering_path_and_stats_are_narrated(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, op = await _open_peer(sc)
            a.peers.WEBRTC_STATS_EVERY_S = 0.3
            _clear_log()
            sid, ep = op["sid"], op["ep"]
            ev = lambda **kw: pa.ws.send_json(dict(t="peer", sid=sid, ep=ep, **kw))                  # noqa: E731
            await ev(type="candidate", candidate={"candidate": HOSTC, "sdpMid": "0"})
            await ev(type="candidate", candidate={"candidate": SRFLXC, "sdpMid": "0"})
            await ev(type="ice_error", code=701, url="stun:stun.example:3478", text="STUN binding request timed out", ms=4000)
            await ev(type="gathering", state="complete", ms=340)
            await ev(type="state", ice="connected", conn="connected", gather="complete", ms=910)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 5, "connected")
            pair = {"lt": "srflx", "rt": "srflx", "proto": "udp", "rp": "", "lnet": "public", "rnet": "public", "net": "wifi"}
            st = dict(type="stats", level=0.0, energy=0.0, rx=1000, tx=1000, lost=0, pr=50, jitter=0.004, rtt=38, pair="srflx>srflx",
                      pairinfo=pair, rlost=0.0, avail=300, playing=True, muted=False, vol=1.0, sink="")
            await ev(**st)
            await asyncio.sleep(0.4)
            await ev(**dict(st, rx=9000, tx=7000, pr=450, lost=2))
            await until(lambda: "TRACE desktop webrtc_stats name=Bobby" in _log(), 3, "the periodic line")
            log = _log()
            assert "webrtc_candidate name=Bobby side=local type=host proto=udp network=private" in log
            assert "webrtc_candidate name=Bobby side=local type=srflx proto=udp network=public" in log
            assert "93.184.216.34" not in log and "192.168.1.5" not in log, "addresses are not logged unless asked for"
            assert "webrtc_ice_error name=Bobby code=701 server=stun:stun.example:3478" in log
            assert "webrtc_gathered name=Bobby gather_ms=340 local_candidates=host:1,srflx:1 stun_errors=1 public_address_found=yes" in log
            assert "webrtc_connected name=Bobby ice_ms=910" in log and "local_candidates=host:1,srflx:1" in log
            assert "webrtc_path name=Bobby path=direct-nat" in log and "turn_used=no" in log and "pair=srflx>srflx proto=udp" in log
            line = next(l for l in log.splitlines() if "webrtc_stats name=Bobby" in l)
            for want in ("path=direct-nat", "rtt_ms=38", "jitter_ms=4", "loss_out_pct=0.0", "in_kbps=", "out_kbps=", "send_budget_kbps=300"):
                assert want in line, (want, line)
            assert float(line.split("in_kbps=")[1].split()[0]) > 100, "bitrate comes from the byte counters"
            snap = next(p for p in a.build_state()["peers"] if p["name"] == "Bobby")
            assert snap["path"] == "direct-nat" and snap["candidates"]["local"] == {"host": 1, "srflx": 1}
            res = [l for l in log.splitlines() if "webrtc_result name=Bobby" in l]
            assert any("result=DIRECT_STUN" in l and "phase=connected" in l for l in res), res
            settled = next(l for l in res if "phase=settled" in l)
            for want in ("result=DIRECT_STUN", "pair=srflx>srflx", "proto=udp", "local_network=public", "remote_network=public", "rtt_ms=38",
                         "jitter_ms=4", "loss_in_pct=0.4", "loss_out_pct=0.0", "turn_used=no", "setup_ms="):
                assert want in settled, (want, settled)
    run(go(), 60)


def test_relayed_paths_are_labelled_as_turn(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, op = await _open_peer(sc)
            _clear_log()
            ev = lambda **kw: pa.ws.send_json(dict(t="peer", sid=op["sid"], ep=op["ep"], **kw))      # noqa: E731
            await ev(type="state", ice="connected", conn="connected", gather="complete", ms=3000)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 5, "connected")
            await ev(type="stats", level=0, energy=0, rx=1, tx=1, pair="host>relay",
                     pairinfo={"lt": "host", "rt": "relay", "proto": "udp", "rp": "tls", "lnet": "private", "rnet": "public", "net": ""})
            await until(lambda: "webrtc_path name=Bobby" in _log(), 3, "the path line")
            assert "path=relayed" in _log() and "turn_used=yes" in _log() and "relay_protocol=tls" in _log()
            assert "webrtc_result name=Bobby result=RELAY_TURN" in _log() and "turn_used=yes" in _log()
    run(go(), 60)


def test_a_connection_that_cannot_form_says_why_and_the_client_carries_on(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, op = await _open_peer(sc)
            _clear_log()
            ev = lambda **kw: pa.ws.send_json(dict(t="peer", sid=op["sid"], ep=op["ep"], **kw))      # noqa: E731
            await ev(type="candidate", candidate={"candidate": HOSTC})                       # no public address was ever learned
            await ev(type="ice_error", code=701, url="stun:stun.example:3478", text="timed out")
            await ev(type="state", ice="failed", conn="failed", gather="complete", ms=20000)
            await until(lambda: pstate(a, "Bobby") == "DISCONNECTED", 5, "the attempt ends")
            log = _log()
            assert "webrtc_failed name=Bobby" in log and "reason=no_public_address" in log and "local_candidates=host:1" in log
            assert "turn_available=no" in log and "attempt=1" in log
            assert "webrtc_result name=Bobby result=FAILED reason=no_public_address" in log
            snap = next(p for p in a.build_state()["peers"] if p["name"] == "Bobby")
            assert "could not learn its public address" in snap["err"] and snap["retry_in"] > 0, "it will try again, politely"
            st = a.status_summary()
            assert "Cannot connect to Bobby: " in (st["text"] + " ".join(st["more"])) and st["level"] in ("warn", "bad"), st
            assert a.diag()["peers"], "and the client is otherwise fine"
    run(go(), 60)


def test_when_both_have_public_addresses_the_failure_blames_the_nat_and_wants_a_relay(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a, pa, op = await _open_peer(sc)
            _clear_log()
            ev = lambda **kw: pa.ws.send_json(dict(t="peer", sid=op["sid"], ep=op["ep"], **kw))      # noqa: E731
            await ev(type="candidate", candidate={"candidate": SRFLXC})
            a.peers.on_signal(op["sid"], "ice", op["ep"], {"candidate": {"candidate": SRFLXC}})
            await ev(type="state", ice="failed", conn="failed", gather="complete", ms=20000)
            await until(lambda: pstate(a, "Bobby") == "DISCONNECTED", 5, "the attempt ends")
            log = _log()
            assert "reason=nat_traversal_failed" in log and "symmetric" in log and "remote_candidates=srflx:1" in log
    run(go(), 60)


def test_a_voice_radius_wider_than_the_hubs_link_radius_is_flagged(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            _clear_log()
            a = await sc.add("Alice", at=(0, 0), cfg={"proximity": {"say_radius": 60.0, "peak_radius": 120.0, "full_radius": 15.0}})
            b = await sc.add("Bobby", at=(2000, 0))
            await until(lambda: a.signalling.limits.get("link_radius"), 10, "the hub's limits")
            await until(lambda: any("wider than the server will link" in w for w in a.build_state()["warnings"]), 10, "the warning")
            await until(lambda: "media_warning problem=link_radius" in _log(), 12, "and it is traced once")
            assert not any("wider than" in w for w in b.build_state()["warnings"]), "the default radius fits the default hub"
    run(go(), 60)


def test_the_radius_can_be_set_from_the_command_line():
    from eqvoice_client.__main__ import overrides, parse
    o = overrides(parse(["--say-radius", "28"]))
    assert o["proximity"] == {"say_radius": 28.0}, "the radius of full volume is its own setting"
    o = overrides(parse(["--say-radius", "60", "--full-radius", "20"]))
    assert o["proximity"] == {"say_radius": 60.0, "full_radius": 20.0}
    assert "proximity" not in overrides(parse([]))


# ------------------------------------------------------------ the window says in one line whether it is working

def test_the_status_line_says_ready_or_names_the_first_thing_to_fix(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            pa = FakePage(a, auto=True, ua=NORMAL_UA)
            await pa.connect(sc.session)
            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Microphone (USB)", "level": 0, "tx": False, "pcs": 0})
            await until(lambda: a.build_state()["status"]["level"] == "ok", 15, "ready: " + repr(a.status_summary()))
            s = a.build_state()["status"]
            assert s["text"].startswith("Ready: Alice on Testrealm") and "Nobody in range" in s["text"] and s["more"] == []
            b = await sc.add("Bobby", at=(10, 0))
            await until(lambda: pstate(a, "Bobby") == "CONNECTED", 20, "connected")
            assert "1 player in range, 1 connected" in a.build_state()["status"]["text"]

            await pa.ws.send_json({"t": "mic", "state": "denied", "label": "NotAllowedError", "level": 0, "tx": False, "pcs": 0})
            await until(lambda: a.build_state()["status"]["level"] == "bad", 5, "a microphone problem is red")
            assert "Microphone problem (denied)" in a.build_state()["status"]["text"]

            await pa.ws.send_json({"t": "mic", "state": "ok", "label": "Microphone (USB)", "level": 0, "tx": False, "pcs": 1})
            await until(lambda: a.mic.get("state") == "ok", 5, "the microphone is fine again")
            a.signalling.state, a.signalling.last_error = "backoff", "connection refused"
            st = a.status_summary()
            assert st["level"] == "bad" and "Not connected to the voice server: connection refused" in st["text"], st
            a.signalling.state = "connecting"
            assert a.status_summary()["level"] == "warn" and "Connecting to the voice server" in a.status_summary()["text"]
            a.signalling.state = "registered"

            a.fake_media = False
            a.mic["label"] = "Fake Default Audio Input"
            st = a.status_summary()
            assert st["level"] == "warn" and "FAKE" in st["text"], st
            await pa.close()
            await until(lambda: "No voice window is connected." in repr(a.status_summary()), 5, "a missing window is named")
            assert a.status_summary()["level"] == "bad"
    run(go(), 90)


def test_the_status_line_before_the_game_is_seen(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            a = await sc.add("Alice", at=(0, 0), page=False)
            pa = FakePage(a, auto=False, ua=NORMAL_UA)
            await pa.connect(sc.session)
            await until(lambda: any(p.media for p in a.pages), 5, "the window has said hello")
            a.ident = None                                   # as if no character has been reported yet (nothing awaits before the check)
            s = a.status_summary()
            assert s["level"] == "warn" and s["text"].startswith("Waiting for the game."), s
            await pa.close()
    run(go(), 30)
