"""CPU and memory of this process and of the media-engine browser (a tree of processes), without psutil.

    meter = ResourceMeter()
    meter.read("client", [os.getpid()])                   -> {"cpu_pct": 3.1, "rss_mb": 74.0, "private_mb": 58.2, "procs": 1}
    meter.read("engine", tree_pids(browser_pid))

``cpu_pct`` is a percentage of ONE core since the previous read for that label (100 = one core fully busy; a multi-process
browser can exceed it). ``rss_mb`` is the working set, ``private_mb`` the memory not shared with other processes (the better
figure for a browser whose helper processes share a lot).

Windows uses the kernel API directly (a process list snapshot, GetProcessTimes, GetProcessMemoryInfo): about a millisecond a
read. Linux reads /proc. Anything unreadable is skipped and reported as fewer ``procs``.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, Iterable, Optional

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x2
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                           "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage",
                                           "PeakPagefileUsage", "PrivateUsage")]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]


def _ft(ft) -> float:
    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7          # 100 ns ticks -> seconds


def process_table() -> dict:
    """{pid: parent pid} of every running process."""
    out: dict = {}
    if IS_WINDOWS:
        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE:
            return out
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = _k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                out[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
                ok = _k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            _k32.CloseHandle(snap)
    elif os.path.isdir("/proc"):
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open("/proc/%s/stat" % d, "rb") as f:
                        out[int(d)] = int(f.read().rsplit(b")", 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    pass
    return out


def tree_pids(root: Optional[int], table: Optional[dict] = None) -> list:
    """``root`` and all of its descendants (a browser keeps its renderer, GPU and network helpers as children)."""
    if not root:
        return []
    table = table if table is not None else process_table()
    if root not in table:
        return []
    kids: dict = {}
    for pid, ppid in table.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.append(p)
        todo.extend(kids.get(p, []))
    return out


def process_usage(pid: int) -> Optional[tuple]:
    """(cpu seconds used so far, working set bytes, private bytes) or None when it cannot be read."""
    if IS_WINDOWS:
        h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, x, k, u = (wintypes.FILETIME() for _ in range(4))
            if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x), ctypes.byref(k), ctypes.byref(u)):
                return None
            m = PROCESS_MEMORY_COUNTERS_EX()
            m.cb = ctypes.sizeof(m)
            if not _k32.K32GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
                return None
            return _ft(k) + _ft(u), float(m.WorkingSetSize), float(m.PrivateUsage)
        finally:
            _k32.CloseHandle(h)
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            f_ = f.read().rsplit(b")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        rss = int(f_[21]) * os.sysconf("SC_PAGE_SIZE")
        return (int(f_[11]) + int(f_[12])) / ticks, float(rss), float(rss)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class ResourceMeter:
    """Turns cumulative CPU seconds into a percentage between two reads, per label."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self.clock = clock
        self.prev: dict = {}              # label -> (time, {pid: cpu seconds})

    def read(self, label: str, pids: Iterable[int]) -> dict:
        now = self.clock()
        cpu: dict = {}
        rss = private = 0.0
        for pid in pids:
            u = process_usage(pid)
            if u is None:
                continue
            cpu[pid] = u[0]
            rss += u[1]
            private += u[2]
        pct: Optional[float] = None
        if label in self.prev:
            t0, before = self.prev[label]
            dt = now - t0
            if dt > 0.05:
                # processes that appeared since the last read count from zero; ones that left are simply gone
                used = sum(c - before.get(pid, 0.0) for pid, c in cpu.items())
                pct = max(0.0, 100.0 * used / dt)
        self.prev[label] = (now, cpu)
        return {"cpu_pct": None if pct is None else round(pct, 1), "rss_mb": round(rss / 1048576, 1),
                "private_mb": round(private / 1048576, 1), "procs": len(cpu)}
