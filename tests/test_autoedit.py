from __future__ import annotations

from types import SimpleNamespace

from clipforge import autoedit


def _pieces(n=3):
    clip = SimpleNamespace(path="x.mp4", events=[], markers=[], game="Test", thumb="")
    return [{"clip": clip, "start": i * 10.0, "end": i * 10.0 + 8, "effects": [], "speed": 1.0} for i in range(n)]


def test_every_cut_gets_a_transition_and_input_is_untouched():
    s = SimpleNamespace(auto_transitions=True, auto_gifs=False)
    src = _pieces()
    out, note = autoedit.edit_pieces(src, s, {}, add_fx=False)
    names = [next((e["name"] for e in p["effects"] if e["type"] == "transition"), None) for p in out]
    assert names[0] is None and all(names[1:])
    assert all(a != b for a, b in zip(names[1:], names[2:]))       # never the same one twice in a row
    assert not any(p["effects"] for p in src)                        # works on a copy
    assert "transition" in note


def test_caster_is_off_unless_switched_on():
    s = SimpleNamespace(caster_auto=False)
    assert autoedit.caster_for(_pieces(), s, "Test", {}) == []


def test_transition_styles():
    from clipforge.editor import add_auto_transitions
    for style, allowed in (("smooth", {"crossfade"}), ("punchy", {"flash", "zoom", "whip", "glitch"})):
        ps = [{"start": 0.0, "effects": []} for _ in range(6)]
        add_auto_transitions(ps, seed=1, style=style)
        assert {e["name"] for p in ps[1:] for e in p["effects"]} <= allowed
