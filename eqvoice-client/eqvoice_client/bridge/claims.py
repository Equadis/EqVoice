"""Which character each running EqVoice serves.

Two game windows on one PC need two EqVoice copies, one for each, and a copy must not take the character another copy already has (two
copies flipping between the same two characters hammered the voice server until it refused them). A claim is a named Windows mutex per
character: it is held for as long as the copy lives, and it is gone the moment the copy is, however it ends (a crash included), so no
lock file is ever left behind to say a character is taken when nothing is serving it.
"""
from __future__ import annotations

import sys

PREFIX = "Local\\EqVoice-char-"
ERROR_ALREADY_EXISTS = 183


class Claims:
    def __init__(self, prefix: str = PREFIX):
        self.prefix = prefix
        self.held: dict = {}                       # character (lower case) -> mutex handle
        self._k32 = None
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            self._k32 = k32

    def try_claim(self, character: str) -> bool:
        """True if this copy now serves ``character`` (or already did); False if another copy has them."""
        key = character.lower()
        if key in self.held:
            return True
        if self._k32 is None:                      # not Windows: nothing outside this process to ask
            self.held[key] = None
            return True
        import ctypes
        handle = self._k32.CreateMutexW(None, 0, self.prefix + key)
        if not handle:
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self._k32.CloseHandle(handle)
            return False
        self.held[key] = handle
        return True

    def taken_by_other(self, character: str) -> bool:
        """Does a copy other than this one serve ``character``? (Asks without keeping the answer.)"""
        key = character.lower()
        if key in self.held:
            return False
        if self.try_claim(character):
            self.release(character)
            return False
        return True

    def release(self, character: str) -> None:
        handle = self.held.pop(character.lower(), None)
        if handle and self._k32 is not None:
            self._k32.CloseHandle(handle)

    def release_all(self) -> None:
        for key in list(self.held):
            self.release(key)


CLAIMS = Claims()                                  # this process's claims
