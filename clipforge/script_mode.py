"""Script & voiceover mode: you write and talk, ClipForge edits.

1. Write the script, one beat per line (blank lines are ignored).
   Optional tags on a line:
     [clip: tilted towers]   use a clip whose title / game / file name contains that text
     [kill]                  use a kill-streak moment for this line
     [same]                  keep showing the previous clip
2. Record the voiceover in the app (or import a file).
3. ClipForge finds where each line starts in your recording (speech recognition if installed,
   otherwise the pauses between lines), picks the best matching gameplay moment for each line,
   lines the big moment up near the end of the sentence, ducks the game sound under your voice
   and puts the line on screen as a caption.
"""
from __future__ import annotations

import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import ff
from .editor import Piece
from .highlights import find_moments
from .library import Clip

TAG = re.compile(r"\[(clip|kill|same)(?::\s*([^\]]+))?\]", re.I)


@dataclass
class Line:
    text: str
    clip_query: str = ""
    want_kill: bool = False
    same: bool = False
    start: float = 0.0
    end: float = 0.0
    words: list = field(default_factory=list)


def parse_script(text: str) -> list[Line]:
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        ln = Line(text="")
        for m in TAG.finditer(raw):
            kind = m.group(1).lower()
            if kind == "clip":
                ln.clip_query = (m.group(2) or "").strip().lower()
            elif kind == "kill":
                ln.want_kill = True
            elif kind == "same":
                ln.same = True
        ln.text = TAG.sub("", raw).strip()
        if ln.text:
            lines.append(ln)
    return lines


# ------------------------------------------------------------------ timing
def silences(path: str, noise_db=-35, min_d=0.3) -> list[tuple[float, float]]:
    r = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-i", path, "-af", f"silencedetect=noise={noise_db}dB:d={min_d}",
                "-f", "null", "-"], timeout=300)
    out, cur = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            cur = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, float(m.group(1))))
            cur = None
    return out


def _speech_bounds(path: str, sil: list[tuple[float, float]]) -> tuple[float, float]:
    total = ff.duration(path)
    start = sil[0][1] if sil and sil[0][0] <= 0.05 else 0.0
    end = sil[-1][0] if sil and sil[-1][1] >= total - 0.05 else total
    return start, end


def time_lines(lines: list[Line], voice: str, use_speech_recognition=True) -> list[Line]:
    """Find where each script line is spoken in the recording."""
    total = ff.duration(voice)
    words = []
    if use_speech_recognition:
        try:
            from .captions import available, transcribe
            if available():
                words = transcribe(voice)
        except Exception as e:
            print("transcribe failed, using pauses:", e)
    counts = [max(1, len(l.text.split())) for l in lines]
    if words:
        # walk the transcript, giving each line as many words as it has (robust to small mistakes)
        idx = 0
        total_script = sum(counts)
        scale = len(words) / total_script
        for ln, c in zip(lines, counts):
            n = max(1, round(c * scale))
            chunk = words[idx: idx + n] or words[-1:]
            ln.start, ln.end, ln.words = chunk[0][0], chunk[-1][1], chunk
            idx += n
        lines[-1].end = max(lines[-1].end, words[-1][1])
    else:
        sil = silences(voice)
        s0, s1 = _speech_bounds(voice, sil)
        inner = [(a, b) for a, b in sil if a > s0 + 0.2 and b < s1 - 0.2]
        # expected boundary positions from word counts, then the best ordered set of pauses (DP):
        # long pauses near where a line break is expected win; a boundary may also have no pause
        cum, acc = [], 0
        for c in counts[:-1]:
            acc += c
            cum.append(s0 + (s1 - s0) * acc / sum(counts))
        cuts = _align_cuts(cum, inner, s1 - s0)
        bounds = [s0] + cuts + [s1]
        for i, ln in enumerate(lines):
            ln.start, ln.end = bounds[i], bounds[i + 1]
    # lines tile the timeline with no gaps (footage must never stop)
    for i, ln in enumerate(lines):
        ln.start = 0.0 if i == 0 else lines[i - 1].end
        if i == len(lines) - 1:
            ln.end = max(ln.end, total)
        ln.end = max(ln.end, ln.start + 0.8)
    return lines


def _align_cuts(expected: list[float], pauses: list[tuple[float, float]], span: float) -> list[float]:
    nb, m = len(expected), len(pauses)
    if nb == 0:
        return []
    mids = [(a + b) / 2 for a, b in pauses]
    lens = [b - a for a, b in pauses]
    NO_PAUSE = 2.5
    INF = 1e18

    def cost(i, k):  # boundary i placed on pause k
        return 2.0 * abs(mids[k] - expected[i]) / max(span, 1e-3) - 2.0 * min(lens[k], 1.2)

    # dp[i][k]: best cost with boundary i on pause k (k == m means "no pause, use expected")
    dp = [[INF] * (m + 1) for _ in range(nb)]
    back = [[-1] * (m + 1) for _ in range(nb)]
    for k in range(m):
        dp[0][k] = cost(0, k)
    dp[0][m] = NO_PAUSE
    for i in range(1, nb):
        for k in range(m + 1):
            here = NO_PAUSE if k == m else cost(i, k)
            for pk in range(m + 1):
                if dp[i - 1][pk] >= INF:
                    continue
                if k < m and pk < m and pk >= k:
                    continue  # pauses must stay in order
                prev_pos = expected[i - 1] if pk == m else mids[pk]
                pos = expected[i] if k == m else mids[k]
                if pos <= prev_pos + 0.3:
                    continue
                v = dp[i - 1][pk] + here
                if v < dp[i][k]:
                    dp[i][k], back[i][k] = v, pk
    k = min(range(m + 1), key=lambda x: dp[nb - 1][x])
    if dp[nb - 1][k] >= INF:
        return expected
    cuts = []
    for i in range(nb - 1, -1, -1):
        cuts.append(expected[i] if k == m else mids[k])
        k = back[i][k]
    return cuts[::-1]


