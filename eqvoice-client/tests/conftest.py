import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, HERE)
# The signalling package is a sibling project; the client tests drive a real hub.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "eqvoice-signalling")))


import pytest


@pytest.fixture(autouse=True)
def _no_real_game_processes(monkeypatch):
    """The tests must not depend on whether the person running them has WoW open: the installer sees no running game unless a test says so.
    (A test that patches ``running_wow_dirs`` or ``running_wow_processes`` itself wins: its patch is applied after this one.)"""
    try:
        from eqvoice_client import installer
    except Exception:                                                            # noqa: BLE001
        return
    monkeypatch.setattr(installer, "running_wow_processes", lambda: [])
