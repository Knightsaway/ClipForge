"""Your week in numbers: clips saved, videos made and posted, views, and the best video of the week."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path


def week_key(now: datetime | None = None) -> str:
    y, w, _ = (now or datetime.now()).isocalendar()
    return f"{y}-W{w:02d}"


def due(s, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return bool(getattr(s, "weekly_summary", True)) and now.weekday() == 6 and now.hour >= 18 \
        and getattr(s, "summary_week", "") != week_key(now)


def build(clips, exports: list[Path], queue_items) -> tuple[str, str]:
    """(title, message) for the last 7 days."""
    since = time.time() - 7 * 86400
    n_clips = sum(1 for c in clips if c.created >= since)
    made = [e for e in exports if e.exists() and e.stat().st_mtime >= since and "TikTok" not in e.name]
    posted = [it for it in queue_items if it.status in ("scheduled", "published") and it.added >= since]

    def views(it) -> int:
        try:
            return int(it.stats.get("viewCount", 0) or 0)
        except (TypeError, ValueError):
            return 0
    week_views = sum(views(it) for it in queue_items if it.added >= since)
    best = max((it for it in queue_items if it.added >= since), key=views, default=None)
    lines = [f"🎮 {n_clips} clips saved", f"🎬 {len(made)} videos made", f"📤 {len(posted)} posted to YouTube"]
    if week_views:
        lines.append(f"👀 {week_views:,} views on this week's videos".replace(",", " "))
    if best is not None and views(best):
        lines.append(f"🏆 Best: “{best.title[:60]}” — {views(best):,} views".replace(",", " "))
    if not made:
        lines.append("Tip: open Drafts → Inbox, your session Shorts may be waiting for a ✔")
    return "📊 Your ClipForge week", "\n".join(lines)
