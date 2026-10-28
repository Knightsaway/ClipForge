"""Sensitive settings stored outside plaintext settings.json.

On Windows, values are DPAPI-encrypted in %APPDATA%/ClipForge/secrets.bin (this user only).
Elsewhere they stay in a mode-0600 file (still kept out of backups).
"""
from __future__ import annotations

import json
import os
import tempfile

from .config import IS_WINDOWS, app_data_dir

SECRET_KEYS = ("giphy_key", "phone_token", "ntfy_topic")
SECRETS_FILE = app_data_dir() / "secrets.bin"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    if not IS_WINDOWS:
        return data
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load_secrets() -> dict[str, str]:
    if not SECRETS_FILE.exists():
        return {}
    try:
        raw = SECRETS_FILE.read_bytes()
        data = json.loads(_dpapi(raw, False).decode("utf-8"))
        return {k: str(data.get(k, "") or "") for k in SECRET_KEYS}
    except Exception:
        return {}


def save_secrets(values: dict[str, str]) -> None:
    payload = {k: str(values.get(k, "") or "") for k in SECRET_KEYS}
    raw = _dpapi(json.dumps(payload).encode("utf-8"), True)
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(SECRETS_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, SECRETS_FILE)
        if not IS_WINDOWS:
            try:
                os.chmod(SECRETS_FILE, 0o600)
            except OSError:
                pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def redact_settings_file() -> bool:
    """Clear SECRET_KEYS in settings.json on disk. Returns True if the file was rewritten."""
    from .config import SETTINGS_FILE

    if not SETTINGS_FILE.exists():
        return False
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    changed = False
    for k in SECRET_KEYS:
        if data.get(k):
            data[k] = ""
            changed = True
    if not changed:
        return False
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(SETTINGS_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return True


def apply_to_settings(s) -> None:
    """Overlay secret fields from the secure store onto a Settings instance."""
    sec = load_secrets()
    if not any(sec.values()):
        # Migrate once from plaintext settings.json if present
        migrated = {k: getattr(s, k, "") or "" for k in SECRET_KEYS}
        if any(migrated.values()):
            save_secrets(migrated)
            for k, v in migrated.items():
                setattr(s, k, v)
            redact_settings_file()
            return
    for k in SECRET_KEYS:
        if k in sec:
            setattr(s, k, sec[k])
    # Older builds may still have plaintext keys on disk after secrets.bin exists.
    redact_settings_file()


def persist_from_settings(s) -> None:
    save_secrets({k: getattr(s, k, "") or "" for k in SECRET_KEYS})


def redact_for_json(data: dict) -> dict:
    out = dict(data)
    for k in SECRET_KEYS:
        if k in out:
            out[k] = ""
    return out
