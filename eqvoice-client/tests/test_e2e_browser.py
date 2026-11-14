"""Real clients, real (headless) Chromium media engines, real WebRTC audio, the whole roster pipeline in front.

What this proves that the other tests cannot: the page's JavaScript works, the SDP/ICE exchange through the
real signalling server completes, Opus audio actually flows peer to peer, push-to-talk really stops the audio,
a distance fade really reaches the speaker gain, we stop transmitting to someone out of range, and leaving
range really closes the RTCPeerConnection.

Needs Edge or Chrome. Skipped when there is none, or with EQVOICE_E2E=0.
"""
import asyncio
import os

import pytest

from eqvoice_client import browser
from eqvoice_client.peers import voice_gain
from scene import Scene, until as _until

pytestmark = pytest.mark.skipif(
    browser.find_browser() is None or os.environ.get("EQVOICE_E2E") == "0",
    reason="needs a Chromium-based browser")

REAL = {"ice_servers": [],                                       # host candidates only: no internet needed
        "audio": {"mode": "open", "processing": False,           # raw fake-mic beeps, not gated by noise suppression ...
                  "keyboard_filter": False,                    # ... nor by the keyboard filter (a 30 ms beep is exactly what it exists to drop)
                  "auto_level": False},                       # ... and the volumes asserted here are the curve's own, not the curve times a levelling gain
        "proximity": {"fade_in_ms": 200, "fade_out_ms": 300, "teardown_s": 3.0, "connect_timeout_s": 25.0,
                      "retry_backoff_s": [2], "forget_s": 10}}


def run(coro, timeout=150):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def until(pred, timeout=20.0, what=""):
    return await _until(pred, timeout, what)


def peer(app, name):
    return next((p for p in app.peers.snapshot() if p["name"] == name), None)


def stat(app, name, key, default=0.0):
    v = ((peer(app, name) or {}).get("stats") or {}).get(key)
    return default if v is None else v


def test_real_webrtc_audio_between_two_unrelated_characters(tmp_path):
    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a = await sc.add("Alice", at=(0, 0), real_browser=True, cfg=REAL)
            b = await sc.add("Bobby", at=(2000, 0), real_browser=True, cfg=REAL)
            await until(lambda: any(p.media for p in a.pages) and any(p.media for p in b.pages), 40, "both media pages")
            await until(lambda: a.mic.get("state") == "ok" and b.mic.get("state") == "ok", 30,
                        "fake microphones (a=%r b=%r)" % (a.mic, b.mic))
            await until(lambda: sc.hub.stats()["sessions"] == 2, 20, "both registered")
            assert peer(a, "Bobby") is None, "far apart: no call, nothing even seen"

            # --- walk up to each other (neither is grouped, guilded or friended: they only share a realm)
            sc.walk_to("Alice", "Bobby", 6)
            await until(lambda: (peer(a, "Bobby") or {}).get("state") == "CONNECTED"
                        and (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40,
                        "both CONNECTED: a=%r b=%r" % (a.peers.snapshot(), b.peers.snapshot()))

            # --- audio really flows, both directions
            await until(lambda: stat(a, "Bobby", "rx") > 4000 and stat(b, "Alice", "rx") > 4000, 15, "RTP bytes both ways")
            await until(lambda: stat(a, "Bobby", "energy") > 0 and stat(b, "Alice", "energy") > 0, 15, "decoded audio energy both ways")
            pair = stat(a, "Bobby", "pair", "")
            assert "host" in pair or "prflx" in pair, "same-PC connection uses host candidates (got %r)" % pair

            # --- distance reaches the speaker gain in the page
            sc.walk_to("Alice", "Bobby", 60)
            want = voice_gain(60, a.peers.cfg.curve)
            await until(lambda: abs(stat(a, "Bobby", "gain", -1) - want) < 0.05, 8,
                        "page gain follows distance (want %.2f, page says %r)" % (want, stat(a, "Bobby", "gain", -1)))
            sc.walk_to("Alice", "Bobby", 6)
            await until(lambda: stat(a, "Bobby", "gain") > 0.95, 8, "and returns to full volume up close")

            # --- push to talk: key up means silence on the other side
            a.cfg.audio.mode = "ptt"
            a._apply_voice()
            await until(lambda: a.mic.get("tx") is False, 5, "tx off")
            await asyncio.sleep(1.0)
            e0, rx0 = stat(b, "Alice", "energy"), stat(b, "Alice", "rx")
            await asyncio.sleep(3.0)
            e1, rx1 = stat(b, "Alice", "energy"), stat(b, "Alice", "rx")
            a.ptt.virtual = True
            a.ptt.sample()
            await until(lambda: a.mic.get("tx") is True, 5, "tx on")
            await asyncio.sleep(1.0)
            e2, rx2 = stat(b, "Alice", "energy"), stat(b, "Alice", "rx")
            await asyncio.sleep(3.0)
            e3, rx3 = stat(b, "Alice", "energy"), stat(b, "Alice", "rx")
            print("\n[e2e] audio energy at B over 3 s: key up %.3g, key down %.3g; pair %s; rtt %s ms" % (
                e1 - e0, e3 - e2, pair, stat(a, "Bobby", "rtt", None)))
            print("[e2e] RTP payload received at B: key up %.0f bytes/s (%.1f kbit/s), key down %.0f bytes/s (%.1f kbit/s)" % (
                (rx1 - rx0) / 3.0, (rx1 - rx0) * 8 / 3000.0, (rx3 - rx2) / 3.0, (rx3 - rx2) * 8 / 3000.0))
            assert (e3 - e2) > 20 * max(e1 - e0, 1e-9), \
                "audio must be silent with the key up and audible with it down (up: %g, down: %g)" % (e1 - e0, e3 - e2)
            a.ptt.virtual = False
            a.ptt.sample()
            a.cfg.audio.mode = "open"
            a._apply_voice()

            # --- Bobby walks away: fade first (and Alice stops sending to him), the call closes later
            sc.vanish("Bobby")
            await until(lambda: peer(a, "Bobby")["state"] == "OUT_OF_RANGE_GRACE", 6, "grace")
            await until(lambda: stat(a, "Bobby", "gain", -1) == 0, 5, "page gain reached 0 (fade applied)")
            await until(lambda: (peer(a, "Bobby") or {}).get("stats", {}).get("sending") is False, 5,
                        "Alice's page stopped sending her microphone to him")
            await until(lambda: (peer(a, "Bobby") or {"state": "gone"})["state"] in ("DISCONNECTED", "gone", "SEEN"), 12, "closed after teardown")
            await until(lambda: a.mic.get("pcs") == 0, 5, "RTCPeerConnection really closed on A")
            await until(lambda: b.mic.get("pcs") == 0, 12, "and on B (it lost the link too)")

            # --- back in range: a new call, audio again
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(a, "Bobby") or {}).get("state") == "CONNECTED"
                        and (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40, "reconnected")
            await until(lambda: stat(a, "Bobby", "rx") > 4000, 15, "audio again")
    run(go())


