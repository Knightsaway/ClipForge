"""The recording engine.

How it stays light and stable:
* FFmpeg does all the work in its own process: Desktop Duplication (ddagrab) hands
  GPU frames straight to the NVENC encoder, so frames never touch the CPU.
* The replay buffer is a rolling set of 2-second .ts files on disk, not RAM.
  Saving a clip is a file copy + lossless join of the video: instant, no re-encode, no stutter.
* Video, webcam, game sound and mic each run in their OWN FFmpeg process with their own files.
  In one shared process FFmpeg throttles the fastest input to match the slowest, so a sound card
  running 3% slow turned 60 fps into a slideshow. Separate, they can't hold each other back; every
  segment carries real clock time, and saving lines them up to the millisecond.
* Full-session recording uses the same segments, so you can even start a recording
  "in the past" (everything still in the buffer is kept).
* A watchdog restarts FFmpeg if it ever dies, and segments survive a PC crash.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import ff
from .audio import AudioSender
from .config import IS_WINDOWS, Settings
from .gameinfo import current_game, safe_folder_name

SEG = 2  # seconds per buffer segment (= keyframe interval)
AAC_DELAY = 2 * 1024 / 48000  # measured: recording + saving each delay the sound by one AAC frame
TEST_MODE = os.environ.get("CLIPFORGE_TEST") == "1"

RES = {"2160p": (3840, 2160), "1440p": (2560, 1440), "1080p": (1920, 1080), "720p": (1280, 720)}


@dataclass
class Segment:
    path: Path
    wall_start: float
    wall_end: float | None = None     # None while FFmpeg is still writing it


class Series:
    """One stream of 2-second files (video, webcam, game sound or mic) from one FFmpeg run.

    FFmpeg writes `<prefix>.csv` with each finished file's start/end time; wall-clock time is
    `offset + csv time` (video/webcam: offset = the shared session base, timestamps are wall-clock;
    audio: offset = the moment the first sample was sent)."""

    def __init__(self, kind: str, folder: Path, prefix: str, offset: float | None):
        self.kind, self.folder, self.prefix = kind, folder, prefix
        self.offset = offset
        self.offset_fn = None          # for audio: read the sender's t0 once it's known
        self.segs: list[Segment] = []
        self._csv_pos = 0
        self._by_name: dict[str, Segment] = {}

    @property
    def csv(self) -> Path:
        return self.folder / f"{self.prefix}.csv"

    def pattern(self) -> str:
        return str(self.folder / f"{self.prefix}_%06d.ts")

    def scan(self):
        if self.offset is None and self.offset_fn:
            self.offset = self.offset_fn()
        if self.offset is None:
            return
        # finished files, with exact times
        try:
            with open(self.csv, "rb") as f:
                f.seek(self._csv_pos)
                data = f.read()
        except OSError:
            data = b""
        done = data.rfind(b"\n")
        if done >= 0:
            self._csv_pos += done + 1
            for line in data[:done].decode("utf-8", "replace").splitlines():
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                try:
                    start, end = float(parts[1]), float(parts[2])
                except ValueError:
                    continue
                name = Path(parts[0]).name
                sg = self._by_name.get(name)
                if sg is None:
                    sg = Segment(self.folder / name, 0.0)
                    self._by_name[name] = sg
                    self.segs.append(sg)
                if start == 0.0 and name.endswith("_000000.ts") and self.kind in ("video", "cam") and end > 50:
                    # the first file's start is reported as 0; work it out from its length instead
                    start = end - (ff.duration(sg.path) or SEG)
                sg.wall_start, sg.wall_end = self.offset + start, self.offset + end
        # the file being written right now (not in the csv yet)
        for path in self.folder.glob(f"{self.prefix}_*.ts"):
            if path.name not in self._by_name:
                prev = max((g.wall_end for g in self.segs if g.wall_end), default=None)
                sg = Segment(path, prev if prev is not None else time.time() - 0.5)
                self._by_name[path.name] = sg
                self.segs.append(sg)
        self.segs.sort(key=lambda g: g.path.name)

    def drop_before(self, t: float):
        keep = []
        for g in self.segs:
            if g.wall_end is not None and g.wall_end < t:
                self._by_name.pop(g.path.name, None)
                try:
                    g.path.unlink()
                except OSError:
                    pass
            else:
                keep.append(g)
        self.segs = keep


class SideProc(threading.Thread):
    """Runs one helper recorder (webcam or one audio source) and restarts it if it stops,
    without ever touching the game-video recorder."""

    def __init__(self, engine, kind: str, folder: Path, base: float):
        super().__init__(name=f"rec-{kind}", daemon=True)
        self.engine, self.kind, self.folder, self.base = engine, kind, folder, base
        self.halt = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.sender: AudioSender | None = None
        self.tail: deque[str] = deque(maxlen=20)
        self.runs = 0

    def _audio_problem(self, msg: str):
        self.engine._log(f"[{self.kind}] {msg}")
        self.engine.audio_warnings[self.kind] = msg
        short = msg.split(". ")[0].split(" — ")[0]
        self.engine.emit("toast", ("🎤 " if self.kind == "mic" else "🔊 ") + short + " — details in Diagnostics")

    def stop(self):
        self.halt.set()
        if self.sender:
            self.sender.stop()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin.write("q")
                p.stdin.flush()
                p.wait(3)
            except Exception:
                p.kill()

    def run(self):
        fails = 0
        while not self.halt.is_set():
            self.runs += 1
            prefix = {"cam": "cam", "game": "aga", "mic": "ami"}[self.kind] + str(self.runs)
            if self.kind == "cam":
                series = Series("cam", self.folder, prefix, self.base)
                cmd = self.engine._cam_cmd(series, self.base)
                if cmd is None:     # camera busy / unplugged: look again in a minute
                    if self.halt.wait(60):
                        return
                    self.engine._cam_ok = None
                    continue
            else:
                series = Series(self.kind, self.folder, prefix, None)
                port = _free_port()
                cmd = self.engine._audio_cmd(series, port)
            started = time.time()
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                             errors="replace", **ff.popen_flags("high"))
                ff.adopt(self.proc)
                if self.halt.is_set():        # told to stop while it was starting: don't leave it running
                    self.proc.kill()
                    return
            except OSError as e:
                self.engine._log(f"{self.kind} recorder couldn't start: {e}")
                return
            if self.kind != "cam":
                self.sender = AudioSender("desktop" if self.kind == "game" else "mic", port,
                                          self.engine.s.mic_device,
                                          on_error=self._audio_problem)
                self.sender.start()
                series.offset_fn = lambda snd=self.sender: snd.t0_wall
            with self.engine._lock:
                self.engine._series.append(series)
            for line in self.proc.stderr:
                self.tail.append(line.rstrip())
            code = self.proc.wait()
            if self.sender:
                self.sender.stop()
            if self.halt.is_set():
                return
            fails = 0 if time.time() - started > 30 else fails + 1
            self.engine._log(f"{self.kind} recorder stopped (code {code}), restarting\n" + "\n".join(self.tail))
            if fails >= 5:
                self.engine.emit("toast", f"{'Webcam' if self.kind == 'cam' else self.kind.title() + ' audio'}"
                                          " recording keeps failing — see Diagnostics")
            self.halt.wait([1, 2, 5, 15, 60][min(fails, 4)])


@dataclass
class EngineState:
    buffering: bool = False
    recording: bool = False
    record_started: float = 0.0
    paused_no_game: bool = False
    game: str | None = None
    error: str = ""
    pipeline: str = ""
    markers: list = field(default_factory=list)


def _enable_privilege(name: str = "SeIncreaseBasePriorityPrivilege") -> bool:
    """Admins have this right but it's switched off by default; it must be on to raise GPU priority."""
    import ctypes
    from ctypes import wintypes as wt

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

    class TP(ctypes.Structure):
        _fields_ = [("Count", wt.DWORD), ("Luid", LUID), ("Attr", wt.DWORD)]
    # every call is typed: GetCurrentProcess() returns (HANDLE)-1, which as a plain Python int overflowed the
    # default C int argument ("argument 1: OverflowError: int too long to convert") — so priority never rose
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.GetCurrentProcess.argtypes = []
    k32.CloseHandle.argtypes = [wt.HANDLE]
    adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.OpenProcessToken.restype = wt.BOOL
    adv.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
    adv.LookupPrivilegeValueW.restype = wt.BOOL
    adv.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.POINTER(TP), wt.DWORD, ctypes.c_void_p,
                                          ctypes.c_void_p]
    adv.AdjustTokenPrivileges.restype = wt.BOOL
    tok = wt.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(tok)):
        return False
    luid = LUID()
    ok = adv.LookupPrivilegeValueW(None, name, ctypes.byref(luid))
    if ok:
        tp = TP(1, luid, 0x2)  # SE_PRIVILEGE_ENABLED
        ctypes.set_last_error(0)
        ok = adv.AdjustTokenPrivileges(tok, False, ctypes.byref(tp), 0, None, None) and \
            ctypes.get_last_error() == 0
    k32.CloseHandle(tok)
    return bool(ok)


