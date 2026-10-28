"""Finds the exciting parts of a clip from its audio: gunfire bursts, explosions,
you and your friends yelling or laughing. Markers you pressed while playing win."""
from __future__ import annotations

import subprocess

import numpy as np

from . import ff

RATE = 8000
HOP = 0.25  # seconds per analysis window


def loudness_curve(path: str) -> np.ndarray:
    """Short-term loudness (dB) of all audio tracks mixed, one value per 0.25 s."""
    n = ff.audio_stream_count(path)
    if n == 0:
        return np.zeros(0)
    if n >= 2:
        fc = ["-filter_complex", f"[0:a:0][0:a:1]amix=inputs=2:normalize=0[a]", "-map", "[a]"]
    else:
        fc = ["-map", "0:a:0"]
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-i", path] + fc + \
          ["-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300, **ff.popen_flags("low"))
    pcm = np.frombuffer(r.stdout, dtype=np.float32)
    hop = int(RATE * HOP)
    if len(pcm) < hop:
        return np.zeros(0)
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) + 1e-6
    return 20 * np.log10(rms)


def find_moments(path: str, markers: list[float] | None = None, max_moments: int = 6,
                 min_gap: float = 6.0) -> list[tuple[float, float]]:
    """Returns [(time_seconds, score)], best first."""
    db = loudness_curve(path)
    moments: list[tuple[float, float]] = [(float(m), 100.0) for m in (markers or [])]
    if len(db) >= 8:
        # excitement = how much louder than the clip's normal level, plus sudden jumps
        k = max(1, int(1.0 / HOP))
        smooth = np.convolve(db, np.ones(k) / k, mode="same")
        base = np.median(smooth)
        spread = np.std(smooth) + 1e-3
        jump = np.maximum(0, np.diff(smooth, prepend=smooth[0]))
        score = (smooth - base) / spread + 0.6 * jump / spread
        order = np.argsort(score)[::-1]
        taken: list[float] = [m for m, _ in moments]
        for idx in order:
            if score[idx] < 0.8:
                break
            t = float(idx * HOP)
            if all(abs(t - x) >= min_gap for x in taken):
                moments.append((t, float(score[idx])))
                taken.append(t)
            if len(moments) >= max_moments:
                break
    moments.sort(key=lambda m: m[1], reverse=True)
    return moments


def mic_peaks(path: str, start: float, end: float, max_peaks: int = 4, min_gap: float = 3.0) -> list[float]:
    """Moments you laugh / yell on the mic track (track 2), as seconds from `start`."""
    if ff.audio_stream_count(path) < 2:
        return []
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", path, "-map", "0:a:1", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=120, **ff.popen_flags("low"))
    pcm = np.frombuffer(r.stdout, dtype=np.float32)
    hop = int(RATE * HOP)
    if len(pcm) < hop * 4:
        return []
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) + 1e-6)
    talk = db[db > -50]
    base = np.median(talk) if len(talk) else -40
    out: list[float] = []
    for idx in np.argsort(db)[::-1]:
        if db[idx] < max(base + 12, -22):   # clearly louder than normal talking
            break
        t = float(idx * HOP)
        if all(abs(t - x) >= min_gap for x in out):
            out.append(t)
        if len(out) >= max_peaks:
            break
    return sorted(out)
