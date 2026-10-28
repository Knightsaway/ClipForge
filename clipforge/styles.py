""""My style" presets: your look for automatic edits (layout, webcam spot, text size / place, captions,
caster, music, GIFs…). One can be the default, and each game can have its own."""
from __future__ import annotations

STYLE_KEYS = ("layout", "cam", "hook_pos", "hook_scale", "watermark", "music", "music_volume", "loop", "tiktok",
              "captions", "caption_style", "caption_y", "caster", "caster_duck", "caster_volume", "gifs",
              "cut_boring", "beat_sync")


def style_for(s, game: str = "") -> dict:
    """The style automatic edits should use for this game ({} = the normal settings)."""
    styles = getattr(s, "styles", {}) or {}
    name = (getattr(s, "style_by_game", {}) or {}).get(game) or getattr(s, "default_style", "")
    return dict(styles.get(name, {})) if name else {}


def style_name_for(s, game: str = "") -> str:
    return (getattr(s, "style_by_game", {}) or {}).get(game) or getattr(s, "default_style", "")


def apply_to_opts(style: dict, opts, vertical: bool = True):
    """Puts a style onto RenderOptions used by the automatic (no-Studio) flows."""
    if not style:
        return opts
    if vertical and style.get("layout"):
        opts.layout = style["layout"]
    if "watermark" in style:
        opts.watermark = style["watermark"]
    if style.get("music"):
        opts.music = style["music"]
        opts.music_volume = float(style.get("music_volume", opts.music_volume))
    if "captions" in style:
        opts.captions = bool(style["captions"])
    for k in ("caption_style", "caption_y", "hook_pos", "hook_scale"):
        if style.get(k) is not None:
            opts.extra[k] = style[k]
    if style.get("cam"):
        opts.extra["cam"] = dict(style["cam"])
    if "loop" in style and vertical:
        opts.extra["loop"] = bool(style["loop"])
    return opts
