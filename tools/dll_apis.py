"""List which Lua-facing API names appear inside the client mod DLLs you have installed.

Read-only: the files are opened as bytes and searched for strings. Nothing is loaded or executed.
This answers "does my copy of the mod really contain function X", which the mod's documentation
cannot (versions differ).

    python tools/dll_apis.py "D:\\Octowow"                 # default probe list
    python tools/dll_apis.py "D:\\Octowow" UnitPosition GetUnitData
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DLLS = ["ClassicAPI.dll", "SuperWoWhook.dll", "nampower.dll", "UnitXP_SP3.dll", "VanillaHelpers.dll", "Scan.dll",
        "ACE.dll", "VfPatcher.dll", "VanillaFixes.exe"]

# What proximity discovery could use.
PROBE = [
    "C_NamePlate", "GetNamePlateGUIDs", "GetNamePlates", "NAME_PLATE_UNIT_ADDED",
    "UnitDistanceSquared", "UnitPosition", "UnitInRange", "UnitTokenFromName", "UnitTokenFromGUID",
    "UnitInLineOfSight", "UnitNameFromGUID", "GUIDIsPlayer", "UnitGUID", "GetUnitGUID",
    "UnitNameplate", "distanceBetween", "GetUnitData", "GetUnitField", "CursorPosition",
    "NameplateRange", "NP_NameplateDistance", "ChatBubbleRange", "GetNearbyUnits", "EnumerateUnits",
    "GetVisibleUnits", "GetNearbyPlayers", "NearbyPlayers",
]


def strings_in(data: bytes, needles):
    found = {}
    for n in needles:
        b = n.encode("ascii")
        i = data.find(b)
        found[n] = i >= 0
    return found


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    needles = argv[2:] or PROBE
    present = {}
    for name in DLLS:
        p = root / name
        if not p.exists():
            continue
        data = p.read_bytes()
        present[name] = strings_in(data, needles)
        print("%-18s %9d bytes" % (name, len(data)))
    print()
    width = max(len(n) for n in needles)
    header = "%-*s " % (width, "") + " ".join("%-9s" % n.replace(".dll", "")[:9] for n in present)
    print(header)
    for n in needles:
        row = " ".join("%-9s" % ("yes" if present[d][n] else ".") for d in present)
        print("%-*s %s" % (width, n, row))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
