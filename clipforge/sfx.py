"""Sound effects for edits.

ClipForge synthesizes its own originals (no copyright strikes). Drop any extra .wav/.mp3 into
Videos/ClipForge/SFX — e.g. a royalty-free vine boom — and it shows up by file name."""
from __future__ import annotations

from pathlib import Path

from . import ff
from .config import app_data_dir

BUILTIN = {
    "boom": ("aevalsrc", "0.9*sin(2*PI*(55*t-18*t*t))*exp(-3.5*t)+0.35*(random(0)*2-1)*exp(-28*t)", 1.4),
    "bass_drop": ("aevalsrc", "0.95*sin(2*PI*(110*t-40*t*t))*exp(-1.1*t)", 1.6),
    "ding": ("aevalsrc", "0.45*sin(2*PI*1760*t)*exp(-3*t)+0.25*sin(2*PI*2640*t)*exp(-4*t)", 1.4),
    "pop": ("aevalsrc", "0.8*sin(2*PI*(900*t-2600*t*t))*exp(-28*t)", 0.25),
    "riser": ("aevalsrc", "0.35*sin(2*PI*(180*t+260*t*t))*(t/1.6)+0.12*(random(0)*2-1)*(t/1.6)", 1.6),
    "airhorn": ("aevalsrc", "0.22*((2*gt(mod(466*t,1),0.5)-1)+(2*gt(mod(587*t,1),0.5)-1)+"
                "(2*gt(mod(698*t,1),0.5)-1))*(between(t,0,0.14)+between(t,0.2,0.34)+between(t,0.42,1.15))", 1.2),
    "glitch": ("aevalsrc", "0.45*(random(1)*2-1)*gt(sin(2*PI*13*t),0)*exp(-2*t)", 0.6),
    "whoosh": ("noise", "", 0.7),
}

DESCRIPTIONS = {
    "boom": "deep impact (for kills / punchlines)", "bass_drop": "long bass drop", "ding": "bell ding",
    "pop": "quick pop", "riser": "tension riser (before a big moment)", "airhorn": "air horn",
    "glitch": "glitch stutter", "whoosh": "whoosh (transitions)",
}


def builtin_dir() -> Path:
    d = app_data_dir() / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make(name: str, out: Path) -> bool:
    kind, expr, dur = BUILTIN[name]
    if kind == "noise":
        src = ["-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:r=48000:a=0.6"]
        af = ["-af", f"bandpass=f=1400:width_type=o:w=2,afade=t=in:d={dur/2}:curve=exp,"
                     f"afade=t=out:st={dur/2}:d={dur/2}"]
    else:
        src = ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={dur}"]
        af = ["-af", "afade=t=out:st=%.2f:d=0.08" % (dur - 0.08)]
    r = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y"] + src + af + ["-ac", "2", str(out)], timeout=30)
    return r.returncode == 0


def ensure_builtin() -> None:
    d = builtin_dir()
    for name in BUILTIN:
        f = d / f"{name}.wav"
        if not f.exists():
            _make(name, f)


def all_sfx(user_dir: str = "") -> dict[str, Path]:
    ensure_builtin()
    found = {p.stem: p for p in builtin_dir().glob("*.wav")}
    if user_dir and Path(user_dir).exists():
        for p in Path(user_dir).iterdir():
            if p.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a", ".flac"):
                found[p.stem] = p
    return found


def sfx_path(name: str, user_dir: str = "") -> Path | None:
    return all_sfx(user_dir).get(name)
