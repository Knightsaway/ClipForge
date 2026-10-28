"""Backup restore path safety."""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest import mock

import pytest

from clipforge import backup


def test_safe_member_name():
    assert backup._safe_member_name("settings.json")
    assert not backup._safe_member_name("../x")
    assert not backup._safe_member_name("/etc/passwd")
    assert not backup._safe_member_name("C:/Windows/x")
    assert not backup._safe_member_name("foo/C:/evil")
    assert not backup._safe_member_name("clipforge_backup.txt")


def test_restore_stays_under_appdata(tmp_path: Path):
    appdata = tmp_path / "ClipForge"
    appdata.mkdir()
    zpath = tmp_path / "b.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("clipforge_backup.txt", "ok\n")
        z.writestr("settings.json", "{}")
        z.writestr("C:/Windows/evil.txt", "nope")
        z.writestr("../escape.txt", "nope")
    with mock.patch.object(backup, "app_data_dir", return_value=appdata):
        n = backup.restore_backup(zpath)
    assert n == 1
    assert (appdata / "settings.json").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_backup_redacts_secrets(tmp_path: Path):
    appdata = tmp_path / "ClipForge"
    appdata.mkdir()
    (appdata / "settings.json").write_text(
        '{"giphy_key":"secret","phone_token":"tok","ntfy_topic":"topic","quality":"High"}',
        encoding="utf-8",
    )
    dest = tmp_path / "out.zip"
    with mock.patch.object(backup, "app_data_dir", return_value=appdata):
        backup.make_backup(dest, include_media=False)
    import json
    with zipfile.ZipFile(dest) as z:
        data = json.loads(z.read("settings.json").decode())
    assert data["giphy_key"] == ""
    assert data["phone_token"] == ""
    assert data["ntfy_topic"] == ""
    assert data["quality"] == "High"
