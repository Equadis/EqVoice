"""Lua 5.0 compliance check for the EqVoice addon.

The test suite runs on LuaJIT (Lua 5.1); the 1.12 client runs Lua 5.0. A
construct that 5.1 accepts and 5.0 rejects passes every test and then fails the
whole file in game. This script is the missing half.

  1. TOC <-> disk: every listed file exists, every .lua on disk is listed.
  2. Syntax: luajit -bl on every file.
  3. 5.1-isms: the # operator, the % operator, `...`, string.match/gmatch,
     :match/:gmatch, select(), table.maxn, goto, bit operators.
  4. Globals: every global read must be defined by the addon or be a known
     1.12 client / Lua 5.0 name. Catches typos that only fail when the line runs.

Usage:  python tools/lint_lua50.py [addon_dir]      exit code 1 on any problem
"""
import io
import os
import re
import subprocess
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "eqvoice-addon", "EqVoice")
ROOT = os.path.abspath(ROOT)

# Names that exist on a 1.12 client (or in Lua 5.0) and are read by the addon.
# Anything outside this set and outside the addon's own globals is a typo.
KNOWN = set("""
_G _VERSION assert collectgarbage error getmetatable ipairs loadstring next pairs pcall
print rawequal rawget rawset setmetatable tonumber tostring type unpack xpcall
string table math getglobal setglobal
time date GetTime GetLocale GetBuildInfo GetCVar UnitName IsInGuild
GetNumRaidMembers GetNumPartyMembers CreateFrame UIParent WorldMapFrame DEFAULT_CHAT_FRAME
SlashCmdList SendAddonMessage SendChatMessage SetMapToCurrentZone GetCurrentMapContinent GetCurrentMapZone
GetPlayerMapPosition GetRealZoneText
JoinChannelByName GetChannelName LeaveChannelByName
event arg1 arg2 arg3 arg4
UnitIsPlayer UnitIsEnemy WorldFrame UnitExists GetRealmName GetFramerate
SLASH_EQVOICE1 SLASH_EQVOICE2 SLASH_EQVOICEUI1 SLASH_EQVOICEUI2
this GameTooltip UISpecialFrames IsShiftKeyDown GameFontHighlight GetCursorPosition IsMouseButtonDown
EqVoice EqVoiceDB
WriteCustomFile ReadCustomFile ExportFile ImportFile UnitXP
""".split())
# Optional APIs the addon only touches after a type() check:
OPTIONAL = set("RegisterAddonMessagePrefix C_ChatInfo UnitPosition UnitNameplate LoggingChat CustomFileExists GetNetStats C_NamePlate UnitDistanceSquared UnitTokenFromName".split())

ISMS = [
    (r"string\.match\s*\(", "string.match"),
    (r"string\.gmatch\s*\(", "string.gmatch"),
    (r":match\s*\(", ":match"),
    (r":gmatch\s*\(", ":gmatch"),
    (r"table\.maxn\s*\(", "table.maxn"),
    (r"(?<![\w.])select\s*\(", "select("),
    (r"(?<![\w\]\)])#\s*[\w(\[{]", "# length operator"),
    (r"[\w\)\]]\s*#", "# length operator"),
    (r"[\w\)\]\"']\s*%\s*[\w(\"']", "% modulo operator"),
    (r"\.\.\.", "vararg ..."),
    (r"\bgoto\b", "goto"),
    (r"<<|>>|~=\s*=|\w\s*&\s*\w", "bit operator"),
    (r"\bstring\.format\s*\(\s*['\"][^'\"]*%[^'\"]*['\"]\s*,\s*\.\.\.", "format with ..."),
    (r"\bmath\.mod\s*\(", "math.mod (use OV.mod)"),
    (r"\btable\.getn\s*\(\s*arg\s*\)", "table.getn(arg)"),
]


def strip(src):
    """Blank comments and string contents so patterns only see code."""
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", " ", src, flags=re.S)
    src = re.sub(r"\[(=*)\[.*?\]\1\]", '""', src, flags=re.S)
    src = re.sub(r"--[^\n]*", " ", src)
    src = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', src)
    src = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", src)
    return src


def toc_files():
    toc = [f for f in os.listdir(ROOT) if f.lower().endswith(".toc")]
    out = []
    for line in io.open(os.path.join(ROOT, toc[0]), encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.replace("\\", "/"))
    return out


def bytecode_names(path, op):
    r = subprocess.run(["luajit", "-bl", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    names = set()
    for line in r.stdout.splitlines():
        m = re.match(r"\s*\d+\s+(\w+)\s+.*?;\s+\"([^\"]+)\"", line)
        if m and m.group(1) == op:
            names.add(m.group(2))
    return names


def main():
    problems = []
    listed = toc_files()
    print("addon dir: %s (%d files in TOC)" % (ROOT, len(listed)))

    for f in listed:
        if not os.path.exists(os.path.join(ROOT, f)):
            problems.append("TOC lists a missing file: " + f)
    on_disk = [f for f in os.listdir(ROOT) if f.endswith(".lua")]
    for f in on_disk:
        if f not in listed:
            problems.append("file on disk but not in the TOC: " + f)

    for f in listed:
        r = subprocess.run(["luajit", "-bl", os.path.join(ROOT, f)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            problems.append("syntax %s: %s" % (f, (r.stderr.strip().splitlines() or ["?"])[-1]))

    for f in listed:
        src = strip(io.open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read())
        for pat, label in ISMS:
            for m in re.finditer(pat, src):
                line = src.count("\n", 0, m.start()) + 1
                ctx = src[max(0, m.start() - 20):m.end() + 20].replace("\n", " ").strip()
                problems.append("%s:%d  %s   ...%s..." % (f, line, label, ctx))

    reads, writes = {}, set()
    for f in listed:
        p = os.path.join(ROOT, f)
        for n in bytecode_names(p, "GGET"):
            reads.setdefault(n, set()).add(f)
        writes |= bytecode_names(p, "GSET")
    for name, files in sorted(reads.items()):
        if name in writes or name in KNOWN or name in OPTIONAL:
            continue
        problems.append("global read but defined nowhere: %s (in %s)" % (name, ", ".join(sorted(files))))

    # Optional APIs must never be called without a guard; crude check: each
    # mention must sit on a line that also has type( or a pcall/getglobal.
    for f in listed:
        src = strip(io.open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read())
        for ln, text in enumerate(src.splitlines(), 1):
            for n in OPTIONAL:
                if re.search(r"(?<![\w.])%s\s*\(" % n, text) and "type(" not in text and "pcall" not in text:
                    problems.append("%s:%d  unguarded call to optional API %s" % (f, ln, n))

    if problems:
        print("\nPROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("lua 5.0 lint: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
