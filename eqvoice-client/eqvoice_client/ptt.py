"""Push-to-talk: poll one key or mouse button, even while WoW has the focus.

Uses GetAsyncKeyState, which only reads whether a key is down. No keyboard hook
is installed, nothing is injected, and no keystrokes are recorded or forwarded.
On other platforms the in-window button and the open-mic mode still work.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Callable, Optional

log = logging.getLogger("eqvoice.ptt")

IS_WINDOWS = sys.platform == "win32"

# KeyboardEvent.code names a *physical* key. Letters, digits and punctuation produce different virtual-key
# codes on different keyboard layouts (the key left of "1" is VK_OEM_3 on a US layout and VK_OEM_8 on a UK
# one), so those are resolved through the Windows layout with MapVirtualKeyW. Everything else has one
# virtual key on every layout.
_FIXED = {
    "Space": (0x20, "Space"), "Tab": (0x09, "Tab"), "CapsLock": (0x14, "Caps Lock"), "Backspace": (0x08, "Backspace"),
    "ShiftLeft": (0xA0, "Left Shift"), "ShiftRight": (0xA1, "Right Shift"),
    "ControlLeft": (0xA2, "Left Ctrl"), "ControlRight": (0xA3, "Right Ctrl"),
    "AltLeft": (0xA4, "Left Alt"), "AltRight": (0xA5, "Right Alt"),
    "Insert": (0x2D, "Insert"), "Delete": (0x2E, "Delete"), "Home": (0x24, "Home"), "End": (0x23, "End"),
    "PageUp": (0x21, "Page Up"), "PageDown": (0x22, "Page Down"), "ScrollLock": (0x91, "Scroll Lock"),
    "Pause": (0x13, "Pause"), "NumpadAdd": (0x6B, "Num +"), "NumpadSubtract": (0x6D, "Num -"),
    "NumpadMultiply": (0x6A, "Num *"), "NumpadDivide": (0x6F, "Num /"), "NumpadDecimal": (0x6E, "Num ."),
}
for _i in range(10):
    _FIXED["Numpad%d" % _i] = (0x60 + _i, "Num %d" % _i)
for _i in range(1, 25):
    _FIXED["F%d" % _i] = (0x6F + _i, "F%d" % _i)

# physical main-block keys: (set-1 scan code, US virtual key, US label)
_MAIN = {"Backquote": (0x29, 0xC0, "`"), "Minus": (0x0C, 0xBD, "-"), "Equal": (0x0D, 0xBB, "="),
         "BracketLeft": (0x1A, 0xDB, "["), "BracketRight": (0x1B, 0xDD, "]"), "Semicolon": (0x27, 0xBA, ";"),
         "Quote": (0x28, 0xDE, "'"), "Backslash": (0x2B, 0xDC, "\\"), "IntlBackslash": (0x56, 0xE2, "\\"),
         "Comma": (0x33, 0xBC, ","), "Period": (0x34, 0xBE, "."), "Slash": (0x35, 0xBF, "/")}
for _n, _sc in enumerate((0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A)):
    _MAIN["Digit%d" % (_n + 1)] = (_sc, 0x31 + _n, str(_n + 1))
_MAIN["Digit0"] = (0x0B, 0x30, "0")
for _row, _scs in (("QWERTYUIOP", range(0x10, 0x1A)), ("ASDFGHJKL", range(0x1E, 0x27)), ("ZXCVBNM", range(0x2C, 0x33))):
    for _ch, _sc in zip(_row, _scs):
        _MAIN["Key" + _ch] = (_sc, ord(_ch), _ch)

# MouseEvent.button -> (virtual key, label). 0 and 2 are left/right click: refused, they are needed to use the PC.
_MOUSE = {1: (0x04, "Mouse 3 (wheel click)"), 3: (0x05, "Mouse 4 (back)"), 4: (0x06, "Mouse 5 (forward)")}


def _layout_vk(scan: int) -> Optional[int]:
    """The virtual key the *current layout* puts on a physical key (MAPVK_VSC_TO_VK_EX)."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        vk = ctypes.windll.user32.MapVirtualKeyW(scan, 3)
        return int(vk) or None
    except Exception:                                   # noqa: BLE001
        return None


def vk_from_code(code: str, key: Optional[str] = None) -> Optional[tuple]:
    """(virtual key, label) for a KeyboardEvent.code, resolved through the keyboard layout."""
    if code in _FIXED:
        return _FIXED[code]
    if code in _MAIN:
        scan, us_vk, us_label = _MAIN[code]
        vk = _layout_vk(scan) or us_vk
        label = key.upper() if key and len(key) == 1 and key.strip() else us_label
        return vk, label
    return None


def vk_from_mouse(button: int) -> Optional[tuple]:
    return _MOUSE.get(button)


