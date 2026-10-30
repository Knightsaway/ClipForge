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


def _no_dialogs(opened: list):
    """A dialog a test didn't expect would wait forever offscreen: refuse it and remember it, so the test fails
    (Qt only prints an exception raised inside a shortcut or signal). Tests patch the dialogs they expect."""
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    def boom(*a, **k):
        opened.append(a[1:3] if len(a) > 2 else a)
        raise AssertionError("a dialog opened during a Studio test")
    patches = [mock.patch.object(QDialog, "exec", boom)]
    patches += [mock.patch.object(QMessageBox, n, boom) for n in ("information", "question", "warning", "critical")]
    patches += [mock.patch.object(QInputDialog, n, boom) for n in ("getText", "getItem", "getInt", "getDouble")]
    patches += [mock.patch.object(QFileDialog, n, boom) for n in ("getOpenFileName", "getOpenFileNames",
                                                                  "getSaveFileName", "getExistingDirectory")]
    return patches


@pytest.fixture
def studio(app, tmp_path: Path):
    from contextlib import ExitStack

    from clipforge.ui.pages import Jobs, StudioPage
    s = Settings(library_dir=str(tmp_path / "lib"))
    (tmp_path / "lib" / "Game").mkdir(parents=True)
    opened: list = []
    with ExitStack() as stack:
        for pt in _no_dialogs(opened):
            stack.enter_context(pt)
        stack.enter_context(mock.patch.object(library.ff, "duration", lambda p: 30.0))
        stack.enter_context(mock.patch.object(library, "_video_tracks", lambda p: 1))
        stack.enter_context(mock.patch.object(drafts, "DIR", tmp_path / "drafts"))
        stack.enter_context(mock.patch("clipforge.config.SETTINGS_FILE", tmp_path / "settings.json"))
        stack.enter_context(mock.patch("clipforge.ui.pages.save_settings"))
        stack.enter_context(mock.patch("clipforge.ui.studio.presets.save_settings"))
        st = StudioPage(s, Library(s.library), Jobs())
        yield st
        st._hist_timer.stop()
        assert not opened, f"unexpected dialog(s): {opened}"
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
    with mock.patch("PySide6.QtWidgets.QMessageBox.information") as box:
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
    assert len(studio.history) == 0 and not studio.history.can_undo


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
    with mock.patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("Facecam right", True)):
        studio._style_save()
    assert "Facecam right" in s.styles and s.default_style == "Facecam right"
    studio._style_set(game="Game", name="Facecam right")
    assert s.style_by_game == {"Game": "Facecam right"}
    labels = [studio.style_box.itemText(i) for i in range(studio.style_box.count())]
    assert "Facecam right  (default; Game)" in labels
    studio._style_delete("Facecam right")
    assert s.styles == {} and s.default_style == "" and s.style_by_game == {}


def test_undo_restores_a_copy_of_friend_angles(studio):
    alt = {"path": "friend.mp4", "offset": 1.5, "label": "me", "alt_label": "Sam"}
    studio.load_draft(_draft(studio))
    studio.pieces[0]["alt"] = dict(alt)
    studio._commit()
    studio.watermark.setText("x")
    studio._commit()
    studio.undo()
    studio.pieces[0]["alt"]["offset"] = 9.0                 # nudging the angle after undo…
    studio.redo()
    studio.undo()
    assert studio.pieces[0]["alt"]["offset"] == 1.5         # …doesn't rewrite the history


def _press(studio, key: str):
    from PySide6.QtGui import QKeySequence, QShortcut
    hits = [sc for sc in studio.findChildren(QShortcut) if sc.key() == QKeySequence(key)]
    assert len(hits) == 1, key
    hits[0].activated.emit()


def test_keyboard_split_delete_duplicate_and_text(studio):
    from clipforge.ui.player import PlayerPanel
    studio.load_draft(_draft(studio))
    studio._select(0)
    with mock.patch.object(PlayerPanel, "position", new_callable=mock.PropertyMock, return_value=4.0):
        _press(studio, "S")                                   # split the selected clip where the playhead is
    assert [(p["start"], p["end"]) for p in studio.pieces] == [(1.0, 4.0), (4.0, 9.0), (2.0, 6.0)]
    assert [e["t"] for e in studio.pieces[1]["effects"]] == []
    assert [e["t"] for e in studio.pieces[0]["effects"]] == [3.0]
    studio._select(2)
    _press(studio, "Ctrl+D")
    assert len(studio.pieces) == 4 and studio.pieces[3]["start"] == 2.0
    assert studio.pieces[3]["effects"] is not studio.pieces[2]["effects"]
    studio._select(0)
    studio.timeline.sel = None
    _press(studio, "Delete")
    assert [(p["start"], p["end"]) for p in studio.pieces][0] == (4.0, 9.0) and len(studio.pieces) == 3
    n = len(studio.items)
    with mock.patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("GG", True)):
        _press(studio, "T")
    _press(studio, "B")
    assert len(studio.items) == n + 2
    assert (studio.items[-2]["kind"], studio.items[-2]["text"], studio.items[-1]["kind"]) == ("text", "GG", "blur")


