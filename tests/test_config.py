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
