"""Never post the same moment twice: every rendered video records which parts of which clips it used,
and the automatic edits (Auto Short, session inbox, today's content) skip those parts."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .config import app_data_dir

FILE = app_data_dir() / "used_moments.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def record(pieces, video: str = ""):
    """pieces: editor Pieces (or dicts with path/start/end) that went into a finished video."""
    with _lock:
        data = _load()
        now = time.time()
        for p in pieces:
            path = p.path if hasattr(p, "path") else p["path"]
            start = p.start if hasattr(p, "start") else p["start"]
            end = p.end if hasattr(p, "end") else p["end"]
            data.append({"path": os.path.normcase(str(path)), "start": round(start, 2), "end": round(end, 2),
                         "at": now, "video": str(video)})
        data = [d for d in data if now - d.get("at", now) < 120 * 86400][-5000:]   # keep ~4 months
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, FILE)


def exclusions(days: int = 120) -> list[tuple[str, float, float]]:
    """[(path, start, end)] already used — what plan_short's `exclude` expects."""
    now = time.time()
    out = []
    for d in _load():
        if now - d.get("at", 0) < days * 86400:
            out.append((d["path"], d["start"], d["end"]))
    return out


def is_used(path: str, t: float, excl=None) -> bool:
    key = os.path.normcase(str(path))
    return any(p == key and a - 1 <= t <= b + 1 for p, a, b in (excl if excl is not None else exclusions()))