def test_the_voice_chain_runs_in_a_real_engine_and_never_costs_the_voice(tmp_path):
    """The one thing the fake page cannot show: the Web Audio chain really passes a real call's voice through, and the bitrate cap
    reaches the sender. If the chain ever produced silence the page would fall back by itself."""
    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a = await sc.add("Alice", at=(0, 0), real_browser=True, cfg=REAL)
            b = await sc.add("Bobby", at=(2000, 0), real_browser=True, cfg=REAL)
            await until(lambda: any(p.media for p in a.pages) and any(p.media for p in b.pages), 40, "both media pages")
            await until(lambda: a.mic.get("state") == "ok" and b.mic.get("state") == "ok", 30, "fake microphones")
            sc.walk_to("Alice", "Bobby", 6)
            await until(lambda: (peer(a, "Bobby") or {}).get("state") == "CONNECTED"
                        and (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40, "both CONNECTED")

            # --- the voice comes out of the chain (meter, boost, limiter)
            await until(lambda: stat(a, "Bobby", "fx", "") == "chain", 20, "the chain carries the voice (page says %r)" % stat(a, "Bobby", "fx", ""))
            await until(lambda: stat(a, "Bobby", "out") > 0.01, 20, "the voice comes out of the chain (out=%r)" % stat(a, "Bobby", "out"))
            assert a.fx_error == "" and b.fx_error == "", "and the safety net did not have to act"
            await until(lambda: stat(a, "Bobby", "maxbr", -1) == 24, 15, "the bitrate cap reached the sender (page says %r)" % stat(a, "Bobby", "maxbr", -1))

            # --- the safety net: if the chain is ever given up on, voices play on the plain path and the app is told
            a.media({"cmd": "fail_chain"})
            await until(lambda: stat(a, "Bobby", "fx", "") == "direct", 10, "plain path after the chain is given up")
            assert await until(lambda: a.fx_error, 5, "the app is told why")
            e0 = stat(a, "Bobby", "energy")
            await until(lambda: stat(a, "Bobby", "energy") > e0 + 0.001, 15, "audio still arrives on the plain path (the browser's own energy figure counts again)")
            assert any("play plain" in t for _, t in a.media_warnings()), "and the window says voices play plain"

            # --- and a lower cap, on the plain path
            a._on_ui({"cmd": "max_bitrate", "value": 16})
            await until(lambda: stat(a, "Bobby", "maxbr", -1) == 16, 15, "the new cap reached the sender")
    run(go())


def test_four_strangers_form_a_mesh_and_one_leaves(tmp_path):
    """A small crowd: four real media engines, six simultaneous WebRTC connections, then one player leaves."""
    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            names = ["Ann", "Bobby", "Cyrus", "Dina"]
            apps = {}
            for i, n in enumerate(names):
                apps[n] = await sc.add(n, at=(3000.0 * (i + 1), 0.0), real_browser=True, cfg=REAL)
            await until(lambda: all(any(p.media for p in a.pages) for a in apps.values()), 90, "four media pages")
            await until(lambda: all(a.mic.get("state") == "ok" for a in apps.values()), 40, "four microphones")
            await until(lambda: sc.hub.stats()["sessions"] == 4, 20, "all registered")
            for i, n in enumerate(names):                # a tight huddle: everyone within a few yards
                sc.world.move(n, 5.0 * i, 0.0)

            def mesh(group):
                return all((peer(apps[s], o) or {}).get("state") == "CONNECTED" for s in group for o in group if o != s)
            await until(lambda: mesh(names), 90, "full mesh CONNECTED: " + repr(
                {s: [(p["name"], p["state"]) for p in apps[s].peers.snapshot()] for s in names}))
            await until(lambda: all(stat(apps[s], o, "rx") > 500 for s in names for o in names if o != s), 25,
                        "audio on all twelve directions")
            assert sc.hub.counters["rate_limited"] == 0, "a crowd connecting must not trip the server's flood limits"

            sc.vanish("Dina")
            others = names[:3]
            await until(lambda: all((peer(apps[s], "Dina") or {"state": "DISCONNECTED"})["state"] in ("OUT_OF_RANGE_GRACE", "DISCONNECTED", "SEEN")
                                    for s in others), 12, "everyone fades Dina")
            await until(lambda: all(apps[s].mic.get("pcs") == 2 for s in others), 20, "Dina's connections closed, the triangle remains")
            assert mesh(others), "the others were not disturbed"
    run(go(), timeout=300)


def test_an_unreachable_stun_server_is_reported_and_a_direct_path_is_labelled(tmp_path):
    """The real page: STUN that never answers does not stop two PCs finding each other, and the log says what happened."""
    from eqvoice_client.logbuf import RING
    cfg = dict(REAL, ice_servers=[{"urls": ["stun:192.0.2.1:3478"]}])       # a documentation address: packets go nowhere

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            RING.records.clear()
            a = await sc.add("Alice", at=(0, 0), real_browser=True, cfg=cfg)
            b = await sc.add("Bobby", at=(2000, 0), real_browser=True, cfg=cfg)
            for app in (a, b):
                app.peers.WEBRTC_STATS_EVERY_S = 1.0
            await until(lambda: a.mic.get("state") == "ok" and b.mic.get("state") == "ok", 60, "microphones")
            sc.walk_to("Alice", "Bobby", 6)
            await until(lambda: (peer(a, "Bobby") or {}).get("state") == "CONNECTED", 60, "connected without STUN")
            text = lambda: "\n".join(RING.tail(600))                      # noqa: E731
            await until(lambda: "webrtc_gathered name=Bobby" in text() and "webrtc_gathered name=Alice" in text(), 90, "address gathering to finish")
            await until(lambda: "webrtc_stats name=Bobby" in text(), 20, "a periodic stats line")
            log = text()
            # (Chromium raises no icecandidateerror for a silent STUN server; it simply never produces a srflx candidate.)
            assert "public_address_found=no" in log and "type=srflx" not in log, "no public address was learned, and that is said"
            assert "type=host" in log and "webrtc_gathered name=Bobby" in log
            assert "webrtc_path name=Bobby path=direct-host" in log and "turn_used=no" in log
            line = next(l for l in log.splitlines() if "webrtc_connected name=Bobby" in l)
            assert "ice_ms=" in line and "total_ms=" in line
            print("\n[e2e]", line.split("TRACE desktop ")[1])
            print("[e2e]", next(l for l in log.splitlines() if "webrtc_stats name=Bobby" in l).split("TRACE desktop ")[1])
    run(go(), 240)


def test_five_strangers_real_engines(tmp_path):
    """Five real media engines (a crowd of ten WebRTC pairs): simultaneous voices, people leaving, one engine restarting."""
    from eqvoice_client.logbuf import RING
    PTT = dict(REAL, audio={"mode": "ptt", "processing": False, "keyboard_filter": False})
    names = ["Ann", "Bobby", "Cyrus", "Dina", "Edda"]

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            RING.records.clear()
            apps = {}
            for i, n in enumerate(names):
                apps[n] = await sc.add(n, at=(3000.0 * (i + 1), 0.0), real_browser=True, cfg=PTT)
            await until(lambda: all(any(p.media for p in a.pages) and a.mic.get("state") == "ok" for a in apps.values()), 120,
                        "five engines with microphones")
            await until(lambda: sc.hub.stats()["sessions"] == 5, 20, "all registered")
            for i, n in enumerate(names):
                sc.world.move(n, 2.0 * i, 0.0)                         # everyone within 8 yards: full volume for all pairs

            def mesh(group):
                return all((peer(apps[s], o) or {}).get("state") == "CONNECTED" for s in group for o in group if o != s)
            await until(lambda: mesh(names), 120, "full mesh: " + repr({s: [(p["name"], p["state"]) for p in apps[s].peers.snapshot()] for s in names}))
            assert sc.hub.stats()["links"] == 10 and sc.hub.counters["rate_limited"] == 0
            await until(lambda: all(apps[n].mic.get("pcs") == 4 for n in names), 20, "four peer connections in every engine")
            setups = [apps[n].peers.aggregate() for n in names]
            print("\n[mesh] connection setup ms (avg/max per client):", [(a["setup_ms_avg"], a["setup_ms_max"]) for a in setups])

            # --- three people talk at once; a fourth listens (Edda), a fifth stays silent (Dina)
            talkers = ["Ann", "Bobby", "Cyrus"]
            e0 = {t: stat(apps["Edda"], t, "energy") for t in names[:4]}
            for t in talkers:
                apps[t].ptt.virtual = True
                apps[t].ptt.sample()
            await until(lambda: all(apps[t].mic.get("tx") is True for t in talkers), 5, "three microphones open")
            await asyncio.sleep(4.0)
            e1 = {t: stat(apps["Edda"], t, "energy") for t in names[:4]}
            heard = {t: e1[t] - e0[t] for t in e1}
            print("[mesh] energy heard by Edda over 4 s:", {k: round(v, 4) for k, v in heard.items()})
            assert all(heard[t] > 0.01 for t in talkers), "Edda hears all three at once: %r" % heard
            assert heard["Dina"] < 0.001, "and nothing from the one who is not talking: %r" % heard
            r = apps["Edda"].read_resources()
            print("[mesh] Edda while three talk: engine %s | mesh %s" % (r["engine"], r["mesh"]))
            assert r["mesh"]["in_kbps"] > 6, "three voices arrive (a 24 kbps cap and a wideband ceiling keep a beep cheap)"
            # (the engine process count reads 0 when this Edge hands the window to a process that is not a child of the one started: the figure is only a diagnostic)
            assert r["engine"]["procs"] == 0 or r["engine"]["procs"] >= 3

            # --- everyone stops; Bobby walks off: no stale audio from him anywhere
            for t in talkers:
                apps[t].ptt.virtual = False
                apps[t].ptt.sample()
            sc.world.move("Bobby", 20000.0, 0.0)
            others = [n for n in names if n != "Bobby"]
            await until(lambda: all((peer(apps[s], "Bobby") or {"state": "gone"})["state"] in ("OUT_OF_RANGE_GRACE", "DISCONNECTED", "SEEN", "gone")
                                    for s in others), 15, "everyone fades Bobby")
            await until(lambda: all(apps[s].mic.get("pcs") == 3 for s in others), 25, "his calls are closed everywhere; the rest remain")
            assert mesh(others)

            # --- an engine dies and comes back (its page connection is cut; the page reconnects by itself)
            victim = apps["Dina"]
            page = next(p for p in victim.pages if p.media)
            await page.ws.close()
            await until(lambda: all((peer(apps[s], "Dina") or {"state": "DISCONNECTED"})["state"] != "CONNECTED" for s in ["Ann", "Cyrus", "Edda"]), 15,
                        "Dina's calls drop")
            assert mesh(["Ann", "Cyrus", "Edda"]), "and the others keep talking to each other"
            await until(lambda: mesh(others), 90, "Dina's engine reconnects and the mesh heals")
            print("[mesh] resources:", [(n, apps[n].read_resources()["engine"]) for n in names])
    run(go(), 600)


# ----------------------------------------------------------------- range, loudness and the keyboard filter, in real engines

def _two_real(sc, a_audio=None, b_audio=None):
    """Alice and Bobby in real engines (their audio settings may differ); returns once both microphones work."""
    async def make():
        cfg_a = dict(REAL, audio=dict(REAL["audio"], **(a_audio or {})))
        cfg_b = dict(REAL, audio=dict(REAL["audio"], **(b_audio or {})))
        a = await sc.add("Alice", at=(0, 0), real_browser=True, cfg=cfg_a)
        b = await sc.add("Bobby", at=(2000, 0), real_browser=True, cfg=cfg_b)
        await until(lambda: any(p.media for p in a.pages) and any(p.media for p in b.pages), 40, "both media pages")
        await until(lambda: a.mic.get("state") == "ok" and b.mic.get("state") == "ok", 30, "fake microphones")
        return a, b
    return make()


def test_the_keyboard_filter_keeps_typing_off_the_line_and_the_switch_works(tmp_path, monkeypatch):
    """The microphone plays a recording of someone typing, and nothing else. With the filter on the other player hears nothing; with
    it switched off (in the window) they hear the typing."""
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.typing_wav(tmp_path / "typing.wav")))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc, {"keyboard_filter": True})
            await until(lambda: a.mic.get("gate") is True, 15, "Alice's microphone goes through the filter (mic: %r, error %r)" % (a.mic, a.gate_error))
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED" and (peer(a, "Bobby") or {}).get("state") == "CONNECTED", 40, "connected")
            e0 = stat(b, "Alice", "energy")
            await asyncio.sleep(9.0)                                    # eight seconds of typing on Alice's microphone
            heard = stat(b, "Alice", "energy") - e0
            print("\n[e2e] energy Bobby heard from Alice's typing with the filter on: %.3g" % heard)
            assert heard < 0.0005, "typing does not get through the filter: %g" % heard
            assert a.gate_error == "", a.gate_error

            a._on_ui({"cmd": "keyboard_filter", "value": False})
            await until(lambda: a.mic.get("gate") is False and a.mic.get("state") == "ok", 15, "the filter is switched off")
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED", 20, "still connected after the microphone was reopened")
            e0 = stat(b, "Alice", "energy")
            await until(lambda: stat(b, "Alice", "energy") - e0 > 0.002, 25, "with the filter off the typing is heard (%.3g so far)" % (stat(b, "Alice", "energy") - e0))
    run(go(), 240)


