from __future__ import annotations

import threading
import time
from pathlib import Path

from clipforge.capture import CaptureEngine, Segment


class FakeSeries:
    """A sound recorder whose newest file is finished only `delay` seconds after the clip is saved."""

    def __init__(self, kind, folder: Path, start: float, n: int, delay: float):
        self.kind, self.segs = kind, []
        self._finish_at, self._pending = time.time() + delay, None
        for i in range(n):
            p = folder / f"{kind}_{i:06d}.ts"
            p.write_bytes(b"full" * 10)
            last = i == n - 1
            g = Segment(p, start + 2 * i, None if last else start + 2 * i + 2)
            if last:
                p.write_bytes(b"half")                 # still being written
                self._pending = (g, start + 2 * i + 2)
            self.segs.append(g)

    def scan(self):
        if self._pending and time.time() >= self._finish_at:
            g, end = self._pending
            g.path.write_bytes(b"full" * 10)
            g.wall_end = end
            self._pending = None


def _engine(series):
    e = object.__new__(CaptureEngine)
    e._series, e._lock = series, threading.RLock()
    return e


def test_save_waits_for_the_sound_files_to_reach_the_clip_end(tmp_path):
    src, out = tmp_path / "buf", tmp_path / "save"
    src.mkdir()
    out.mkdir()
    t0 = time.time() - 6
    game = FakeSeries("game", src, t0, 3, delay=1.0)
    e = _engine([game])
    end = t0 + 5.9                                   # the clip ends inside the file still being written
    parts = e._copy_parts([], t0, end, out)
    assert parts["partial"]["game"] == ["game_000002.ts"]
    first = parts["sides"]["game"][0][-1][0]
    assert first.read_bytes() == b"half"             # what we'd have saved before the fix
    t = time.time()
    e._complete_sides(parts, out, wait=5)
    assert 0.9 < time.time() - t < 3                 # waited for it, not the full timeout
    files = parts["sides"]["game"][0]
    assert len(files) == 3
    assert files[-1][0].read_bytes() == b"full" * 10  # the finished file, not the half one
    assert files[-1][1] == 2                          # with its exact length


def test_no_wait_when_everything_is_on_disk(tmp_path):
    src, out = tmp_path / "buf", tmp_path / "save"
    src.mkdir()
    out.mkdir()
    t0 = time.time() - 10
    game = FakeSeries("game", src, t0, 3, delay=0)
    game.scan()
    e = _engine([game])
    parts = e._copy_parts([], t0, t0 + 5.5, out)
    t = time.time()
    e._complete_sides(parts, out, wait=5)
    assert time.time() - t < 0.2