def _raise_gpu_priority(proc) -> str:
    """Ask Windows to schedule the recorder's GPU work ahead of the game's (what OBS does as admin)."""
    if not IS_WINDOWS or proc is None:
        return "n/a"
    try:
        import ctypes
        priv = _enable_privilege()
        fn = ctypes.WinDLL("gdi32").D3DKMTSetProcessSchedulingPriorityClass
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn.restype = ctypes.c_long
        handle = int(proc._handle)
        codes = []
        for level, name in ((5, "realtime"), (4, "high"), (3, "above normal")):
            st = fn(handle, level)
            if st == 0:   # STATUS_SUCCESS
                return name
            codes.append(f"{name}=0x{st & 0xFFFFFFFF:08X}")
        return f"normal (privilege {'on' if priv else 'off'}; {', '.join(codes)})"
    except Exception as e:
        return f"normal ({e})"


def dshow_args(name: str, res: str = "", fps: int = 0, vcodec: str = "") -> list[str]:
    a = ["-f", "dshow", "-rtbufsize", "64M"]   # ~10 s of backlog at 1080p; 256M let a stall eat RAM
    if res:
        a += ["-video_size", res]
    if fps:
        a += ["-framerate", str(fps)]
    if vcodec:
        a += ["-vcodec", vcodec]
    return a + ["-i", f"video={name}"]


def find_webcam_mode(name: str, res: str, fps: int) -> tuple[list[str] | None, str]:
    """Tries the chosen size/fps first, then gentler combinations (cameras only accept the exact modes they
    list). Returns (working dshow args, '') or (None, FFmpeg's error)."""
    res = res or "1280x720"
    tries = [(res, fps, ""), (res, fps, "mjpeg"), (res, 0, ""), (res, 60 if fps != 60 else 30, ""),
             ("1920x1080", 30, ""), ("1920x1080", 60, ""), ("", 0, "")]
    last = ""
    seen = set()
    for r, f, c in tries:
        if (r, f, c) in seen:
            continue
        seen.add((r, f, c))
        args = dshow_args(name, r, f, c)
        try:
            p = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error"] + args + ["-frames:v", "5", "-f", "null", "-"],
                       priority="normal", timeout=20)
        except Exception as e:
            last = str(e)
            continue
        if p.returncode == 0:
            return args, ""
        last = (p.stderr or "").strip().splitlines()[-1] if (p.stderr or "").strip() else f"code {p.returncode}"
        if "I/O error" in (p.stderr or "") or "Could not run graph" in (p.stderr or ""):
            break       # camera is busy: other modes won't help
    return None, last


def webcam_hint(err: str) -> str:
    e = err.lower()
    if "could not run graph" in e or "i/o error" in e or "in use" in e or "busy" in e:
        return ("another app is using the camera. Close Elgato Camera Hub, Discord video, OBS, Teams or the "
                "browser tab using it, or turn on 'allow multiple apps' in Camera Hub.")
    if "could not find" in e or "not found" in e or "no such" in e:
        return "Windows doesn't see that camera right now. Is it plugged in? Pick it again in Settings."
    if "set video options" in e or "video size" in e or "framerate" in e:
        return "the camera refused every size/fps tried. Try Webcam size 1920x1080 in Settings."
    return "see Diagnostics for FFmpeg's message."


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _GameSettings:
    """Your settings with one game's own frame rate / quality / buffer on top (Settings → Per-game recording)."""

    def __init__(self, base, overrides: dict):
        object.__setattr__(self, "_b", base)
        object.__setattr__(self, "_o", dict(overrides))

    def __getattr__(self, k):
        o = object.__getattribute__(self, "_o")
        return o[k] if k in o else getattr(object.__getattribute__(self, "_b"), k)

    def __setattr__(self, k, v):
        setattr(object.__getattribute__(self, "_b"), k, v)


