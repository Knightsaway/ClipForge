from __future__ import annotations

import shutil
import subprocess

import pytest

from clipforge.analysis import sound_end

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _clip(path, dead_from=None):
    af = f"volume=0:enable='gte(t,{dead_from})'" if dead_from else "anull"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=160x90:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "8", "-af", af,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return str(path)


def test_dead_tail_is_found(tmp_path):
    assert abs(sound_end(_clip(tmp_path / "a.mp4", dead_from=5)) - 5.0) < 0.15


def test_clean_clip_has_no_dead_tail(tmp_path):
    assert sound_end(_clip(tmp_path / "b.mp4")) is None
