"""CPU and memory readings for the client and its media engine, taken without psutil."""
import os
import subprocess
import sys
import textwrap
import time

import pytest

from eqvoice_client import resources

pytestmark = pytest.mark.skipif(not (sys.platform == "win32" or os.path.isdir("/proc")), reason="needs Windows or Linux /proc")


def test_cpu_percentage_is_computed_between_reads(monkeypatch):
    clock = [100.0]
    used = {1: 10.0, 2: 3.0}
    monkeypatch.setattr(resources, "process_usage", lambda pid: (used[pid], 50 * 1048576.0, 40 * 1048576.0) if pid in used else None)
    m = resources.ResourceMeter(clock=lambda: clock[0])
    first = m.read("engine", [1, 2, 99])
    assert first["cpu_pct"] is None and first["rss_mb"] == 100.0 and first["private_mb"] == 80.0 and first["procs"] == 2, "99 is gone: skipped"
    clock[0], used[1], used[2] = 102.0, 11.0, 4.0                       # 2 s later: 1 s + 1 s of CPU
    assert m.read("engine", [1, 2])["cpu_pct"] == 100.0, "two helpers each half busy = one whole core"
    clock[0], used[1], used[2] = 104.0, 11.0, 4.0
    assert m.read("engine", [1, 2])["cpu_pct"] == 0.0
    clock[0] = 106.0
    used[3] = 5.0                                                         # a helper that appeared since: counted from zero
    assert m.read("engine", [1, 2, 3])["cpu_pct"] == 250.0
    assert m.read("client", [1])["cpu_pct"] is None, "labels are independent"


def test_a_real_process_tree_is_found_and_measured(tmp_path):
    child = tmp_path / "child.py"
    child.write_text(textwrap.dedent("""
        import subprocess, sys, time
        if len(sys.argv) == 1:                               # the middle of the tree: start a busy grandchild, then wait
            g = subprocess.Popen([sys.executable, __file__, "busy"])
            time.sleep(25)
        else:                                                # the grandchild: hold ~150 MB and spin
            x = bytearray(150 * 1024 * 1024)
            for i in range(0, len(x), 4096):
                x[i] = 1
            t = time.time()
            while time.time() - t < 25:
                pass
    """))
    p = subprocess.Popen([sys.executable, str(child)])
    try:
        pids = []
        end = time.time() + 20
        while time.time() < end and len(pids) < 3:                       # this test process + child + grandchild
            pids = resources.tree_pids(os.getpid())
            time.sleep(0.3)
        assert os.getpid() in pids and p.pid in pids and len(pids) >= 3, pids
        meter = resources.ResourceMeter()
        # wait until the grandchild has touched its memory
        end = time.time() + 20
        while time.time() < end and meter.read("e", resources.tree_pids(p.pid))["private_mb"] < 140:
            time.sleep(0.3)
        meter.read("t", resources.tree_pids(p.pid))
        time.sleep(1.5)
        r = meter.read("t", resources.tree_pids(p.pid))
        assert r["procs"] >= 2 and r["private_mb"] >= 140, r
        assert 60 <= r["cpu_pct"] <= 140, "a spinning process is about one core: %r" % r
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True) if sys.platform == "win32" else p.kill()


def test_unknown_roots_and_dead_pids_are_harmless():
    assert resources.tree_pids(None) == [] and resources.tree_pids(0) == []
    assert resources.tree_pids(2 ** 30) == []
    assert resources.process_usage(2 ** 30) is None
    assert resources.ResourceMeter().read("x", [2 ** 30]) == {"cpu_pct": None, "rss_mb": 0.0, "private_mb": 0.0, "procs": 0}