class CaptureEngine:
    def __init__(self, settings: Settings, on_event: Callable[[str, object], None], library):
        self.s = settings
        self._base_s = settings
        self._ov_applied: dict = {}
        self.emit = on_event
        self.library = library
        self.state = EngineState()
        self._proc: subprocess.Popen | None = None
        self._series: list[Series] = []          # every stream of files still on disk
        self._sides: list[SideProc] = []         # webcam / audio recorders for the current session
        self._session_dir: Path | None = None
        self._base = 0.0                          # wall-clock time that video timestamps count from
        self._lock = threading.RLock()
        self._want_running = False
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._pipeline_cache: tuple | None = None
        self._last_auto = 0.0
        self._last_game: str | None = None
        self._not_gaming_since = 0.0
        self._threads_started = False
        self._shutdown = threading.Event()
        self._cam_ok: bool | None = None
        self._followed_hwnd = None
        self.squad = None          # set by the app when a squad folder is configured
        self._fails = 0            # crashes in a row (reset once capture runs for a while)
        self._started_at = 0.0
        self._strip: set[str] = set()   # optional options this FFmpeg build rejected
        self.gpu_priority = "n/a"
        self.audio_warnings: dict[str, str] = {}   # kind -> last problem with that sound source
        self.real_fps: float | None = None   # frames that actually change per second (smoothness)
        self.starved_checks = 0

    @property
    def _segments(self) -> list[Segment]:
        """All game-video files in time order (across capture restarts)."""
        out = [g for sr in self._series if sr.kind == "video" for g in sr.segs]
        out.sort(key=lambda g: g.wall_start)
        return out

    @property
    def _senders(self) -> list[AudioSender]:
        return [sp.sender for sp in self._sides if sp.sender is not None]

    def _pick_side(self, kind: str, start: float, end: float) -> list[Segment]:
        """Files of one helper stream covering [start, end] — from the single run that covers it best."""
        best, best_cover = [], 0.0
        for sr in self._series:
            if sr.kind != kind:
                continue
            segs = [g for g in sr.segs if (g.wall_end or time.time()) > start - 0.5 and g.wall_start < end]
            cover = sum(min(g.wall_end or end, end) - max(g.wall_start, start) for g in segs)
            if segs and cover > best_cover:
                best, best_cover = segs, cover
        return best

    def _copy_parts(self, video: list[Segment], start: float, end: float, tmp: Path, link=False) -> dict:
        """Copies (or hard-links) every stream's files for [start, end] into tmp before they rotate out."""
        def put(src: Path, dst: Path) -> bool:
            try:
                if link:
                    try:
                        os.link(src, dst)
                        return True
                    except OSError:
                        pass
                shutil.copyfile(src, dst)
                return True
            except OSError:
                return False
        def dur(g: Segment):
            return (g.wall_end - g.wall_start) if g.wall_end else None
        parts = {"video": [], "sides": {}, "start": start, "end": end, "partial": {}, "names": {}}
        for i, g in enumerate(video):
            dst = tmp / f"v_{i:06d}.ts"
            if put(g.path, dst):
                parts["video"].append((dst, dur(g)))
        for kind in ("cam", "game", "mic"):
            segs = self._pick_side(kind, start, end)
            files = []
            for i, g in enumerate(segs):
                dst = tmp / f"{kind}_{i:06d}.ts"
                if put(g.path, dst):
                    files.append((dst, dur(g)))
                    parts["names"].setdefault(kind, []).append(g.path.name)
                    if g.wall_end is None:                       # still being written: fetch it again later
                        parts["partial"].setdefault(kind, []).append(g.path.name)
            if files:
                parts["sides"][kind] = (files, segs[0].wall_start)
        return parts

    def _complete_sides(self, parts: dict, tmp: Path, wait: float = 6.0) -> None:
        """The sound recorders run a second or more behind the video, so when a clip is saved their last
        file isn't finished yet — the clip's sound would stop early and the gap would be filled with
        silence (heard as the game AND your mic cutting out at the end of the clip). Wait until each
        one has finished the file covering the clip's end, then take the late files again."""
        end, start = parts.get("end"), parts.get("start")
        kinds = [k for k in ("game", "mic") if k in parts.get("sides", {})]
        if not end or not kinds:
            return

        def covered(kind):
            return any(g.wall_end and g.wall_end >= end - 0.05
                       for sr in self._series if sr.kind == kind for g in sr.segs)
        with self._lock:
            if all(covered(k) for k in kinds) and not parts.get("partial"):
                return                                            # everything was on disk already
        deadline = time.time() + wait
        while time.time() < deadline:
            with self._lock:
                for sr in self._series:
                    if sr.kind in kinds:
                        sr.scan()
                if all(covered(k) for k in kinds):
                    break
            time.sleep(0.25)
        with self._lock:
            for kind in kinds:
                segs = self._pick_side(kind, start, end)
                if not segs:
                    continue
                old_files, old_start = parts["sides"][kind]
                names = parts["names"].get(kind, [])
                done = dict(zip(names, old_files))                 # source name -> (copy, length)
                late = set(parts["partial"].get(kind, []))
                now_names = {g.path.name for g in segs}
                # files that rotated out of the buffer while we waited: keep the copies we took
                files = [done[n] for n in names if n not in now_names and n in done and n not in late
                         and n < min(now_names)]
                first_start = old_start if files else segs[0].wall_start
                for i, g in enumerate(segs):
                    d = (g.wall_end - g.wall_start) if g.wall_end else None
                    if g.path.name in done and g.path.name not in late:
                        files.append(done[g.path.name])           # was already complete: keep our copy
                        continue
                    dst = tmp / f"{kind}_late_{i:06d}.ts"
                    try:
                        shutil.copyfile(g.path, dst)
                        files.append((dst, d))
                    except OSError:                               # rotated out meanwhile: use what we had
                        if g.path.name in done:
                            files.append(done[g.path.name])
                if files:
                    parts["sides"][kind] = (files, first_start)

    # ------------------------------------------------------------------ public
    def start(self):
        self._want_running = True
        if not self._threads_started:
            self._threads_started = True
            self._recover_crashed_sessions()
            threading.Thread(target=self._janitor_loop, name="janitor", daemon=True).start()
            threading.Thread(target=self._monitor_loop, name="monitor", daemon=True).start()
            threading.Thread(target=self._vision_loop, name="vision", daemon=True).start()
            threading.Thread(target=self._health_loop, name="health", daemon=True).start()
        if self.s.only_while_gaming and IS_WINDOWS and not TEST_MODE and not self.state.recording:
            _, gaming = current_game(self.s.game_aliases)
            if not gaming:   # nothing to record yet: wait quietly, the monitor starts capture with the game
                self.state.paused_no_game = True
                self.emit("state", self.state)
                return
        self.state.paused_no_game = False
        self._launch()

    def stop(self):
        self._want_running = False
        self._kill()

    def labelled_pids(self) -> list[tuple[str, int]]:
        import os
        out = [("ClipForge", os.getpid())]
        if self._proc is not None and self._proc.poll() is None:
            out.append(("video recorder", self._proc.pid))
        names = {"cam": "webcam", "game": "game sound", "mic": "mic"}
        for sp in self._sides:
            if sp.proc is not None and sp.proc.poll() is None:
                out.append((names.get(sp.kind, sp.kind), sp.proc.pid))
        return out

    def pids(self) -> list[int]:
        """ClipForge itself plus its running FFmpeg recorders (for the performance meter)."""
        import os
        out = [os.getpid()]
        for p in [self._proc] + [sp.proc for sp in self._sides]:
            if p is not None and p.poll() is None:
                out.append(p.pid)
        return out

    def shutdown(self):
        if self.state.recording:
            self.stop_recording()
        self.stop()
        self._shutdown.set()

    def restart(self):
        """Apply new capture settings."""
        self._pipeline_cache = None
        self._cam_ok = None
        was = self._want_running
        self._kill()
        if was:
            self._want_running = True
            self._launch()

    def save_clip(self, seconds: int | None = None, highlight=False, marker_times=None, title: str = "",
                  squad: dict | None = None):
        seconds = seconds or self.s.clip_seconds
        now = time.time()
        game = self.state.game or self._last_game or "Desktop"
        tmp = Path(self.s.buffer_dir) / f"save_{int(now*1000)}"
        tmp.mkdir(parents=True, exist_ok=True)
        with self._lock:  # copy now, before the janitor rotates the files out
            for sr in self._series:
                sr.scan()
            segs = self._segments
            chosen = [g for g in segs if (g.wall_end or now) > now - seconds]
            if not chosen:
                shutil.rmtree(tmp, ignore_errors=True)
                self.emit("toast", "Nothing in the buffer yet")
                return
            clip_start = chosen[0].wall_start
            parts = self._copy_parts(chosen, clip_start, now, tmp)
        markers = [m - clip_start for m in (marker_times or self.state.markers) if clip_start <= m <= now]
        threading.Thread(target=self._finish_save, args=(parts, tmp, game, clip_start, markers, highlight),
                         kwargs={"title": title, "squad": squad}, daemon=True).start()

    def add_marker(self, t: float | None = None, title: str = ""):
        t = t or time.time()
        with self._lock:
            self.state.markers.append(t)
            self.state.markers = [m for m in self.state.markers if t - m < 3600 * 6]
        self.emit("marker", t)
        if self.squad is not None and self.squad.enabled and not title:  # tell the squad (not auto-kills)
            try:
                self.squad.publish_marker(t, self.state.game or self._last_game or "")
            except Exception as e:
                print("squad publish:", e)
        if not self.state.recording:
            # save a ready-made highlight once the "after" part has been captured
            def later():
                wait = t + self.s.marker_post + 1.0 - time.time()
                if wait > 0:
                    time.sleep(wait)
                secs = int(time.time() - t) + self.s.marker_pre
                sq = None
                if self.squad is not None and self.squad.enabled:
                    sq = {"by": self.squad.me, "t": round(t, 2), "share": True}
                self.save_clip(secs, highlight=True, marker_times=[t], title=title, squad=sq)
            threading.Thread(target=later, daemon=True).start()

    def start_recording(self, from_seconds_ago: int = 0):
        with self._lock:
            self.state.recording = True
            self.state.record_started = time.time() - from_seconds_ago
            (Path(self.s.buffer_dir) / "RECORDING").write_text(str(self.state.record_started))
        if not self.state.buffering:
            self._want_running = True
            self._launch()
        self.emit("state", self.state)

    def stop_recording(self):
        game = self.state.game or self._last_game or "Desktop"
        now = time.time()
        tmp = Path(self.s.buffer_dir) / f"rec_{int(now*1000)}"
        with self._lock:  # link the files while holding the lock so the janitor can't rotate them
            if not self.state.recording:
                return
            start = self.state.record_started
            self.state.recording = False
            try:
                (Path(self.s.buffer_dir) / "RECORDING").unlink()
            except OSError:
                pass
            for sr in self._series:
                sr.scan()
            segs = [g for g in self._segments if (g.wall_end or now) >= start]
            parts = None
            if segs:
                tmp.mkdir(parents=True, exist_ok=True)
                parts = self._copy_parts(segs, segs[0].wall_start, now, tmp, link=True)
            markers = [m - segs[0].wall_start for m in self.state.markers if segs and m >= segs[0].wall_start]
        self.emit("state", self.state)
        if not parts or not parts["video"]:
            return
        threading.Thread(target=self._finish_save,
                         args=(parts, tmp, game, segs[0].wall_start, markers, False, "Session"),
                         daemon=True).start()

    # ------------------------------------------------------------------ ffmpeg
    def _quality(self, w: int, h: int) -> tuple[int, int]:
        cq, maxrate = Settings.QUALITY_TABLE.get(self.s.quality, (19, 50))
        scale = (w * h) / (1920 * 1080) * (self.s.fps / 60)
        return cq, max(8, int(maxrate * max(0.5, scale)))

    def _target_size(self) -> tuple[int, int] | None:
        return RES.get(self.s.resolution)

    def _method_for_game(self) -> str:
        m = self.s.capture_method
        if m != "auto":
            return m
        if self.s.follow_game:
            return "window"   # only the game window: alt-tabbing never records your browser/desktop
        return self.s.method_by_game.get(self.state.game or self._last_game or "", "window")

    def _video_candidates(self) -> list[tuple[str, list[str]]]:
        """Ordered list of (name, input+filter args), best first."""
        fps = self.s.fps
        if TEST_MODE or not IS_WINDOWS:
            return [("test pattern", ["-re", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={fps}"])]
        from .gameinfo import LAST_GAME_WINDOW
        mouse = 1 if self.s.capture_cursor else 0
        tgt = self._target_size()
        method = self._method_for_game()
        window, desktop, gdi = [], [], []
        # 1. the game window itself (Windows Graphics Capture): no desktop, no Discord pop-ups when you alt-tab
        hwnd = LAST_GAME_WINDOW.get("hwnd")
        if hwnd:
            wgc = f"gfxcapture=hwnd={hwnd}:max_framerate={fps}:capture_cursor={mouse}:capture_border=0"
            if tgt:
                wgc += f":width={tgt[0]}:height={tgt[1]}:resize_mode=scale_aspect"
            window.append(("Game window capture (zero-copy)", ["-f", "lavfi", "-i", wgc]))
            window.append(("Game window capture + download",
                           ["-f", "lavfi", "-i", f"{wgc},hwdownload,format=bgra,format=nv12"]))
        # 2. the whole monitor (Desktop Duplication)
        dda = f"ddagrab=output_idx={self.s.monitor}:framerate={fps}:draw_mouse={mouse}"
        if tgt:
            w, h = tgt
            desktop.append(("Screen capture + GPU scale",
                            ["-f", "lavfi", "-i", f"{dda},scale_d3d11=width={w}:height={h}"]))
            desktop.append(("Screen capture + CPU scale",
                            ["-f", "lavfi", "-i", f"{dda},hwdownload,format=bgra,scale={w}:{h}:flags=bicubic,format=nv12"]))
        else:
            desktop.append(("Screen capture (zero-copy)", ["-f", "lavfi", "-i", dda]))
            desktop.append(("Screen capture + download", ["-f", "lavfi", "-i", f"{dda},hwdownload,format=bgra,format=nv12"]))
        scale = f",scale={tgt[0]}:{tgt[1]}" if tgt else ""
        gdi.append(("GDI capture (compatibility)",
                    ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", str(mouse), "-i", "desktop",
                     "-vf", f"format=nv12{scale}"]))
        order = {"window": window + desktop + gdi, "desktop": desktop + window + gdi, "gdi": gdi}
        return order.get(method, window + desktop + gdi)

    def _encoder_args(self, vendor: str, enc: str) -> list[str]:
        tgt = self._target_size() or (2560, 1440)
        cq, maxrate = self._quality(*tgt)
        gop = str(self.s.fps * SEG)
        keyf = ["-force_key_frames", f"expr:if(isnan(prev_forced_t),1,gte(t-prev_forced_t,{SEG}))", "-g", gop]
        if vendor == "nvenc":
            return ["-c:v", enc, "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
                    "-maxrate", f"{maxrate}M", "-bufsize", f"{maxrate*2}M", "-spatial-aq", "1", "-bf", "0",
                    "-forced-idr", "1"] + keyf
        if vendor == "amf":
            return ["-c:v", enc, "-quality", "balanced", "-rc", "qvbr", "-qvbr_quality_level", str(cq),
                    "-maxrate", f"{maxrate}M", "-bf", "0"] + keyf
        if vendor == "qsv":
            return ["-c:v", enc, "-preset", "faster", "-global_quality", str(cq), "-maxrate", f"{maxrate}M"] + keyf
        return ["-c:v", enc, "-preset", "veryfast", "-tune", "zerolatency", "-crf", str(cq + 2), "-maxrate", f"{maxrate}M",
                "-bufsize", f"{maxrate*2}M", "-pix_fmt", "yuv420p"] + keyf

    def _webcam_ok(self) -> bool:
        if not self.s.webcam or not getattr(self.s, "use_webcam", True):
            return False
        if TEST_MODE:
            return True
        if not IS_WINDOWS:
            return False
        if self._cam_ok is None:
            args, err = find_webcam_mode(self.s.webcam, self.s.webcam_res, self.s.webcam_fps)
            self._cam_ok = args is not None
            self._cam_args = args
            if self._cam_ok:
                self.audio_warnings.pop("cam", None)
                self._log(f"webcam '{self.s.webcam}' opened with: {' '.join(args)}")
            else:
                self.audio_warnings["cam"] = err
                self._log(f"webcam '{self.s.webcam}' couldn't be opened: {err}")
                self.emit("toast", f"📷 Webcam couldn't be opened — {webcam_hint(err)[:90]}")
        return bool(self._cam_ok)

    def _seg_out(self, series: Series) -> list[str]:
        return ["-f", "segment", "-segment_time", str(SEG), "-segment_format", "mpegts",
                "-segment_list", str(series.csv), "-segment_list_type", "csv", series.pattern()]

    def _cam_cmd(self, series: Series, base: float) -> list[str] | None:
        if TEST_MODE:
            cam_in = ["-re", "-use_wallclock_as_timestamps", "1", "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30"]
        elif self._webcam_ok():
            cam_in = ["-use_wallclock_as_timestamps", "1"] + self._dshow_args()
        else:
            return None
        vendor, enc = ff.pick_encoder("h264", self.s.encoder)
        if vendor == "nvenc":
            venc = ["-c:v", enc, "-preset", "p3", "-rc", "vbr", "-cq", "24", "-maxrate", "8M", "-bufsize", "16M"]
        elif vendor in ("amf", "qsv"):
            venc = ["-c:v", enc, "-b:v", "6M", "-maxrate", "8M"]
        else:
            venc = ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "24"]
        venc += ["-force_key_frames", f"expr:if(isnan(prev_forced_t),1,gte(t-prev_forced_t,{SEG}))",
                 "-g", str(self.s.webcam_fps * SEG)]
        return ([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "warning", "-nostats"] + cam_in +
                ["-copyts", "-output_ts_offset", f"{-base:.6f}", "-map", "0:v", "-vf", "format=yuv420p"] +
                self._without_stripped(venc) + self._seg_out(series))

    def _audio_cmd(self, series: Series, port: int) -> list[str]:
        return [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "warning", "-nostats",
                "-probesize", "32", "-analyzeduration", "0", "-f", "f32le", "-ar", "48000", "-ac", "2",
                "-i", f"tcp://127.0.0.1:{port}?listen=1&listen_timeout=15000",
                "-map", "0:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"] + self._seg_out(series)

    def _dshow_args(self) -> list[str]:
        return list(getattr(self, "_cam_args", None) or dshow_args(self.s.webcam, self.s.webcam_res,
                                                                    self.s.webcam_fps))

    def _test_pipeline(self, vin: list[str], venc: list[str]) -> bool:
        cmd = [ff.ffmpeg_bin(), "-hide_banner", "-v", "error"] + vin + ["-t", "1"] + venc + ["-f", "null", "-"]
        try:
            r = ff.run(cmd, priority="normal", timeout=20)
            return r.returncode == 0
        except Exception:
            return False

    def _pick_pipeline(self) -> tuple[list[str], list[str], str]:
        from .gameinfo import LAST_GAME_WINDOW
        key = (self._method_for_game(), LAST_GAME_WINDOW.get("hwnd"))
        if self._pipeline_cache and self._pipeline_cache[0] == key:
            return self._pipeline_cache[1]
        result = self._probe_pipeline()
        self._pipeline_cache = (key, result)
        return result

    def _probe_pipeline(self) -> tuple[list[str], list[str], str]:
        vendor, enc = ff.pick_encoder(self.s.codec, self.s.encoder)
        venc = self._encoder_args(vendor, enc)
        cands = self._video_candidates()
        if TEST_MODE or not IS_WINDOWS:
            return cands[0][1], venc, f"{cands[0][0]} → {enc}"
        for name, vin in cands:
            if self._test_pipeline(vin, venc):
                return vin, venc, f"{name} → {enc}"
        # last resort: software encode
        venc = self._encoder_args("x264", "libx264")
        return cands[-1][1], venc, f"{cands[-1][0]} → libx264"

    def _build_cmd(self) -> tuple[list[str], Series]:
        vin, venc, label = self._pick_pipeline()
        self.state.pipeline = label
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_dir = Path(self.s.buffer_dir) / f"session_{stamp}"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._base = float(int(time.time()) - 60)   # timestamps = seconds since this moment
        series = Series("video", self._session_dir, "seg", self._base)
        # game video ONLY: nothing else in this process can slow it down
        cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "warning", "-nostats",
               "-use_wallclock_as_timestamps", "1"] + vin + \
              ["-copyts", "-output_ts_offset", f"{-self._base:.6f}", "-map", "0:v"] + venc + self._seg_out(series)
        return cmd, series

    def _launch(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            try:
                cmd, series = self._build_cmd()
            except FileNotFoundError as e:
                self.state.error = "FFmpeg is missing — reinstall the win64 release (ffmpeg\\bin) or run setup.bat"
                self._log(str(e))
                self.emit("state", self.state)
                return
            cmd = self._without_stripped(cmd)
            if (self.s.follow_game and self._method_for_game() == "window" and not TEST_MODE
                    and not self.state.pipeline.startswith("Game window")
                    and time.time() - getattr(self, "_fallback_note", 0) > 600):
                self._fallback_note = time.time()
                self.emit("toast", "Couldn't capture just the game window — recording the whole screen for now")
            self._stderr_tail.clear()
            self._started_at = time.time()
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                          errors="replace", **ff.popen_flags("high"))
            ff.adopt(self._proc)
            self._series.append(series)
            threading.Thread(target=self._apply_gpu_priority, args=(self._proc,), daemon=True).start()
            threading.Thread(target=self._read_stderr, args=(self._proc,), daemon=True).start()
            self._start_sides()
            self.state.buffering = True
            self.state.error = ""
            self.emit("state", self.state)

    def _start_sides(self):
        """Webcam and audio recorders run next to the video one (started once per buffer session)."""
        if self._sides and all(sp.is_alive() for sp in self._sides):
            return
        self._stop_sides()
        folder = self._session_dir
        kinds = []
        if self.s.webcam and getattr(self.s, "use_webcam", True):
            kinds.append("cam")
        if self.s.desktop_audio:
            kinds.append("game")
        if self.s.mic_audio:
            kinds.append("mic")
        self._sides = [SideProc(self, k, folder, self._base) for k in kinds]
        for sp in self._sides:
            sp.start()

    def _stop_sides(self):
        sides, self._sides = self._sides, []
        for sp in sides:
            sp.stop()

    def _apply_gpu_priority(self, proc):
        time.sleep(3)   # once FFmpeg has its graphics device
        if proc.poll() is None:
            self.gpu_priority = _raise_gpu_priority(proc)
            self._log(f"GPU priority: {self.gpu_priority}")

    OPTIONAL = {"-thread_queue_size": 1, "-probesize": 1, "-analyzeduration": 1, "-spatial-aq": 1,
                "-forced-idr": 1, "-tune": 1, "-bf": 1, "-rtbufsize": 1, "-qvbr_quality_level": 1}

    def _without_stripped(self, cmd: list[str]) -> list[str]:
        out, skip = [], 0
        for a in cmd:
            if skip:
                skip -= 1
                continue
            if a in self._strip:
                skip = self.OPTIONAL.get(a, 0)
                continue
            out.append(a)
        return out

    def _learn_incompatible(self, text: str) -> bool:
        """FFmpeg builds differ; if one rejects an optional setting, stop using that setting."""
        import re as _re
        for m in _re.finditer(r"[Oo]ption '?-?([\w-]+)'?.{0,80}?(cannot be applied|not found|Unrecognized)", text):
            opt = "-" + m.group(1)
            if opt in self.OPTIONAL and opt not in self._strip:
                self._strip.add(opt)
                return True
        for m in _re.finditer(r"Unrecognized option '([\w-]+)'", text):
            opt = "-" + m.group(1)
            if opt in self.OPTIONAL and opt not in self._strip:
                self._strip.add(opt)
                return True
        return False

    def _log(self, text: str):
        try:
            from .config import app_data_dir
            with open(app_data_dir() / "capture.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
        except OSError:
            pass

    def _read_stderr(self, proc: subprocess.Popen):
        for line in proc.stderr:
            self._stderr_tail.append(line.rstrip())
        code = proc.wait()
        with self._lock:
            if proc is not self._proc:
                return
            self.state.buffering = False
        if not self._want_running or self._shutdown.is_set() or self.state.paused_no_game:
            self.emit("state", self.state)
            return
        tail = "\n".join(list(self._stderr_tail)[-12:])
        self._log(f"capture exited with code {code}\n{tail}")
        if time.time() - self._started_at > 30:
            self._fails = 0            # it ran fine for a while: a one-off hiccup
        self._fails += 1
        fixed = self._learn_incompatible(tail)
        # plain words for the app; the full FFmpeg output goes to capture.log
        self.state.error = ("Adjusting capture settings for this PC…" if fixed else
                            "Capture keeps stopping — details in capture.log" if self._fails >= 3 else
                            "Capture restarting…")
        self.emit("state", self.state)
        if self._fails >= 3 and self.state.game:
            self.emit("capture_failing", self.state.error)
        # back off so a broken setup doesn't hammer the PC: 1 s, 2 s, 5 s, 15 s, then every 60 s
        delay = 1 if fixed else [2, 2, 5, 15, 60][min(self._fails - 1, 4)]
        time.sleep(delay)
        if self._want_running and not self.state.paused_no_game and not self._shutdown.is_set():
            self._pipeline_cache = None
            self._launch()

    def _kill(self):
        with self._lock:
            proc, self._proc = self._proc, None
        self._stop_sides()
        if proc and proc.poll() is None:
            try:
                proc.stdin.write("q")
                proc.stdin.flush()
                proc.wait(4)
            except Exception:
                proc.kill()
        self.state.buffering = False
        self.emit("state", self.state)

    # ------------------------------------------------------------------ housekeeping
    def _scan_segments(self):
        for sr in self._series:
            sr.scan()

    def _janitor_loop(self):
        while not self._shutdown.is_set():
            try:
                with self._lock:
                    self._scan_segments()
                    now = time.time()
                    keep_after = now - self.s.buffer_seconds - SEG * 2
                    if self.state.recording:
                        keep_after = min(keep_after, self.state.record_started - SEG)
                    for sr in self._series:
                        sr.drop_before(keep_after)
                    live = {sp.folder for sp in self._sides} | {self._session_dir}
                    self._series = [sr for sr in self._series if sr.segs or sr.folder in live]
                    self._remove_empty_sessions()
            except Exception as e:
                print("janitor:", e)
            time.sleep(0.25)

    def _remove_empty_sessions(self):
        for d in Path(self.s.buffer_dir).glob("session_*"):
            if d != self._session_dir and d.is_dir() and not any(d.glob("*.ts")):
                for f in d.glob("*.csv"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                try:
                    d.rmdir()
                except OSError:
                    pass

    def _recover_crashed_sessions(self):
        """If the PC crashed mid-recording, stitch the leftover segments into a clip."""
        buf = Path(self.s.buffer_dir)
        flag = buf / "RECORDING"
        if flag.exists():
            try:
                started = float(flag.read_text())
            except ValueError:
                started = 0
            segs = []
            for d in sorted(buf.glob("session_*")):
                files = sorted(d.glob("seg_*.ts"))   # game video (sound can't be re-aligned after a crash)
                if files and files[-1].stat().st_mtime >= started:
                    segs += files
            if segs:
                tmp = buf / f"recover_{int(time.time())}"
                tmp.mkdir(exist_ok=True)
                copies = []
                for i, p in enumerate(segs):
                    dst = tmp / f"v_{i:06d}.ts"
                    shutil.move(str(p), dst)
                    copies.append((dst, None))
                threading.Thread(target=self._finish_save,
                                 args=({"video": copies, "sides": {}}, tmp, "Recovered", started, [], False,
                                       "Recovered session"), daemon=True).start()
            flag.unlink(missing_ok=True)
        for d in buf.glob("session_*"):
            shutil.rmtree(d, ignore_errors=True)
        for d in buf.glob("save_*"):
            shutil.rmtree(d, ignore_errors=True)

    def _monitor_loop(self):
        """Tracks the focused game, pauses the buffer when you're not gaming, auto-clips hype."""
        while not self._shutdown.is_set():
            try:
                game, gaming = (("Test Game", True) if TEST_MODE else current_game(self.s.game_aliases))
                if not TEST_MODE and IS_WINDOWS:
                    from .gameinfo import LAST_GAME_WINDOW, window_alive
                    hwnd = LAST_GAME_WINDOW.get("hwnd")
                    if gaming and hwnd != self._followed_hwnd:
                        # a different game window (new game, or the game restarted): point capture at it
                        old, self._followed_hwnd = self._followed_hwnd, hwnd
                        if old and self.state.buffering and self._method_for_game() == "window":
                            self.restart()
                    if (not gaming and self.s.follow_game and self._followed_hwnd
                            and window_alive(self._followed_hwnd)):
                        # alt-tabbed out: the game is still running, keep recording it
                        game, gaming = LAST_GAME_WINDOW.get("name") or self._last_game, True
                if game:
                    self._last_game = game
                if game != self.state.game:
                    self.state.game = game
                    if game:
                        self._apply_game_overrides(game)
                    self.emit("state", self.state)
                if self.s.only_while_gaming and not TEST_MODE and IS_WINDOWS:
                    now = time.time()
                    if gaming:
                        self._not_gaming_since = 0
                        if self.state.paused_no_game and self._want_running:
                            self.state.paused_no_game = False
                            self._launch()
                    else:
                        if not self._not_gaming_since:
                            self._not_gaming_since = now
                        idle = now - self._not_gaming_since
                        if (idle > 20 and (self.state.buffering or self.state.error) and not self.state.recording
                                and not self.state.paused_no_game):
                            self.state.paused_no_game = True
                            self.state.error = ""
                            self._fails = 0
                            with self._lock:
                                proc, self._proc = self._proc, None
                            self._stop_sides()
                            if proc:
                                try:
                                    proc.stdin.write("q"); proc.stdin.flush(); proc.wait(4)
                                except Exception:
                                    proc.kill()
                            self.state.buffering = False
                            self.emit("state", self.state)
                if self.state.buffering and self.state.error and time.time() - self._started_at > 10:
                    self.state.error = ""       # recovered
                    self.emit("state", self.state)
                if self.s.auto_clip and self.state.buffering:
                    self._check_hype()
            except Exception as e:
                print("monitor:", e)
            time.sleep(1)

    def _apply_game_overrides(self, game: str):
        ov = (getattr(self._base_s, "game_overrides", {}) or {}).get(game, {})
        if ov == self._ov_applied:
            return
        before = self._ov_applied
        self._ov_applied = dict(ov)
        self.s = _GameSettings(self._base_s, ov) if ov else self._base_s
        self._log(f"settings for {game}: {ov or 'normal'}")
        if self.state.buffering and (ov.get("fps") != before.get("fps") or ov.get("quality") != before.get("quality")):
            self.restart()

    def _check_hype(self):
        """Shouting/laughing on the mic or a big audio spike = probably a moment worth keeping."""
        now = time.time()
        if now - self._last_auto < 25:
            return
        for snd in self._senders:
            hist = snd.level_history[-300:]  # ~30 s
            if len(hist) < 100:
                continue
            vals = sorted(v for _, v in hist[:-10])
            median = vals[len(vals) // 2] or 1e-4
            recent = max(v for _, v in hist[-10:])
            threshold = 0.12 if snd.kind == "mic" else 0.25
            if recent > threshold and recent > median * 5:
                self._last_auto = now
                self.emit("toast", "Hype moment detected — auto clipped")
                self.add_marker()
                return

    @staticmethod
    def measure_smoothness(seg_path: str, fps: int) -> tuple[float, float]:
        """(real frames per second, how much the picture moves between real frames).
        Few real frames + big jumps between them = starved capture (stutter). Few real frames + tiny
        changes = just a still scene (menus, AFK), which is fine."""
        import numpy as np
        args = [ff.ffmpeg_bin(), "-hide_banner", "-v", "error"]
        if IS_WINDOWS:
            args += ["-hwaccel", "auto"]
        args += ["-i", seg_path, "-map", "0:v:0", "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"]
        r = subprocess.run(args, capture_output=True, timeout=60, **ff.popen_flags("low"))
        a = np.frombuffer(r.stdout, np.uint8)
        n = a.size // (160 * 90)
        if n < fps:       # need at least ~1 s of frames
            return -1.0, 0.0
        a = a[: n * 160 * 90].reshape(n, 90, 160).astype(np.int16)
        d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
        real = d >= 0.15
        dur = n / fps
        motion = float(d[real].mean()) if real.any() else 0.0
        return float(real.sum() + 1) / dur, motion

    def _health_loop(self):
        tried: dict[str, set] = {}
        while not self._shutdown.wait(15):
            try:
                if not (self.state.buffering and self.state.game) or TEST_MODE:
                    continue
                if time.time() - self._started_at < 12:
                    continue
                with self._lock:
                    segs = list(self._segments)[-3:-1]   # finished ones only
                if not segs:
                    continue
                real, motion = self.measure_smoothness(str(segs[-1].path), self.s.fps)
                if real < 0:
                    continue
                self.real_fps = round(real, 1)
                starved = real < self.s.fps * 0.5 and motion > 2.5
                self.starved_checks = self.starved_checks + 1 if starved else 0
                self.emit("state", self.state)
                if self.starved_checks < 2:
                    continue
                self.starved_checks = 0
                game = self.state.game
                self._log(f"choppy capture in {game}: {real:.1f} real fps (target {self.s.fps}), "
                          f"motion {motion:.1f}, method {self._method_for_game()}, gpu priority {self.gpu_priority}")
                if self.s.capture_method == "auto" and not self.s.follow_game:
                    done = tried.setdefault(game, set())
                    done.add(self._method_for_game())
                    other = next((m for m in ("window", "desktop") if m not in done), None)
                    if other:
                        self.s.method_by_game[game] = other
                        try:
                            from .config import save_settings
                            save_settings(self.s)
                        except Exception as e:
                            from .config import log_note
                            log_note(f"couldn't save the capture method for {game}: {e}")
                        nice = {"window": "game window capture", "desktop": "full screen capture"}[other]
                        self.emit("toast", f"Capture was choppy — switching to {nice} for {game}")
                        self.restart()
                        continue
                self.emit("capture_starved", {"game": game, "fps": real})
            except Exception as e:
                print("health:", e)

    def _vision_loop(self):
        """Watches finished buffer segments for taught kill icons; multi-kills become highlights."""
        from . import vision
        scanned: set[str] = set()
        recent: list[dict] = []           # events with wall-clock "t"
        done_clusters: set[float] = set()
        gd_cache: tuple[str, float, object] | None = None
        while not self._shutdown.is_set():
            time.sleep(4)
            try:
                game = self.state.game
                if not (game and self.state.buffering):
                    continue
                if not gd_cache or gd_cache[0] != game or time.time() - gd_cache[1] > 30:
                    gd_cache = (game, time.time(), vision.load(game))
                gd = gd_cache[2]
                if not gd.live or not any(d.enabled for d in gd.detectors):
                    continue
                with self._lock:
                    segs = list(self._segments)[:-1]   # the newest one is still being written
                for sg in segs[-6:]:
                    key = str(sg.path)
                    if key in scanned:
                        continue
                    scanned.add(key)
                    for e in vision.detect(key, game, gd=gd):
                        e["t"] += sg.wall_start
                        recent.append(e)
                if len(scanned) > 2000:
                    scanned = set(list(scanned)[-500:])
                now = time.time()
                recent = [e for e in recent if now - e["t"] < 90]
                for c in vision.clusters(recent):
                    ended = now - c["t"] > 9.5          # streak is over (allows for scan lag)
                    if c["count"] < gd.min_kills_for_highlight or c["start"] in done_clusters or not ended:
                        continue
                    done_clusters.add(c["start"])
                    name = vision.label_for(c["count"])
                    self.emit("toast", f"💀 {name}! Saving highlight")
                    secs = int(now - c["start"]) + 5
                    marks = [e["t"] for e in recent if c["start"] <= e["t"] <= c["t"]]
                    self.save_clip(secs, highlight=True, marker_times=marks, title=name)
            except Exception as e:
                print("vision:", e)

    # ------------------------------------------------------------------ saving
    def _finish_save(self, parts: dict, tmp: Path, game: str, clip_start: float,
                     markers: list[float], highlight: bool, kind: str = "Clip", title: str = "",
                     squad: dict | None = None):
        try:
            if not parts or not parts.get("video"):
                return
            try:
                self._complete_sides(parts, tmp)
            except Exception as e:
                self._log(f"waiting for the sound files: {e}")
            folder = self.s.library / safe_folder_name(game)
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(clip_start).strftime("%Y-%m-%d %H-%M-%S")
            label = "Highlight" if highlight else kind
            out = folder / f"{safe_folder_name(game)} {stamp} {label}.mp4"
            i = 2
            while out.exists():
                out = folder / f"{safe_folder_name(game)} {stamp} {label} ({i}).mp4"
                i += 1
            cmd = build_mux_cmd(parts, tmp, clip_start, out, self.s.audio_offset_ms / 1000.0,
                                ff.pick_encoder("h264", self.s.encoder))
            r = ff.run(cmd, priority="low", timeout=3600)
            if (r.returncode != 0 or not out.exists()) and parts.get("sides"):
                self._log(f"save with webcam/sound failed, saving video only:\n{r.stderr[-1500:]}")
                cmd = build_mux_cmd({"video": parts["video"], "sides": {}}, tmp, clip_start, out, 0.0, None)
                r = ff.run(cmd, priority="low", timeout=3600)
            if r.returncode != 0 or not out.exists():
                self._log(f"save failed:\n{r.stderr[-1500:]}")
                self.emit("error", f"Saving failed: {r.stderr[-400:]}")
                return
            sq = {k: v for k, v in (squad or {}).items() if k != "share"}
            clip = self.library.add_clip(out, game=game, created=clip_start, markers=markers,
                                         highlight=highlight, kind=label, title=title, squad=sq)
            self.emit("saved", clip)
            if squad and squad.get("share") and self.squad is not None:
                try:
                    self.squad.share_clip(str(out))
                except Exception as e:
                    print("squad share:", e)
            try:
                from . import vision
                if vision.has_detectors(game):
                    clip.events = vision.detect(str(out), game)
                    clip.scanned = True
                    clip.save()
                    self.emit("updated", clip)
            except Exception as e:
                print("kill scan:", e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _concat_list(tmp: Path, name: str, files: list) -> Path:
    """Concat list with each file's exact length (FFmpeg's own estimate for short .ts files is off by
    ~10%, which used to shorten the sound and shift it out of sync)."""
    lines = []
    for item in files:
        path, dur = item if isinstance(item, tuple) else (item, None)
        lines.append(f"file '{Path(path).as_posix()}'")
        if dur:
            lines.append(f"duration {dur:.6f}")
    lst = tmp / f"{name}.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def join_video(parts: dict, tmp: Path) -> tuple[Path, float]:
    """Losslessly joins the game-video files into one (fast) and returns it with its exact length."""
    lst = _concat_list(tmp, "video", parts["video"])
    joined = tmp / "video_joined.mkv"
    ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-map", "0:v:0", "-c", "copy", str(joined)], priority="low", timeout=3600)
    return joined, ff.duration(joined)


def build_mux_cmd(parts: dict, tmp: Path, clip_start: float, out: Path, audio_offset: float,
                  encoder: tuple[str, str] | None) -> list[str]:
    """Game video is copied as-is; webcam and sound are shifted onto the video's clock and trimmed to it."""
    joined, vdur = join_video(parts, tmp)
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-i", str(joined)]
    filters, maps, codecs = [], ["-map", "0:v:0"], ["-c:v:0", "copy"]
    n_in, n_audio, has_cam = 1, 0, False
    sides = parts.get("sides", {})
    order = [k for k in ("cam", "game", "mic") if k in sides]
    for k in order:
        files, start = sides[k]
        lst = _concat_list(tmp, k, files)
        cmd += ["-f", "concat", "-safe", "0", "-i", str(lst)]
        # sound: the AAC encoder adds 1024 samples of start-up delay when recording and again when saving
        shift = start - clip_start + ((audio_offset - AAC_DELAY) if k != "cam" else 0.0)
        end = f"{vdur:.3f}" if vdur > 0 else None
        if k == "cam":
            if shift >= 0:
                f = f"[{n_in}:v:0]tpad=start_duration={shift:.3f}:start_mode=clone"
            else:
                f = f"[{n_in}:v:0]trim=start={-shift:.3f},setpts=PTS-STARTPTS"
            if end:
                f += f",tpad=stop_mode=clone:stop_duration=5,trim=end={end}"
            filters.append(f + ",format=yuv420p[cam]")
            maps += ["-map", "[cam]"]
            has_cam = True
        else:
            if shift >= 0:
                f = f"[{n_in}:a:0]aresample=48000,adelay={int(shift*1000)}:all=1"
            else:
                f = f"[{n_in}:a:0]aresample=48000,atrim=start={-shift:.3f},asetpts=PTS-STARTPTS"
            if end:
                f += f",apad,atrim=end={end}"
            filters.append(f + f"[a{n_audio}]")
            maps += ["-map", f"[a{n_audio}]"]
            n_audio += 1
        n_in += 1
    if has_cam:
        vendor, enc = encoder or ("x264", "libx264")
        if vendor == "nvenc":
            codecs += ["-c:v:1", enc, "-preset", "p4", "-cq", "23"]
        elif vendor in ("amf", "qsv"):
            codecs += ["-c:v:1", enc, "-b:v:1", "6M"]
        else:
            codecs += ["-c:v:1", "libx264", "-preset", "veryfast", "-crf", "23"]
        codecs += ["-metadata:s:v:1", "title=Webcam"]
    if n_audio:
        codecs += ["-c:a", "aac", "-b:a", "192k"]
        titles = [{"game": "Game", "mic": "Mic"}[k] for k in order if k != "cam"]
        for j, t in enumerate(titles):
            codecs += [f"-metadata:s:a:{j}", f"title={t}"]
    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    return cmd + maps + codecs + ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero", str(out)]


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def restart_as_admin() -> bool:
    """Relaunch ClipForge elevated (Windows asks for permission). The new copy waits for this one to exit."""
    if not IS_WINDOWS:
        return False
    import ctypes
    import os
    import sys

    from .paths import app_root, is_frozen

    exe = sys.executable
    root = str(app_root())
    if is_frozen():
        args = f"--after {os.getpid()}"
    else:
        if exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        args = f"-m clipforge --after {os.getpid()}"
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, root, 0)
    return r > 32   # > 32 = started; the user may also have said No