def test_every_studio_shortcut_is_registered_once(studio):
    from PySide6.QtGui import QKeySequence, QShortcut
    keys = [sc.key().toString() for sc in studio.findChildren(QShortcut)]
    for k in ("S", "Del", "Backspace", "J", "K", "L", "Left", "Right", "Shift+Left", "Shift+Right", "Ctrl+D",
              "T", "B", "M", "Space"):
        assert keys.count(QKeySequence(k).toString()) == 1, k


# ----------------------------------------------------------------------------- effects

def _at(pos: float):
    from clipforge.ui.player import PlayerPanel
    return mock.patch.object(PlayerPanel, "position", new_callable=mock.PropertyMock, return_value=pos)


def test_add_effect_at_the_playhead(studio):
    studio.load_draft(_draft(studio))
    studio._select(0)                                         # piece 0 is clip time 1 .. 9
    with _at(5.678):
        studio._add_fx({"type": "zoom", "amount": 0.3})
    with _at(20.0):                                           # playhead outside the piece: its middle
        studio._add_fx({"type": "slowmo", "dur": 1.2})
    with _at(5.0):
        studio._add_fx({"type": "sfx", "name": ""})           # a sound effect needs a sound
    fx = studio.pieces[0]["effects"]
    assert [(e["type"], e["t"]) for e in fx] == [("zoom", 3.0), ("zoom", 5.68), ("slowmo", 5.0)]
    assert studio.player.timeline.effects is fx
    studio._clear_fx()
    assert studio.pieces[0]["effects"] == [] and studio.player.timeline.effects == []


def test_add_effect_needs_a_selected_piece(studio):
    studio.load_draft(_draft(studio))
    studio._current = -1
    with mock.patch("PySide6.QtWidgets.QMessageBox.information") as box:
        studio._add_fx({"type": "zoom"})
        studio._add_gif()
    assert box.call_count == 2
    assert [len(p["effects"]) for p in studio.pieces] == [1, 0]


def test_add_gif(studio):
    from PySide6.QtWidgets import QDialog
    studio.load_draft(_draft(studio))
    studio._select(1)                                         # piece 1 is clip time 2 .. 6, vertical preset
    picker = mock.MagicMock(chosen="party.gif", seconds=2.5)
    picker.exec.return_value = QDialog.Accepted
    with _at(4.0), mock.patch("clipforge.ui.gifs.GifPicker", return_value=picker), \
            mock.patch("clipforge.ui.gifs.gif_ratio", return_value=1.0):
        studio._add_gif()
    g = studio.pieces[1]["effects"][-1]
    assert (g["type"], g["t"], g["dur"], g["path"], g["ratio"]) == ("gif", 4.0, 2.5, "party.gif", 1.0)
    assert g["box"] == {"x": 0.275, "y": 0.16, "w": 0.45}      # centred near the top of a Short
    assert studio.mid_tabs.currentIndex() == 1                # straight to placing it
    picker.exec.return_value = QDialog.Rejected
    with mock.patch("clipforge.ui.gifs.GifPicker", return_value=picker):
        studio._add_gif()
    assert len(studio.pieces[1]["effects"]) == 1              # cancelled: nothing added


def test_removed_gif_leaves_the_piece(studio):
    studio.load_draft(_draft(studio))
    studio._select(0)
    studio.pieces[0]["effects"].append({"type": "gif", "t": 2.0, "path": "x.gif", "_remove": True})
    studio._gifs_changed()
    assert [e["type"] for e in studio.pieces[0]["effects"]] == ["zoom"]


def test_transition_and_speed(studio):
    studio.load_draft(_draft(studio))
    studio._select(1)
    i = studio.trans_box.findData("crossfade")
    assert i > 0
    studio.trans_box.setCurrentIndex(i)
    studio._set_transition()
    studio._set_transition()                                  # choosing again doesn't stack transitions
    tr = [e for e in studio.pieces[1]["effects"] if e["type"] == "transition"]
    assert tr == [{"type": "transition", "name": "crossfade", "t": 2.0}]
    studio._select(0)
    assert studio.trans_box.currentData() == ""               # the box follows the selected piece
    studio._select(1)
    assert studio.trans_box.currentData() == "crossfade"
    studio.trans_box.setCurrentIndex(0)
    studio._set_transition()
    assert not [e for e in studio.pieces[1]["effects"] if e["type"] == "transition"]
    studio.speed.setValue(2.0)
    assert studio.pieces[1]["speed"] == 2.0


