"""Captions: big, bouncy 1-3 word captions like the viral shorts use, and script-line captions.

Speech-to-text needs `pip install faster-whisper` (setup.bat offers it). It runs on your GPU,
so a 30 s short transcribes in a couple of seconds. Script captions need nothing extra."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from . import ff
from .editor import Piece, expand_speed

_model = None


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _get_model(cpu=False):
    global _model
    if _model is None or cpu:
        from faster_whisper import WhisperModel
        if cpu:
            _model = WhisperModel("small", device="cpu", compute_type="int8")
        else:
            try:
                _model = WhisperModel("small", device="cuda", compute_type="float16")
            except Exception:
                _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def transcribe(wav: str | Path) -> list[tuple[float, float, str]]:
    """Word-level transcript [(start, end, word)]. Falls back to CPU if CUDA libraries are missing."""
    def run(model):
        segs, _ = model.transcribe(str(wav), word_timestamps=True, vad_filter=True)
        return [(w.start, w.end, w.word.strip()) for seg in segs for w in (seg.words or [])]
    try:
        return run(_get_model())
    except Exception:
        return run(_get_model(cpu=True))


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _header(vertical: bool, size_v=96, size_h=64, margin_frac=0.30) -> list[str]:
    W, H = (1080, 1920) if vertical else (1920, 1080)
    size = size_v if vertical else size_h
    margin_v = int(H * margin_frac) if vertical else 80
    return [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Pop,Impact,{size},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,2,"
        f"60,60,{margin_v},1",
        f"Style: Line,Arial,{64 if vertical else 54},&H00FFFFFF,&H0000FFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,"
        f"12,0,2,70,70,{margin_v},1",
        f"Style: Rank,Impact,{150 if vertical else 130},&H0000D4FF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,"
        f"0,0,1,9,4,7,60,60,{int(H*0.12) if vertical else 50},1",
        f"Style: Sub,Arial,{48 if vertical else 40},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"4,2,7,66,60,{int(H*0.12) + 170 if vertical else 200},1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]


def _clean(t: str) -> str:
    return t.replace("{", "").replace("}", "").replace("\n", " ")


CAPTION_STYLES = {
    "pop3": "Pop: 3 words, the spoken one turns yellow",
    "word": "One word at a time, big",
    "karaoke": "Karaoke: the line fills in as you speak",
    "boxed": "Boxed: each word on a coloured box (TikTok style)",
}


def caster_words(lines: list[dict]) -> list[tuple[float, float, str]]:
    """The AI caster's lines as timed words (spread over each line by word length), so they get captions too."""
    out = []
    for c in lines:
        words = str(c.get("text", "")).split()
        if not words:
            continue
        t0, dur = float(c["t"]), float(c.get("dur", 1.5))
        total = sum(len(w) + 2 for w in words)
        t = t0
        for w in words:
            d = dur * (len(w) + 2) / total
            out.append((round(t, 3), round(t + d, 3), w))
            t += d
    return out


def _groups(words, size: int, gap: float = 0.6):
    groups, chunk = [], []
    for w in words:
        if chunk and (len(chunk) == size or w[0] - chunk[-1][1] > gap):
            groups.append(chunk)
            chunk = []
        chunk.append(w)
    if chunk:
        groups.append(chunk)
    return groups


def words_to_ass(words: list[tuple[float, float, str]], work: Path, vertical=True, name="caps.ass",
                 style: str = "pop3", cy: float | None = None) -> str | None:
    if not words:
        return None
    words = sorted(words, key=lambda w: w[0])
    margin = (1 - cy) if cy else 0.30
    lines = _header(vertical, margin_frac=max(0.05, min(0.9, margin - 0.04)))
    # extra styles: one big word, and the TikTok box
    W, H = (1080, 1920) if vertical else (1920, 1080)
    mv = int(H * max(0.05, min(0.9, margin - 0.04))) if vertical else 80
    big = 130 if vertical else 84
    ev = lines.index("[Events]")
    lines[ev - 1:ev - 1] = [
        f"Style: Big,Impact,{big},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,4,2,"
        f"60,60,{mv},1",
        f"Style: Box,Arial Black,{96 if vertical else 64},&H00FFFFFF,&H0000FFFF,&H00F05B5B,&H00F05B5B,-1,0,0,0,"
        f"100,100,0,0,3,14,0,2,60,60,{mv},1",
    ]
    if style == "word":
        for s, e, t in words:
            pop = "{\\fscx70\\fscy70\\t(0,90,\\fscx108\\fscy108)\\t(90,160,\\fscx100\\fscy100)}"
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(max(e, s + 0.18))},Big,,0,0,0,,{pop}{_clean(t.upper())}")
    elif style == "boxed":
        for s, e, t in words:
            pop = "{\\fscx85\\fscy85\\t(0,80,\\fscx100\\fscy100)}"
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(max(e, s + 0.18))},Box,,0,0,0,,{pop}{_clean(t.upper())}")
    elif style == "karaoke":
        for g in _groups(words, 5, 0.8):
            for j, (s, e, _t) in enumerate(g):
                end = g[j + 1][0] if j + 1 < len(g) else e + 0.25
                parts = []
                for k, (_s, _e, t) in enumerate(g):
                    t = _clean(t.upper())
                    if k < j:
                        parts.append(t)                                         # already said: white
                    elif k == j:
                        parts.append("{\\c&H00FFFF&}" + t + "{\\c&HFFFFFF&}")   # saying now: yellow
                    else:
                        parts.append("{\\alpha&H90&}" + t + "{\\alpha&H00&}")   # coming up: faded
                lines.append(f"Dialogue: 0,{_ts(s)},{_ts(end)},Pop,,0,0,0,,{' '.join(parts)}")
    else:   # pop3: chunks of up to 3 words; each chunk pops in, the spoken word turns yellow
        for g in _groups(words, 3):
            for j, (s, e, _txt) in enumerate(g):
                end = g[j + 1][0] if j + 1 < len(g) else e + 0.15
                parts = []
                for k, (_s, _e, t) in enumerate(g):
                    t = _clean(t.upper())
                    parts.append("{\\c&H00FFFF&}" + t + "{\\c&HFFFFFF&}" if k == j else t)
                pop = "{\\fscx115\\fscy115\\t(0,80,\\fscx100\\fscy100)}" if j == 0 else ""
                lines.append(f"Dialogue: 0,{_ts(s)},{_ts(end)},Pop,,0,0,0,,{pop}{' '.join(parts)}")
    (work / name).write_text("\n".join(lines), encoding="utf-8")
    return name


