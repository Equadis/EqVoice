"""The Cloudflare Durable Object adapter (eqvoice-signalling-cf/src/worker.js), run in headless Chromium against a
mock of the Workers runtime. Catches mistakes in the adapter's own logic: the hibernation rebuild, alarms, close
handling, legal close codes. It cannot prove the real runtime agrees with the mock (try `npx wrangler dev`)."""
import asyncio

import pytest

import jsrun


@pytest.mark.skipif(not jsrun.available(), reason="needs a Chromium-based browser")
def test_the_durable_object_adapter_against_the_mock_runtime():
    res = asyncio.run(jsrun.run_page("adapter.html"))
    results = res["results"]
    bad = [r for r in results if not r["ok"]]
    assert not bad, "\n".join("%s: %s" % (r["name"], r.get("detail")) for r in bad)
    assert len(results) >= 30, "the checks really ran (%d)" % len(results)
