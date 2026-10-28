"""FFmpeg helpers: finding the binaries, probing files, picking the best hardware encoder."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from .config import IS_WINDOWS

ROOT = Path(__file__).resolve().parent.parent

# Windows: hide console windows of child processes
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL = 0x00004000
ABOVE_NORMAL = 0x00008000


def popen_flags(priority: str = "normal") -> dict:
    if not IS_WINDOWS:
        return {}
    flags = CREATE_NO_WINDOW
    if priority == "low":
        flags |= BELOW_NORMAL
    elif priority == "high":
        flags |= ABOVE_NORMAL
    return {"creationflags": flags}


@lru_cache(maxsize=None)
def ffmpeg_bin(name: str = "ffmpeg") -> str:
    exe = name + (".exe" if IS_WINDOWS else "")
    for cand in (ROOT / "ffmpeg" / "bin" / exe, ROOT / "ffmpeg" / exe):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} not found. Run setup.bat (it downloads FFmpeg into the ClipForge folder).")


def run(args: list[str], priority: str = "low", timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **popen_flags(priority))


def probe(path: str | Path) -> dict:
    r = run([ffmpeg_bin("ffprobe"), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)], timeout=30)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def duration(path: str | Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return 0.0


def video_size(path: str | Path) -> tuple[int, int]:
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            return int(s.get("width", 0)), int(s.get("height", 0))
    return 0, 0


def audio_stream_count(path: str | Path) -> int:
    return sum(1 for s in probe(path).get("streams", []) if s.get("codec_type") == "audio")


@lru_cache(maxsize=None)
def _encoder_works(encoder: str) -> bool:
    """Actually try a tiny encode - listing an encoder doesn't mean the GPU supports it."""
    try:
        r = run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                 "color=black:s=256x256:r=30:d=0.2", "-c:v", encoder, "-f", "null", "-"],
                timeout=20)
        return r.returncode == 0
    except Exception:
        return False


VENDOR_ENCODERS = {
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"},
    "amf": {"h264": "h264_amf", "hevc": "hevc_amf", "av1": "av1_amf"},
    "qsv": {"h264": "h264_qsv", "hevc": "hevc_qsv", "av1": "av1_qsv"},
    "x264": {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1"},
}


def pick_encoder(codec: str = "h264", preference: str = "auto") -> tuple[str, str]:
    """Returns (vendor, ffmpeg encoder name)."""
    order = [preference] if preference != "auto" else ["nvenc", "amf", "qsv", "x264"]
    if "x264" not in order:
        order.append("x264")
    for vendor in order:
        enc = VENDOR_ENCODERS.get(vendor, {}).get(codec)
        if enc and _encoder_works(enc):
            return vendor, enc
    return "x264", "libx264"


def export_video_args(vendor: str, encoder: str, bitrate_m: float, fps: int) -> list[str]:
    """High-quality, upload-friendly H.264 settings for each encoder family."""
    br = f"{bitrate_m:.1f}M"
    maxr = f"{bitrate_m * 1.5:.1f}M"
    buf = f"{bitrate_m * 2:.1f}M"
    gop = str(fps * 2)  # 2-second keyframes, what YouTube/TikTok recommend
    if vendor == "nvenc":
        return ["-c:v", encoder, "-preset", "p6", "-tune", "hq", "-rc", "vbr",
                "-b:v", br, "-maxrate", maxr, "-bufsize", buf, "-spatial-aq", "1",
                "-profile:v", "high", "-bf", "2", "-g", gop, "-pix_fmt", "yuv420p"]
    if vendor == "amf":
        return ["-c:v", encoder, "-quality", "quality", "-rc", "vbr_peak", "-b:v", br,
                "-maxrate", maxr, "-g", gop, "-pix_fmt", "yuv420p"]
    if vendor == "qsv":
        return ["-c:v", encoder, "-preset", "slower", "-b:v", br, "-maxrate", maxr,
                "-g", gop, "-pix_fmt", "nv12"]
    return ["-c:v", "libx264", "-preset", "medium", "-b:v", br, "-maxrate", maxr,
            "-bufsize", buf, "-profile:v", "high", "-g", gop, "-pix_fmt", "yuv420p"]


def windows_font() -> str | None:
    if IS_WINDOWS:
        for f in ("C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
                  "C:/Windows/Fonts/segoeuib.ttf"):
            if Path(f).exists():
                return f
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if Path(f).exists():
            return f
    return None


def python_exe() -> str:
    return sys.executable


def list_webcams() -> list[str]:
    """DirectShow video devices (Windows)."""
    from .config import IS_WINDOWS
    if not IS_WINDOWS:
        return []
    try:
        r = run([ffmpeg_bin(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"], timeout=15)
    except Exception:
        return []
    import re
    names = []
    for line in r.stderr.splitlines():
        m = re.search(r'"([^"]+)"\s*\(video\)', line)
        if m:
            names.append(m.group(1))
    return names