def ass_from_lines(items: list, work: Path, vertical=True) -> str | None:
    """items: [(start, end, text)] or [(start, end, words)] where words is a word list from transcribe()."""
    words_all = []
    lines = _header(vertical)
    for item in items:
        start, end, text = item[0], item[1], item[2]
        style = item[3] if len(item) > 3 else "Line"
        if isinstance(text, list):          # word-timed voiceover -> karaoke-style pop captions
            words_all += text
            continue
        if style in ("Rank", "Sub"):        # countdown numbers / labels, top-left
            anim = "{\\fscx140\\fscy140\\t(0,120,\\fscx100\\fscy100)\\fad(0,200)}" if style == "Rank" else "{\\fad(150,200)}"
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},{style},,0,0,0,,{anim}{_clean(text)}")
            continue
        wrapped = "\\N".join(textwrap.wrap(_clean(text), 26 if vertical else 48))
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Line,,0,0,0,,{{\\fad(120,80)}}{wrapped}")
    if words_all:
        return words_to_ass(words_all, work, vertical)
    (work / "lines.ass").write_text("\n".join(lines), encoding="utf-8")
    return "lines.ass"


def transcribe_pieces(pieces: list[Piece], work: Path) -> list[tuple[float, float, str]]:
    """Word timings in the finished video's time for the clips' own speech (mic track preferred)."""
    _get_model()
    words = []
    offset = 0.0
    for i, p in enumerate(expand_speed(pieces)):
        wav = Path(work) / f"cap_{i}.wav"
        n = ff.audio_stream_count(p.path)
        src = ["-map", "0:a:1"] if n >= 2 else ["-map", "0:a:0"]
        try:
            subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{p.start}", "-t", f"{p.dur}",
                            "-i", p.path] + src + ["-ac", "1", "-ar", "16000", str(wav)],
                           capture_output=True, timeout=120, **ff.popen_flags("low"))
        except subprocess.TimeoutExpired:  # a stuck read must not hang the render: this piece gets no captions
            wav.unlink(missing_ok=True)
        if wav.exists() and abs(p.speed - 1) < 1e-3:  # skip slow-mo bits: speech there is unintelligible
            words += [(offset + s_, offset + e, t) for s_, e, t in transcribe(wav)]
        offset += p.out_dur
    return words


def make_ass(pieces: list[Piece], work: Path, vertical: bool = True, style: str = "pop3",
             extra_words: list | None = None, cy: float | None = None) -> str | None:
    """Transcribe the clips' own speech (mic track preferred) and caption it (+ the caster's lines)."""
    try:
        words = transcribe_pieces(pieces, work)
    except Exception as e:
        print("captions: speech-to-text not available:", e)
        words = []
    return words_to_ass(words + list(extra_words or []), work, vertical, style=style, cy=cy)


# swearing that YouTube's ad rules care about (English + Norwegian); matched on the start of each word
SWEARS = ("fuck", "shit", "bitch", "cunt", "dick", "pussy", "asshole", "motherf", "bastard", "whore", "slut",
          "faen", "fakk", "helvete", "jævl", "javla", "dritt", "fitte", "kuk", "hore", "pikk", "satan", "faan")


def swear_spans(words: list[tuple[float, float, str]]) -> list[tuple[float, float]]:
    out = []
    for s_, e, t in words:
        w = "".join(ch for ch in t.lower() if ch.isalpha() or ch in "æøå")
        if any(w.startswith(x) for x in SWEARS):
            out.append((round(max(0.0, s_ - 0.03), 3), round(e + 0.05, 3)))
    return out