def test_a_voice_gets_through_the_keyboard_filter_in_a_real_call(tmp_path, monkeypatch):
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.speech_wav(tmp_path / "speech.wav")))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc, {"keyboard_filter": True})
            await until(lambda: a.mic.get("gate") is True, 15, "the filter is on")
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40, "connected")
            e0 = stat(b, "Alice", "energy")
            await asyncio.sleep(8.0)
            heard = stat(b, "Alice", "energy") - e0
            print("\n[e2e] energy Bobby heard from Alice's voice through the filter: %.3g" % heard)
            assert heard > 0.01, "a voice goes through the filter and the call: %g" % heard
            await until(lambda: stat(b, "Alice", "out") > 0.01, 10, "and comes out of Bobby's audio chain")
    run(go(), 240)


def test_volume_is_boosted_up_close_full_to_30_yd_quiet_at_60_and_a_shout_stays_louder_further_out(tmp_path, monkeypatch):
    """Alice talks for half a minute, then shouts for a couple of seconds, on a loop. Bobby listens from 60 yd: an ordinary voice is
    quiet there and a shout is louder. At 30 yd the voice is at its full volume, right next to each other it is boosted."""
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.shouting_wav(tmp_path / "shout.wav")))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc, {"keyboard_filter": True})
            curve = b.peers.cfg.curve
            sc.walk_to("Alice", "Bobby", 60)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED" and (peer(a, "Bobby") or {}).get("state") == "CONNECTED", 60,
                        "connected at 60 yd")
            s = lambda k, d=0.0: stat(b, "Alice", k, d)             # noqa: E731
            seen = []                                                # what Bobby's icon for Alice says, ten times a second

            async def sample():
                while True:
                    seen.append((peer(b, "Alice") or {}).get("vlevel"))
                    await asyncio.sleep(0.1)
            sampler = asyncio.create_task(sample())
            # an ordinary voice is quiet at 60 yd ...
            await until(lambda: s("fx", "") == "chain" and s("lvl", None) is not None and s("loud") < 0.1, 30, "Bobby's page is hearing Alice talk")
            talk = voice_gain(60, curve, 0.0)
            await until(lambda: abs(s("gain", -1) - talk) < 0.05 and s("flat") == pytest.approx(curve["full"], abs=0.6), 20,
                        "talking at 60 yd has volume %.2f (page says %r, full volume to %r yd)" % (talk, s("gain", -1), s("flat")))
            # ... a shout is louder and stays at full volume further out
            await until(lambda: s("loud") > 0.8 and s("gain") > talk + 0.1 and s("flat") > curve["full"] + 0.8 * (curve["full_loud"] - curve["full"]), 100,
                        "the shout is louder at 60 yd (loud %r, full volume to %r yd, volume %r)" % (s("loud"), s("flat"), s("gain")))
            print("\n[e2e] 60 yd: talking %.2f, a shout %.2f (loudness %.2f, full volume to %.1f yd)" % (talk, s("gain"), s("loud"), s("flat")))
            await until(lambda: s("loud") < 0.05 and abs(s("gain", -1) - talk) < 0.05, 40, "and when the shout is over the voice is back to its talking volume")
            await asyncio.sleep(3.5)                                 # (the icon holds a moment longer than the range does)
            sampler.cancel()
            runs = [v for i, v in enumerate(seen) if v is not None and (i == 0 or v != seen[i - 1])]
            print("[e2e] the icon beside Alice's name, in order: %s" % runs)
            assert 2 in runs, "the shout showed as loud (vol_high): %r" % runs
            first_loud = runs.index(2)
            assert 1 in runs[:first_loud], "and it was speaking (vol_mid) before that, not idle straight to loud: %r" % runs
            assert 1 in runs[first_loud:], "and speaking again when the shout was over: %r" % runs
            assert len(runs) <= 8, "and steady: a handful of changes in all, not a flicker: %r" % runs
            assert (peer(b, "Alice") or {}).get("vstate") in ("s", "o"), "(and the letter the game is told follows it)"

            # closer: 30 yd is the full volume, and right next to each other it is boosted
            sc.walk_to("Alice", "Bobby", 30)
            await until(lambda: s("loud") < 0.1 and abs(s("gain") - 1.0) < 0.05, 30, "at 30 yd an ordinary voice is at 100%% (page says %r)" % s("gain"))
            sc.walk_to("Alice", "Bobby", 2)
            want2 = voice_gain(2, curve)
            await until(lambda: abs(s("gain") - want2) < 0.03 and abs(s("boost") - want2) < 0.03, 20,
                        "next to each other the volume is boosted by the curve's own %.2f (gain %r, boost %r)" % (want2, s("gain"), s("boost")))
            sc.walk_to("Alice", "Bobby", 6)
            want6 = voice_gain(6, curve)
            await until(lambda: abs(s("gain") - want6) < 0.03 and 1.05 < s("boost") < want2 - 0.1, 20,
                        "at 6 yd less so (%.2f): the boost scales with the distance (gain %r, boost %r)" % (want6, s("gain"), s("boost")))
            assert s("vol") == 1.0, "the speaker element is at full volume and the boost carries the rest"
            assert a.gate_error == "" and b.fx_error == "", (a.gate_error, b.fx_error)
    run(go(), 420)


