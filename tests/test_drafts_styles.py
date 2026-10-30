"""Draft and style helpers the Studio uses (no Qt)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from clipforge import drafts, styles
from clipforge.config import Settings


def test_pieces_round_trip(tmp_path: Path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    clip = SimpleNamespace(path=str(f))
    pieces = [{"clip": clip, "start": 1.23456, "end": 5.0, "effects": [{"type": "zoom", "t": 2.0}]},
              {"clip": clip, "start": 0.0, "end": 2.0, "speed": 2.0, "effects": [],
               "alt": {"path": "b.mp4", "offset": 1.0, "label": "", "alt_label": "Sam"}}]
    saved = drafts.pieces_to_json(pieces)
    assert saved[0] == {"path": str(f), "start": 1.235, "end": 5.0, "speed": 1.0,
                        "effects": [{"type": "zoom", "t": 2.0}]}
    assert saved[1]["alt"]["alt_label"] == "Sam" and "alt" not in saved[0]
    saved[0]["effects"][0]["t"] = 9                         # a copy, not the Studio's own effects
    assert pieces[0]["effects"][0]["t"] == 2.0

    back = drafts.pieces_from_json(saved + [dict(saved[0], path=str(tmp_path / "gone.mp4"))],
                                   lambda p: SimpleNamespace(path=str(p)))
    assert len(back) == 2                                   # the deleted clip is left out
    assert back[1]["speed"] == 2.0 and back[1]["alt"] == saved[1]["alt"]
    assert back[1]["alt"] is not saved[1]["alt"]


def test_which_draft_a_save_goes_into():
    new = drafts.for_saving(None, "auto")
    assert new["status"] == "draft" and new["origin"] == "auto" and new["id"]
    for finished in ("done", "skipped"):
        d = drafts.for_saving({"id": "x", "status": finished}, "studio")
        assert d["id"] != "x" and d["status"] == "draft"
    for st, want in (("failed", "draft"), ("", "draft"), (None, "draft"), ("draft", "draft"), ("queued", "queued")):
        existing = {"id": "x", "status": st, "title": "keep"}
        d = drafts.for_saving(existing, "studio")
        assert (d["id"], d["status"], d["title"]) == ("x", want, "keep")
        assert existing["status"] == st                    # the loaded dict isn't changed


def test_style_management():
    s = Settings()
    styles.save(s, "A", {"layout": "crop"})
    styles.save(s, "B", {"layout": "blur"})
    assert s.default_style == "A"                           # the first one saved
    styles.use_for_game(s, "Apex", "B")
    styles.use_for_game(s, "Valorant", "B")
    styles.use_for_game(s, "Halo", "B")
    assert styles.label(s, "A") == "A  (default)"
    assert styles.label(s, "B") == "B  (Apex, Valorant)"   # at most two games named
    assert styles.style_name_for(s, "Apex") == "B" and styles.style_name_for(s, "Doom") == "A"
    styles.use_for_game(s, "Apex", "")
    assert "Apex" not in s.style_by_game
    styles.delete(s, "A")
    assert s.default_style == "" and "A" not in s.styles
    styles.delete(s, "B")
    assert s.style_by_game == {} and s.styles == {}
