"""Backup & restore: settings, styles, kill icons, drafts, approvals and upload history in one .zip
(optionally your GIFs and sound effects too). Your YouTube login isn't included: it's encrypted for this
Windows account only, so connect again on a new PC."""
from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path

from .config import app_data_dir

FILES = ["settings.json", "used_moments.json", "pending.json", "upload_queue.json", "bestof.json", "trends.json",
         "script_draft.txt"]
FOLDERS = ["detectors", "drafts"]
EXTRA = ["gifs", "sfx"]
SKIP_FILES = {"youtube_token.bin"}


def make_backup(dest: str | Path, include_media: bool = True) -> Path:
    d = app_data_dir()
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("clipforge_backup.txt", f"ClipForge backup {time.strftime('%Y-%m-%d %H:%M')}\n")
        for f in FILES:
            if (d / f).exists():
                z.write(d / f, f)
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
    n = 0
    with zipfile.ZipFile(src) as z:
        if "clipforge_backup.txt" not in z.namelist():
            raise ValueError("That isn't a ClipForge backup.")
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name == "clipforge_backup.txt" or ".." in name.split("/") or name.startswith("/"):
                continue
            dst = d / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and name in FILES:
                shutil.copyfile(dst, dst.with_name(dst.name + ".before-restore"))
            with z.open(info) as s, open(dst, "wb") as out:
                shutil.copyfileobj(s, out)
            n += 1
    return n
