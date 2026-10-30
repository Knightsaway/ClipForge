"""Studio undo / redo history."""
from __future__ import annotations

from types import SimpleNamespace

from clipforge.edit_history import EditHistory, snapshot_key


def _st(hook="", current=0, starts=(0.0,)):
    clip = SimpleNamespace(path="a.mp4")
    return {"pieces": [{"clip": clip, "start": s, "end": s + 5} for s in starts], "hook": hook, "current": current}


def test_undo_redo_walk():
    h = EditHistory()
    assert not h.can_undo and not h.can_redo and h.undo() is None and h.redo() is None
    for t in ("a", "b", "c"):
        assert h.record(_st(t))
    assert h.can_undo and not h.can_redo
    assert h.undo()["hook"] == "b"
    assert h.undo()["hook"] == "a"
    assert h.undo() is None                       # the first step is where you started: nothing before it
    assert h.redo()["hook"] == "b"
    assert h.redo()["hook"] == "c"
    assert h.redo() is None


def test_same_edit_is_not_a_new_step():
    h = EditHistory()
    assert h.record(_st("a", current=0))
    assert not h.record(_st("a", current=3))      # only the selected piece changed
    assert len(h) == 1


def test_new_change_after_undo_drops_redo():
    h = EditHistory()
    for t in ("a", "b", "c"):
        h.record(_st(t))
    h.undo()
    h.undo()
    assert h.record(_st("x"))
    assert not h.can_redo
    assert h.undo()["hook"] == "a"
    assert h.redo()["hook"] == "x"


def test_going_back_to_an_earlier_look_is_still_a_step():
    h = EditHistory()
    for t in ("a", "b", "a"):
        assert h.record(_st(t))
    assert len(h) == 3


def test_limit_keeps_the_newest():
    h = EditHistory(limit=5)
    for i in range(8):
        h.record(_st(str(i)))
    assert len(h) == 5
    seen = []
    while (st := h.undo()) is not None:
        seen.append(st["hook"])
    assert seen == ["6", "5", "4", "3"]


def test_reset_and_key():
    h = EditHistory()
    h.record(_st("a"))
    h.reset()
    assert len(h) == 0 and not h.can_undo
    assert snapshot_key(_st(starts=(0.0, 1.0))) != snapshot_key(_st(starts=(0.0, 2.0)))
    assert '"clip": "a.mp4"' in snapshot_key(_st())
