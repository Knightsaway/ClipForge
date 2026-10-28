"""Friends' POV: when anyone in your squad presses the marker key, everyone saves their angle.

No servers, no port forwarding: the squad shares one folder through Google Drive, OneDrive or
Dropbox (whatever you already use). Inside it each player has their own subfolder:

    <squad folder>/<name>/markers.jsonl     one line per marker press  {"t": wall time, "game": ...}
    <squad folder>/<name>/clips/            that player's POV clips, shared with everyone

Your ClipForge watches the other players' markers.jsonl. When a friend marks a moment that is
still in your replay buffer, it saves your view of it into your clips folder, which syncs to them.
Clips of the same moment are matched by who pressed and when, then lined up precisely by
cross-correlating the audio (your voice chat is in everyone's recording).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from . import ff


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip() or "player"


class Squad:
    def __init__(self, folder: str, me: str):
        self.folder = Path(folder) if folder else None
        self.me = _safe(me)

    @property
    def enabled(self) -> bool:
        return bool(self.folder and self.folder.exists() and self.me)

    def my_dir(self) -> Path:
        d = self.folder / self.me
        (d / "clips").mkdir(parents=True, exist_ok=True)
        return d

    def friend_dirs(self) -> list[Path]:
        if not self.enabled:
            return []
        return [d for d in self.folder.iterdir() if d.is_dir() and d.name != self.me]

    def clip_roots(self) -> list[str]:
        """Friends' clip folders, shown in your library."""
        return [str(d / "clips") for d in self.friend_dirs() if (d / "clips").exists()]

    def publish_marker(self, t: float, game: str):
        if not self.enabled:
            return
        with open(self.my_dir() / "markers.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(t, 2), "game": game, "by": self.me}) + "\n")

    def share_clip(self, clip_path: str) -> Path | None:
        """Copy one of your clips into the shared folder."""
        if not self.enabled:
            return None
        import shutil
        dst = self.my_dir() / "clips" / Path(clip_path).name
        shutil.copyfile(clip_path, dst)
        side = Path(clip_path).with_suffix(".json")
        if side.exists():
            shutil.copyfile(side, dst.with_suffix(".json"))
        return dst


class SquadWatcher(threading.Thread):
    """Saves your POV when a friend marks a moment that's still in your buffer."""

    def __init__(self, squad: Squad, engine, settings, emit):
        super().__init__(name="squad", daemon=True)
        self.squad, self.engine, self.s, self.emit = squad, engine, settings, emit
        self._offsets: dict[str, int] = {}
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def _new_markers(self) -> list[dict]:
        out = []
        for d in self.squad.friend_dirs():
            f = d / "markers.jsonl"
            if not f.exists():
                continue
            key = str(f)
            size = f.stat().st_size
            if key not in self._offsets:      # first look: skip history, only react to new presses
                self._offsets[key] = size
                continue
            if size <= self._offsets[key]:
                self._offsets[key] = min(self._offsets[key], size)
                continue
            with open(f, "rb") as fh:
                fh.seek(self._offsets[key])
                chunk = fh.read()
            # only consume complete lines (cloud sync can deliver half a file)
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                continue
            self._offsets[key] += last_nl + 1
            for line in chunk[:last_nl].decode("utf-8", "replace").splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

    def run(self):
        pending: list[dict] = []
        while not self._halt.wait(3):
            if not self.squad.enabled:
                continue
            try:
                pending += self._new_markers()
                now = time.time()
                keep = []
                for m in pending:
                    t = float(m["t"])
                    if now < t + self.s.marker_post + 1:      # wait until the "after" part is recorded
                        keep.append(m)
                        continue
                    if now - (t - self.s.marker_pre) > self.s.buffer_seconds - 4:
                        continue                               # synced too late, it's gone from the buffer
                    if not self.engine.state.buffering:
                        continue
                    secs = int(now - (t - self.s.marker_pre)) + 1
                    self.emit("toast", f"👥 {m.get('by', 'A friend')} marked a moment — saving your POV")
                    self.engine.save_clip(secs, highlight=True, marker_times=[t],
                                          title=f"{m.get('by', 'Friend')}'s moment (my POV)",
                                          squad={"by": m.get("by", ""), "t": t, "share": True})
                pending = keep
            except Exception as e:
                print("squad:", e)


# ------------------------------------------------------------------ syncing angles
def same_moment(a, b) -> bool:
    sa, sb = getattr(a, "squad", {}) or {}, getattr(b, "squad", {}) or {}
    if sa and sb:
        return sa.get("by") == sb.get("by") and abs(float(sa.get("t", 0)) - float(sb.get("t", 0))) < 1.5
    # fall back to overlapping recording time
    return not (a.created + a.duration < b.created or b.created + b.duration < a.created)


def _mono(path: str, rate=8000) -> np.ndarray:
    n = ff.audio_stream_count(path)
    if n == 0:
        return np.zeros(0, np.float32)
    fc = (["-filter_complex", "[0:a:0][0:a:1]amix=inputs=2:normalize=0[a]", "-map", "[a]"] if n >= 2
          else ["-map", "0:a:0"])
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-i", path] + fc + ["-ac", "1", "-ar", str(rate), "-f",
                       "f32le", "-"], capture_output=True, timeout=300, **ff.popen_flags("low"))
    return np.frombuffer(r.stdout, np.float32)


def align(a_path: str, a_created: float, b_path: str, b_created: float, search=2.5) -> tuple[float, float]:
    """Offset so that time t in clip A shows the same instant as time t+offset in clip B.

    Starts from the wall clocks, then refines with audio cross-correlation (voice chat is in both).
    Returns (offset, confidence 0..1); confidence < 0.25 means the clock guess was kept."""
    rate = 8000
    guess = a_created - b_created
    a, b = _mono(a_path, rate), _mono(b_path, rate)
    if len(a) < rate or len(b) < rate:
        return guess, 0.0
    # loudness envelopes (robust to different game sounds / volumes), 100 Hz
    hop = rate // 100

    def env(x):
        x = x[: len(x) // hop * hop].reshape(-1, hop)
        e = np.log1p(np.sqrt((x.astype(np.float64) ** 2).mean(1)) * 50)
        e = np.diff(e, prepend=e[0])          # onsets matter, not overall level
        return (e - e.mean()) / (e.std() + 1e-9)
    ea, eb = env(a), env(b)
    n = len(ea) + len(eb)
    corr = np.fft.irfft(np.fft.rfft(eb, n) * np.conj(np.fft.rfft(ea, n)), n)
    lags = np.arange(n)
    lags[lags > n // 2] -= n                   # lag L: eb[i+L] ~ ea[i]  ->  offset = L/100
    center = guess * 100
    win = (lags >= center - search * 100) & (lags <= center + search * 100)
    if not win.any():
        return guess, 0.0
    overlap = np.minimum(len(ea), len(eb))
    sub = corr[win] / overlap
    k = int(np.argmax(sub))
    peak = float(sub[k])
    rest = np.delete(sub, range(max(0, k - 10), min(len(sub), k + 10)))
    conf = float(np.clip((peak - (rest.mean() + 2 * rest.std() if len(rest) else 0)) / (abs(peak) + 1e-9), 0, 1))
    offset = lags[win][k] / 100
    return (offset if conf >= 0.25 else guess), conf
