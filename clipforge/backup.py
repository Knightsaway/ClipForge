"""Backup & restore: settings, styles, kill icons, drafts, approvals and upload history in one .zip
(optionally your GIFs and sound effects too). Secrets (API keys, phone token, ntfy topic) and the
YouTube login are not included — reconnect / re-enter those on a new PC.
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

from .config import app_data_dir
from .secrets_store import SECRET_KEYS as SECRET_SETTINGS_KEYS

FILES = ["settings.json", "used_moments.json", "pending.json", "upload_queue.json", "bestof.json", "trends.json",
         "script_draft.txt"]
FOLDERS = ["detectors", "drafts"]
EXTRA = ["gifs", "sfx"]
SKIP_FILES = {"youtube_token.bin", "secrets.bin"}


def _safe_member_name(name: str) -> bool:
    n = name.replace("\\", "/")
    if not n or n == "clipforge_backup.txt":
        return False
    if n.startswith("/") or n.startswith("//") or ".." in n.split("/"):
        return False
    # Reject Windows drive-letter paths (C:/… or nested foo/C:/…)
    import re
    drive = re.compile(r"^[A-Za-z]:")
    if drive.match(n):
        return False
    for part in n.split("/"):
        if drive.match(part):
            return False
    return True


def _redact_settings_bytes(raw: bytes) -> bytes:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return raw
    if not isinstance(data, dict):
        return raw
    for k in SECRET_SETTINGS_KEYS:
        if k in data:
            data[k] = ""
    return json.dumps(data, indent=2).encode("utf-8")


def make_backup(dest: str | Path, include_media: bool = True) -> Path:
    d = app_data_dir()
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("clipforge_backup.txt", f"ClipForge backup {time.strftime('%Y-%m-%d %H:%M')}\n")
        for f in FILES:
            p = d / f
            if not p.exists():
                continue
            if f == "settings.json":
                z.writestr(f, _redact_settings_bytes(p.read_bytes()))
            else:
                z.write(p, f)
        for folder in FOLDERS + (EXTRA if include_media else []):
            base = d / folder
            if base.exists():
                for p in base.rglob("*"):
                    if p.is_file() and p.name not in SKIP_FILES:
                        z.write(p, str(p.relative_to(d)))
    return dest


def restore_backup(src: str | Path) -> int:
    """Puts a backup back (the current files are kept as *.before-restore). Restart ClipForge afterwards."""
    d = app_data_dir()
    root = d.resolve()
    n = 0
    with zipfile.ZipFile(src) as z:
        if "clipforge_backup.txt" not in z.namelist():
            raise ValueError("That isn't a ClipForge backup.")
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or not _safe_member_name(name):
                continue
            dst = (d / name).resolve()
            try:
                dst.relative_to(root)
            except ValueError:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and name in FILES:
                shutil.copyfile(dst, dst.with_name(dst.name + ".before-restore"))
            with z.open(info) as s, open(dst, "wb") as out:
                shutil.copyfileobj(s, out)
            n += 1
    return n
