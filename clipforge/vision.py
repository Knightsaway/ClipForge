"""Visual event detection: "teach" ClipForge what a kill looks like in your game.

You pause a clip on a frame where the kill icon / hitmarker skull / medal is showing, draw a
box around it, and name it ("kill", "headshot", "double kill"…). From then on every clip of
that game is scanned for that icon, and — if you turn it on — the live buffer is watched too,
so multi-kills become highlights automatically.

Works for any game with an on-screen kill feed or medal, not just Call of Duty. It uses
normalized cross-correlation on a small region at 6 fps, so it costs almost nothing.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import ff
from .config import app_data_dir

REF_H = 720      # frames are analysed as if they were 720p
FPS = 6
MARGIN = 0.035   # search a bit around where the icon was taught (fraction of frame)


@dataclass
class Detector:
    name: str                      # e.g. "kill", "headshot", "double kill"
    box: list                      # icon box [x, y, w, h], normalized 0..1 of the frame
    threshold: float = 0.72
    weight: float = 1.0            # how exciting: medals > normal kills
    template: str = ""             # .npy file with the grayscale template (720p scale)
    enabled: bool = True


@dataclass
class GameDetectors:
    game: str
    detectors: list = field(default_factory=list)
    live: bool = True              # also watch the live buffer and auto-highlight
    min_kills_for_highlight: int = 2


def _dir() -> Path:
    d = app_data_dir() / "detectors"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(game: str) -> str:
    return re.sub(r"[^\w\-]+", "_", game.lower()).strip("_") or "game"


def load(game: str) -> GameDetectors:
    f = _dir() / f"{_slug(game)}.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            gd = GameDetectors(game=data.get("game", game), live=data.get("live", True),
                               min_kills_for_highlight=data.get("min_kills_for_highlight", 2))
            gd.detectors = [Detector(**d) for d in data.get("detectors", [])]
            return gd
        except Exception:
            pass
    return GameDetectors(game=game)


def save(gd: GameDetectors):
    f = _dir() / f"{_slug(gd.game)}.json"
    data = asdict(gd)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")


def has_detectors(game: str) -> bool:
    return any(d.enabled for d in load(game).detectors)


# ------------------------------------------------------------------ frame grabbing
def _src_size(path: str) -> tuple[int, int]:
    w, h = ff.video_size(path)
    return (w or 1920, h or 1080)


def _region_px(path: str, box, margin=MARGIN) -> tuple[int, int, int, int, int]:
    """Search region (x, y, w, h) in REF_H space, plus the scaled frame width."""
    sw, sh = _src_size(path)
    W = int(round(sw * REF_H / sh / 2)) * 2
    x, y, w, h = box
    x0 = max(0.0, x - margin * sh / sw)
    y0 = max(0.0, y - margin)
    x1 = min(1.0, x + w + margin * sh / sw)
    y1 = min(1.0, y + h + margin)
    rx, ry = int(x0 * W), int(y0 * REF_H)
    rw, rh = max(8, int((x1 - x0) * W)) // 2 * 2, max(8, int((y1 - y0) * REF_H)) // 2 * 2
    return rx, ry, rw, rh, W


def _frames(path: str, rx, ry, rw, rh, start=0.0, dur=None, fps=FPS) -> np.ndarray:
    args = [ff.ffmpeg_bin(), "-hide_banner", "-v", "error"]
    from .config import IS_WINDOWS
    if IS_WINDOWS:
        args += ["-hwaccel", "auto"]  # GPU decode: the scan barely touches the CPU
    if start:
        args += ["-ss", f"{start:.3f}"]
    if dur:
        args += ["-t", f"{dur:.3f}"]
    args += ["-i", path, "-map", "0:v:0",
             "-vf", f"fps={fps},scale=-2:{REF_H}:flags=bilinear,crop={rw}:{rh}:{rx}:{ry},format=gray",
             "-f", "rawvideo", "-"]
    r = subprocess.run(args, capture_output=True, timeout=600, **ff.popen_flags("low"))
    raw = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(raw) // (rw * rh)
    return raw[: n * rw * rh].reshape(n, rh, rw).astype(np.float32)


def grab_box(path: str, t: float, box) -> np.ndarray:
    """The exact icon pixels at time t (used to teach a detector)."""
    rx, ry, rw, rh, _ = _region_px(path, box, margin=0.0)
    fr = _frames(path, rx, ry, rw, rh, start=t, dur=0.25, fps=FPS)
    if not len(fr):
        raise RuntimeError("couldn't read that frame")
    return fr[0]


def teach(game: str, name: str, clip_path: str, t: float, box, weight=1.0) -> Detector:
    tpl = grab_box(clip_path, t, box)
    if tpl.std() < 4:
        raise RuntimeError("That box is almost a flat colour — draw it tightly around the icon.")
    gd = load(game)
    f = _dir() / f"{_slug(game)}_{_slug(name)}_{len(gd.detectors)}.npy"
    np.save(f, tpl)
    det = Detector(name=name, box=list(box), weight=weight, template=str(f))
    gd.detectors.append(det)
    save(gd)
    return det


# ------------------------------------------------------------------ matching
def ncc_max(frames: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """Best normalized cross-correlation of the template in each frame (FFT, fast)."""
    n, H, W = frames.shape
    th, tw = tpl.shape
    if th > H or tw > W or n == 0:
        return np.zeros(n)
    t = tpl - tpl.mean()
    tnorm = np.sqrt((t ** 2).sum()) + 1e-6
    fh, fw = H + th, W + tw
    T = np.fft.rfft2(t[::-1, ::-1], s=(fh, fw))
    F = np.fft.rfft2(frames, s=(fh, fw))
    corr = np.fft.irfft2(F * T, s=(fh, fw))[:, th - 1:H, tw - 1:W]
    # local sums of f and f^2 over each window via integral images
    ii = np.pad(frames, ((0, 0), (1, 0), (1, 0))).cumsum(1).cumsum(2)
    ii2 = np.pad(frames ** 2, ((0, 0), (1, 0), (1, 0))).cumsum(1).cumsum(2)

    def box_sum(a):
        return a[:, th:, tw:] - a[:, :-th, tw:] - a[:, th:, :-tw] + a[:, :-th, :-tw]
    s1 = box_sum(ii)
    s2 = box_sum(ii2)
    m = th * tw
    var = np.maximum(s2 - s1 ** 2 / m, 1e-6)
    ncc = corr / (np.sqrt(var) * tnorm)
    ncc[var < 16 * m] = 0  # flat areas can't match
    return ncc.reshape(n, -1).max(axis=1)


def detect(path: str, game: str, start=0.0, dur=None, gd: GameDetectors | None = None) -> list[dict]:
    """Events [{"t", "name", "weight", "score"}] in clip time."""
    gd = gd or load(game)
    events = []
    for d in gd.detectors:
        if not d.enabled or not Path(d.template).exists():
            continue
        tpl = np.load(d.template).astype(np.float32)
        rx, ry, rw, rh, _ = _region_px(path, d.box)
        fr = _frames(path, rx, ry, rw, rh, start=start, dur=dur)
        if not len(fr):
            continue
        scores = ncc_max(fr, tpl)
        hit = scores >= d.threshold
        last_on = -99.0
        for i, h in enumerate(hit):
            t = start + i / FPS
            if h and (i == 0 or not hit[i - 1]) and t - last_on > 0.4:
                events.append({"t": round(t, 2), "name": d.name, "weight": d.weight,
                               "score": round(float(scores[i]), 3)})
            if h:
                last_on = t
    events.sort(key=lambda e: e["t"])
    return events


def clusters(events: list[dict], gap=4.5) -> list[dict]:
    """Groups kills close together: [{"t" (last kill), "start", "count", "score"}] — multi-kills score higher."""
    out, cur = [], []
    for e in events:
        if cur and e["t"] - cur[-1]["t"] > gap:
            out.append(cur)
            cur = []
        cur.append(e)
    if cur:
        out.append(cur)
    res = []
    for c in out:
        weight = sum(e["weight"] for e in c)
        res.append({"t": c[-1]["t"], "start": c[0]["t"], "count": len(c),
                    "score": weight * (1 + 0.6 * (len(c) - 1))})
    return res


def label_for(count: int) -> str:
    return {1: "Kill", 2: "Double kill", 3: "Triple kill", 4: "Quad kill"}.get(count, f"{count}-kill streak")
