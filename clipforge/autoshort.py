"""Auto Short: turns a pile of clips into one punchy vertical video.

Structure that works on Shorts/TikTok:
  1. Hook - the single best moment goes FIRST (people swipe in under 2 seconds)
  2. The rest of the moments in the order they happened, tight cuts, no dead air
  3. Big hook text on screen for the first 3 seconds, captions if enabled
"""
from __future__ import annotations

import os as _os

import random

from .editor import Piece
from .highlights import find_moments
from .library import Clip

HOOKS = [
    "Wait for it…", "This was NOT planned", "I can't believe this worked",
    "{game} is actually broken", "Rate this play 1-10", "How did this happen?!",
    "Nobody expected this", "The ending though…", "POV: you're having a good day in {game}",
    "Clutch or luck?", "My friends will never let me forget this",
]


def suggest_hook(game: str, weights: dict | None = None) -> str:
    """Picks a hook template; with stats it leans towards the hooks that win on your channel."""
    from .analytics import pick_weighted
    return pick_weighted(HOOKS, weights or {}).format(game=game or "this game")


def hook_template(text: str, game: str) -> str:
    """Which template a hook came from (for learning), or '' for a custom/AI hook."""
    for h in HOOKS:
        if h.format(game=game or "this game").lower() == (text or "").strip().lower():
            return h
    return ""


def _ensure_scanned(c: Clip):
    """Run the taught kill detectors on clips saved before they existed."""
    if c.scanned:
        return
    try:
        from . import vision
        if vision.has_detectors(c.game):
            c.events = vision.detect(c.path, c.game)
            c.scanned = True
            c.save()
    except Exception as e:
        print("kill scan:", e)


def plan_short(clips: list[Clip], target: float = 30.0, before: float = 4.5, after: float = 2.0,
               progress=None, auto_fx: bool = True, exclude: list | None = None) -> list[Piece]:
    """exclude: [(path, start, end)] moments already used in another video."""
    exclude = exclude or []
    from .vision import clusters
    # candidate = (score, clip, start, end, key moments inside it, is_kill_streak)
    candidates: list[tuple[float, Clip, float, float, list[float], bool]] = []
    for i, c in enumerate(clips):
        if progress:
            progress(i / max(1, len(clips)), f"Scanning {c.display_title}…")
        lo = c.trim_in or 0.0
        hi = c.trim_out or c.duration or 0.0
        if hi - lo < 1:
            continue
        _ensure_scanned(c)
        taken: list[tuple[float, float]] = []
        # 1) kill streaks you taught ClipForge to see — the best signal there is
        for cl in clusters([e for e in c.events if lo <= e["t"] <= hi]):
            s0, e0 = max(lo, cl["start"] - 2.5), min(hi, cl["t"] + 1.6)
            kills = [e["t"] for e in c.events if cl["start"] <= e["t"] <= cl["t"]]
            candidates.append((120 + 20 * cl["score"], c, s0, e0, kills, True))
            taken.append((s0, e0))
        # 2) a marker-highlight is already a tight moment: take the payoff at the end
        if c.highlight and (hi - lo) <= 20 and not c.markers and not taken:
            candidates.append((50.0, c, max(lo, hi - (before + after + 1)), hi, [hi - after - 0.5], False))
            continue
        # 3) your markers + loud moments
        for t, score in find_moments(c.path, c.markers, max_moments=4):
            if not (lo <= t <= hi) or any(a - 1 <= t <= b + 1 for a, b in taken):
                continue
            pre, post = (before + 2, after + 1) if score >= 100 else (before, after)
            key = t - 0.4 if score >= 100 else t   # you press the marker just after the moment
            candidates.append((score, c, max(lo, t - pre), min(hi, t + post), [key], False))
    if not candidates:  # nothing stood out: fall back to the end of each clip
        for c in clips:
            hi = c.trim_out or c.duration
            if hi:
                candidates.append((0.0, c, max(0.0, hi - 8), hi, [], False))

    candidates.sort(key=lambda x: x[0], reverse=True)
    picked, total = [], 0.0
    for cand in candidates:
        if total >= target:
            break
        _, c, s, e, keys, streak = cand
        if any(p[1] is c and not (e <= p[2] or s >= p[3]) for p in picked):
            continue  # overlaps a moment we already took
        if any(_os.path.normcase(x[0]) == _os.path.normcase(c.path) and not (e <= x[1] + 0.5 or s >= x[2] - 0.5)
               for x in exclude):
            continue  # used in another video already
        if total + (e - s) > target + 4:
            e = s + max(2.5, target + 2 - total)
            keys = [k for k in keys if k <= e]
        picked.append((cand[0], c, s, e, keys, streak))
        total += e - s
    if not picked:
        return []
    hook, rest = picked[0], picked[1:]
    rest.sort(key=lambda x: (x[1].created, x[2]))
    pieces = []
    for n, (_, c, s, e, keys, streak) in enumerate([hook] + rest):
        fx = []
        if auto_fx and keys:
            for k in keys:   # punch-in on every kill / moment
                fx.append({"type": "zoom", "t": k, "amount": 0.22})
            last = keys[-1]
            if streak and len(keys) >= 2:
                fx.append({"type": "sfx", "t": last, "name": "boom"})
                fx[-2]["amount"] = 0.32
            if n == 0:        # the hook gets the cinematic slow-mo on its payoff
                fx.append({"type": "slowmo", "t": last, "dur": 1.0, "speed": 0.4})
                if not (streak and len(keys) >= 2):
                    fx.append({"type": "sfx", "t": last, "name": "boom"})
        pieces.append(Piece(c.path, s, e, effects=fx))
    return pieces
