"""Record the hub conformance scenarios against the Python hub (the reference implementation).

    python tools/gen_conformance.py            write protocol/conformance.json
    python tools/gen_conformance.py --check    exit 1 if the file on disk is out of date

Both the Python hub (eqvoice-signalling) and the JavaScript hub (eqvoice-signalling-cf) must replay these
conversations and reproduce every recorded message. Run this after changing the Python hub's behaviour or the
scenarios, then make the JavaScript hub agree.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "eqvoice-signalling"), str(ROOT / "eqvoice-signalling" / "tests")]
import conformance_scenarios  # noqa: E402

OUT = ROOT / "protocol" / "conformance.json"


def build() -> str:
    doc = {"version": 1, "about": "recorded hub conversations; see tools/gen_conformance.py",
           "scenarios": conformance_scenarios.documents()}
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        same = OUT.exists() and json.loads(OUT.read_text(encoding="utf-8")) == json.loads(text)
        print("conformance.json is %s" % ("up to date" if same else "OUT OF DATE (run tools/gen_conformance.py)"))
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print("wrote %s (%d scenarios, %d bytes)" % (OUT, len(json.loads(text)["scenarios"]), len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
