"""Print what the EqVoice addon recorded: diagnostics results, last stats, and its log.

The game writes EqVoice.lua only at logout or /reload. Usage:

    python tools/read_savedvars.py "D:\\Octowow"            # finds WTF\\Account\\*\\SavedVariables\\EqVoice.lua
    python tools/read_savedvars.py path\\to\\EqVoice.lua
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from savedvars import parse  # noqa: E402


def find(arg: str):
    p = Path(arg)
    if p.is_file():
        return [p]
    return sorted(p.glob("WTF/Account/*/SavedVariables/EqVoice.lua"), key=lambda f: f.stat().st_mtime, reverse=True)


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    files = find(argv[1])
    if not files:
        print("no EqVoice.lua found (log out or /reload in game first, then try again)")
        return 1
    db = parse(files[0].read_text(encoding="utf-8", errors="replace")).get("EqVoiceDB") or {}
    print("file:", files[0])
    print("settings:", {k: v for k, v in db.items() if k not in ("diag", "log", "lastStats")})
    diag = db.get("diag") or {}
    print("\n== diagnostics ==")
    for key in sorted(diag):
        d = diag[key]
        print("  %-10s %s  %s" % (key, "OK " if d.get("ok") else "NO ", d.get("result")))
    if db.get("lastStats"):
        print("\n== last stats ==\n ", db["lastStats"])
    print("\n== log ==")
    for line in db.get("log") or []:
        print("  " + str(line))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
