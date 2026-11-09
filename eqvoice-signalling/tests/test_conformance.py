"""Cross-implementation conformance: the recorded conversations in protocol/conformance.json.

* the file is current with the Python hub (the reference);
* the Python hub replays every conversation to the byte;
* the JavaScript hub (eqvoice-signalling-cf/src/hub.js) does too, run in headless Chromium (needs Edge or
  Chrome with WebCrypto Ed25519; skipped otherwise, or with EQVOICE_E2E=0).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONF = ROOT / "protocol" / "conformance.json"

sys.path.insert(0, str(HERE))
import conformance_lib  # noqa: E402
import jsrun  # noqa: E402
import conformance_scenarios  # noqa: E402


def doc():
    return json.loads(CONF.read_text(encoding="utf-8"))


def test_the_recorded_file_is_current():
    fresh = {"version": 1, "about": doc()["about"], "scenarios": conformance_scenarios.documents()}
    assert doc() == json.loads(json.dumps(fresh)), "run: python tools/gen_conformance.py"


def test_the_scenarios_cover_the_protocol():
    seen_t, seen_err, seen_close = set(), set(), set()
    for sc in doc()["scenarios"]:
        for step in sc["transcript"]:
            for e in step:
                if len(e) == 2 and isinstance(e[1], dict):
                    seen_t.add(e[1]["t"])
                    if e[1]["t"] == "error":
                        seen_err.add(e[1]["code"])
                elif len(e) >= 3 and e[1] == "close":
                    seen_close.add(e[2])
    assert {"hello", "registered", "roster_update", "peer_available", "peer_out_of_range", "offer", "answer", "ice", "bye",
            "pong", "error"} <= seen_t
    assert {"bad_sid", "bad_realm", "bad_name", "bad_sig", "bad_code", "unsupported_version", "name_in_use", "sid_taken",
            "not_registered", "already_registered", "realm_full", "not_linked", "rate_limited", "too_big", "too_many",
            "bad_msg", "bad_json", "register_timeout"} <= seen_err
    assert {1000, 1009, 4408, 4409} <= seen_close


@pytest.mark.parametrize("sc", doc()["scenarios"], ids=lambda s: s["name"])
def test_python_hub_reproduces_the_recording(sc):
    got = conformance_lib.replay({"setup": sc["setup"], "steps": sc["steps"]})
    for i, (want, have) in enumerate(zip(sc["transcript"], got["transcript"])):
        assert have == want, "step %d %r" % (i, sc["steps"][i])
    assert got["stats"] == sc["stats"]


# ----------------------------------------------------------------- the JavaScript hub

@pytest.mark.skipif(not jsrun.available(), reason="needs a Chromium-based browser")
def test_javascript_hub_reproduces_the_recording():
    res = asyncio.run(jsrun.run_page("conformance.html", extra_files={"/conformance.json": CONF}))
    if "unsupported" in res:
        pytest.skip(res["unsupported"])
    assert "crashed" not in res, res.get("crashed")
    assert res["scenarios"] == len(doc()["scenarios"])
    assert res["steps"] == sum(len(s["steps"]) for s in doc()["scenarios"]), "every step was actually replayed"
    assert not res["failures"], json.dumps(res["failures"][:2], indent=1)[:3000]
