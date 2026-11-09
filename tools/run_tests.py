"""Run every EqVoice test suite and print one summary.

    python tools/run_tests.py             everything except the slow real-browser WebRTC test
    python tools/run_tests.py --all       include it (needs Edge or Chrome; about 80 s)

Suites: Lua 5.0 lint, addon suite (LuaJIT), bridge-vector and hub-conformance freshness, signalling (pytest, which
also replays the recorded conversations against the JavaScript hub and the Cloudflare adapter in headless Chromium),
client (pytest).
Set EQVOICE_TMP to put pytest's temporary folders somewhere other than the system temp.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(name: str, cmd: list, cwd: Path, env=None) -> tuple:
    t = time.time()
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    ok = r.returncode == 0
    tail = [ln for ln in (r.stdout + r.stderr).strip().splitlines() if ln.strip()][-3:]
    print("%-28s %s  (%.1fs)  %s" % (name, "PASS" if ok else "FAIL", time.time() - t, tail[-1] if tail else ""))
    if not ok:
        print("\n".join("    " + ln for ln in (r.stdout + r.stderr).strip().splitlines()[-40:]))
    return name, ok


def scratch_folder() -> Path:
    """Where every temporary file of a run goes: EQVOICE_TMP, else a folder on D: (C: on this PC has been left with 167 MB free by a few hundred
    browser profiles, and every test that then wrote a file failed with "no space left"), else the system's own."""
    chosen = os.environ.get("EQVOICE_TMP")
    if not chosen and Path("D:\\").exists():
        chosen = r"D:\Octowow\.eco-work\tmp\eqvoice-suite"
    return Path(chosen or tempfile.gettempdir()) / "eqvoice-run"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    scratch = scratch_folder()
    shutil.rmtree(scratch, ignore_errors=True)         # this folder is the run's own: last run's leftovers go before this run starts
    tmp = scratch / "pytest"
    tmp.mkdir(parents=True, exist_ok=True)     # pytest will not create the parents of --basetemp
    (scratch / "tmp").mkdir(exist_ok=True)
    # Every child process (pytest, the browsers the tests start, the helpers) puts its temporary files under the scratch folder, not in C:\Temp
    os.environ["TEMP"] = os.environ["TMP"] = str(scratch / "tmp")
    tempfile.tempdir = None
    results = []

    luajit = shutil.which("luajit")
    if luajit:
        results.append(run("lua 5.0 lint", [PY, "tools/lint_lua50.py"], ROOT))
        results.append(run("addon suite (luajit)", [luajit, "eqvoice-addon/tests/run.lua"], ROOT))
        fresh = Path(tempfile.mkdtemp()) / "vectors.json"
        subprocess.run([luajit, "tools/gen_bridge_vectors.lua", str(fresh)], cwd=str(ROOT), capture_output=True)
        same = fresh.exists() and filecmp.cmp(fresh, ROOT / "protocol" / "bridge_vectors.json", shallow=False)
        print("%-28s %s" % ("bridge vectors up to date", "PASS" if same else "FAIL (run: luajit tools/gen_bridge_vectors.lua)"))
        results.append(("vectors", same))
    else:
        print("luajit not found: skipping the Lua checks")

    results.append(run("conformance recording", [PY, "tools/gen_conformance.py", "--check"], ROOT))
    results.append(run("signalling (pytest)", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                              "--basetemp=%s" % (tmp / "sig")], ROOT / "eqvoice-signalling"))
    env = dict(os.environ)
    if not a.all:
        env["EQVOICE_E2E"] = "0"
    results.append(run("client (pytest%s)" % (", +browser" if a.all else ""),
                       [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=%s" % (tmp / "client")],
                       ROOT / "eqvoice-client", env))
    bad = [n for n, ok in results if not ok]
    print("\n%s" % ("ALL GREEN" if not bad else "FAILED: " + ", ".join(bad)))
    shutil.rmtree(scratch, ignore_errors=True)         # nothing is left behind (browser profiles are tens of megabytes each)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