def test_what_each_player_is_doing_is_shared_over_the_voice_connection(tmp_path):
    """Muting and deafening on one side show on the other, over a real WebRTC data channel, both ways."""
    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc)
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED" and (peer(a, "Bobby") or {}).get("state") == "CONNECTED", 40, "connected")
            vs = lambda app, who: (peer(app, who) or {}).get("vstate")             # noqa: E731
            await until(lambda: vs(b, "Alice") in ("o", "s") and vs(a, "Bobby") in ("o", "s"), 20, "each side learns the other's microphone is open (%r, %r)" % (vs(b, "Alice"), vs(a, "Bobby")))
            a._on_ui({"cmd": "mute", "value": True})
            await until(lambda: vs(b, "Alice") == "m", 15, "Alice mutes: Bobby's side shows muted")
            assert vs(a, "Bobby") in ("o", "s"), "and Bobby is unaffected"
            assert stat(a, "Bobby", "muted", None) is False, "before deafening, Bobby's voice is playing on Alice's side"
            a._on_ui({"cmd": "deafen", "value": True})
            await until(lambda: vs(b, "Alice") == "d", 15, "Alice deafens: Bobby's side shows deafened")
            await until(lambda: stat(a, "Bobby", "muted", None) is True, 10, "and on Alice's side every voice's output is muted: all incoming sound stops")
            assert stat(b, "Alice", "muted", None) is False, "while Bobby still hears Alice (deafen is only about what Alice hears)"
            a._on_ui({"cmd": "deafen", "value": False})
            await until(lambda: stat(a, "Bobby", "muted", None) is False, 10, "undeafened: voices play again")
            a._on_ui({"cmd": "mute", "value": False})
            await until(lambda: vs(b, "Alice") in ("o", "s"), 15, "and back")
            b._on_ui({"cmd": "set_mode", "mode": "ptt"})
            await until(lambda: vs(a, "Bobby") == "m", 15, "push to talk with the key up counts as muted")
            b.ptt.virtual = True
            b.ptt.sample()
            await until(lambda: vs(a, "Bobby") in ("o", "s"), 15, "and open while the key is held")
            # what the game is shown for each of them
            st = b.build_state()
            assert st["peers"][0]["vstate"] in ("o", "s")
    run(go(), 240)


