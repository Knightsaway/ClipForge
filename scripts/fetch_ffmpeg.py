#!/usr/bin/env python3
"""Download a pinned BtbN win64-gpl FFmpeg build into ./ffmpeg and copy license files.

GPL note: this build includes GPL libraries (e.g. x264). ClipForge invokes ffmpeg.exe as a
separate process (not linked). Redistributors must keep licenses/ffmpeg/ and THIRD_PARTY_NOTICES.md.
See THIRD_PARTY_NOTICES.md for the source offer URL.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Pinned BtbN release (win64-gpl-shared 7.1) - keep in sync with setup.bat.
# The shared build lets ffmpeg.exe and ffprobe.exe share one set of DLLs: ~150 MB instead of
# ~410 MB for three static exes. ffplay, headers and docs are dropped (ClipForge never uses them).
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "autobuild-2025-10-31-13-40/ffmpeg-n7.1.2-7-g24c44c34dc-win64-gpl-shared-7.1.zip"
)
FFMPEG_SHA256 = "65df8e744fa3728ba9dd06d30c31079d9169ca578b6656f4ed208438a73d93dd"
FFMPEG_SOURCE_OFFER = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2025-10-31-13-40"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    dest = ROOT / "ffmpeg"
    if (dest / "bin" / "ffmpeg.exe").exists() and "--force" not in sys.argv:
        print(f"FFmpeg already present at {dest}")
        _ensure_license_stub(dest)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="ffmpeg_dl_"))
    try:
        zpath = tmp / "ffmpeg.zip"
        print(f"Downloading {FFMPEG_URL}")
        urllib.request.urlretrieve(FFMPEG_URL, zpath)
        got = sha256(zpath)
        if got != FFMPEG_SHA256:
            print(f"SHA256 mismatch: expected {FFMPEG_SHA256}, got {got}", file=sys.stderr)
            return 1
        extract = tmp / "out"
        extract.mkdir()
        with zipfile.ZipFile(zpath) as z:
            z.extractall(extract)
        inner = next(p for p in extract.iterdir() if p.is_dir())
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(inner), str(dest))
        _slim(dest)
        _ensure_license_stub(dest)
        print(f"Installed FFmpeg -> {dest}")
        print(f"Source offer: {FFMPEG_SOURCE_OFFER}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _slim(ffmpeg_dir: Path) -> None:
    """Drop what ClipForge never runs: ffplay, C headers, import libraries, HTML docs."""
    (ffmpeg_dir / "bin" / "ffplay.exe").unlink(missing_ok=True)
    for sub in ("include", "lib", "doc"):
        shutil.rmtree(ffmpeg_dir / sub, ignore_errors=True)


def _ensure_license_stub(ffmpeg_dir: Path) -> None:
    lic_dir = ROOT / "licenses" / "ffmpeg"
    lic_dir.mkdir(parents=True, exist_ok=True)
    notice = lic_dir / "README.txt"
    notice.write_text(
        "This folder accompanies the vendored BtbN win64-gpl FFmpeg build.\n"
        f"Binary tree: {ffmpeg_dir}\n"
        f"Upstream release / source offer: {FFMPEG_SOURCE_OFFER}\n"
        "License: GPL (see COPYING.GPLv3 if present in the FFmpeg zip, and "
        "https://ffmpeg.org/legal.html).\n"
        "ClipForge runs ffmpeg.exe as a separate program via subprocess.\n",
        encoding="utf-8",
    )
    # Copy any license files that shipped inside the build tree
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.GPLv3", "COPYING.LGPLv2.1", "COPYING.LGPLv3"):
        for found in ffmpeg_dir.rglob(name):
            if found.is_file():
                shutil.copy2(found, lic_dir / found.name)


if __name__ == "__main__":
    raise SystemExit(main())
