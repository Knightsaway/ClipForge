"""Settings load: a damaged file is kept aside, wrong-typed values fall back to defaults."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from clipforge import config, secrets_store


def _load(tmp_path: Path, settings: Path) -> config.Settings:
    with mock.patch.object(config, "SETTINGS_FILE", settings), \
         mock.patch.object(config, "app_data_dir", lambda: tmp_path), \
         mock.patch.object(secrets_store, "SECRETS_FILE", tmp_path / "secrets.bin"), \
         mock.patch.object(secrets_store, "IS_WINDOWS", False):
        return config.load_settings()


def test_damaged_settings_are_kept_not_overwritten(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"quality": "Ultra", ', encoding="utf-8")
    s = _load(tmp_path, settings)
    assert s.quality == config.Settings().quality
    assert not settings.exists()
    kept = list(tmp_path.glob("settings.json.damaged-*"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == '{"quality": "Ultra", '
    assert "could not be read" in (tmp_path / "clipforge.log").read_text(encoding="utf-8")


def test_non_object_settings_are_kept(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text("[1, 2]", encoding="utf-8")
    _load(tmp_path, settings)
    assert list(tmp_path.glob("settings.json.damaged-*"))


def test_missing_settings_use_defaults_quietly(tmp_path: Path):
    s = _load(tmp_path, tmp_path / "settings.json")
    assert s.quality == config.Settings().quality
    assert not (tmp_path / "clipforge.log").exists()


def test_wrong_types_are_ignored(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "fps": "sixty",               # str for int -> default
        "follow_game": 1,             # int for bool -> default
        "music_volume": 1,            # int for float -> fine
        "buffer_seconds": 240.0,      # float for int -> fine (older saves)
        "styles": [],                 # list for dict -> default
        "quality": "Ultra",
        "QUALITY_TABLE": {},          # not a setting
        "no_such_setting": True,
        "hotkeys": {"marker": "End", "save_clip": 5},
    }), encoding="utf-8")
    s = _load(tmp_path, settings)
    d = config.Settings()
    assert s.fps == d.fps
    assert s.follow_game is d.follow_game
    assert s.music_volume == 1
    assert s.buffer_seconds == 240.0
    assert s.styles == {}
    assert s.quality == "Ultra"
    assert config.Settings.QUALITY_TABLE["High"] == (19, 50)
    assert not hasattr(s, "no_such_setting")
    assert s.hotkeys.marker == "End"
    assert s.hotkeys.save_clip == d.hotkeys.save_clip
    assert settings.exists()          # readable file is never moved


def test_damaged_clip_sidecar_is_kept(tmp_path: Path):
    from clipforge import library
    root = tmp_path / "lib"
    (root / "Game").mkdir(parents=True)
    vid = root / "Game" / "clip.mp4"
    vid.write_bytes(b"x")
    vid.with_suffix(".json").write_text('{"favorite": tr', encoding="utf-8")
    with mock.patch.object(config, "app_data_dir", lambda: tmp_path), \
         mock.patch.object(library.ff, "duration", lambda p: 10.0), \
         mock.patch.object(library, "_video_tracks", lambda p: 1):
        library.Library(root).load(vid)
    kept = list((root / "Game").glob("clip.json.damaged-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"favorite": tr'


def test_damaged_sidecar_outside_library_is_left_alone(tmp_path: Path):
    from clipforge import library
    shared = tmp_path / "squad" / "friend" / "clips"
    shared.mkdir(parents=True)
    vid = shared / "clip.mp4"
    vid.write_bytes(b"x")
    vid.with_suffix(".json").write_text("{oops", encoding="utf-8")
    with mock.patch.object(config, "app_data_dir", lambda: tmp_path), \
         mock.patch.object(library.ff, "duration", lambda p: 0.0), \
         mock.patch.object(library, "_video_tracks", lambda p: 1):
        library.Library(tmp_path / "lib").load(vid)
    assert not list(shared.glob("*.damaged-*"))   # shared/synced folders never get extra files from us


def test_damaged_draft_is_kept_and_others_listed(tmp_path: Path):
    from clipforge import drafts
    d = tmp_path / "drafts"
    with mock.patch.object(drafts, "DIR", d), mock.patch.object(config, "app_data_dir", lambda: tmp_path):
        good = drafts.save({"title": "ok"})
        (d / "bad.json").write_text("{", encoding="utf-8")
        listed = drafts.all_drafts()
        assert [x["id"] for x in listed] == [good["id"]]
        assert list(d.glob("bad.json.damaged-*"))
        assert drafts.all_drafts() == listed       # nothing left to complain about
