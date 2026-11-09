"""Boundary narration for the real-game test: one greppable line per thing that crosses a boundary.

    TRACE desktop <event> key=value key="value with spaces" ...
    TRACE addon <the addon's own words, as it sent them through the bridge file>

The lines go to the ordinary log (``<home>/logs/eqvoice.log``, the console and the UI's log view), so a failed
run can be read top to bottom, and ``tools/pipeline_report.py`` can say which boundary was the last one reached.
"""
from __future__ import annotations

import logging

log = logging.getLogger("eqvoice.trace")


def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return "%.1f" % v if v == 0 or abs(v) >= 1 else "%.3g" % v      # 0.004 must not print as 0.0
    if isinstance(v, (list, tuple, set)):
        return ",".join(fmt(x) for x in v) or "-"
    s = str(v)
    if not s:
        return '""'
    return '"%s"' % s.replace('"', "'") if any(c.isspace() for c in s) else s


def trace(event: str, **fields) -> None:
    log.info("TRACE desktop %s %s", event, " ".join("%s=%s" % (k, fmt(v)) for k, v in fields.items()))


def addon_line(text: str) -> None:
    log.info("TRACE addon %s", text)


def names(items, limit: int = 8) -> str:
    items = list(items)
    shown = ",".join(items[:limit]) or "-"
    return shown + (",...(%d)" % len(items) if len(items) > limit else "")
