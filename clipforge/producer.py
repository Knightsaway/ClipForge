"""One way to produce a finished, ready-to-post package, used by every automatic flow:

    video + title/description/hashtags (.txt) + metadata (.meta.json)
    + optional TikTok version (own hook, TikTok-safe text placement, TikTok-style hashtags)
    + an entry in the approval list (approve on the PC or from your phone)
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import analytics, editor
from .autoshort import HOOKS, hook_template, suggest_hook
from .config import app_data_dir
from .editor import PRESETS, Piece, RenderOptions

PENDING_FILE = app_data_dir() / "pending.json"


def post_txt(title: str, desc: str, tags: list[str]) -> str:
    return f"TITLE:\n{title}\n\nDESCRIPTION:\n{desc}\n\nHASHTAGS:\n{' '.join(tags)}\n"


def _tiktok_tags(game: str, tags: list[str]) -> list[str]:
    g = "#" + "".join(ch for ch in game.title() if ch.isalnum())
    base = [g, "#gaming", "#fyp", "#gamingclips", "#funny"]
    extra = [t for t in tags if t.lower() not in {b.lower() for b in base} and t.lower() != "#shorts"]
    return (base + extra)[:8]


def produce(pieces: list[Piece], preset_key: str, opts: RenderOptions, out: Path, game: str, *,
            kind: str = "short", hook: str = "", ai_post: dict | None = None, ai_on: bool = False,
            ai_model: str = "", clips=None, tiktok: bool = True, title: str = "", description: str = "",
            meta_extra: dict | None = None, progress=None, to_approval: bool = True,
            publish_at: str = "", series: str = "", package: bool = True) -> list[Path]:
    """Render + package. Returns [main video, (tiktok video)]."""
    preset = PRESETS[preset_key]
    vertical = preset.vertical
    prog = progress or (lambda f, m: None)

    # 1. words: AI when available, templates otherwise
    post = ai_post
    if post is None and ai_on:
        try:
            from . import ai
            if ai.available():
                prog(0.01, "🤖 Writing hook and title…")
                post = ai.write_post(game, ai.clip_context(clips or []), vertical, ai_model)
        except Exception as e:
            print("ai:", e)
    hooks = (post or {}).get("hooks") or []
    if vertical and not hook and not opts.timed_text:
        hook = hooks[0] if hooks else suggest_hook(game)
    opts.hook_text = hook if vertical or hook else opts.hook_text
    yt_title = title or (post or {}).get("title") or (hook or f"{game} moments")
    yt_desc = description or (post or {}).get("description") or f"{yt_title}\nFollow for more {game}!"
    yt_tags = (post or {}).get("tags") or ["#gaming", "#" + "".join(ch for ch in game.title() if ch.isalnum())]
    if vertical and "#shorts" not in [t.lower() for t in yt_tags]:
        yt_tags = yt_tags + ["#shorts"]

    meta = dict(game=game, hook=hook, hook_template=hook_template(hook, game), preset=preset_key,
                vertical=vertical, layout=opts.layout if vertical else "16:9", n_pieces=len(pieces),
                effects=sum(len(p.effects) for p in pieces), ai=bool(post), kind=kind,
                duration=sum(p.out_dur for p in editor.expand_speed(pieces)), loop=bool(opts.extra.get("loop")))
    meta.update(meta_extra or {})

    # one folder per video, named after the game and title (callers pass a path inside Exports)
    if package:
        from .library import package_path
        label = "YouTube Short" if vertical else "YouTube"
        out = package_path(out.parent, game, yt_title, label)

    # 2. main (YouTube) version
    span = 0.55 if (tiktok and vertical) else 0.98
    editor.render(pieces, preset_key, opts, out, progress=lambda f, m: prog(0.02 + span * f, m))
    Path(str(out) + ".txt").write_text(post_txt(yt_title, yt_desc, yt_tags), encoding="utf-8")
    analytics.write_meta(out, platform="youtube", **meta)
    outs = [out]
    try:   # never post the same moment twice: automatic edits skip what this video used
        from . import used
        used.record(editor.expand_speed(pieces), str(out))
    except Exception as e:
        print("used moments:", e)

    # 3. TikTok version: different hook (A/B across platforms), TikTok-safe layout, TikTok hashtags
    if tiktok and vertical:
        topts = copy.deepcopy(opts)
        topts.extra["platform"] = "tiktok"
        if hook and not opts.timed_text:
            alts = [h for h in hooks if h != hook]
            if alts:
                topts.hook_text = alts[0]
            else:
                others = [h.format(game=game) for h in HOOKS if h.format(game=game) != hook]
                topts.hook_text = others[int(time.time()) % len(others)]
        if package:
            from .library import sibling_path
            tout = sibling_path(out, "TikTok")
        else:
            tout = out.with_name(out.stem.replace(" Short ", " TikTok ") +
                                 (" TikTok" if " Short " not in out.stem else "") + out.suffix)
        editor.render(pieces, preset_key, topts, tout, progress=lambda f, m: prog(0.58 + 0.4 * f, "TikTok version: " + m))
        caption = f"{topts.hook_text or yt_title}"
        Path(str(tout) + ".txt").write_text(post_txt(caption, caption, _tiktok_tags(game, yt_tags)), encoding="utf-8")
        tmeta = dict(meta, hook=topts.hook_text, hook_template=hook_template(topts.hook_text, game))
        analytics.write_meta(tout, platform="tiktok", **tmeta)
        outs.append(tout)

    if to_approval:
        store = Approvals()
        for i, path in enumerate(outs):
            platform = "tiktok" if i == 1 else "youtube"
            if i == 0:
                title_i, desc_i, tags_i = yt_title, yt_desc, yt_tags
            else:
                title_i, desc_i, tags_i = _read_txt(path)
            store.add(PendingItem(path=str(path), platform=platform, kind=kind, title=title_i, description=desc_i,
                                  tags=tags_i, publish_at=publish_at, series=series,
                                  thumb=str(path) + ".thumb.jpg" if Path(str(path) + ".thumb.jpg").exists() else ""))
    prog(1.0, "Done")
    return outs


def _read_txt(path: Path):
    from .youtube import parse_post_text
    return parse_post_text(Path(str(path) + ".txt").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ approvals
@dataclass
class PendingItem:
    path: str
    platform: str = "youtube"     # youtube / tiktok
    kind: str = "short"           # short / long / recap / series / bestof / monthly
    title: str = ""
    description: str = ""
    tags: list = field(default_factory=list)
    thumb: str = ""
    publish_at: str = ""          # fixed time (series parts); "" = next free slot
    series: str = ""
    status: str = "pending"       # pending / approved / rejected / posted
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


class Approvals:
    _lock = threading.Lock()

    def __init__(self):
        self.items: list[PendingItem] = []
        self.load()

    def load(self):
        try:
            data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            known = set(PendingItem.__dataclass_fields__)
            self.items = [PendingItem(**{k: v for k, v in d.items() if k in known}) for d in data]
        except Exception:
            self.items = []

    def save(self):
        with Approvals._lock:
            tmp = PENDING_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(i) for i in self.items], indent=1), encoding="utf-8")
            os.replace(tmp, PENDING_FILE)

    def add(self, item: PendingItem):
        with Approvals._lock:
            self.load_unlocked()
            self.items.append(item)
        self.save()

    def load_unlocked(self):
        try:
            data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            known = set(PendingItem.__dataclass_fields__)
            self.items = [PendingItem(**{k: v for k, v in d.items() if k in known}) for d in data]
        except Exception:
            self.items = []

    def pending(self) -> list[PendingItem]:
        self.load()
        return [i for i in self.items if i.status == "pending" and Path(i.path).exists()]

    def get(self, item_id: str) -> PendingItem | None:
        self.load()
        return next((i for i in self.items if i.id == item_id), None)

    def decide(self, item_id: str, approve: bool, queue=None, settings=None, uploader=None) -> str:
        """Approve → YouTube items go into the upload queue (scheduled); TikTok items wait for you to post."""
        self.load()
        it = next((i for i in self.items if i.id == item_id), None)
        if not it:
            return "not found"
        if not approve:
            it.status = "rejected"
            self.save()
            return "skipped"
        it.status = "approved"
        msg = "approved"
        if it.platform == "youtube" and queue is not None and settings is not None:
            from . import youtube
            if youtube.connected():
                when = datetime.fromisoformat(it.publish_at) if it.publish_at else None
                q = queue.add(it.path, it.title, it.description, it.tags, settings.yt_privacy, when is None,
                              settings.post_times, thumb=it.thumb, publish_at=when)
                it.status = "posted"
                msg = "scheduled for " + datetime.fromisoformat(q.publish_at).strftime("%a %d %b %H:%M") \
                    if q.publish_at else "uploading now"
                if uploader is not None:
                    uploader.wake.set()
            else:
                msg = "approved — connect YouTube in Settings to auto-post"
        elif it.platform == "tiktok":
            msg = "approved — post it from your phone (Save → TikTok app)"
        self.save()
        return msg
