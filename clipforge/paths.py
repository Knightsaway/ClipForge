"""Install / resource paths — works for source checkouts and frozen (PyInstaller) builds."""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def app_root() -> Path:
    """Folder that contains ClipForge.exe (frozen) or setup.bat / the repo root (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def meipass() -> Path | None:
    """PyInstaller unpack dir for one-file builds; None for onedir / source."""
    p = getattr(sys, "_MEIPASS", None)
    return Path(p) if p else None


def resource_root() -> Path:
    """Where bundled read-only assets live (ffmpeg may sit next to the exe or under meipass)."""
    mp = meipass()
    return mp if mp is not None else app_root()
