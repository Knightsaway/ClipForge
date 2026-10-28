"""Where everything sits in the finished video: pieces, effects, GIFs, caster lines and text.

A Studio piece is {"clip", "start", "end", "speed", "effects"}; effect / GIF times are seconds in the clip
(source time). The timeline shows output time — after speed changes and slow-mo ramps — so these helpers
convert between the two.
"""
from __future__ import annotations

from .editor import Piece, expand_speed


def _subs(pc: dict) -> list[Piece]:
    p = Piece(pc["clip"].path if hasattr(pc["clip"], "path") else str(pc["clip"]), pc["start"], pc["end"],
              speed=pc.get("speed", 1.0), effects=pc.get("effects", []))
    return expand_speed([p])


def piece_out_dur(pc: dict) -> float:
    return sum(s.out_dur for s in _subs(pc))


def layout(pieces: list[dict]) -> list[tuple[float, float]]:
    """[(output start, output length)] for each piece."""
    out, t = [], 0.0
    for pc in pieces:
        d = piece_out_dur(pc)
        out.append((t, d))
        t += d
    return out


def total(pieces: list[dict]) -> float:
    return sum(d for _, d in layout(pieces))


def src_to_out(pc: dict, t: float) -> float:
    """Seconds into the clip → seconds into this piece's part of the video."""
    acc = 0.0
    for s in _subs(pc):
        if t <= s.end or s is _subs(pc)[-1]:
            return acc + max(0.0, min(t, s.end) - s.start) / max(0.1, s.speed)
        acc += s.out_dur
    return acc


def out_to_src(pc: dict, o: float) -> float:
    """Seconds into this piece's part of the video → seconds into the clip."""
    acc = 0.0
    subs = _subs(pc)
    for s in subs:
        if o <= acc + s.out_dur or s is subs[-1]:
            return min(s.end, s.start + max(0.0, o - acc) * max(0.1, s.speed))
        acc += s.out_dur
    return pc["end"]


def locate(pieces: list[dict], out_t: float) -> tuple[int, float]:
    """Output time → (piece index, seconds into that clip)."""
    for i, (a, d) in enumerate(layout(pieces)):
        if out_t < a + d or i == len(pieces) - 1:
            return i, out_to_src(pieces[i], max(0.0, out_t - a))
    return -1, 0.0


def split_piece(pc: dict, t: float) -> tuple[dict, dict] | None:
    """Cut a piece in two at clip time t; each half keeps the effects / GIFs that start in it."""
    if not (pc["start"] + 0.3 < t < pc["end"] - 0.3):
        return None
    a = dict(pc, end=t, effects=[dict(e) for e in pc.get("effects", []) if e.get("t", 0) < t])
    b = dict(pc, start=t, effects=[dict(e) for e in pc.get("effects", []) if e.get("t", 0) >= t])
    return a, b