# ------------------------------------------------------------------ footage
@dataclass
class Moment:
    score: float
    clip: Clip
    t: float
    kill: bool


def collect_moments(clips: list[Clip]) -> list[Moment]:
    from .autoshort import _ensure_scanned
    from .vision import clusters
    out = []
    for c in clips:
        _ensure_scanned(c)
        for cl in clusters(c.events):
            out.append(Moment(120 + 20 * cl["score"], c, cl["t"], True))
        for t, score in find_moments(c.path, c.markers, max_moments=5):
            if not any(abs(m.t - t) < 3 and m.clip is c for m in out):
                out.append(Moment(score, c, t, False))
        if not out or all(m.clip is not c for m in out):
            out.append(Moment(0, c, (c.trim_out or c.duration) * 0.6, False))
    out.sort(key=lambda m: m.score, reverse=True)
    return out


def plan_script(lines: list[Line], clips: list[Clip], effects=True) -> list[Piece]:
    moments = collect_moments(clips)
    used: list[Moment] = []
    pieces: list[Piece] = []
    for ln in lines:
        need = ln.end - ln.start
        if ln.same and pieces:
            last = pieces[-1]
            hi = next((c.trim_out or c.duration for c in clips if c.path == last.path), last.end + need)
            if last.end + need <= hi:
                pieces.append(Piece(last.path, last.end, last.end + need))
                continue
        pool = [m for m in moments if m not in used] or moments
        if ln.clip_query:
            q = ln.clip_query
            match = [m for m in pool if q in (m.clip.display_title + " " + m.clip.game + " " + m.clip.p.name).lower()]
            pool = match or pool
        if ln.want_kill:
            pool = [m for m in pool if m.kill] or pool
        m = pool[0]
        used.append(m)
        lo, hi = m.clip.trim_in or 0.0, m.clip.trim_out or m.clip.duration
        s = m.t - 0.7 * need          # the big moment lands near the end of the sentence
        s = max(lo, min(s, hi - need))
        e = min(hi, s + need)
        fx = []
        if effects:
            fx.append({"type": "zoom", "t": m.t, "amount": 0.25 if m.kill else 0.18})
        pieces.append(Piece(m.clip.path, s, e, effects=fx))
        if e - s < need - 0.3:  # clip too short: top up with the next moment
            m2 = next((x for x in moments if x not in used), m)
            used.append(m2)
            lo2, hi2 = m2.clip.trim_in or 0.0, m2.clip.trim_out or m2.clip.duration
            rest = need - (e - s)
            s2 = max(lo2, min(m2.t - 0.5 * rest, hi2 - rest))
            pieces.append(Piece(m2.clip.path, s2, min(hi2, s2 + rest)))
    return pieces


def timed_text(lines: list[Line], word_captions: bool) -> list:
    if word_captions and any(l.words for l in lines):
        return [(l.start, l.end, l.words) for l in lines]
    return [(l.start, l.end, l.text) for l in lines]


# ------------------------------------------------------------------ recording
class VoiceRecorder:
    """Records the microphone to a 48 kHz WAV."""

    def __init__(self, out: Path, mic_name: str = ""):
        self.out = Path(out)
        self.mic_name = mic_name
        self._halt = threading.Event()
        self._t: threading.Thread | None = None
        self.level = 0.0
        self.error = ""
        self.started = 0.0

    def start(self):
        self._halt.clear()
        self.started = time.time()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self) -> Path:
        self._halt.set()
        if self._t:
            self._t.join(3)
        return self.out

    def _run(self):
        try:
            from .audio import _com_init
            _com_init()
            import soundcard as sc
            mic = sc.get_microphone(self.mic_name) if self.mic_name else sc.default_microphone()
            with wave.open(str(self.out), "wb") as wf, mic.recorder(samplerate=48000, channels=1, blocksize=960) as rec:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                while not self._halt.is_set():
                    data = rec.record(numframes=4800)
                    if data.ndim > 1:
                        data = data[:, 0]
                    self.level = float(np.max(np.abs(data))) if len(data) else 0.0
                    wf.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
        except Exception as e:
            self.error = str(e)


def clean_voice(src: str, out: str) -> str:
    """Light polish for a home mic: cut rumble, gentle noise reduction, compress, normalize."""
    r = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-i", src, "-af",
                "highpass=f=80,afftdn=nr=10:nf=-40,acompressor=threshold=-20dB:ratio=3:attack=10:release=150,"
                "loudnorm=I=-16:TP=-1.5:LRA=7", "-ar", "48000", out], timeout=300)
    return out if r.returncode == 0 else src
