"""A full rehearsal of the real-WoW procedure, with simulated games, and the boundary report must pass all 20 steps.

Processes, as in the real test: a hub (Python, tracing on), two desktop clients (each with a headless Edge media
engine, fake microphone), two pretend games sharing ONE folder (so the per-character files are what keeps the two
clients apart). The test then does what a person would: walks the characters together, holds push-to-talk in each
client in turn, walks one away, and waits for the connection to close. At the end tools/pipeline_report.py reads the
logs of the hub and both clients and says which boundaries were reached.

Needs Edge or Chrome; skipped without one or with EQVOICE_E2E=0. Takes about a minute and a half.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from eqvoice_client import browser
from eqvoice_client.bridge.sim import SimWorld

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pipeline_report  # noqa: E402

pytestmark = pytest.mark.skipif(browser.find_browser() is None or os.environ.get("EQVOICE_E2E") == "0",
                                reason="needs a Chromium-based browser")


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def api(port, token, path="/api/state", method="GET", body=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s?token=%s" % (port, path, token), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def wait(pred, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = pred()
        except Exception:                                      # noqa: BLE001
            v = None
        if v:
            return v
        time.sleep(0.25)
    raise AssertionError("timed out waiting for " + what)


def peer(state, name):
    return next((p for p in state["peers"] if p["name"] == name), None)


def test_the_real_procedure_with_simulated_games(tmp_path):
    procs = []
    try:
        hub_port = free_port()
        hub_log = open(tmp_path / "hub.log", "w")
        procs.append(subprocess.Popen([sys.executable, "-m", "eqvoice_signalling", "--port", str(hub_port), "--trace"],
                                      cwd=str(ROOT / "eqvoice-signalling"), stdout=hub_log, stderr=subprocess.STDOUT))
        wait(lambda: urllib.request.urlopen("http://127.0.0.1:%d/health" % hub_port, timeout=1).read(), 15, "the hub")

        world_path = tmp_path / "world.json"
        world = SimWorld(world_path)
        world.add("Alice", 0, 0).add("Bobby", 500, 0)                # 500 yards apart: not even loaded
        game_dir = tmp_path / "game"                                # ONE game folder for both
        clients = {}
        for i, name in enumerate(["Alice", "Bobby"]):
            port = free_port()
            home = tmp_path / ("home-" + name.lower())
            args = [sys.executable, "-m", "eqvoice_client", "--home", str(home), "--port", str(port), "--token", "tok",
                    "--signalling", "ws://127.0.0.1:%d/ws" % hub_port, "--source", "sim", "--sim-world", str(world_path),
                    "--sim-character", name, "--sim-folder", str(game_dir), "--character", name,
                    "--headless", "--fake-media", "--mute-audio", "--log-level", "INFO"]
            procs.append(subprocess.Popen(args, cwd=str(ROOT / "eqvoice-client"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            clients[name] = (port, home)

        def state(name):
            return api(clients[name][0], "tok")

        # step 1-6: identity, registration, rosters
        wait(lambda: all(state(n)["identity"] for n in clients), 40, "both clients to learn who they are")
        wait(lambda: all(state(n)["signalling"]["state"] == "registered" for n in clients), 20, "both to register")
        wait(lambda: all(state(n)["metrics"]["roster"] == 1 for n in clients), 20, "each to receive the other's name")
        wait(lambda: all(state(n)["mic"]["state"] == "ok" for n in clients), 30, "both media engines and fake microphones")
        assert (game_dir / "CustomData" / "eqvoice-Alice.txt").exists() and (game_dir / "CustomData" / "eqvoice-Bobby.txt").exists(), \
            "each game writes its own per-character file"
        assert (game_dir / "CustomData" / "eqvoice-in-Alice.txt").read_text() != (game_dir / "CustomData" / "eqvoice-in-Bobby.txt").read_text(), \
            "and each client wrote its own roster file"

        # 7-13: walk within range
        world.move("Bobby", 12, 0)
        wait(lambda: all((peer(state(n), "Bobby" if n == "Alice" else "Alice") or {}).get("state") == "CONNECTED" for n in clients), 30,
             "the two to connect")

        # 14: push to talk, one client at a time (the keys are virtual: the REST button)
        for speaker, listener in (("Alice", "Bobby"), ("Bobby", "Alice")):
            api(clients[speaker][0], "tok", "/api/ptt", "POST", {"down": True})
            wait(lambda: state(speaker)["voice"]["tx"] is True, 5, "%s's transmit to switch on" % speaker)
            wait(lambda: peer(state(listener), speaker)["stats"].get("energy", 0) > 0.05, 15, "%s to hear %s" % (listener, speaker))
            time.sleep(1.5)
            api(clients[speaker][0], "tok", "/api/ptt", "POST", {"down": False})
            time.sleep(1.0)

        # 15-17: walk apart
        for yards in (20, 30, 38):
            world.move("Bobby", yards, 0)
            time.sleep(2.5)
        # 18-20: past the leave radius, then wait for the teardown
        world.move("Bobby", 100, 0)
        wait(lambda: (peer(state("Alice"), "Bobby") or {}).get("state") == "OUT_OF_RANGE_GRACE", 15, "Alice to fade Bobby")
        wait(lambda: (peer(state("Alice"), "Bobby") or {}).get("state") == "DISCONNECTED", 20, "the connection to close")
        time.sleep(2.0)
    finally:
        for p in procs:
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=20)
            except Exception:                                  # noqa: BLE001
                p.kill()
        # A killed client never stops its media engine. Left alone, that headless fake-microphone browser kept reconnecting
        # and, on 2026-10-16, became the voice engine of a REAL client. Stop every browser that used this test's profiles.
        browser.kill_profile(tmp_path)
        time.sleep(1.0)
        assert not browser.profile_pids(tmp_path), "the rehearsal left a media engine running"

    clients_loaded, hub = pipeline_report.load(["Alice=%s" % (tmp_path / "home-alice"), "Bobby=%s" % (tmp_path / "home-bobby")],
                                               str(tmp_path / "hub.log"))
    steps = pipeline_report.evaluate(clients_loaded, hub)
    report = pipeline_report.render(steps)
    print("\n" + report)
    bad = [s for s in steps if s.status != pipeline_report.PASS]
    assert not bad, "boundaries not reached:\n" + report
