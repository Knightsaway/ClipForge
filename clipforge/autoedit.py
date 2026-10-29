"""The automatic edit, done in the background without the Studio.

Auto Short, Create Montage and the session inbox all build a finished edit here (dead air cut,
effects on the big moments, transitions, GIFs, hook, AI caster when it's on) and save it as a
draft. Nothing plays while it works; the Studio opens the draft afterwards for the human touch.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def auto_fx_for(pieces: list[dict]) -> tuple[list[list], bool]:
    """Effects for each piece: on kills / your markers, or — if a piece has neither — on the loudest,
    most exciting moments in its sound. Safe to run in a background thread."""
    from . import vision
    from .highlights import find_moments
    out, used_sound = [], False
    for n, pc in enumerate(pieces):
        c, a, b = pc["clip"], pc["start"], pc["end"]
        kills = [e["t"] for e in c.events if a <= e["t"] <= b]
        marks = [m - 0.4 for m in c.markers if a <= m <= b]
        keys = sorted(kills or marks)
        if not keys:
            n_max = max(2, int((b - a) / 12))
            try:
                keys = sorted(t for t, _ in find_moments(c.path, max_moments=n_max * 2) if a + 0.5 <= t <= b - 0.5)
                keys = keys[:n_max]
                used_sound = used_sound or bool(keys)
            except Exception as e:
                print("moments:", e)
        fx = [{"type": "zoom", "t": k, "amount": 0.22} for k in keys]
        streaks = [x for x in vision.clusters([e for e in c.events if a <= e["t"] <= b]) if x["count"] >= 2]
        fx += [{"type": "sfx", "t": x["t"], "name": "boom"} for x in streaks]
        if n == 0 and keys:
            fx.append({"type": "slowmo", "t": keys[-1], "dur": 1.0, "speed": 0.4})
            if not streaks:
                fx.append({"type": "sfx", "t": keys[-1], "name": "boom"})
        out.append(fx)
    return out, used_sound


def edit_pieces(pieces: list[dict], s, style: dict | None, *, montage: bool = False, add_fx: bool = True,
                vertical: bool = True, progress=None) -> tuple[list[dict], str]:
    """Dead air (montages), effects, transitions and GIFs on Studio-style piece dicts. Returns (pieces, note)."""
    style = style or {}
    prog = progress or (lambda f, m: None)
    note = ""
    pieces = [dict(p, effects=[dict(e) for e in p.get("effects", [])]) for p in pieces]
    if montage and style.get("cut_boring", getattr(s, "auto_cut_boring", True)):
        prog(0.15, "✂ Cutting the boring parts…")
        try:
            from .analysis import cut_spans, dead_spans
            before = sum(p["end"] - p["start"] for p in pieces)
            new = []
            for p in pieces:
                ev = tuple(sorted({round(e["t"], 1) for e in p["clip"].events} | set(p["clip"].markers)))
                new += cut_spans(p, dead_spans(p["clip"].path, round(p["start"], 2), round(p["end"], 2), ev))
            pieces = new or pieces
            gone = before - sum(p["end"] - p["start"] for p in pieces)
            if gone > 0.5:
                note += f", cut {gone:.0f} s of dead air"
        except Exception as e:
            print("dead air:", e)
    if add_fx and not any(e.get("type") in ("zoom", "slowmo") for p in pieces for e in p["effects"]):
        prog(0.35, "💥 Adding effects on the big moments…")
        fxs, _ = auto_fx_for(pieces)
        for p, fx in zip(pieces, fxs):
            p["effects"] = fx + [e for e in p["effects"] if e.get("type") == "gif"]
    if getattr(s, "auto_transitions", True) and len(pieces) > 1:
        from .editor import add_auto_transitions
        nt = add_auto_transitions(pieces)
        note += f", {nt} transition{'s' if nt != 1 else ''}" if nt else ""
    if style.get("gifs", getattr(s, "auto_gifs", True)):
        prog(0.55, "🎞 Picking GIFs…")
        try:
            from . import autogifs
            if autogifs.available(s):
                n = autogifs.add_gifs(s, pieces, vertical, montage=montage)
                note += f", {n} GIF{'s' if n != 1 else ''}" if n else ""
        except Exception as e:
            print("auto gifs:", e)
    return pieces, note


def caster_for(pieces: list[dict], s, game: str, style: dict | None = None) -> list[dict]:
    """The AI caster's lines, only when it's switched on (it's off by default)."""
    if not (style or {}).get("caster", getattr(s, "caster_auto", False)):
        return []
    try:
        from . import commentator
        from .editor import Piece
        if not commentator.available():
            return []
        ps = [Piece(p["clip"].path, p["start"], p["end"], speed=p.get("speed", 1.0), effects=p["effects"])
              for p in pieces]
        return commentator.build_lines(ps, game, Path(tempfile.mkdtemp(prefix="caster_")),
                                       s.ai_enabled, s.ai_model, s.caster_voice)
    except Exception as e:
        print("caster:", e)
        return []


def generate_draft(pieces: list[dict], s, *, origin: str, preset: str = "short", hook: str = "",
                   ai_post: dict | None = None, montage: bool = False, add_fx: bool = True,
                   progress=None) -> dict | None:
    """Builds the whole automatic edit and saves it as a draft (status 'draft'). Nothing renders here."""
    from . import drafts
    from . import timeline_model as tm
    from .autoshort import suggest_hook
    from .editor import PRESETS
    from .styles import style_for
    if not pieces:
        return None
    prog = progress or (lambda f, m: None)
    game = pieces[0]["clip"].game or "Clips"
    style = style_for(s, game)
    vertical = PRESETS[preset].vertical
    pieces, note = edit_pieces(pieces, s, style, montage=montage, add_fx=add_fx, vertical=vertical, progress=prog)
    if vertical and not hook:
        hook = ((ai_post or {}).get("hooks") or [suggest_hook(game)])[0]
    caster = []
    if getattr(s, "caster_auto", False) or style.get("caster"):
        prog(0.75, "🎙 The caster is writing his lines…")
        caster = caster_for(pieces, s, game, style)
    prog(0.95, "💾 Saving the edit…")
    edit = drafts.edit_from(pieces, s, game, hook, style, caster, ai_post)
    edit["opts"]["preset"] = preset
    if not vertical:
        edit["hook"] = ""
    title = hook or f"{game} montage"
    return drafts.save({"origin": origin, "status": "draft", "title": title, "game": game,
                        "length": round(tm.total(pieces), 1), "thumb": str(pieces[0]["clip"].thumb),
                        "note": note.lstrip(", "), "edit": edit})
