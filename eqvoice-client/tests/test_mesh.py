"""Five strangers in one place: the mesh forms without a storm, volumes follow each pair's own distance, people come and go
without disturbing the others, a failing or restarting peer heals, and nobody is left audible after leaving.

The media pages here are scripted (they answer like a working WebRTC stack and record every command); the real-engine version of
the same crowd is test_e2e_browser.py::test_five_strangers_real_engines and tools/mesh_bench.py.
"""
import asyncio

import pytest

from eqvoice_client.peers import voice_gain
from scene import FakePage, Scene, pstate, run, until

NAMES = ["Ann", "Bobby", "Cyrus", "Dina", "Edda"]
LINE = {"Ann": 0, "Bobby": 5, "Cyrus": 15, "Dina": 25, "Edda": 35}          # yards along one road: every pair is within 40


def sid_of(apps, viewer, other):
    return next(p["sid"] for p in apps[viewer].peers.snapshot() if p["name"] == other)


def connected(apps, group):
    return all(pstate(apps[s], o) == "CONNECTED" for s in group for o in group if o != s)


async def crowd(sc, names, spots):
    apps = {}
    for n in names:
        apps[n] = await sc.add(n, at=(spots[n], 0.0))
    return apps


def opens(sc, who=None):
    return {n: len(p.named("open")) for n, p in sc.pages.items() if who is None or n in who}


def closes_for(sc, viewer, sid):
    return [c for c in sc.pages[viewer].named("close") if c["sid"] == sid]


