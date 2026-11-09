"""The client's whole pipeline against a hub that is already running: `npx wrangler dev` (the Cloudflare Worker on
the real Workers runtime) or a deployed Worker. Opt-in:

    EQVOICE_CF_URL=ws://127.0.0.1:8787/ws python -m pytest tests/test_external_hub.py
"""
import asyncio
import os
import secrets

import pytest

from scene import Scene, pstate, run, until

URL = os.environ.get("EQVOICE_CF_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set EQVOICE_CF_URL to a running hub")


def test_unrelated_characters_meet_talk_and_part_through_an_external_hub(tmp_path):
    async def go():
        async with Scene(tmp_path, hub_url=URL, realm="Ext" + secrets.token_hex(3)) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(5000, 0))
            pa, pb = sc.pages["Alice"], sc.pages["Bobby"]
            await until(lambda: a.signalling.registered and b.signalling.registered, 15, "both registered with the hub")
            await until(lambda: "Bobby" in a.roster and "Alice" in b.roster, 10, "rosters")
            assert pstate(a, "Bobby") is None, "far apart: nothing"
            sc.walk_to("Alice", "Bobby", 25)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 25, "connected")
            await until(lambda: 0.1 < pa.last_gain()["value"] < 0.4, 5, "volume for 25 yards")
            sc.walk_to("Alice", "Bobby", 5)
            await until(lambda: pa.last_gain()["value"] == 1.0, 6, "full volume close up")
            sc.vanish("Bobby")
            await until(lambda: pstate(a, "Bobby") == "OUT_OF_RANGE_GRACE", 8, "fades")
            await until(lambda: pa.named("close") and pb.named("close"), 15, "both close the connection later")
            # logging out removes the character from the roster at once
            sc.games["Alice"].logout()
            await until(lambda: "Alice" not in b.roster, 8, "gone from the roster")
    run(go(), 120)


def test_a_client_survives_the_hub_object_hibernating(tmp_path):
    """Two clients sit still for 30 s (their only traffic is the 10 s ping and a 4 s keepalive when near
    someone); then they walk up to each other and connect."""
    async def go():
        async with Scene(tmp_path, hub_url=URL, realm="Hib" + secrets.token_hex(3)) as sc:
            a, b = await sc.add("Alice", at=(0, 0)), await sc.add("Bobby", at=(5000, 0))
            await until(lambda: a.signalling.registered and b.signalling.registered and "Bobby" in a.roster, 15, "registered")
            await asyncio.sleep(30)
            assert a.signalling.registered and b.signalling.registered
            sc.walk_to("Alice", "Bobby", 12)
            await until(lambda: pstate(a, "Bobby") == "CONNECTED" and pstate(b, "Alice") == "CONNECTED", 25, "connected after the pause")
    run(go(), 120)
