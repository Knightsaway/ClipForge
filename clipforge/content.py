"""Bigger content flows built on top of Auto Short and the best-of countdown.

* make_today()  — one button after a session: 3 Shorts (+ TikTok versions), 1 long video, thumbnail,
                  all waiting for your approval (on the PC or your phone).
* plan_series() — turns a session into "Part 1 / 2 / 3…" Shorts posted on consecutive days, each
                  ending with "Part 2 tomorrow" so viewers come back.
* plan_recap()  — the 60-second "tonight in <game>" recap, made automatically after a session.
* monthly       — an 8+ minute best-of-the-month (long enough for mid-roll ads) with YouTube chapters.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from .autoshort import plan_short
from .editor import Piece, RenderOptions
from .library import Clip


# ------------------------------------------------------------------ series
def plan_series(clips: list[Clip], parts: int = 3, part_len: float = 50.0) -> list[dict]:
    """[{"pieces", "hook", "timed_text", "part"}] in story order."""
    from .script_mode import collect_moments
    clips = sorted(clips, key=lambda c: c.created)
    moments = collect_moments(clips)
    per = 11.0                       # each moment with its build-up
    need = max(parts, int(parts * part_len / per))
    top = sorted(moments[:need], key=lambda m: (m.clip.created, m.t))   # chronological = a story
    # windows, merged when they overlap in the same clip
    wins: list[list] = []
    for m in top:
        lo, hi = m.clip.trim_in or 0.0, m.clip.trim_out or m.clip.duration
        s, e = max(lo, m.t - 8.0), min(hi, m.t + 3.0)
        if wins and wins[-1][0] is m.clip and s <= wins[-1][2] + 1.0:
            wins[-1][2] = max(wins[-1][2], e)
            wins[-1][3].append(m.t)
        else:
            wins.append([m.clip, s, e, [m.t]])
    if len(wins) < parts:
        parts = max(1, len(wins))
    # split the story into parts of roughly equal length
    total = sum(w[2] - w[1] for w in wins)
    target = total / parts
    out, cur, acc = [], [], 0.0
    for w in wins:
        cur.append(w)
        acc += w[2] - w[1]
        if acc >= target * 0.95 and len(out) < parts - 1:
            out.append(cur)
            cur, acc = [], 0.0
    if cur:
        out.append(cur)
    series = []
    n = len(out)
    for i, group in enumerate(out):
        pieces = [Piece(c.path, s, e, effects=[{"type": "zoom", "t": t, "amount": 0.22} for t in ts])
                  for c, s, e, ts in group]
        dur = sum(p.dur for p in pieces)
        texts = []
        if i < n - 1:   # cliffhanger end card
            texts.append((max(0.0, dur - 2.6), dur, f"PART {i + 2} TOMORROW", "Line"))
        series.append({"pieces": pieces, "part": i + 1, "of": n, "timed_text": texts,
                       "hook": f"Part {i + 1}" + ("" if i else f" of {n}")})
    return series


def series_dates(first_slot: datetime, n: int) -> list[datetime]:
    return [first_slot + timedelta(days=i) for i in range(n)]


# ------------------------------------------------------------------ recap
RECAP_HOOKS = ["Tonight in {game} 💀", "Last night's {game} session", "{game} session recap",
               "We played {game}. This happened."]


def plan_recap(clips: list[Clip], target: float = 55.0) -> tuple[list[Piece], str]:
    pieces = plan_short(clips, target=target, auto_fx=True)
    pieces.sort(key=lambda p: next((c.created for c in clips if c.path == p.path), 0) + p.start)  # tell it in order
    game = clips[0].game if clips else "the game"
    hook = RECAP_HOOKS[int(time.time()) % len(RECAP_HOOKS)].format(game=game).replace(" 💀", "")
    return pieces, hook


# ------------------------------------------------------------------ make today's content
def make_today(clips: list[Clip], shorts: int = 3) -> dict:
    """Plans (doesn't render) a day's content from the given clips."""
    plans = []
    used: list[tuple[str, float, float]] = []
    for _ in range(shorts):
        p = plan_short(clips, target=30, auto_fx=True, exclude=used)
        if not p:
            break
        plans.append(p)
        used += [(x.path, x.start, x.end) for x in p]
    from .bestof import plan_bestof
    long_pieces, long_texts, long_hook = plan_bestof(clips, count=10, days=1, long=False)
    return {"shorts": plans, "long": (long_pieces, long_texts, long_hook)}


def session_clips(clips: list[Clip], since: float) -> list[Clip]:
    return [c for c in clips if c.created >= since - 60]
