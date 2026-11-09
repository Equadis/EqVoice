"""Reach the local test hub from the internet without deploying anything: a Cloudflare "quick tunnel".

    cloudflared tunnel --url http://127.0.0.1:8787     prints https://<random-words>.trycloudflare.com

No Cloudflare account is involved and nothing is deployed to Cloudflare: the tunnel just forwards the internet to the hub
running on this PC for as long as the process lives. This module only *finds and starts* ``cloudflared``; it never downloads it.
Look for it at $CLOUDFLARED, D:\\Octowow\\.eco-work\\cloudflared\\cloudflared.exe, or on PATH.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

WORK = Path(r"D:\Octowow\.eco-work")
URL = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def find_cloudflared() -> Optional[Path]:
    for cand in (os.environ.get("CLOUDFLARED"), str(WORK / "cloudflared" / "cloudflared.exe"), shutil.which("cloudflared")):
        if cand and Path(cand).exists():
            return Path(cand)
    return None


def to_wss(https_url: str) -> str:
    return re.sub(r"^https://", "wss://", https_url.rstrip("/")) + "/ws"


def start_quick_tunnel(exe: Path, port: int, log_path: Path, timeout: float = 60.0) -> tuple:
    """(process, 'wss://.../ws'). The process keeps running; stop it with taskkill /T /F. Raises RuntimeError on failure."""
    log = open(log_path, "w", encoding="utf-8")
    p = subprocess.Popen([str(exe), "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    found: list = []

    def pump():
        for line in p.stdout:                       # type: ignore[union-attr]
            log.write(line)
            log.flush()
            m = URL.search(line)
            if m and not found:
                found.append(m.group(0))
    threading.Thread(target=pump, daemon=True).start()
    end = time.time() + timeout
    while time.time() < end and not found and p.poll() is None:
        time.sleep(0.2)
    if not found:
        if p.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        raise RuntimeError("cloudflared did not print a tunnel address in %.0f s; see %s" % (timeout, log_path))
    return p, to_wss(found[0])
