"""Source time <-> output time across speed changes and slow-mo ramps (what every Studio drag relies on)."""
from __future__ import annotations

import pytest

from clipforge import timeline_model as tm
from clipforge.editor import Piece, expand_speed


def _pc(start=0.0, end=20.0, speed=1.0, effects=None):
    return {"clip": "x.mp4", "start": start, "end": end, "speed": speed, "effects": effects or []}


SLOW = {"type": "slowmo", "t": 8.0, "dur": 1.2, "speed": 0.4}
SNAP = dict(SLOW, smooth=False)


def test_plain_piece_is_one_part():
    subs = expand_speed([Piece("x.mp4", 2.0, 10.0)])
    assert [(s.start, s.end, s.speed) for s in subs] == [(2.0, 10.0, 1.0)]
    assert tm.piece_out_dur(_pc(2.0, 10.0)) == pytest.approx(8.0)
    assert tm.piece_out_dur(_pc(2.0, 10.0, speed=2.0)) == pytest.approx(4.0)


@pytest.mark.parametrize("slow", [SLOW, SNAP], ids=["smooth", "snap"])
def test_slowmo_parts_join_up_and_cover_the_piece(slow):
    subs = expand_speed([Piece("x.mp4", 0.0, 20.0, effects=[slow])])
    assert len(subs) > 1
    assert subs[0].start == 0.0 and subs[-1].end == 20.0
    for a, b in zip(subs, subs[1:]):
        assert a.end == pytest.approx(b.start)
    assert min(s.speed for s in subs) == pytest.approx(0.4)
    assert tm.piece_out_dur(_pc(effects=[slow])) > 20.0          # slow-mo makes the piece longer
    assert subs[0].fade_in and subs[-1].fade_out                  # audio fades only at the real cuts


def test_smooth_ramp_joins_without_fades_snap_keeps_them():
    smooth = expand_speed([Piece("x.mp4", 0.0, 20.0, effects=[SLOW])])
    assert not any(s.fade_out for s in smooth[:-1])
    snap = expand_speed([Piece("x.mp4", 0.0, 20.0, effects=[SNAP])])
    assert all(s.fade_in and s.fade_out for s in snap)


def test_each_effect_lands_in_exactly_one_part():
    fx = [{"type": "zoom", "t": t, "amount": 0.3} for t in (0.0, 7.9, 8.0, 8.5, 9.6, 15.0, 20.0)]
    subs = expand_speed([Piece("x.mp4", 0.0, 20.0, effects=[SLOW] + fx)])
    seen = [e["t"] for s in subs for e in s.effects]
    assert sorted(seen) == sorted(e["t"] for e in fx)
    for s in subs:
        for e in s.effects:
            assert s.start <= e["t"] <= s.end


def test_gif_spanning_a_ramp_shows_in_every_part_it_overlaps():
    gif = {"type": "gif", "t": 7.0, "dur": 3.0, "path": "g.gif"}
    subs = expand_speed([Piece("x.mp4", 0.0, 20.0, effects=[SLOW, gif])])
    with_gif = [s for s in subs if gif in s.effects]
    assert with_gif == [s for s in subs if s.start < 10.0 and s.end > 7.0]
    assert len(with_gif) > 1


@pytest.mark.parametrize("pc", [_pc(), _pc(speed=1.5), _pc(effects=[SLOW]), _pc(effects=[SNAP]),
                                _pc(3.0, 12.0, effects=[dict(SLOW, t=4.0), dict(SLOW, t=9.0)])],
                         ids=["plain", "fast", "smooth", "snap", "two-slowmos"])
def test_src_out_round_trip(pc):
    for i in range(41):
        t = pc["start"] + (pc["end"] - pc["start"]) * i / 40
        o = tm.src_to_out(pc, t)
        assert tm.out_to_src(pc, o) == pytest.approx(t, abs=1e-6)
    assert tm.src_to_out(pc, pc["start"]) == 0.0
    assert tm.src_to_out(pc, pc["end"]) == pytest.approx(tm.piece_out_dur(pc))
    outs = [tm.src_to_out(pc, pc["start"] + 0.25 * i) for i in range(int((pc["end"] - pc["start"]) / 0.25) + 1)]
    assert outs == sorted(outs)                                   # never runs backwards


def test_layout_and_locate_across_pieces():
    pieces = [_pc(0.0, 5.0), _pc(10.0, 14.0, speed=2.0), _pc(0.0, 20.0, effects=[SLOW])]
    lay = tm.layout(pieces)
    assert lay[0] == (0.0, pytest.approx(5.0))
    assert lay[1] == (pytest.approx(5.0), pytest.approx(2.0))
    assert lay[2][0] == pytest.approx(7.0)
    assert tm.total(pieces) == pytest.approx(sum(d for _, d in lay))
    assert tm.locate(pieces, 1.0) == (0, pytest.approx(1.0))
    assert tm.locate(pieces, 6.0) == (1, pytest.approx(12.0))     # 1 s into a 2x piece = 2 s of clip
    i, t = tm.locate(pieces, tm.total(pieces) + 5)                # past the end: last piece, its end
    assert i == 2 and t == pytest.approx(20.0)


def test_split_piece_divides_effects_and_refuses_edges():
    fx = [{"type": "zoom", "t": 2.0}, {"type": "sfx", "t": 6.0}, {"type": "zoom", "t": 8.0}]
    pc = _pc(0.0, 10.0, effects=fx)
    a, b = tm.split_piece(pc, 6.0)
    assert (a["start"], a["end"], b["start"], b["end"]) == (0.0, 6.0, 6.0, 10.0)
    assert [e["t"] for e in a["effects"]] == [2.0]
    assert [e["t"] for e in b["effects"]] == [6.0, 8.0]
    a["effects"][0]["t"] = 99                                     # halves don't share effect dicts
    assert fx[0]["t"] == 2.0
    assert tm.split_piece(pc, 0.2) is None and tm.split_piece(pc, 9.8) is None


@pytest.mark.parametrize("smooth", [True, False])
def test_effects_right_after_a_late_slowmo_are_not_lost(smooth):
    # the slow-mo ends 0.1 s before the clip does; that sliver is dropped, its effects must not be
    slow = {"type": "slowmo", "t": 18.6 if smooth else 19.0, "dur": 1.2 if smooth else 1.15, "speed": 0.4,
            "smooth": smooth}
    fx = [{"type": "sfx", "t": 19.95, "name": "boom"}, {"type": "zoom", "t": 20.0}]
    subs = expand_speed([Piece("x.mp4", 0.0, 20.0, effects=[slow] + fx)])
    assert subs[-1].end < 20.0                                    # the sliver really was dropped
    names = [e.get("name", e["type"]) for s in subs for e in s.effects]
    assert sorted(names) == ["boom", "zoom"]
    assert all(s.start <= e["t"] <= s.end for s in subs for e in s.effects)
    assert fx[0]["t"] == 19.95                                    # the Studio's own effect isn't moved
