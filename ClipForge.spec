# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec: ClipForge.exe + _internal + vendored ffmpeg/ next to the exe.

Build (Windows):
  python scripts/fetch_ffmpeg.py
  pyinstaller --noconfirm ClipForge.spec
"""
from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
ROOT = Path(SPECPATH).resolve()

datas = []
binaries = []
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "numpy",
    "soundcard",
]

# Qt plugins / translations shipped with PySide6
for pkg in ("PySide6",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Optional: include license docs next to the exe (copied again by the release script into dist root)
for rel in ("LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"):
    p = ROOT / rel
    if p.exists():
        datas.append((str(p), "."))

licenses = ROOT / "licenses"
if licenses.is_dir():
    datas.append((str(licenses), "licenses"))

a = Analysis(
    [str(ROOT / "clipforge" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["faster_whisper", "torch", "torchaudio", "piper", "piper_tts"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClipForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed; use run_debug / python -m for console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ClipForge",
)
