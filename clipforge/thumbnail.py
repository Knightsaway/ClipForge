"""Auto thumbnails for long YouTube videos: 3 options, 1280x720 JPG under 2 MB (YouTube's limit).

Frames are chosen where the action is (kills, markers, loud moments) and that are sharp and
colourful (no motion-blur mush). Each option gets a punchy 2-4 word text in a different style."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

from . import ff
from .highlights import find_moments

FILLER = set("the a an and of to in on my i was is this that with for it just so how what".split())


def thumb_text(title: str, max_words=4) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9'!?]+", title)]
    keep = [w for w in words if w.lower() not in FILLER] or words
    return " ".join(keep[:max_words]).upper()


def _gray(path: str, t: float) -> np.ndarray | None:
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=320:180,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=30, **ff.popen_flags("low"))
    a = np.frombuffer(r.stdout, np.uint8)
    return a.reshape(180, 320).astype(np.float32) if a.size == 320 * 180 else None


def _rgb_colorfulness(path: str, t: float) -> float:
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=160:90,format=rgb24", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=30, **ff.popen_flags("low"))
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size != 160 * 90 * 3:
        return 0.0
    a = a.reshape(-1, 3).astype(np.float32)
    rg = a[:, 0] - a[:, 1]
    yb = 0.5 * (a[:, 0] + a[:, 1]) - a[:, 2]
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def sharpness(g: np.ndarray) -> float:
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def pick_frames(video: str, n=3, hint_times: list[float] | None = None) -> list[float]:
    dur = ff.duration(video)
    cands = [t for t in (hint_times or []) if 0.5 < t < dur - 0.5]
    cands += [t for t, _ in find_moments(video, max_moments=8)]
    cands += [dur * f for f in (0.15, 0.3, 0.5, 0.7, 0.85)]
    scored = []
    for i, t in enumerate(cands):
        for dt in (-0.3, 0.0, 0.25):   # the exact peak is often blurry; try neighbours
            tt = min(max(0.2, t + dt), max(0.2, dur - 0.3))
            g = _gray(video, tt)
            if g is None or g.std() < 12:   # black / flat frames are useless
                continue
            bonus = 1.6 if i < len(hint_times or []) else (1.25 if i < len(cands) - 5 else 1.0)
            scored.append((sharpness(g) ** 0.5 * (1 + _rgb_colorfulness(video, tt) / 60) * bonus, tt))
    scored.sort(reverse=True)
    picked: list[float] = []
    for gap in (3.0, 1.0, 0.3):   # prefer different moments; relax for short videos
        for _, t in scored:
            if len(picked) == n:
                break
            if all(abs(t - p) > gap for p in picked):
                picked.append(t)
    while len(picked) < n:        # same frame, different style is still a real option
        picked.append(picked[0] if picked else dur / 2)
    return picked


STYLES = [
    # (box filter, line1 color, line2 color, text x, y, size)
    ("drawbox=x=0:y=ih*0.22:w=iw:h=ih*0.46:color=black@0.42:t=fill", "white", "0xFFD400", "50", "h*0.25", 128),
    ("drawbox=x=0:y=0:w=iw:h=ih*0.30:color=0xE0182D@0.92:t=fill", "white", "white", "(w-text_w)/2", "28", 112),
    ("drawbox=x=0:y=ih*0.62:w=iw:h=ih*0.38:color=black@0.55:t=fill", "white", "0x3DDC97", "(w-text_w)/2", "h*0.65", 120),
]


def make_thumbnails(video: str, text: str, out_dir: Path, hint_times: list[float] | None = None,
                    exact: float | None = None) -> list[Path]:
    """exact = use exactly this moment (picked on the Studio timeline) for all 3 styles."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = ff.windows_font()
    if font:
        shutil.copyfile(font, out_dir / "font.ttf")
    words = thumb_text(text).split()
    split = max(1, len(words) - (2 if len(words) >= 4 else 1))
    l1, l2 = " ".join(words[:split]), " ".join(words[split:])
    (out_dir / "l1.txt").write_text(l1 or " ", encoding="utf-8")
    (out_dir / "l2.txt").write_text(l2 or " ", encoding="utf-8")
    w, h = ff.video_size(video)
    vertical = h > w
    base = ("crop=iw:iw*9/16," if vertical else "") + \
        "scale=1472:828:flags=lanczos,crop=1280:720,eq=contrast=1.12:saturation=1.4:brightness=0.02,unsharp=5:5:0.8"
    outs = []
    for i, t in enumerate([exact] * 3 if exact is not None else pick_frames(video, 3, hint_times)):
        box, c1, c2, x, y, size = STYLES[i % len(STYLES)]
        vf = f"{base},{box}"
        if font:
            # shrink each line until it fits (Impact is narrow, fallback fonts are wider)
            k = 0.52 if "impact" in font.lower() else 0.8
            s1 = int(min(size, 1120 / max(1, len(l1) * k)))
            s2 = int(min(size * 1.12, 1120 / max(1, len(l2) * k)))
            vf += (f",drawtext=fontfile=font.ttf:textfile=l1.txt:fontsize={s1}:fontcolor={c1}:borderw=8:"
                   f"bordercolor=black:x={x}:y={y}"
                   f",drawtext=fontfile=font.ttf:textfile=l2.txt:fontsize={s2}:fontcolor={c2}:borderw=9:"
                   f"bordercolor=black:x={x}:y={y}+{int(s1*1.08)}")
        out = out_dir / f"thumb_{i+1}.jpg"
        for q in (2, 4, 7):  # keep under YouTube's 2 MB limit
            r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                                "-vf", vf, "-q:v", str(q), out.name], cwd=out_dir, capture_output=True, text=True,
                               timeout=60, **ff.popen_flags("low"))
            if r.returncode == 0 and out.exists() and out.stat().st_size < 2_000_000:
                break
        if out.exists():
            outs.append(out)
    return outs