def test_five_strangers_form_a_full_mesh_and_each_pair_has_its_own_volume(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            apps = await crowd(sc, NAMES, LINE)
            await until(lambda: connected(apps, NAMES), 60, "full mesh CONNECTED: " + repr({n: apps[n].peers.snapshot() for n in NAMES}))
            assert sc.hub.stats()["links"] == 10, "five people, ten pairs"
            assert all(apps[n].peers.aggregate()["connected"] == 4 for n in NAMES)
            # exactly one offer per pair: nobody dialled twice, there was no connection storm
            assert sum(opens(sc).values()) == 20, "ten pairs, each side opens its end once: %r" % opens(sc)
            assert sc.hub.counters["rate_limited"] == 0
            # every pair's page volume follows THAT pair's distance (the commands reach the page a moment after CONNECTED)
            def last_gain(v, o):
                gs = [c for c in sc.pages[v].named("gain") if c["sid"] == sid_of(apps, v, o)]
                return gs[-1] if gs else None
            await until(lambda: all(last_gain(v, o) for v in NAMES for o in NAMES if v != o), 15, "a volume command for every pair")
            for v in NAMES:
                for o in NAMES:
                    if v == o:
                        continue
                    prox = apps[v].cfg.proximity
                    want = voice_gain(abs(LINE[v] - LINE[o]), apps[v].peers.cfg.curve)
                    await until(lambda: abs(last_gain(v, o)["value"] - want) < 0.07, 8, "%s hears %s at %.2f" % (v, o, want))
                    g = last_gain(v, o)
                    assert g["send"] is True, "we transmit to everyone in call range: a raised voice carries further than an ordinary one"
                    assert g["gate"] is True and g["dist"] == pytest.approx(abs(LINE[v] - LINE[o]), abs=0.1), "and the page is told how far away they are"
    run(go(), 120)


def test_people_arriving_and_leaving_do_not_disturb_the_others(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            huddle = {"Ann": 0, "Bobby": 3, "Cyrus": 6}
            far = {"Dina": 4000, "Edda": 5000}
            apps = await crowd(sc, NAMES, dict(huddle, **far))
            await until(lambda: connected(apps, list(huddle)), 40, "the first three connect")
            base = opens(sc, list(huddle))

            # one person arrives
            sc.world.move("Dina", 9.0, 0.0)
            await until(lambda: connected(apps, ["Ann", "Bobby", "Cyrus", "Dina"]), 40, "four in the mesh")
            now = opens(sc, ["Ann", "Bobby", "Cyrus"])
            assert all(now[n] == base[n] + 1 for n in now), "the three only opened one new call each: %r -> %r" % (base, now)

            # two more arrive at once (one of them is new, one is Dina's neighbour)
            sc.world.move("Edda", 12.0, 0.0)
            await until(lambda: connected(apps, NAMES), 60, "all five connected")
            assert sc.hub.stats()["links"] == 10

            # one leaves: everybody fades him first (volume 0, no more sending), then the call is closed
            edda = {v: sid_of(apps, v, "Edda") for v in NAMES if v != "Edda"}
            sc.world.move("Edda", 20000.0, 0.0)                              # out of everyone's lookup range, and alone
            await until(lambda: all(pstate(apps[v], "Edda") in ("OUT_OF_RANGE_GRACE", "DISCONNECTED", None) for v in edda), 15, "faded")
            def last_gain(v, sid):
                gs = [c for c in sc.pages[v].named("gain") if c["sid"] == sid]
                return gs[-1] if gs else {"value": None, "send": None}
            await until(lambda: all(last_gain(v, sid)["value"] == 0 for v, sid in edda.items()), 8, "the fade-out reached every page")
            for v, sid in edda.items():
                g = last_gain(v, sid)
                assert g["value"] == 0 and g["send"] is False, "no stale audio: %s still hears or sends to Edda: %r" % (v, g)
            await until(lambda: all(closes_for(sc, v, sid) for v, sid in edda.items()), 20, "the calls to Edda are closed")
            assert connected(apps, NAMES[:4]), "the rest of the mesh was not disturbed"
            assert all(apps[v].peers.aggregate()["connected"] == 3 for v in NAMES[:4])

            # two leave together
            gone = {v: [sid_of(apps, v, "Cyrus"), sid_of(apps, v, "Dina")] for v in ("Ann", "Bobby")}
            sc.world.move("Cyrus", 30000.0, 0.0)
            sc.world.move("Dina", 40000.0, 0.0)
            await until(lambda: all(closes_for(sc, v, s) for v, sids in gone.items() for s in sids), 25, "both calls closed on both survivors")
            assert connected(apps, ["Ann", "Bobby"])
            await until(lambda: sc.hub.stats()["links"] == 1, 15, "the hub drops the other links after its grace period")
    run(go(), 240)


def test_one_failing_pair_is_retried_politely_and_the_rest_carry_on(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            apps = await crowd(sc, NAMES, {"Ann": 0, "Bobby": 3, "Cyrus": 6, "Dina": 9, "Edda": 12})
            await until(lambda: connected(apps, NAMES), 60, "full mesh")
            before = opens(sc)
            page, sid = sc.pages["Ann"], sid_of(apps, "Ann", "Bobby")
            ep = next(p for p in apps["Ann"].peers.peers.values() if p.name == "Bobby").ep
            await page.ws.send_json({"t": "peer", "sid": sid, "ep": ep, "type": "state", "ice": "failed", "conn": "failed"})
            await until(lambda: pstate(apps["Ann"], "Bobby") in ("DISCONNECTED", "NEARBY_PENDING", "CONNECTING"), 8, "the attempt is dropped")
            assert connected(apps, ["Cyrus", "Dina", "Edda"]) and all(pstate(apps["Ann"], o) == "CONNECTED" for o in ("Cyrus", "Dina", "Edda")), \
                "a failure between two people is theirs alone"
            await until(lambda: connected(apps, NAMES), 40, "the pair heals by itself (back-off, then a fresh call)")
            after = opens(sc)
            assert after["Ann"] + after["Bobby"] == before["Ann"] + before["Bobby"] + 2, "one new call each, not a storm: %r -> %r" % (before, after)
            assert all(after[n] == before[n] for n in ("Cyrus", "Dina", "Edda")), "the other three never opened anything"
    run(go(), 200)


def test_a_peer_whose_media_engine_restarts_rejoins_the_mesh(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            apps = await crowd(sc, NAMES, {"Ann": 0, "Bobby": 3, "Cyrus": 6, "Dina": 9, "Edda": 12})
            await until(lambda: connected(apps, NAMES), 60, "full mesh")
            others = NAMES[:4]
            kept = opens(sc, others)
            await sc.pages["Edda"].close()                                     # her browser window was closed
            await until(lambda: all(pstate(apps[v], "Edda") != "CONNECTED" for v in others), 15, "everyone drops Edda's calls")
            assert connected(apps, others), "and nobody else is touched"
            assert opens(sc, others) == kept
            pg = FakePage(apps["Edda"])                                        # ... and she reopens it
            await pg.connect(sc.session)
            sc.pages["Edda"] = pg
            await until(lambda: connected(apps, NAMES), 60, "the whole mesh again")
            assert sc.hub.stats()["links"] == 10
    run(go(), 240)


def test_the_mesh_numbers_are_reported(tmp_path):
    async def go():
        async with Scene(tmp_path) as sc:
            apps = await crowd(sc, NAMES, {"Ann": 0, "Bobby": 3, "Cyrus": 6, "Dina": 9, "Edda": 12})
            await until(lambda: connected(apps, NAMES), 60, "full mesh")
            agg = apps["Ann"].peers.aggregate()
            assert agg["active"] == agg["connected"] == 4 and agg["setups"] == 4
            assert agg["setup_ms_avg"] is not None and 0 <= agg["setup_ms_avg"] <= agg["setup_ms_max"] < 20000
            m = apps["Ann"].metrics()
            assert m["mesh"]["connected"] == 4 and "resources" in m
            res = apps["Ann"].read_resources()                                # no engine of ours here (scripted pages)
            assert res["client"]["rss_mb"] > 10 and res["engine"]["procs"] == 0
            assert apps["Ann"].build_state()["metrics"]["mesh"]["connected"] == 4
    run(go(), 100)
