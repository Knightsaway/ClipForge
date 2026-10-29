"""How light ClipForge really is: CPU, memory and disk use of ClipForge plus its FFmpeg recorders.

CPU is shown like Task Manager (share of the whole PC, all cores). The GPU numbers come from
nvidia-smi when it exists and are for the whole card (the game included), so the encoder load is
the honest one to quote: NVENC is a separate chip, which is why recording barely touches FPS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

STARTED = time.perf_counter()          # set when this module is first imported (early in startup)
startup_seconds: float | None = None    # filled in by the main window once it's on screen


def mark_started() -> None:
    global startup_seconds
    if startup_seconds is None:
        startup_seconds = round(time.perf_counter() - STARTED, 2)


@dataclass
class Sample:
    t: float                 # perf_counter
    cpu: float               # seconds of CPU used so far, all processes
    ram: float               # MB working set, all processes
    written: float           # MB written so far, all processes
    gpu: float | None = None      # % whole card
    enc: float | None = None      # % NVENC encoder


@dataclass
class Summary:
    seconds: float
    cpu_pct: float
    cpu_peak: float
    ram_mb: float
    ram_peak: float
    write_mbs: float
    gpu_pct: float | None = None
    enc_pct: float | None = None
    processes: int = 0
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"CPU {self.cpu_pct:.1f}% (peak {self.cpu_peak:.1f}%)",
                 f"RAM {self.ram_mb:.0f} MB",
                 f"disk {self.write_mbs:.1f} MB/s"]
        if self.enc_pct is not None:
            parts.append(f"NVENC {self.enc_pct:.0f}%")
        return " · ".join(parts)


def summarize(samples: list[Sample], cores: int | None = None, processes: int = 0) -> Summary | None:
    """Averages over the samples (needs at least two). Pure, so it's testable anywhere."""
    if len(samples) < 2:
        return None
    cores = cores or os.cpu_count() or 1
    span = samples[-1].t - samples[0].t
    if span <= 0:
        return None
    steps = []
    for a, b in zip(samples, samples[1:]):
        dt = b.t - a.t
        if dt > 0:
            steps.append(max(0.0, (b.cpu - a.cpu) / dt / cores * 100))
    gpus = [s.gpu for s in samples if s.gpu is not None]
    encs = [s.enc for s in samples if s.enc is not None]
    return Summary(
        seconds=round(span, 1),
        cpu_pct=max(0.0, (samples[-1].cpu - samples[0].cpu) / span / cores * 100),
        cpu_peak=max(steps) if steps else 0.0,
        ram_mb=sum(s.ram for s in samples) / len(samples),
        ram_peak=max(s.ram for s in samples),
        write_mbs=max(0.0, (samples[-1].written - samples[0].written) / span),
        gpu_pct=sum(gpus) / len(gpus) if gpus else None,
        enc_pct=sum(encs) / len(encs) if encs else None,
        processes=processes,
    )


# ------------------------------------------------------------------ reading processes (Windows)
def _proc_stats(pids: list[int]) -> tuple[float, float, float, int]:
    """(cpu seconds, working set MB, MB written, processes found) summed over pids."""
    if os.name != "nt":
        return _proc_stats_posix(pids)
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    class IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                       "OtherOperationCount", "ReadTransferCount",
                                                       "WriteTransferCount", "OtherTransferCount")]

    cpu = ram = written = 0.0
    found = 0
    for pid in pids:
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
            if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                for ft in (kt, ut):
                    cpu += ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            if k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                ram += pmc.WorkingSetSize / 1048576
            io = IOC()
            if k32.GetProcessIoCounters(h, ctypes.byref(io)):
                written += io.WriteTransferCount / 1048576
            found += 1
        finally:
            k32.CloseHandle(h)
    return cpu, ram, written, found


def _proc_stats_posix(pids: list[int]) -> tuple[float, float, float, int]:
    cpu = ram = written = 0.0
    found = 0
    tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                st = f.read().rsplit(")", 1)[1].split()
            cpu += (int(st[11]) + int(st[12])) / tick
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        ram += int(line.split()[1]) / 1024
            try:
                with open(f"/proc/{pid}/io") as f:
                    for line in f:
                        if line.startswith("write_bytes:"):
                            written += int(line.split()[1]) / 1048576
            except OSError:
                pass
            found += 1
        except (OSError, IndexError, ValueError):
            continue
    return cpu, ram, written, found


def _nvidia() -> tuple[float | None, float | None]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None, None
    try:
        r = subprocess.run([exe, "--query-gpu=utilization.gpu,utilization.encoder", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        g, e = (float(x) for x in r.stdout.strip().splitlines()[0].split(","))
        return g, e
    except Exception:
        return None, None


def sample(pids: list[int], gpu: bool = False) -> tuple[Sample, int]:
    cpu, ram, written, found = _proc_stats(pids)
    g, e = _nvidia() if gpu else (None, None)
    return Sample(time.perf_counter(), cpu, ram, written, g, e), found


# ------------------------------------------------------------------ measuring
class Monitor:
    """Light background meter: one sample every `every` seconds while ClipForge records, keeping the
    last ~30 minutes, so the Diagnostics page always has real numbers without anyone pressing a button."""

    def __init__(self, pids_fn, active_fn, every: float = 5.0, keep: int = 360):
        self.pids_fn, self.active_fn, self.every, self.keep = pids_fn, active_fn, every, keep
        self.samples: list[Sample] = []
        self._procs = 0
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                if not self.active_fn():
                    self.samples.clear()          # a new recording session starts a new average
                    continue
                s, self._procs = sample(self.pids_fn())
                self.samples = (self.samples + [s])[-self.keep:]
            except Exception:
                continue

    def summary(self) -> Summary | None:
        return summarize(list(self.samples), processes=self._procs)

    def stop(self):
        self._stop.set()


def measure(pids_fn, seconds: float = 60, progress=None) -> Summary | None:
    """Samples once a second for `seconds` (with GPU), e.g. while you play a match."""
    samples, procs = [], 0
    end = time.perf_counter() + seconds
    while True:
        s, procs = sample(pids_fn(), gpu=True)
        samples.append(s)
        left = end - time.perf_counter()
        if progress:
            progress(min(1.0, 1 - left / seconds))
        if left <= 0:
            break
        time.sleep(min(1.0, left))
    return summarize(samples, processes=procs)


def proc_memory(pid: int, kind: str = "private") -> float | None:
    """A process's memory in MB. kind="private": memory only it owns (committed); kind="ws": what it has in
    RAM right now (its working set — close to Task Manager's Memory column)."""
    if os.name != "nt":
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except OSError:
            return None
        return None
    import ctypes
    from ctypes import wintypes

    class PMCX(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        m = PMCX()
        m.cb = ctypes.sizeof(PMCX)
        if k32.K32GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
            return (m.WorkingSetSize if kind == "ws" else m.PrivateUsage) / 1048576
        return None
    finally:
        k32.CloseHandle(h)