def test_a_player_on_the_mute_list_is_not_played_and_comes_back_when_taken_off(tmp_path):
    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc)
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40, "connected")
            await until(lambda: stat(b, "Alice", "rx") > 4000 and stat(b, "Alice", "vol") > 0.9, 20, "Alice is heard at full element volume (vol %r)" % stat(b, "Alice", "vol"))
            e0 = stat(b, "Alice", "rx")
            b._on_ui({"cmd": "mute_player", "name": "Alice", "value": True})
            await until(lambda: stat(b, "Alice", "vol") == 0 and stat(b, "Alice", "boost") <= 1.0, 10, "muted: nothing is played (vol %r boost %r)" % (stat(b, "Alice", "vol"), stat(b, "Alice", "boost")))
            await until(lambda: stat(b, "Alice", "rx") > e0 + 2000, 10, "the connection stays up and audio still arrives (they can still hear you)")
            assert (peer(b, "Alice") or {}).get("muted_by_me") is True
            b._on_ui({"cmd": "mute_player", "name": "Alice", "value": False})
            await until(lambda: stat(b, "Alice", "vol") > 0.9, 10, "taken off the list: heard again")
    run(go(), 200)


@pytest.mark.parametrize("level_db, want", [(-48, "lift"), (-10, "ease")])
def test_every_microphone_is_brought_to_about_the_same_level_on_the_listeners_side(tmp_path, monkeypatch, level_db, want):
    """A very quiet microphone is lifted and a very hot one eased down, within limits, once the listener has heard the speaker for a few seconds."""
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.speech_wav(tmp_path / "speech.wav", seconds=20.0, level_db=level_db)))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc, {"keyboard_filter": False}, {"auto_level": True})
            sc.walk_to("Alice", "Bobby", 12)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40, "connected")
            lev = lambda: stat(b, "Alice", "lev", None)                              # noqa: E731
            if want == "lift":
                await until(lambda: lev() is not None and lev() >= 9.0, 90, "the quiet voice is lifted (now %r dB)" % lev())
                assert lev() <= 12.01, "by no more than 12 dB"
                assert stat(b, "Alice", "boost") > 2.0, "which the page carries on its boost stage (%r)" % stat(b, "Alice", "boost")
            else:
                await until(lambda: lev() is not None and lev() <= -5.0, 90, "the hot voice is eased (now %r dB)" % lev())
                assert lev() >= -6.01, "by no more than 6 dB"
            print("\n[e2e] a microphone at %d dB: levelled by %.1f dB, base %r" % (level_db, lev(), stat(b, "Alice", "lvl", None)))
    run(go(), 260)


