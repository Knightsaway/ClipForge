"""Disk space: how much your clips take, and tidying away old clips nobody needs any more.

Never deleted: favorites, clips in any draft (Studio, inbox, queue), clips that went into a video you made,
and anything newer than the age you chose."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def usage(lib) -> dict:
    root = Path(lib.root)
    exports = root / "Exports"
    all_b = folder_size(root)
    exp_b = folder_size(exports) if exports.exists() else 0
    try:
        du = shutil.disk_usage(root)
        free, disk = du.free, du.total
    except OSError:
        free, disk = 0, 0
    return {"clips": all_b - exp_b, "exports": exp_b, "free": free, "disk": disk}


def gb(b: float) -> str:
    return f"{b / 1e9:.1f} GB" if b >= 1e8 else f"{b / 1e6:.0f} MB"


def protected_paths() -> set[str]:
    """Clips that must stay: in a draft, or used in a video you made."""
    from . import drafts, used
    keep = set()
    for d in drafts.all_drafts():
        for p in d.get("edit", {}).get("pieces", []):
            keep.add(os.path.normcase(p.get("path", "")))
    for path, _a, _b in used.exclusions(days=3650):
        keep.add(os.path.normcase(path))
    return keep


def candidates(clips, days: int) -> list:
    """Clips older than `days` that aren't favorites, in a draft or used in a video."""
    if days <= 0:
        return []
    keep = protected_paths()
    cutoff = time.time() - days * 86400
    out = []
    for c in clips:
        if c.favorite or c.created > cutoff:
            continue
        if os.path.normcase(c.path) in keep:
            continue
        out.append(c)
    return out


def clean(lib, clips) -> tuple[int, int]:
    """Deletes these clips permanently. Returns (how many, bytes freed)."""
    n, freed = 0, 0
    for c in clips:
        try:
            size = c.p.stat().st_size
        except OSError:
            size = 0
        if lib.delete(c):
            n += 1
            freed += size
    return n, freed
