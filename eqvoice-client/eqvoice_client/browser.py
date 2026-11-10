"""Find and start the Chromium-based browser that hosts the WebRTC media engine.

Chromium's WebRTC stack is the only readily available one with acoustic echo
cancellation, noise suppression, automatic gain control and Opus built in, which
is what makes speaker-and-microphone voice chat usable. The client runs it in a
private profile, in a small app window.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("eqvoice.browser")


def candidates() -> list:
    out = []
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
        for r in filter(None, roots):
            out += [Path(r) / "Microsoft/Edge/Application/msedge.exe",
                    Path(r) / "Google/Chrome/Application/chrome.exe",
                    Path(r) / "Chromium/Application/chrome.exe"]
    elif sys.platform == "darwin":
        out += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for name in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            out.append(Path(p))
    return out


def find_browser(preferred: str = "") -> Optional[Path]:
    if preferred and Path(preferred).exists():
        return Path(preferred)
    for c in candidates():
        if c.exists():
            return c
    return None


def build_args(exe: Path, url: str, profile: Path, headless: bool = False, fake_media: bool = False,
               mute: bool = False, extra: tuple = ()) -> list:
    args = [str(exe), ("--app=" + url) if not headless else "--headless=new",
            "--user-data-dir=" + str(profile), "--no-first-run",
            "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
            "--disable-features=CalculateNativeWinOcclusion,Translate,MediaRouter,IntensiveWakeUpThrottling,TabFreeze,PageFreezing,msSleepingTabs,msEfficiencyMode",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", "--window-size=560,860",
            # This window shows only EqVoice's own page, served from this PC, and it is useless without the microphone. A new browser profile
            # (a first launch, or a second copy of EqVoice) would otherwise wait for someone to find and click Edge's "Allow" bubble, with
            # the window saying only "starting the microphone": the single biggest wait a new player met. This answers the prompt with
            # "allow" and uses the default microphone; the microphone is still chosen in the window.
            "--use-fake-ui-for-media-stream"]
    if fake_media:
        args += ["--use-fake-device-for-media-stream"]
        wav = os.environ.get("EQVOICE_FAKE_AUDIO_FILE")          # tests: the fake microphone plays this recording (looped) instead of beeping
        if wav:
            args.append("--use-file-for-fake-audio-capture=" + wav)
    if mute:
        args.append("--mute-audio")
    args += list(extra)
    if headless:
        args.append(url)
    return args


def profile_pids(profile: Path) -> list:
    """Process ids of every Edge/Chrome started with a command line that mentions ``profile`` (a folder or part of one)."""
    if sys.platform != "win32":
        return []
    needle = str(Path(profile)).replace("'", "''")
    script = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf('" + needle + "', [StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
              "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def kill_profile(profile: Path) -> int:
    """Stop every browser process that was started with ``--user-data-dir=<profile>``; returns how many.

    Why this exists: Chromium is one process per profile. A leftover instance (a crashed run, a test browser that
    outlived its client) swallows a new launch with the same profile, so the new window silently becomes a page inside
    the OLD process, with its old flags (a fake microphone, muted output). Killing by profile path is the only reliable
    cure, because the leftover is not always a child of anything we can find.
    """
    pids = profile_pids(profile)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return len(pids)


def launch(exe: Path, url: str, profile: Path, **kw) -> subprocess.Popen:
    profile.mkdir(parents=True, exist_ok=True)
    args = build_args(exe, url, profile, **kw)
    log.info("starting %s", exe.name)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW if kw.get("headless") else 0
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
