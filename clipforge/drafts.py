"""Drafts: every Studio edit is saved as you work, so you can come back days later, and finished edits can
wait in a render queue. A draft is plain JSON in %APPDATA%/ClipForge/drafts/.

draft = {"id", "title", "game", "origin" (studio / auto / montage / session), "created", "updated",
         "status" (draft / queued / rendering / done / failed / skipped), "schedule" (upload when done),
         "output", "error", "edit": {...everything the Studio needs to rebuild the edit...}}
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .config import app_data_dir

DIR = app_data_dir() / "drafts"
_lock = threading.RLock()


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def _path(did: str) -> Path:
    return DIR / f"{did}.json"


def save(d: dict) -> dict:
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        d.setdefault("id", new_id())
        d.setdefault("created", time.time())
        d["updated"] = time.time()
        tmp = _path(d["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, _path(d["id"]))
        return d


def load(did: str) -> dict | None:
    try:
        return json.loads(_path(did).read_text(encoding="utf-8"))
    except Exception:
        return None


def all_drafts() -> list[dict]:
    out = []
    if DIR.exists():
        for f in DIR.glob("*.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    return sorted(out, key=lambda d: d.get("updated", 0), reverse=True)


def delete(did: str):
    with _lock:
        try:
            _path(did).unlink()
        except OSError:
            pass


def update(did: str, **fields) -> dict | None:
    with _lock:
        d = load(did)
        if d is None:
            return None
        d.update(fields)
        return save(d)


def cleanup(days: int = 30):
    """Rendered / skipped drafts older than a month are tidied away (unfinished ones are kept)."""
    now = time.time()
    for d in all_drafts():
        if d.get("status") in ("done", "skipped") and now - d.get("updated", now) > days * 86400:
            delete(d["id"])


# ------------------------------------------------------------------ rendering a draft
def build(edit: dict, s):
    """Draft edit → (pieces, preset key, RenderOptions, game, hook, extras) for producer.produce()."""
    from .editor import PRESETS, Piece, RenderOptions
    o = edit.get("opts", {})
    key = o.get("preset", "short")
    vertical = PRESETS[key].vertical
    pieces = []
    for p in edit.get("pieces", []):
        alt = p.get("alt") or {}
        pieces.append(Piece(p["path"], p["start"], p["end"], o.get("mic_volume", 1.0), o.get("game_volume", 1.0),
                            p.get("speed", 1.0), [dict(e) for e in p.get("effects", [])],
                            alt.get("path", ""), alt.get("offset", 0.0), alt.get("label", ""),
                            alt.get("alt_label", "")))
    opts = RenderOptions(layout=o.get("layout", "blur"), hook_text=(edit.get("hook") or "").strip(),
                         watermark=o.get("watermark", ""), music=o.get("music", ""),
                         music_volume=o.get("music_volume", 0.18), loudnorm=o.get("loudnorm", True),
                         captions=o.get("captions", False), sfx_dir=getattr(s, "sfx_dir", ""))
    script = edit.get("script")
    if script:
        opts.voiceover = script.get("voiceover", "")
        opts.timed_text = script.get("timed_text", [])
    if vertical and o.get("loop") and not script:
        opts.extra["loop"] = True
    opts.extra["cam"] = dict(o.get("cam") or {})
    caster = [dict(c) for c in edit.get("caster", []) if c.get("wav")]
    if caster:
        opts.extra["caster_lines"] = caster
        opts.extra["caster_volume"] = o.get("caster_volume", 1.0)
        opts.extra["caster_duck"] = o.get("caster_duck", 0.7)
    opts.hook_seconds = float(edit.get("hook_secs_eff") or edit.get("hook_secs") or 3.0)
    opts.extra["hook_fixed"] = bool(edit.get("hook_user")) and not edit.get("hook_whole")
    opts.extra["hook_start"] = float(edit.get("hook_start", 0.0))
    if edit.get("hook_pos"):
        opts.extra["hook_pos"] = dict(edit["hook_pos"])
    if abs(float(edit.get("hook_scale", 1.0)) - 1.0) > 0.01:
        opts.extra["hook_scale"] = float(edit["hook_scale"])
    items = edit.get("items") or []
    opts.extra["texts"] = [dict(x) for x in items if x.get("kind") == "text"]
    opts.extra["blurs"] = [dict(x) for x in items if x.get("kind") == "blur"]
    opts.extra["mutes"] = [dict(x) for x in items if x.get("kind") == "mute"]
    for k in ("caption_style", "caption_y"):
        if o.get(k) is not None:
            opts.extra[k] = o[k]
    opts.extra["reaction_zoom"] = getattr(s, "reaction_zoom", True)
    game = edit.get("game") or "Clips"
    return pieces, key, opts, game, opts.hook_text, {"tiktok": vertical and o.get("tiktok", True),
                                                       "thumb_t": edit.get("thumb_t")}


def render(d: dict, s, exports_dir: Path, progress=None) -> list[Path]:
    """Renders a draft (used by the Studio's Render button and the render queue)."""
    from .producer import produce
    edit = d["edit"]
    pieces, key, opts, game, hook, x = build(edit, s)
    if not pieces:
        raise RuntimeError("This draft has no clips")
    missing = [p.path for p in pieces if not Path(p.path).exists()]
    if missing:
        raise RuntimeError(f"A clip in this draft was deleted or moved:\n{missing[0]}")
    out = Path(exports_dir) / f"{game} draft.mp4"
    outs = produce(pieces, key, opts, out, game, kind=d.get("origin", "studio"), hook=hook,
                   ai_post=edit.get("ai_post"), ai_on=getattr(s, "ai_enabled", False),
                   ai_model=getattr(s, "ai_model", ""), tiktok=x["tiktok"], to_approval=False,
                   meta_extra={"script": bool(edit.get("script")), "caster": bool(edit.get("caster")),
                               "draft": d.get("id", "")},
                   progress=progress)
    if x.get("thumb_t") is not None:
        try:
            make_thumb_at(outs[0], float(x["thumb_t"]), hook or game)
        except Exception as e:
            print("thumbnail:", e)
    return outs


def make_thumb_at(video: Path, t: float, text: str):
    """The frame you picked on the timeline, styled like the other auto thumbnails → <video>.thumb.jpg."""
    import shutil

    from .library import thumb_work_dir
    from .thumbnail import make_thumbnails
    files = make_thumbnails(str(video), text, thumb_work_dir(video), exact=t)
    if files:
        shutil.copyfile(files[0], str(video) + ".thumb.jpg")


# ------------------------------------------------------------------ render queue
class RenderQueue:
    """Renders queued drafts one after another in the background. It never starts one while you're in a game
    (busy() says so); callbacks report progress / results (they run on this worker thread)."""

    def __init__(self, s, exports_dir_fn, busy, on_progress=None, on_done=None, on_failed=None):
        self.s, self.exports_dir_fn, self.busy = s, exports_dir_fn, busy
        self.on_progress, self.on_done, self.on_failed = on_progress, on_done, on_failed
        self.current: str = ""
        self._wake = threading.Event()
        threading.Thread(target=self._loop, name="render-queue", daemon=True).start()

    def wake(self):
        self._wake.set()

    def pending(self) -> list[dict]:
        return sorted((d for d in all_drafts() if d.get("status") == "queued"), key=lambda d: d.get("queued_at", 0))

    def _loop(self):
        # a render that was cut off (PC off, app closed) goes back in the queue
        for d in all_drafts():
            if d.get("status") == "rendering":
                update(d["id"], status="queued")
        while True:
            self._wake.wait(15)
            self._wake.clear()
            while True:
                q = self.pending()
                if not q:
                    break
                if self.busy():
                    break                         # in a game: try again later
                d = q[0]
                self.current = d["id"]
                update(d["id"], status="rendering", error="")
                try:
                    outs = render(d, self.s, self.exports_dir_fn(),
                                  progress=lambda f, m, did=d["id"]: self.on_progress and self.on_progress(did, f, m))
                    update(d["id"], status="done", output=str(outs[0]), outputs=[str(o) for o in outs])
                    if self.on_done:
                        self.on_done(d["id"], outs)
                except Exception as e:
                    update(d["id"], status="failed", error=str(e))
                    if self.on_failed:
                        self.on_failed(d["id"], str(e))
                finally:
                    self.current = ""


# ------------------------------------------------------------------ session inbox
def edit_from(pieces: list[dict], s, game: str, hook: str, style: dict | None = None, caster=None,
              ai_post=None) -> dict:
    """A Studio-style edit built without the Studio (session inbox): your settings + your style."""
    import copy
    st = style or {}
    cam = st.get("cam") or dict(getattr(s, "cam_layout", {}) or {})
    return {
        "pieces": [{"path": p["clip"].path, "start": round(p["start"], 3), "end": round(p["end"], 3),
                    "speed": p.get("speed", 1.0), "effects": copy.deepcopy(p.get("effects", []))} for p in pieces],
        "hook": hook, "hook_start": 0.0, "hook_secs": 3.0, "hook_whole": False,
        "hook_pos": st.get("hook_pos"), "hook_scale": st.get("hook_scale", 1.0), "hook_user": False,
        "caster": caster or [], "script": None, "ai_post": ai_post, "thumb_t": None, "game": game,
        "opts": {"preset": "short", "layout": st.get("layout") or getattr(s, "short_layout", "blur"),
                 "watermark": st.get("watermark", getattr(s, "watermark_text", "")),
                 "music": st.get("music", ""), "music_volume": st.get("music_volume", 0.18),
                 "loudnorm": getattr(s, "loudness_normalize", True),
                 "captions": st.get("captions", getattr(s, "captions", False)),
                 "caption_style": st.get("caption_style", getattr(s, "caption_style", "pop3")),
                 "loop": st.get("loop", getattr(s, "loop_shorts", True)),
                 "tiktok": st.get("tiktok", getattr(s, "tiktok_versions", True)),
                 "game_volume": 1.0, "mic_volume": 1.0,
                 "caster_duck": st.get("caster_duck", getattr(s, "caster_game_volume", 0.7)),
                 "caster_volume": st.get("caster_volume", 1.0), "cam": cam},
    }


def build_session_drafts(clips, s, n: int = 3, progress=None) -> list[dict]:
    """After a play session: the best few Shorts, fully auto-edited, saved as inbox drafts (nothing renders)."""
    import tempfile

    from . import used
    from .autoshort import plan_short, suggest_hook
    from .styles import style_for
    excl = used.exclusions() if getattr(s, "no_repeats", True) else []
    made = []
    game = clips[0].game if clips else "Clips"
    style = style_for(s, game)
    by_path = {c.path: c for c in clips}
    for k in range(n):
        if progress:
            progress(k / max(1, n), f"📥 Building Short {k + 1} of {n} from your session…")
        plan = plan_short(clips, target=getattr(s, "short_target_seconds", 30),
                          before=max(3.0, getattr(s, "marker_pre", 8) * 0.4),
                          after=max(1.5, float(getattr(s, "marker_post", 3))), auto_fx=getattr(s, "auto_fx", True),
                          exclude=excl, use_mic=getattr(s, "mic_moments", True))
        if not plan:
            break
        excl = excl + [(p.path, p.start, p.end) for p in plan]
        pieces = [{"clip": by_path[p.path], "start": p.start, "end": p.end, "speed": p.speed,
                   "effects": [dict(e) for e in p.effects]} for p in plan if p.path in by_path]
        if not pieces:
            break
        g = pieces[0]["clip"].game
        from .autoedit import caster_for, edit_pieces
        pieces, _ = edit_pieces(pieces, s, style, add_fx=False)
        hook, ai_post = suggest_hook(g), None
        if getattr(s, "ai_enabled", False):
            try:
                from . import ai
                if ai.available():
                    ai_post = ai.write_post(g, ai.clip_context([p["clip"] for p in pieces]), True, s.ai_model)
                    hook = (ai_post.get("hooks") or [hook])[0]
            except Exception as e:
                print("inbox ai:", e)
        caster = caster_for(pieces, s, g, style)
        from . import timeline_model as tm
        d = save({"origin": "session", "status": "draft", "title": hook, "game": g,
                  "length": round(tm.total(pieces), 1), "thumb": str(pieces[0]["clip"].thumb),
                  "edit": edit_from(pieces, s, g, hook, style, caster, ai_post)})
        made.append(d)
    return made
