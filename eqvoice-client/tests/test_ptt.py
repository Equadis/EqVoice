"""Push-to-talk: key resolution across keyboard layouts, and the key poller's press/release behaviour."""
import sys

import pytest

from eqvoice_client import ptt


def test_layout_independent_keys_are_fixed():
    assert ptt.vk_from_code("ShiftLeft") == (0xA0, "Left Shift")
    assert ptt.vk_from_code("ControlRight")[0] == 0xA3
    assert ptt.vk_from_code("F5")[0] == 0x74
    assert ptt.vk_from_code("F24")[0] == 0x87
    assert ptt.vk_from_code("Space") == (0x20, "Space")
    assert ptt.vk_from_code("Numpad7")[0] == 0x67
    assert ptt.vk_from_code("ScrollLock")[0] == 0x91


def test_unknown_codes_and_mouse_buttons():
    assert ptt.vk_from_code("MediaPlayPause") is None
    assert ptt.vk_from_mouse(3) == (0x05, "Mouse 4 (back)")
    assert ptt.vk_from_mouse(4)[0] == 0x06
    assert ptt.vk_from_mouse(0) is None and ptt.vk_from_mouse(2) is None, "left and right click are never allowed"


def test_main_block_keys_resolve_to_something_usable():
    for code in ("Backquote", "KeyV", "Digit1", "Minus", "Slash", "KeyA", "KeyM", "Semicolon"):
        vk, label = ptt.vk_from_code(code)
        assert 1 <= vk <= 254 and label, code


def test_letters_and_digits_on_a_latin_layout():
    # holds on US, UK and most Latin layouts; AZERTY moves letters, so only digits and V are asserted
    vk, label = ptt.vk_from_code("Digit1")
    assert vk == 0x31
    assert ptt.vk_from_code("KeyV", key="v")[1] == "V", "the label follows what the key types"


@pytest.mark.skipif(sys.platform != "win32", reason="layout lookup is a Windows API")
def test_backquote_follows_the_installed_layout():
    import ctypes
    expected = ctypes.windll.user32.MapVirtualKeyW(0x29, 3)           # physical key left of "1"
    assert ptt.vk_from_code("Backquote")[0] == (expected or 0xC0)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(release_ms=200):
    clock, down, events = Clock(), {"vk": set()}, []
    kp = ptt.KeyPoller(lambda: 0xC0, events.append, lambda: release_ms, sampler=lambda vk: vk in down["vk"], clock=clock)
    return kp, clock, down, events


def test_press_release_with_a_tail():
    kp, clock, down, events = make(200)
    assert kp.sample() is False and events == []
    down["vk"].add(0xC0)
    assert kp.sample() is True and events == [True]
    clock.t += 1.0
    assert kp.sample() is True and events == [True], "held: no repeated events"
    down["vk"].clear()
    clock.t += 0.05
    assert kp.sample() is True, "release delay keeps the end of the word"
    clock.t += 0.2
    assert kp.sample() is False and events == [True, False]


def test_tap_and_retap_inside_the_tail_does_not_flicker():
    kp, clock, down, events = make(200)
    down["vk"].add(0xC0); kp.sample()
    down["vk"].clear(); clock.t += 0.1; kp.sample()
    down["vk"].add(0xC0); clock.t += 0.05; kp.sample()
    assert events == [True], "the tail bridges a quick re-press"


def test_virtual_button_and_no_key_configured():
    kp, clock, down, events = make(0)
    kp.virtual = True
    assert kp.sample() is True
    kp.virtual = False
    clock.t += 0.01
    assert kp.sample() is False
    kp2 = ptt.KeyPoller(lambda: 0, events.append, lambda: 0, sampler=lambda vk: True, clock=clock)
    assert kp2.sample() is False, "vk 0 means no key is configured"


def test_sampler_errors_are_swallowed():
    def boom(vk):
        raise OSError("no")
    kp = ptt.KeyPoller(lambda: 5, lambda d: None, lambda: 0, sampler=boom, clock=Clock())
    assert kp.sample() is False


def test_without_a_sampler_the_poller_reports_unsupported():
    kp = ptt.KeyPoller(lambda: 5, lambda d: None, lambda: 0, sampler=None, clock=Clock())
    if sys.platform != "win32":
        assert kp.supported is False

def test_the_games_key_names_become_virtual_keys():
    assert ptt.vk_from_wow("F9") == (0x78, "F9") and ptt.vk_from_wow("f12")[0] == 0x7B
    assert ptt.vk_from_wow("A") == (0x41, "A") and ptt.vk_from_wow("z")[0] == 0x5A
    assert ptt.vk_from_wow("5") == (0x35, "5")
    assert ptt.vk_from_wow("NUMPAD3") == (0x63, "Num 3") and ptt.vk_from_wow("NUMPADPLUS")[0] == 0x6B
    assert ptt.vk_from_wow("SPACE") == (0x20, "Space") and ptt.vk_from_wow("PAGEUP")[0] == 0x21 and ptt.vk_from_wow("UP")[0] == 0x26
    assert ptt.vk_from_wow("LSHIFT")[0] == 0xA0 and ptt.vk_from_wow("RCTRL")[0] == 0xA3 and ptt.vk_from_wow("RALT")[0] == 0xA5
    assert ptt.vk_from_wow("BUTTON4") == ptt.vk_from_mouse(3) and ptt.vk_from_wow("Button5") == ptt.vk_from_mouse(4)
    assert ptt.vk_from_wow("MiddleButton") == ptt.vk_from_mouse(1), "the wheel click, as the game names it in a mouse event"
    vk, label = ptt.vk_from_wow("`")
    assert 1 <= vk <= 254 and label == "`", "punctuation is the character, resolved through the keyboard layout"
    assert ptt.vk_from_wow("-")[1] == "-" and ptt.vk_from_wow("[")[1] == "["


def test_keys_the_game_offers_that_are_refused():
    for name in ("ESCAPE", "ENTER", "LeftButton", "RightButton", "BUTTON1", "BUTTON2", "UNKNOWN", "", "   ", "F25", "F0", "NUMPAD", "NUMPAD12",
                 "AB", "x" * 40, None, "é"):
        assert ptt.vk_from_wow(name) is None, repr(name)