# The names the game's own key-binding screen uses (the argument of OnKeyDown / OnMouseDown in a WoW 1.12 frame): letters and digits as
# themselves, F1..F12, NUMPAD0.., SPACE, UP, PAGEUP, LSHIFT ..., and punctuation as the character. The addon's "change key" button sends one.
_WOW_NAMED = {
    "SPACE": (0x20, "Space"), "TAB": (0x09, "Tab"), "BACKSPACE": (0x08, "Backspace"), "CAPSLOCK": (0x14, "Caps Lock"),
    "INSERT": (0x2D, "Insert"), "DELETE": (0x2E, "Delete"), "HOME": (0x24, "Home"), "END": (0x23, "End"),
    "PAGEUP": (0x21, "Page Up"), "PAGEDOWN": (0x22, "Page Down"), "PAUSE": (0x13, "Pause"), "SCROLLLOCK": (0x91, "Scroll Lock"),
    "UP": (0x26, "Up"), "DOWN": (0x28, "Down"), "LEFT": (0x25, "Left"), "RIGHT": (0x27, "Right"),
    "LSHIFT": (0xA0, "Left Shift"), "RSHIFT": (0xA1, "Right Shift"), "LCTRL": (0xA2, "Left Ctrl"), "RCTRL": (0xA3, "Right Ctrl"),
    "LALT": (0xA4, "Left Alt"), "RALT": (0xA5, "Right Alt"),
    "NUMPADPLUS": (0x6B, "Num +"), "NUMPADMINUS": (0x6D, "Num -"), "NUMPADMULTIPLY": (0x6A, "Num *"),
    "NUMPADDIVIDE": (0x6F, "Num /"), "NUMPADDECIMAL": (0x6E, "Num ."),
    "BUTTON3": _MOUSE[1], "MIDDLEBUTTON": _MOUSE[1], "BUTTON4": _MOUSE[3], "BUTTON5": _MOUSE[4],
}
# US virtual keys for punctuation, used when the layout cannot say
_WOW_PUNCT = {"`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, "\\": 0xDC, ",": 0xBC, ".": 0xBE, "/": 0xBF}


def _char_vk(ch: str) -> Optional[int]:
    """The virtual key that types a character on the current layout (VkKeyScanW), if the layout has one."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        r = ctypes.windll.user32.VkKeyScanW(ord(ch))
        return None if r in (-1, 0xFFFF) else (int(r) & 0xFF)
    except Exception:                                   # noqa: BLE001
        return None


def vk_from_wow(name: str) -> Optional[tuple]:
    """(virtual key, label) for a key name as the game spells it, or None (left and right click, Escape, Enter and anything unknown are refused)."""
    n = str(name or "").strip()
    if not n or len(n) > 16:
        return None
    u = n.upper()
    if u in _WOW_NAMED:
        return _WOW_NAMED[u]
    if len(u) == 1 and u.isalpha() and u.isascii():
        return ord(u), u
    if len(u) == 1 and u.isdigit():
        return 0x30 + int(u), u
    if u.startswith("F") and u[1:].isdigit() and 1 <= int(u[1:]) <= 24:
        return 0x6F + int(u[1:]), u
    if u.startswith("NUMPAD") and u[6:].isdigit() and len(u) == 7:
        return 0x60 + int(u[6:]), "Num " + u[6:]
    if len(n) == 1 and n in _WOW_PUNCT:
        return _char_vk(n) or _WOW_PUNCT[n], n
    return None


def _windows_sampler() -> Callable[[int], bool]:
    import ctypes
    fn = ctypes.windll.user32.GetAsyncKeyState
    fn.argtypes = [ctypes.c_int]
    fn.restype = ctypes.c_short
    return lambda vk: bool(fn(vk) & 0x8000)


class KeyPoller:
    """Reports key down/up through ``on_change(bool)``.

    ``release_ms`` keeps transmitting briefly after release so the ends of words
    are not clipped. ``virtual`` is an extra "down" source (the in-window button).
    """

    def __init__(self, get_vk: Callable[[], int], on_change: Callable[[bool], None],
                 get_release_ms: Callable[[], int] = lambda: 200,
                 sampler: Optional[Callable[[int], bool]] = None,
                 clock: Callable[[], float] = time.monotonic, hz: float = 60.0):
        self.get_vk, self.on_change, self.get_release_ms = get_vk, on_change, get_release_ms
        self.clock, self.interval = clock, 1.0 / hz
        self.sampler = sampler if sampler is not None else (_windows_sampler() if IS_WINDOWS else None)
        self.virtual = False
        self.down = False
        self._last_raw = -1e9
        self.supported = self.sampler is not None

    def sample(self) -> bool:
        now = self.clock()
        raw = self.virtual
        vk = self.get_vk()
        if not raw and vk and self.sampler is not None:
            try:
                raw = self.sampler(vk)
            except Exception:                    # noqa: BLE001
                raw = False
        if raw:
            self._last_raw = now
        eff = raw or (now - self._last_raw) * 1000.0 < self.get_release_ms()
        if eff != self.down:
            self.down = eff
            self.on_change(eff)
        return eff

    async def run(self) -> None:
        if not self.supported:
            log.info("global push-to-talk is not available here; use the on-screen button or open mic")
        while True:
            self.sample()
            await asyncio.sleep(self.interval)
