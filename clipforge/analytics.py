"""Learn what works on YOUR channel.

Every export gets a small .meta.json (game, hook, length, layout, kills, AI or not). Once videos are
up and stats come in, each one gets a score = views per day (first week) relative to your channel's
typical video (1.0 = normal, 2.0 = twice as good). Then we compare groups: games, hooks, lengths,
posting hours, weekdays, layouts. Auto Short and the AI writer lean towards what wins, while still
trying new things 30% of the time so the data doesn't get stuck.
"""
from __future__ import annotations

import json
import os
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def meta_path(video: str | Path) -> Path:
    return Path(str(video) + ".meta.json")


def write_meta(video: str | Path, **meta):
    meta.setdefault("created", datetime.now().isoformat(timespec="seconds"))
    p = meta_path(video)
    if p.exists() and os.name == "nt":
        try:   # Windows refuses to overwrite a hidden file in place
            p.unlink()
        except OSError:
            pass
    p.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    from .library import hide_file
    hide_file(p)


def read_meta(video: str | Path) -> dict:
    try:
        return json.loads(meta_path(video).read_text(encoding="utf-8"))
    except Exception:
        return {}


def length_bucket(sec: float) -> str:
    if sec < 20:
        return "under 20 s"
    if sec < 35:
        return "20–35 s"
    if sec < 60:
        return "35–60 s"
    if sec < 180:
        return "1–3 min"
    return "3 min+"


@dataclass
class Scored:
    item: object
    score: float          # relative to channel median
    vpd: float            # views per day


def score_items(items) -> list[Scored]:
    """items: youtube.QueueItem list. Only videos live for >= 24 h count."""
    now = datetime.now()
    rows = []
    for it in items:
        if not it.video_id or not it.stats or it.status not in ("published", "scheduled"):
            continue
        when = datetime.fromisoformat(it.publish_at) if it.publish_at else datetime.fromtimestamp(it.added)
        age = (now - when).total_seconds() / 86400
        if age < 1:
            continue
        views = it.stats.get("viewCount", 0)
        rows.append((it, views / min(age, 7.0)))
    if not rows:
        return []
    med = statistics.median(v for _, v in rows) or 1.0
    return [Scored(it, v / med, v) for it, v in rows]


def features(it) -> dict[str, str]:
    m = getattr(it, "meta", {}) or {}
    when = datetime.fromisoformat(it.publish_at) if it.publish_at else datetime.fromtimestamp(it.added)
    f = {
        "Game": m.get("game", "?"),
        "Format": "Short" if m.get("vertical", True) else "Long video",
        "Length": length_bucket(m.get("duration", 0)),
        "Posting hour": f"{when.hour:02d}:00",
        "Weekday": when.strftime("%A"),
        "Layout": m.get("layout", "?"),
        "Kill streak in it": "yes" if m.get("kills", 0) >= 2 else "no",
        "Written by AI": "yes" if m.get("ai") else "no",
        "Hook": m.get("hook_template") or ("custom" if m.get("hook") else "none"),
    }
    return f


def insights(items) -> dict[str, list[tuple[str, float, int]]]:
    """{feature: [(value, avg score, n), ...] best first}"""
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in score_items(items):
        for k, v in features(s.item).items():
            groups[k][v].append(s.score)
    out = {}
    for k, vals in groups.items():
        rows = [(v, sum(xs) / len(xs), len(xs)) for v, xs in vals.items()]
        rows.sort(key=lambda r: r[1], reverse=True)
        out[k] = rows
    return out


def recommendations(items) -> list[str]:
    ins = insights(items)
    tips = []
    n = len(score_items(items))
    if n < 5:
        return [f"Only {n} video(s) with a day of stats so far — post a few more and tips appear here. "
                "Press 📈 Update views now and then (it also runs by itself every few hours)."]

    def best(key, min_n=2):
        rows = [r for r in ins.get(key, []) if r[2] >= min_n]
        # a winner needs something to beat: at least two options with enough videos each
        return rows[0] if len(rows) >= 2 and rows[0][1] > 1.15 * rows[-1][1] and rows[0][1] > 1.1 else None
    for key, fmt in (("Posting hour", "Videos posted around {v} do {x:.1f}× your usual — add it to your posting times."),
                     ("Length", "{v} videos do {x:.1f}× better — aim for that length."),
                     ("Game", "{v} is your strongest game right now ({x:.1f}×)."),
                     ("Hook", "Hook “{v}” is winning ({x:.1f}×) — Auto Short now uses it more often."),
                     ("Layout", "The '{v}' layout does {x:.1f}× better."),
                     ("Weekday", "{v} posts do {x:.1f}× better.")):
        b = best(key)
        if b:
            tips.append(fmt.format(v=b[0], x=b[1]) + f"  (based on {b[2]} videos)")
    ks = dict((r[0], r[1]) for r in ins.get("Kill streak in it", []))
    if "yes" in ks and "no" in ks and ks["yes"] > ks["no"] * 1.2:
        tips.append(f"Videos with a kill streak do {ks['yes'] / max(ks['no'], 0.01):.1f}× better — "
                    "teach more kill icons / medals.")
    return tips or ["No clear winners yet — keep posting, the data gets sharper with every video."]


def hook_weights(items) -> dict[str, float]:
    return {v: x for v, x, n in insights(items).get("Hook", []) if n >= 2}


def winning_hooks(items, k=5) -> list[str]:
    scored = sorted(score_items(items), key=lambda s: s.score, reverse=True)
    out = []
    for s in scored:
        h = (getattr(s.item, "meta", {}) or {}).get("hook")
        if h and h not in out and s.score > 1.0:
            out.append(h)
    return out[:k]


def pick_weighted(options: list[str], weights: dict[str, float], explore=0.3) -> str:
    """Mostly pick what wins, sometimes try something else (so new ideas get a chance)."""
    if not weights or random.random() < explore:
        return random.choice(options)
    w = [max(0.05, weights.get(o, 1.0)) ** 2 for o in options]
    return random.choices(options, weights=w, k=1)[0]
