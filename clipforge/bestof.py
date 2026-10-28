"""Weekly best-of: every week ClipForge turns your best moments into a "Top 10 plays" countdown video.

Countdowns keep people watching to the end (they want to see #1), and it's free extra content
from clips you already have. It runs on the day/time you choose; if the PC was off it catches
up next time ClipForge starts.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from .config import app_data_dir
from .editor import Piece

STATE = app_data_dir() / "bestof.json"


def _week_key(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def due(day: int, hour: int, now: datetime | None = None) -> bool:
    """day: 0=Monday … 6=Sunday. True if this week's video hasn't been made and its time has come."""
    now = now or datetime.now()
    try:
        last = json.loads(STATE.read_text(encoding="utf-8")).get("last_week", "")
    except Exception:
        last = ""
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    slot = week_start + timedelta(days=day, hours=hour)
    if now >= slot:
        return last != _week_key(now)
    # before this week's slot: still owe last week's video?
    prev = now - timedelta(days=7)
    return last not in (_week_key(prev), _week_key(now)) and last != ""


def mark_done(now: datetime | None = None):
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.update({"last_week": _week_key(now or datetime.now()), "at": time.time()})
    STATE.write_text(json.dumps(data), encoding="utf-8")


def plan_bestof(clips, count=10, game: str = "", days: int = 7, long: bool = False,
                min_total: float = 0.0) -> tuple[list[Piece], list, str]:
    """Returns (pieces, timed_text, hook). Clips from the last `days`; #count first, #1 last.
    long=True gives every moment more build-up (for 8+ minute videos); min_total keeps adding moments
    until the video is at least that many seconds (8 min = mid-roll ads)."""
    from .script_mode import collect_moments
    week = [c for c in clips if time.time() - c.created < days * 86400 and (not game or c.game == game)]
    moments = collect_moments(week)
    chosen = []
    per = 22.0 if long else 9.0
    for m in moments:  # best first, no two from the same moment
        if any(o.clip is m.clip and abs(o.t - m.t) < (20 if long else 8) for o in chosen):
            continue
        chosen.append(m)
        if len(chosen) >= count and len(chosen) * per >= min_total:
            break
    if len(chosen) < 3:
        return [], [], ""
    n = len(chosen)
    ordered = list(reversed(chosen))  # countdown: weakest first, #1 last
    pieces, texts, t_out = [], [], 0.0
    from .vision import clusters, label_for
    for i, m in enumerate(ordered):
        rank = n - i
        c = m.clip
        lo, hi = c.trim_in or 0.0, c.trim_out or c.duration
        streak = [x for x in clusters(c.events) if abs(x["t"] - m.t) < 0.5] if m.kill else []
        pre = (16.0 if long else (6.0 if rank > 3 else 8.0))
        start = (streak[0]["start"] - (8.0 if long else 3.0)) if streak else m.t - pre
        s, e = max(lo, start), min(hi, m.t + ((4.0 if long else 2.5) if rank > 1 else (5.0 if long else 3.5)))
        if e - s < 5:  # moment right at the clip's start/end: still give it a few seconds
            e = min(hi, s + 5)
            s = max(lo, e - 5)
        fx = [{"type": "zoom", "t": m.t, "amount": 0.22}]
        if i > 0:
            fx.append({"type": "sfx", "t": s + 0.05, "name": "whoosh"})
        if rank == 1:
            fx += [{"type": "slowmo", "t": m.t, "dur": 1.2, "speed": 0.4}, {"type": "sfx", "t": m.t, "name": "boom"}]
        p = Piece(c.path, s, e, effects=fx)
        label = (label_for(streak[0]["count"]) if streak and streak[0]["count"] > 1 else c.title) or c.game
        texts.append((t_out + 0.1, t_out + 2.6, f"#{rank}", "Rank"))
        texts.append((t_out + 0.25, t_out + 2.6, label.upper(), "Sub"))
        dur = (e - s) + (1.2 / 0.4 - 1.2 if rank == 1 else 0)
        t_out += dur
        pieces.append(p)
    games = sorted({m.clip.game for m in chosen})
    period = {1: "today", 7: "this week"}.get(days, "this month" if days >= 28 else f"of the last {days} days")
    hook = f"Top {n} {games[0] if len(games) == 1 else ''} plays {period}".replace("  ", " ")
    return pieces, texts, hook


def chapters(texts: list, intro_label="Intro") -> str:
    """YouTube chapters from the countdown labels (first at 00:00, each >= 10 s; #1 always kept)."""
    import string
    ranks = [(t[0], t[2]) for t in texts if len(t) > 3 and t[3] == "Rank"]
    subs = {round(t[0] - 0.15, 1): t[2] for t in texts if len(t) > 3 and t[3] == "Sub"}
    chosen: list[tuple[float, str]] = []
    for t, rank in ranks:
        label = string.capwords(subs.get(round(t, 1), "").lower())
        entry = (max(t, 10.0), f"{rank} {label}".rstrip())
        if chosen and entry[0] - chosen[-1][0] < 10:
            if len(chosen) == 1 or entry[0] - chosen[-2][0] >= 10:
                chosen[-1] = entry      # keep the later (higher-ranked) one
            continue
        chosen.append(entry)
    if len(chosen) < 2:
        return ""
    lines = [f"00:00 {intro_label}"] + [f"{int(t)//60:02d}:{int(t)%60:02d} {name}" for t, name in chosen]
    return "\n".join(lines)


def month_due(day_of_month: int = 1, hour: int = 12, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    try:
        last = json.loads(STATE.read_text(encoding="utf-8")).get("last_month", "")
    except Exception:
        last = ""
    prev = (now.replace(day=1) - timedelta(days=1))
    key = f"{prev.year}-{prev.month:02d}"     # the month the video is about
    slot = now.replace(day=min(day_of_month, 28), hour=hour, minute=0, second=0, microsecond=0)
    # catch up only during the first week, so clips still mostly belong to the month it's named after
    return now >= slot and now.day <= 7 and last != key


def month_done(now: datetime | None = None):
    now = now or datetime.now()
    prev = (now.replace(day=1) - timedelta(days=1))
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["last_month"] = f"{prev.year}-{prev.month:02d}"
    STATE.write_text(json.dumps(data), encoding="utf-8")
