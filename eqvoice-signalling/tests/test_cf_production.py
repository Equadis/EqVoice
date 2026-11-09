"""The production Wrangler configuration is checked, and built (not uploaded), without an account or a network.

Nothing here deploys anything. `wrangler deploy --dry-run` only bundles the Worker and prints what it would publish.
"""
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CF = ROOT / "eqvoice-signalling-cf"
sys.path.insert(0, str(ROOT / "eqvoice-signalling"))
from eqvoice_signalling.hub import Limits  # noqa: E402

PROD = tomllib.loads((CF / "wrangler.production.toml").read_text(encoding="utf-8"))
DEV = tomllib.loads((CF / "wrangler.toml").read_text(encoding="utf-8"))
WORKER = (CF / "src" / "worker.js").read_text(encoding="utf-8")

NODE = Path(r"D:\Octowow\.eco-work\node\node.exe")
WRANGLER = CF / "node_modules" / "wrangler" / "bin" / "wrangler.js"


def test_production_is_a_separate_worker_that_cannot_overwrite_development():
    assert PROD["name"] != DEV["name"] and PROD["name"].endswith("-prod")
    assert PROD["main"] == DEV["main"] and PROD["compatibility_date"] == DEV["compatibility_date"]
    assert (CF / PROD["main"]).exists()


def test_the_front_door_is_closed_except_the_custom_domain():
    assert PROD["workers_dev"] is True and PROD["preview_urls"] is False, "test build: the free workers.dev address, no per-version preview URLs"
    assert "routes" not in PROD, "the domain is the owner's to fill in (the example stays commented out)"
    assert "# routes = [{ pattern =" in (CF / "wrangler.production.toml").read_text(encoding="utf-8")


def test_the_object_and_its_migration_are_declared():
    binding = PROD["durable_objects"]["bindings"][0]
    assert binding == {"name": "HUB", "class_name": "HubDO"}
    assert PROD["migrations"][0]["new_sqlite_classes"] == ["HubDO"], "SQLite-backed: the only kind the Free plan allows"
    assert "export class HubDO" in WORKER and 'env.HUB' in WORKER


def test_observability_is_on():
    assert PROD["observability"]["enabled"] is True and PROD["observability"]["head_sampling_rate"] == 1


def test_no_secret_and_no_names_in_the_file():
    text = (CF / "wrangler.production.toml").read_text(encoding="utf-8")
    vars_ = PROD["vars"]
    for forbidden in ("JOIN_CODE", "STATS_TOKEN", "TRACE"):
        assert forbidden not in vars_, forbidden + " must not be a plain variable in the production file"
    assert vars_["LOG_EVENTS"] == "1"
    assert not re.search(r'(?i)(token|secret|password|api[_-]?key)\s*=\s*"[^"]+"', text), "no credential written down"
    assert "TRACE" in text and "never here" in text, "the warning about TRACE is there"


def test_every_variable_is_one_the_worker_reads_and_equals_the_code_default():
    read = set(re.findall(r"env\.([A-Z_]+)", WORKER))
    for name in PROD["vars"]:
        assert name in read, "%s is not read by worker.js: a typo would silently do nothing" % name
    d = Limits()
    expect = {"LEASE_SECONDS": d.reserve_seconds, "LINK_RADIUS": d.link_radius, "DISTANCE_TOLERANCE": d.distance_tolerance,
              "MAX_CONNS_PER_IP": d.max_conns_per_ip, "REGISTERS_PER_MINUTE": d.registers_per_ip_per_minute,
              "MAX_CONNECTIONS": d.max_connections}
    for name, want in expect.items():
        assert float(PROD["vars"][name]) == float(want), "%s is written out as the code default (%s)" % (name, want)


def test_the_deployment_guide_covers_the_file():
    guide = (CF / "DEPLOY.md").read_text(encoding="utf-8")
    for name in list(PROD["vars"]) + ["JOIN_CODE", "STATS_TOKEN"]:
        assert name in guide, name + " is documented"
    for cmd in ("--dry-run", "wrangler rollback", "wrangler deployments list", "wrangler versions list", "wrangler secret put", "wrangler tail",
                "wrangler delete", "custom_domain", "hub_smoke.py"):
        assert cmd in guide, cmd
    assert "prepared, not deployed" in guide.lower()


@pytest.mark.skipif(not (NODE.exists() and WRANGLER.exists()), reason="the portable Node and Wrangler are not installed")
def test_it_builds_with_a_dry_run(tmp_path):
    env = dict(os.environ, PATH=str(NODE.parent) + os.pathsep + os.environ.get("PATH", ""), WRANGLER_SEND_METRICS="false",
               NO_UPDATE_NOTIFIER="1", WRANGLER_LOG_PATH=str(tmp_path / "logs"), XDG_CONFIG_HOME=str(tmp_path / "xdg"),
               TMP=str(tmp_path), TEMP=str(tmp_path))
    out = subprocess.run([str(NODE), str(WRANGLER), "deploy", "-c", "wrangler.production.toml", "--dry-run", "--outdir", str(tmp_path / "build")],
                         cwd=str(CF), env=env, capture_output=True, text=True, timeout=240, encoding="utf-8", errors="replace")
    text = out.stdout + out.stderr
    assert out.returncode == 0, text[-2000:]
    assert "env.HUB" in text and "HubDO" in text and "LOG_EVENTS" in text, text[-1500:]
    assert "--dry-run: exiting now" in text or "dry-run" in text.lower()
    assert (tmp_path / "build" / "worker.js").exists()
