"""Secrets store migrate + redact settings.json."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clipforge import secrets_store


def test_redact_settings_file(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"giphy_key": "sekrit", "quality": "High", "phone_token": "tok"}),
        encoding="utf-8",
    )
    with mock.patch.object(secrets_store, "SECRETS_FILE", tmp_path / "secrets.bin"), \
         mock.patch("clipforge.config.SETTINGS_FILE", settings), \
         mock.patch.object(secrets_store, "IS_WINDOWS", False):
        assert secrets_store.redact_settings_file() is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["giphy_key"] == ""
        assert data["phone_token"] == ""
        assert data["quality"] == "High"
        assert secrets_store.redact_settings_file() is False  # already clean


def test_migrate_writes_secrets_and_redacts_settings(tmp_path: Path):
    settings = tmp_path / "settings.json"
    secrets = tmp_path / "secrets.bin"
    settings.write_text(
        json.dumps({"giphy_key": "giphy-secret", "ntfy_topic": "mytopic", "phone_port": 8765}),
        encoding="utf-8",
    )
    s = SimpleNamespace(giphy_key="giphy-secret", phone_token="", ntfy_topic="mytopic")
    with mock.patch.object(secrets_store, "SECRETS_FILE", secrets), \
         mock.patch("clipforge.config.SETTINGS_FILE", settings), \
         mock.patch.object(secrets_store, "IS_WINDOWS", False):
        secrets_store.apply_to_settings(s)
        assert secrets.exists()
        loaded = secrets_store.load_secrets()
        assert loaded["giphy_key"] == "giphy-secret"
        assert loaded["ntfy_topic"] == "mytopic"
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["giphy_key"] == ""
        assert data["ntfy_topic"] == ""
        assert data["phone_port"] == 8765