def test_auto_effects_keep_your_gifs(studio):
    studio.load_draft(_draft(studio))
    gif = {"type": "gif", "t": 2.0, "path": "mine.gif"}
    studio.pieces[0]["effects"].append(gif)
    new = [[{"type": "zoom", "t": 4.0}], [{"type": "sfx", "t": 3.0, "name": "boom"}]]
    with mock.patch("clipforge.autoedit.auto_fx_for", return_value=(new, True)):
        studio.auto_fx_all()
    assert studio.pieces[0]["effects"] == [{"type": "zoom", "t": 4.0}, gif]
    assert studio.pieces[1]["effects"] == [{"type": "sfx", "t": 3.0, "name": "boom"}]
    assert "loudest" in studio.status.text()


def test_text_blur_and_mute_items(studio):
    studio.load_draft(_draft(studio))
    studio.timeline.playhead = 3.456
    with mock.patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("  nice  ", True)):
        studio._add_text()
    with mock.patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("", True)):
        studio._add_text()                                    # empty text: nothing added
    studio._add_blur()
    tab = studio.mid_tabs.currentIndex()
    studio.mid_tabs.setCurrentIndex(0)
    studio._add_mute()
    kinds = [(it["kind"], it["t"]) for it in studio.items]
    assert kinds == [("text", 3.46), ("blur", 3.46), ("mute", 3.46)]
    assert studio.items[0]["text"] == "nice" and studio.items[2]["bleep"] is True
    assert tab == 1 and studio.mid_tabs.currentIndex() == 0   # a mute isn't placed on the video
    assert studio.timeline.sel == ("item", 2)


def test_bleep_results(studio):
    studio.load_draft(_draft(studio))
    studio.items = [{"kind": "mute", "t": 1.0, "dur": 0.5, "bleep": True}]
    studio._bleep_done([(1.04, 1.4), (5.0, 5.3), (7.25, 7.6)])
    mutes = [(m["t"], m["dur"]) for m in studio.items if m["kind"] == "mute"]
    assert mutes == [(1.0, 0.5), (5.0, 0.3), (7.25, 0.35)]   # the one already muted isn't doubled
    assert "Bleeped 2 words" in studio.status.text()
    studio._bleep_done("model missing")
    assert "Couldn't listen" in studio.status.text() and len(studio.items) == 3


# ----------------------------------------------------------------------------- render

def _wait(app, cond, secs=5.0):
    import time
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond(), "timed out"


def test_render_remembers_the_look_and_finishes_the_draft(app, studio, tmp_path):
    studio.load_draft(_draft(studio))
    out = tmp_path / "Game Short.mp4"
    done = []
    studio.rendered.connect(done.append)
    with mock.patch("clipforge.drafts.render", return_value=[out, tmp_path / "Game TikTok.mp4"]) as render, \
            mock.patch("clipforge.ui.studio.render.save_settings") as save:
        studio.render()
        assert not studio.render_btn.isEnabled() and not studio.bar.isHidden()   # busy until the result arrives
        _wait(app, lambda: done)
    assert done == [out]
    d = render.call_args[0][0]
    assert d["status"] == "rendering" and d["edit"]["opts"]["watermark"] == "@me"
    save.assert_called_once()
    s = studio.s
    assert (s.watermark_text, s.short_layout, s.captions, s.caption_style) == ("@me", "crop", True, "word")
    assert s.music_volume == pytest.approx(0.29) and s.caster_game_volume == pytest.approx(0.5)
    saved = drafts.load(d["id"])
    assert saved["status"] == "done" and saved["output"] == str(out) and len(saved["outputs"]) == 2
    assert studio.render_btn.isEnabled() and "Saved Game Short.mp4" in studio.status.text()


def test_render_failure_is_reported_and_marked(app, studio):
    studio.load_draft(_draft(studio))
    with mock.patch("clipforge.drafts.render", side_effect=RuntimeError("encoder exploded")), \
            mock.patch("clipforge.ui.studio.render.save_settings"), \
            mock.patch("PySide6.QtWidgets.QMessageBox.warning") as warn:
        studio.render()
        _wait(app, lambda: warn.called)
    assert warn.call_args[0][1:] == ("Render failed", "encoder exploded")
    assert drafts.load(studio.draft_id)["status"] == "failed"
    assert studio.render_btn.isEnabled() and studio.status.text() == "Render failed"


def test_render_needs_clips(studio):
    with mock.patch("PySide6.QtWidgets.QMessageBox.information") as box:
        studio.render()
    box.assert_called_once()
