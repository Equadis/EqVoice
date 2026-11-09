"""A browser left over from an earlier run must not become the voice engine of the next one (it did, on 2026-10-16)."""
import sys
import time

import pytest

from eqvoice_client import browser

pytestmark = pytest.mark.skipif(sys.platform != "win32" or browser.find_browser() is None,
                                reason="needs Windows and Edge or Chrome")


def wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.4)
    return pred()


def test_a_leftover_browser_holding_a_profile_is_found_and_stopped(tmp_path):
    profile = tmp_path / "leftover-profile"
    browser.launch(browser.find_browser(), "about:blank", profile, headless=True, fake_media=True, mute=True)
    try:
        assert wait_for(lambda: browser.profile_pids(profile), 25), "the browser is found by its profile folder"
        assert browser.profile_pids(tmp_path / "another-profile") == [], "other profiles are not touched"
        assert browser.kill_profile(profile) >= 1
        assert wait_for(lambda: not browser.profile_pids(profile), 20), "nothing is left holding the profile"
    finally:
        browser.kill_profile(profile)
