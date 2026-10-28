"""AI commentator: a hyped esports-caster voice reacting to your plays.

Lines are written by the local AI (or picked from built-in caster lines), spoken by Piper
(free, offline neural voice — setup.bat installs it) or the built-in Windows voice as fallback,
and placed just after each big moment. The game sound ducks under the caster automatically.
"""
from __future__ import annotations

import random
import shutil
import subprocess
import sys
from pathlib import Path

from . import ff
from .config import IS_WINDOWS, app_data_dir
from .editor import Piece, expand_speed

VOICES = app_data_dir() / "voices"
DEFAULT_VOICE = "en_US-ryan-high"

CASTER_LINES = [
    "Oh he's done it again!", "Absolutely disgusting.", "The lobby is in shambles!",
    "Are you kidding me?!", "That's not even fair.", "Somebody check his PC.",
    "What a play!", "He did NOT just do that.", "Clip it. Clip it right now.",
    "That's going in the highlight reel.", "Unbelievable scenes!", "And they never saw it coming.",
]


def moments_on_timeline(pieces: list[Piece], gap: float = 4.0) -> list[tuple[float, str]]:
    """Output-time of each key moment (zoom / slow-mo / kill effects), at least `gap` seconds apart."""
    out, t_out = [], 0.0
    for p in expand_speed(pieces):
        keys = sorted({round(e["t"], 2) for e in p.effects if e.get("type") in ("zoom", "slowmo")
                       and p.start <= e["t"] <= p.end})
        for k in keys:
            out.append((t_out + (k - p.start) / max(0.1, p.speed), Path(p.path).stem))
        t_out += p.out_dur
    spaced = []
    for t, ctx in sorted(out):
        if not spaced or t - spaced[-1][0] >= gap:
            spaced.append((t, ctx))
    return spaced


def write_lines(moments: list[tuple[float, str]], game: str, ai_on: bool, model: str) -> list[str]:
    n = len(moments)
    if ai_on and n:
        try:
            from . import ai
            if ai.available():
                d = ai.ask_json(
                    f"You are a hyped esports caster commentating {game} clips. Write exactly {n} short reactions "
                    f"(3-8 words each, spoken out loud, no emojis, no hashtags), one per highlight in order. "
                    "Vary them; make them funny. Return JSON {\"lines\": [...]}", model)
                lines = [str(x).strip() for x in d.get("lines", []) if str(x).strip()]
                if len(lines) >= n:
                    return lines[:n]
        except Exception as e:
            print("caster ai:", e)
    pool = CASTER_LINES[:]
    random.shuffle(pool)
    return [pool[i % len(pool)] for i in range(n)]


def _piper(text: str, out: Path, voice: str) -> bool:
    VOICES.mkdir(parents=True, exist_ok=True)
    if not (VOICES / f"{voice}.onnx").exists():   # first use: fetch the voice (~60 MB)
        try:
            subprocess.run([sys.executable, "-m", "piper.download_voices", voice, "--data-dir", str(VOICES)],
                           capture_output=True, timeout=600, **ff.popen_flags("low"))
        except Exception:
            return False
    cmd = [sys.executable, "-m", "piper", "-m", voice, "--data-dir", str(VOICES), "-f", str(out), "--", text]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, **ff.popen_flags("low"))
        return r.returncode == 0 and out.exists() and out.stat().st_size > 1000
    except Exception:
        return False


def _sapi(text: str, out: Path) -> bool:
    if not IS_WINDOWS:
        return False
    safe = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;$s.Rate=2;"
          f"$s.SetOutputToWaveFile('{out}');$s.Speak('{safe}');$s.Dispose()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60,
                           **ff.popen_flags("low"))
        return r.returncode == 0 and out.exists()
    except Exception:
        return False


def speak(text: str, out: Path, voice: str = DEFAULT_VOICE) -> bool:
    return _piper(text, out, voice) or _sapi(text, out)


def build_track(pieces: list[Piece], game: str, work: Path, ai_on=False, model="", voice=DEFAULT_VOICE,
                lines: list[str] | None = None) -> tuple[Path | None, list[tuple[float, str]], list[tuple[float, float]]]:
    """Makes one WAV as long as the edit with the caster lines in place.
    Returns (wav, [(time, line)], [(start, end)] of each spoken line — used to dip the game only while he talks)."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    moments = moments_on_timeline(pieces)
    if not moments:
        return None, [], []
    lines = lines or write_lines(moments, game, ai_on, model)
    total = sum(p.out_dur for p in expand_speed(pieces))
    inputs, filters, placed, spans = [], [], [], []
    for i, ((t, _), line) in enumerate(zip(moments, lines)):
        wav = work / f"line_{i}.wav"
        if not speak(line, wav, voice):
            continue
        ms = int((t + 0.25) * 1000)  # react just after the moment
        inputs += ["-i", str(wav)]
        k = len(placed)
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=90,"
                       f"acompressor=threshold=-18dB:ratio=4,volume=1.6,adelay={ms}|{ms}[c{k}]")
        placed.append((t + 0.25, line))
        spans.append((round(t + 0.25, 3), round(t + 0.25 + (ff.duration(wav) or 1.5), 3)))
    if not placed:
        return None, [], []
    out = work / "caster.wav"
    mix = "".join(f"[c{k}]" for k in range(len(placed)))
    filters.append(f"{mix}amix=inputs={len(placed)}:normalize=0,apad,atrim=0:{total:.3f}[out]")
    r = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y"] + inputs +
                       ["-filter_complex", ";".join(filters), "-map", "[out]", "-ar", "48000", str(out)],
                       capture_output=True, timeout=300, **ff.popen_flags("low"))
    return (out if r.returncode == 0 else None), placed, spans


def speak_line(text: str, work: Path, voice: str = DEFAULT_VOICE) -> tuple[str, float]:
    """One caster line → its own WAV. Returns (path, seconds) or ('', 0) if no voice engine worked."""
    import uuid
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    wav = work / f"line_{uuid.uuid4().hex[:8]}.wav"
    if not speak(text, wav, voice):
        return "", 0.0
    return str(wav), round(ff.duration(wav) or 1.5, 3)


def build_lines(pieces: list[Piece], game: str, work: Path, ai_on=False, model="",
                voice=DEFAULT_VOICE) -> list[dict]:
    """The caster's lines as separate items you can move, re-word or delete in the Studio timeline:
    [{"t": seconds into the video, "text", "wav", "dur"}]. Each reacts just after a big moment."""
    moments = moments_on_timeline(pieces)
    if not moments:
        return []
    texts = write_lines(moments, game, ai_on, model)
    out = []
    for (t, _), text in zip(moments, texts):
        wav, dur = speak_line(text, work, voice)
        if wav:
            out.append({"t": round(t + 0.25, 3), "text": text, "wav": wav, "dur": dur})
    return out


def available() -> str:
    """Which voice engine will be used: 'piper', 'windows' or ''."""
    try:
        import piper  # noqa: F401
        return "piper"
    except Exception:
        return "windows" if IS_WINDOWS else ""