def test_the_microphone_setup_measures_a_real_microphone_in_a_real_page(tmp_path, monkeypatch):
    """A recording stands in for the microphone: four seconds of a quiet room, five of talking, four of shouting (played once, with no
    loop). The page listens for three steps, measures in the gate's own band, the app turns that into a bar between the room and the
    voice, and the page is handed the result."""
    import wavs
    rnd = wavs.Rng(99)
    sig = [0.0] * wavs._sec(14.0)
    wavs.room(sig, -62, rnd)
    wavs.speech(sig, wavs._sec(4.5), wavs._sec(9.5), -24, rnd)
    wavs.speech(sig, wavs._sec(9.5), wavs._sec(13.5), -10, rnd)
    wav = wavs.write(tmp_path / "setup.wav", sig)
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wav) + "%noloop")

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a = await sc.add("Alice", at=(0, 0), real_browser=True, cfg=REAL)
            await until(lambda: any(p.media for p in a.pages), 40, "the media page")
            await until(lambda: a.mic.get("state") == "ok", 30, "the fake microphone")
            a._start_calibration([3, 5, 4])                                  # (the app's own start has no plan: its steps are 5, 7 and 5 seconds)
            seen = set()

            def watch():
                seen.add(a.cal["phase"])
                return a.cal["phase"] in ("done", "idle") and a.cal["quality"]
            await until(watch, 60, "the setup to finish (phase %r)" % a.cal["phase"])
            assert {"quiet", "normal", "loud"} <= seen, "the three steps were announced: %r" % seen
            f = a.cal_facts
            assert a.cal["quality"] == "good", (a.cal, f)
            assert -80 < f["floor_db"] < -55, "the room's noise in the speech band: %r" % f
            assert f["speech_median_db"] > f["floor_db"] + 20, "talking is far above the room: %r" % f
            import math
            bar = f["gate_db"] - 20 * math.log10(f["input_gain"])             # the gate comes after the volume change: undo it to compare with what was heard
            assert f["floor_db"] + 4 < bar < f["speech_low_db"] + 1, "the bar sits between them: %r (before the volume change: %.1f)" % (f, bar)
            assert f["input_gain"] < 1.0, "this recording's shout touches full scale, so the microphone is turned down: %r" % f
            assert a.cfg.audio.gate_mode == "manual" and a.cfg.audio.calibrated is True and a.cfg.audio.gate_db == f["gate_db"]
            assert 0.25 <= a.cfg.audio.input_gain <= 2.0
    run(go(), 150)


