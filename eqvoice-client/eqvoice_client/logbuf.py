"""Logging: a rotating file plus an in-memory ring the UI and diagnostics can read."""
from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from pathlib import Path


class RingHandler(logging.Handler):
    def __init__(self, size: int = 600):
        super().__init__()
        self.records: deque = deque(maxlen=size)
        self.listeners: list = []
        self.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        self.records.append(line)
        for cb in list(self.listeners):
            try:
                cb(line)
            except Exception:
                pass

    def tail(self, n: int = 100) -> list:
        return list(self.records)[-n:]


RING = RingHandler()


def setup_logging(home: Path, level: str = "INFO", console: bool = True) -> RingHandler:
    root = logging.getLogger("eqvoice")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    root.propagate = False
    logdir = Path(home) / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(logdir / "eqvoice.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(RING)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S"))
        root.addHandler(ch)
    return RING


def ensure_logging(home: Path, level: str = "INFO") -> RingHandler:
    """Set logging up quietly (file + ring, no console) unless the program already did."""
    if not logging.getLogger("eqvoice").handlers:
        setup_logging(home, level, console=False)
    return RING
