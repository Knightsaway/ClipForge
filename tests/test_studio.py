"""The real Studio page, offscreen: drafts open exactly as saved, undo / redo, styles."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from clipforge import drafts, library  # noqa: E402
from clipforge.config import Settings  # noqa: E402
from clipforge.library import Library  # noqa: E402

OPTS = {"preset": "short", "layout": "crop", "watermark": "@me", "music": "", "music_volume": 0.29,
        "loudnorm": False, "captions": True, "caption_style": "word", "loop": False, "tiktok": False,
        "game_volume": 0.57, "mic_volume": 0.6, "caster_duck": 0.5, "caster_volume": 0.9}


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def studio(app, tmp_path: Path):
    from clipforge.ui.pages import Jobs, StudioPage
    s = Settings(library_dir=str(tmp_path / "lib"))
    (tmp_path / "lib" / "Game").mkdir(parents=True)
    with mock.patch.object(library.ff, "duration", lambda p: 30.0), \
         mock.patch.object(library, "_video_tracks", lambda p: 1), \
         mock.patch.object(drafts, "DIR", tmp_path / "drafts"), \
         mock.patch("clipforge.ui.pages.save_settings"):
        st = StudioPage(s, Library(s.library), Jobs())
        yield st
        st._hist_timer.stop()
        st.deleteLater()


def _draft(studio, **edit) -> dict:
    root = Path(studio.s.library_dir) / "Game"
    paths = []
    for n in ("a", "b"):
        p = root / f"{n}.mp4"
        p.write_bytes(b"x")
        paths.append(str(p))
    e = {"pieces": [{"path": paths[0], "start": 1.0, "end": 9.0, "speed": 1.0,
                     "effects": [{"type": "zoom", "t": 3.0, "amount": 0.3}]},
                    {"path": paths[1], "start": 2.0, "end": 6.0, "speed": 1.5, "effects": []}],
         "hook": "Watch this", "hook_start": 0.5, "hook_secs": 2.5, "hook_whole": False,
         "hook_pos": {"x": 0.5, "y": 0.3}, "hook_scale": 1.2, "hook_user": True,
         "caster": [{"t": 1.0, "text": "nice"}], "items": [], "script": None, "ai_post": None, "thumb_t": 4.0,
         "opts": dict(OPTS)}
    e.update(edit)
    return {"id": "d1", "status": "draft", "origin": "studio", "title": "T", "edit": e}


def test_draft_opens_exactly_as_saved(studio):
    d = _draft(studio)
    assert studio.load_draft(d)
    e = studio.edit_state()
    for k, v in d["edit"].items():
        if k != "opts":
            assert e[k] == v, k
    for k, v in OPTS.items():
        assert e["opts"][k] == pytest.approx(v), k        # 0.29 must not come back as 0.28
    assert e["game"] == "Game"


def test_draft_with_missing_clips(studio):
    d = _draft(studio)
    d["edit"]["pieces"][0]["path"] = str(Path(studio.s.library_dir) / "gone.mp4")
    with mock.patch("clipforge.ui.pages.QMessageBox.information") as box:
        assert studio.load_draft(d)
        assert len(studio.pieces) == 1
        d["edit"]["pieces"][1]["path"] = d["edit"]["pieces"][0]["path"]
        assert not studio.load_draft(d)
        box.assert_called_once()


def test_old_draft_without_options_gets_defaults(studio):
    assert studio.load_draft(_draft(studio, opts={}))
    o = studio.edit_state()["opts"]
    assert (o["preset"], o["music_volume"], o["loudnorm"], o["loop"]) == ("short", 0.18, True, True)


def test_undo_redo_covers_pieces_text_and_options(studio):
    studio.load_draft(_draft(studio))
    studio._commit()
    studio.watermark.setText("@other")
    studio.music_vol.setValue(40)
    studio._commit()
    studio.pieces[0]["end"] = 7.0
    studio.pieces[0]["effects"].append({"type": "sfx", "t": 4.0, "name": "boom"})
    studio._commit()
    assert studio.undo_btn.isEnabled() and not studio.redo_btn.isEnabled()

    studio.undo()
    assert studio.pieces[0]["end"] == 9.0 and len(studio.pieces[0]["effects"]) == 1
    assert studio.watermark.text() == "@other"
    studio.undo()
    assert studio.watermark.text() == "@me" and studio.music_vol.value() == 29
    assert not studio.undo_btn.isEnabled() and studio.redo_btn.isEnabled()
    studio.redo()
    studio.redo()
    assert studio.pieces[0]["end"] == 7.0 and studio.music_vol.value() == 40
    assert not studio.redo_btn.isEnabled()


def test_undo_restores_a_copy(studio):
    studio.load_draft(_draft(studio))
    studio._commit()
    studio.pieces[0]["effects"][0]["amount"] = 0.9
    studio._commit()
    studio.undo()
    studio.pieces[0]["effects"][0]["amount"] = 0.5          # editing after undo must not change history
    studio.redo()
    assert studio.pieces[0]["effects"][0]["amount"] == 0.9
    studio.undo()
    assert studio.pieces[0]["effects"][0]["amount"] == 0.3


def test_opening_a_draft_starts_fresh_history(studio):
    studio.load_draft(_draft(studio))
    studio._commit()
    studio.watermark.setText("x")
    studio._commit()
    studio.load_draft(_draft(studio))
    assert not studio.undo_btn.isEnabled() or len(studio._history) == 0


def test_every_change_saves_the_draft(studio):
    studio.load_draft(_draft(studio))
    studio._commit()
    studio.watermark.setText("@saved")
    studio._commit()
    d = drafts.load(studio.draft_id)
    assert d["edit"]["opts"]["watermark"] == "@saved" and d["status"] == "draft"


def test_editing_a_finished_draft_starts_a_new_one(studio):
    d = _draft(studio)
    d.update(id="done1", status="done")
    drafts.save(dict(d))
    studio.load_draft(d)
    assert studio.draft_id == ""
    studio.draft_id = "done1"                       # even if it is still pointed at, it isn't overwritten
    new = studio.save_draft()
    assert new["id"] != "done1" and new["status"] == "draft"
    assert drafts.load("done1")["status"] == "done"


def test_failed_draft_goes_back_to_draft(studio):
    d = _draft(studio)
    d.update(id="f1", status="failed")
    drafts.save(dict(d))
    studio.load_draft(d)
    saved = studio.save_draft()
    assert saved["id"] == "f1" and saved["status"] == "draft"
    queued = studio.save_draft(status="queued")
    assert queued["status"] == "queued"


def test_style_round_trip(studio):
    studio.load_draft(_draft(studio))
    look = studio.current_style()
    studio.load_draft(_draft(studio, opts={}, hook_pos=None, hook_scale=1.0))
    studio.apply_style(look, "mine")
    again = studio.current_style()
    for k in ("layout", "watermark", "music_volume", "loop", "tiktok", "captions", "caption_style",
              "caster_duck", "caster_volume", "hook_pos", "hook_scale"):
        assert again[k] == pytest.approx(look[k]), k


def test_style_bookkeeping(studio):
    s = studio.s
    studio.load_draft(_draft(studio))
    with mock.patch("clipforge.ui.pages.QInputDialog.getText", return_value=("Facecam right", True)):
        studio._style_save()
    assert "Facecam right" in s.styles and s.default_style == "Facecam right"
    studio._style_set(game="Game", name="Facecam right")
    assert s.style_by_game == {"Game": "Facecam right"}
    labels = [studio.style_box.itemText(i) for i in range(studio.style_box.count())]
    assert "Facecam right  (default; Game)" in labels
    studio._style_delete("Facecam right")
    assert s.styles == {} and s.default_style == "" and s.style_by_game == {}