def test_the_panels_one_bar_shows_the_signal_the_gate_judges_even_while_the_gate_blocks_it(tmp_path, monkeypatch):
    """A real engine, a real keyboard filter, someone talking steadily at about -24 dB on the microphone. What the game's bar is drawn from (gl, gt, go)
    is the filter's own measure: with the bar set far above the voice the level still rises well over the room's noise while the gate stays shut,
    and with the bar below the voice the same level is what opens it."""
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.speech_wav(tmp_path / "speech.wav")))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc, {"keyboard_filter": True})
            await until(lambda: a.mic.get("gate") is True, 15, "the filter is on")
            a.level_watch_until = a.clock() + 600                                   # a meter is on screen
            a._on_ui({"cmd": "gate_mode", "value": "manual"})
            a._on_ui({"cmd": "gate_db", "value": -5})                               # far above any voice: the gate stays shut
            await until(lambda: a._gate_meter().get("gt") == -5, 15, "the filter holds the new bar (%r)" % (a._gate_meter(),))
            seen = []

            async def sample(seconds):
                end = a.clock() + seconds
                while a.clock() < end:
                    seen.append(a._gate_meter())
                    await asyncio.sleep(0.1)
            await sample(6.0)
            top = max(s.get("gl", -100) for s in seen)
            print("\n[e2e] loudest level the bar showed behind a shut gate: %d dB (bar at -5)" % top)
            assert top > -45, "the live signal is drawn while the gate blocks it: %d" % top
            assert all(s.get("go") == 0 for s in seen if "go" in s), "and the gate never opened for a bar above the voice"
            a._on_ui({"cmd": "gate_db", "value": -70})                              # far below the voice
            await until(lambda: a._gate_meter().get("gt") in (-70, -73), 15, "the bar moved down")
            seen.clear()
            await sample(5.0)
            assert any(s.get("go") == 1 for s in seen), "the same signal now opens the gate: %r" % (seen[-1],)
            open_samples = [s for s in seen if s.get("go") == 1]
            assert all(s["gl"] > s["gt"] - 6 for s in open_samples), "an open gate is a signal above the bar: %r" % (open_samples[:3],)
    run(go(), 240)


