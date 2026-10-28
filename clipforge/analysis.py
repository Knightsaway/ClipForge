"""Looking inside clips: dead air (boring parts), where the action is on screen (smart 9:16 framing),
and the beat of a music track (beat sync). Everything is cached per clip range, and nothing here needs a GPU."""
from __future__ import annotations

import subprocess
from functools import lru_cache

import numpy as np

from . import ff


# ------------------------------------------------------------------ dead air
def _audio_rms(path: str, start: float, end: float, step: float = 0.25) -> np.ndarray:
    """Loudness every `step` seconds, game + mic mixed."""
    n = ff.audio_stream_count(path)
    if n <= 0:
        return np.zeros(0)
    if n >= 2:
        filt = ["-filter_complex", "[0:a:0][0:a:1]amix=inputs=2:normalize=0,aresample=8000,aformat=channel_layouts=mono[a]",
                "-map", "[a]"]
    else:
        filt = ["-map", "0:a:0", "-ac", "1", "-ar", "8000"]
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{max(0.1, end - start):.3f}",
                        "-i", path] + filt + ["-f", "f32le", "-"], capture_output=True, timeout=120,
                       **ff.popen_flags("low"))
    x = np.frombuffer(r.stdout, dtype=np.float32)
    hop = int(8000 * step)
    if len(x) < hop:
        return np.zeros(0)
    x = x[: len(x) // hop * hop].reshape(-1, hop)
    return np.sqrt(np.mean(x.astype(np.float64) ** 2, axis=1) + 1e-12)


@lru_cache(maxsize=64)
def _clip_levels(path: str) -> tuple[float, float, float]:
    """(quiet, busy, spread) loudness of the WHOLE clip in dB — what 'nothing happening' means for it."""
    rms = _audio_rms(path, 0.0, max(1.0, ff.duration(path) or 600.0))
    if len(rms) < 8:
        return (-90.0, -90.0, 0.0)
    db = 20 * np.log10(rms)
    return float(np.percentile(db, 25)), float(np.percentile(db, 75)), float(np.percentile(db, 90) - np.percentile(db, 10))


@lru_cache(maxsize=256)
def dead_spans(path: str, start: float, end: float, events: tuple = (), min_len: float = 2.5) -> list[tuple[float, float]]:
    """Stretches (clip time) at least `min_len` long where nothing happens: clearly quieter than the busy parts
    of the clip, nobody talking, no kills. A little is kept at each edge so cuts don't feel abrupt.
    A clip that's equally loud all the way through has no dead air."""
    step = 0.25
    quiet_db, busy_db, spread = _clip_levels(path)
    if spread < 8.0:
        return []
    rms = _audio_rms(path, start, end, step)
    if len(rms) < int(min_len / step) + 2:
        return []
    db = 20 * np.log10(rms)
    quiet = db < min(quiet_db + 4.0, busy_db - 8.0)
    # smooth: a single loud blip inside a quiet stretch doesn't make it interesting
    k = 3
    quiet = np.convolve(quiet.astype(float), np.ones(k) / k, mode="same") > 0.5
    spans, run = [], None
    for i, q in enumerate(quiet):
        t = start + i * step
        if q and run is None:
            run = t
        if (not q or i == len(quiet) - 1) and run is not None:
            a, b = run, t + (step if q else 0)
            run = None
            a, b = a + 0.4, b - 0.4                  # keep a breath on each side
            if b - a >= min_len and not any(a - 1.0 <= ev <= b + 1.0 for ev in events):
                spans.append((round(a, 2), round(b, 2)))
    return spans


def cut_spans(pc: dict, spans: list[tuple[float, float]]) -> list[dict]:
    """A Studio piece with the dead spans removed → the pieces that are left (effects go with their part)."""
    keep, cur = [], pc["start"]
    for a, b in sorted(spans):
        a, b = max(pc["start"], a), min(pc["end"], b)
        if b <= a:
            continue
        if a - cur >= 0.8:
            keep.append((cur, a))
        cur = max(cur, b)
    if pc["end"] - cur >= 0.8:
        keep.append((cur, pc["end"]))
    if not keep:
        return [pc]
    out = []
    for a, b in keep:
        fx = [dict(e) for e in pc.get("effects", []) if a <= e.get("t", 0) < b]
        out.append(dict(pc, start=round(a, 2), end=round(b, 2), effects=fx))
    return out


# ------------------------------------------------------------------ smart framing
@lru_cache(maxsize=128)
def action_track(path: str, start: float, end: float, fps: float = 5.0) -> tuple[tuple[float, float], ...]:
    """Where the action is, horizontally: ((t from piece start, centre 0..1), ...), smoothed so the camera
    glides instead of jumping. Static parts of the screen (HUD, minimap) don't count — only what moves."""
    W, H = 192, 108
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{max(0.2, end - start):.3f}",
                        "-i", path, "-map", "0:v:0", "-vf", f"fps={fps},scale={W}:{H},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, timeout=180, **ff.popen_flags("low"))
    frames = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(frames) // (W * H)
    if n < 2:
        return ((0.0, 0.5),)
    f = frames[: n * W * H].reshape(n, H, W).astype(np.float32)
    diff = np.abs(np.diff(f, axis=0))                       # n-1 motion maps
    diff[:, : int(H * 0.12), :] = 0                         # top / bottom HUD bars rarely matter
    diff[:, int(H * 0.88):, :] = 0
    cols = diff.sum(axis=1)                                 # motion per column
    xs = np.arange(W) / (W - 1)
    centres = []
    for c in cols:
        c = np.where(c > np.percentile(c, 60), c, 0)
        tot = c.sum()
        centres.append(float((c * xs).sum() / tot) if tot > 1e-3 else 0.5)
    centres = [centres[0]] + centres
    # pull towards the middle (crosshair, character) and glide: max ~25% of the width per second
    out, cur = [], 0.5
    max_step = 0.25 / fps
    for i, c in enumerate(centres):
        target = 0.5 + (c - 0.5) * 0.75
        cur += max(-max_step, min(max_step, (target - cur) * 0.35))
        out.append((round(i / fps, 2), round(cur, 4)))
    return tuple(out)


def crop_x_expr(track: tuple, max_keys: int = 40) -> str:
    """FFmpeg expression for the crop's x (in 'crop' filter terms) that follows the action track."""
    pts = list(track)
    if len(pts) > max_keys:
        step = len(pts) / max_keys
        pts = [pts[int(i * step)] for i in range(max_keys)] + [pts[-1]]
    if len(pts) == 1:
        val = f"{pts[0][1]:.4f}"
    else:
        val = f"{pts[-1][1]:.4f}"
        for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
            span = max(0.01, t1 - t0)
            val = f"if(lt(t,{t1:.2f}),{v0:.4f}+({v1 - v0:.4f})*(t-{t0:.2f})/{span:.2f},{val})"
    return f"max(0,min(iw-ow,({val})*iw-ow/2))"


# ------------------------------------------------------------------ beats
@lru_cache(maxsize=16)
def beats(music: str, max_seconds: float = 600.0) -> tuple[float, ...]:
    """Beat times (seconds) of a music track: onset strength → tempo by autocorrelation → beat grid."""
    sr, hop = 11025, 256
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-t", str(max_seconds), "-i", music, "-ac", "1",
                        "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, timeout=180, **ff.popen_flags("low"))
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if len(x) < sr * 4:
        return ()
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    energy = np.log1p(np.sum(frames.astype(np.float64) ** 2, axis=1) * 100)
    onset = np.maximum(0, np.diff(energy, prepend=energy[0]))
    onset -= onset.mean()
    fps = sr / hop
    # tempo: strongest autocorrelation between 70 and 180 BPM
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    if hi >= len(ac):
        return ()
    k0 = lo + int(np.argmax(ac[lo:hi]))
    # sub-frame period (parabolic peak), then refine on a long lag so small errors don't add up over a song
    a, b, c = ac[k0 - 1], ac[k0], ac[k0 + 1] if k0 + 1 < len(ac) else ac[k0]
    period = k0 + (0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0)
    mult = max(1, int(len(onset) / period / 4))
    lag = int(round(period * mult))
    if 2 < lag < len(ac) - 2:
        w = ac[lag - 2: lag + 3]
        period = (lag - 2 + float(np.argmax(w))) / mult
    # phase: the offset whose grid collects the most onset energy (±1 frame tolerance)
    nb = int((n - 1) / period)
    best, phase = -1e9, 0.0
    for ph in np.arange(0, period, 0.5):
        idx = np.round(ph + np.arange(nb) * period).astype(int)
        idx = idx[idx < n - 1]
        sc = np.maximum(onset[idx], np.maximum(onset[np.maximum(idx - 1, 0)], onset[idx + 1])).sum()
        if sc > best:
            best, phase = sc, ph
    # onsets are detected one hop late on average: shift back half a hop
    return tuple(float(round(max(0.0, (phase + k * period - 0.5)) / fps, 3)) for k in range(int((n - phase) / period) + 1))


def nearest(ts: tuple | list, t: float, within: float) -> float | None:
    if not ts:
        return None
    i = int(np.searchsorted(ts, t))
    cands = [ts[j] for j in (i - 1, i) if 0 <= j < len(ts)]
    b = min(cands, key=lambda v: abs(v - t))
    return b if abs(b - t) <= within else None


# ------------------------------------------------------------------ music in the game sound (Content ID risk)
@lru_cache(maxsize=128)
def music_score(path: str, start: float, end: float) -> float:
    """0..1: how much the game sound in this range sounds like music (sustained notes + a steady beat).
    Game soundtracks and radio songs are what YouTube's Content ID claims; gunfire and voices aren't music."""
    sr = 11025
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{max(1.0, end - start):.3f}",
                        "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, timeout=120, **ff.popen_flags("low"))
    x = np.frombuffer(r.stdout, dtype=np.float32)
    n_fft, hop = 2048, 1024
    if len(x) < sr * 3:
        return 0.0
    n = (len(x) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    frames = np.stack([x[i * hop: i * hop + n_fft] * win for i in range(n)])
    mag = np.abs(np.fft.rfft(frames, axis=1)) + 1e-9
    energy = mag.sum(axis=1)
    loud = energy > np.percentile(energy, 30) * 1.2
    band = mag[:, 8:600]                                       # ~40 Hz – 3 kHz, where melodies live
    flat = np.exp(np.mean(np.log(band), axis=1)) / np.mean(band, axis=1)
    # sustained notes: the same strong frequency bins stay on from frame to frame
    peaks = band > (band.mean(axis=1, keepdims=True) * 6)
    sustain = np.mean(np.sum(peaks[1:] & peaks[:-1], axis=1) / (np.sum(peaks[1:], axis=1) + 1))
    tonal = float(np.mean((flat < 0.25)[loud])) if loud.any() else 0.0
    onset = np.maximum(0, np.diff(np.log1p(energy)))
    onset = onset - onset.mean()
    fps = sr / hop
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    beat = float(np.max(ac[lo:hi]) / (ac[0] + 1e-9)) if hi < len(ac) and ac[0] > 0 else 0.0
    score = 0.45 * tonal + 0.35 * min(1.0, sustain * 1.5) + 0.20 * min(1.0, max(0.0, beat) * 2)
    return round(float(min(1.0, score)), 3)