def test_a_voice_comes_from_the_side_the_speaker_is_on_in_each_sound_mode(tmp_path, monkeypatch):
    """Real engines, a real Web Audio chain: Bobby talks, Alice listens. The loudest sample leaving the chain for each ear says where it comes from."""
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.speech_wav(tmp_path / "speech.wav")))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc)
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(a, "Bobby") or {}).get("state") == "CONNECTED" and (peer(b, "Alice") or {}).get("state") == "CONNECTED", 40, "connected")
            p = a.peers.peers["bobby"]

            async def ears(mode, az, seconds=5.0):
                a.cfg.audio.spatial = mode
                a.media({"cmd": "spatial", "value": mode})
                a.media({"cmd": "dir", "sid": p.sid, "ep": p.ep, "az": az})
                await asyncio.sleep(1.5)                                     # the direction eases in
                left = right = 0.0
                end = a.clock() + seconds
                while a.clock() < end:
                    left, right = max(left, stat(a, "Bobby", "outl")), max(right, stat(a, "Bobby", "outr"))
                    await asyncio.sleep(0.25)
                print("\n[e2e] %-10s az=%-5s left %.4f right %.4f" % (mode, az, left, right))
                return left, right
            for mode in ("headphones", "speakers"):
                l, r = await ears(mode, 90)
                assert r > l * 2 and r > 0.005, "%s: a voice on your right is mostly in your right ear (%.4f vs %.4f)" % (mode, l, r)
                l, r = await ears(mode, -90)
                assert l > r * 2 and l > 0.005, "%s: a voice on your left is mostly in your left ear (%.4f vs %.4f)" % (mode, l, r)
            l, r = await ears("headphones", 0)
            assert abs(l - r) < 0.35 * max(l, r) and max(l, r) > 0.005, "straight ahead is in the middle (%.4f vs %.4f)" % (l, r)
            l, r = await ears("mono", 90)
            assert abs(l - r) < 0.25 * max(l, r) and max(l, r) > 0.005, "mono has no sides whatever the direction (%.4f vs %.4f)" % (l, r)
            assert (peer(a, "Bobby") or {}).get("stats", {}).get("spatial") == "mono", "the engine says which mode it is in"
    run(go(), 300)


def test_the_microphone_test_keeps_your_voice_off_the_line_and_goes_on_until_stopped(tmp_path, monkeypatch):
    import wavs
    monkeypatch.setenv("EQVOICE_FAKE_AUDIO_FILE", str(wavs.speech_wav(tmp_path / "speech.wav")))

    async def go():
        async with Scene(tmp_path, ice_servers=[]) as sc:
            a, b = await _two_real(sc, {"keyboard_filter": True})
            sc.walk_to("Alice", "Bobby", 8)
            await until(lambda: (peer(b, "Alice") or {}).get("state") == "CONNECTED" and (peer(a, "Bobby") or {}).get("state") == "CONNECTED", 40, "connected")
            e0 = stat(b, "Alice", "energy")
            await until(lambda: stat(b, "Alice", "energy") - e0 > 0.01, 25, "Bobby hears Alice talking")
            a._set_mic_test(True)
            await until(lambda: a.mic_test and a.media_page_ok() if hasattr(a, "media_page_ok") else a.mic_test, 10, "testing")
            await asyncio.sleep(2.5)                                         # whatever was already on its way arrives
            e1 = stat(b, "Alice", "energy")
            await asyncio.sleep(8.0)
            leaked = stat(b, "Alice", "energy") - e1
            print("\n[e2e] energy Bobby heard from Alice during her microphone test: %.3g" % leaked)
            assert leaked < 0.0005, "nothing of the test reaches the other player: %g" % leaked
            assert a.mic_test, "the test is still on (nobody pressed the button)"
            a._set_mic_test(False)
            e2 = stat(b, "Alice", "energy")
            await until(lambda: stat(b, "Alice", "energy") - e2 > 0.01, 25, "stopped: Bobby hears Alice again, as before")
    run(go(), 300)
