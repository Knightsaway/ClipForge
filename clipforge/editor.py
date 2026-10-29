"""Rendering: cut + join pieces of clips and export with platform-correct settings.

Presets follow the 2026 upload specs: H.264 High, yuv420p, AAC-LC 48 kHz, MP4 with
faststart, 2-second keyframes. Shorts/TikTok are 1080x1920 9:16 and their bitrate is
capped so files stay under TikTok's ~72 MB Android upload limit."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import ff


@dataclass
class Piece:
    path: str
    start: float
    end: float
    mic_volume: float = 1.0
    game_volume: float = 1.0
    speed: float = 1.0
    # effects use clip time (seconds from the clip's start), like markers and kill events:
    #   {"type": "zoom", "t": 12.3, "amount": 0.3}
    #   {"type": "slowmo", "t": 12.3, "dur": 1.2, "speed": 0.4}
    #   {"type": "sfx", "t": 12.3, "name": "boom"}
    effects: list = field(default_factory=list)
    # Friends' POV split-screen: a second angle of the same moment
    alt_path: str = ""
    alt_offset: float = 0.0       # alt time = main time + alt_offset
    label: str = ""               # name shown on the main angle
    alt_label: str = ""
    fade_in: bool = True          # tiny audio fades at cuts; off between the steps of a smooth slow-mo
    fade_out: bool = True

    @property
    def dur(self) -> float:
        return max(0.1, self.end - self.start)

    @property
    def out_dur(self) -> float:
        return self.dur / max(0.1, self.speed)


def _sub(p: Piece, a: float, b: float, speed: float, fx: list, fade_in: bool = True, fade_out: bool = True) -> Piece:
    return Piece(p.path, a, b, p.mic_volume, p.game_volume, speed, fx, p.alt_path, p.alt_offset, p.label, p.alt_label,
                 fade_in, fade_out)


RAMP = 0.36        # seconds of clip time used to ease into / out of a smooth slow-mo
RAMP_STEPS = 3


def expand_speed(pieces: list[Piece]) -> list[Piece]:
    """Turns slow-mo effects into slowed-down sub-pieces (the 'speed ramp'). Smooth slow-mo (the default) eases in
    and out over a few steps instead of snapping to slow speed; the steps join without audio fades."""
    out: list[Piece] = []
    for p in pieces:
        slows = sorted((e for e in p.effects if e.get("type") == "slowmo" and p.start < e["t"] < p.end),
                       key=lambda e: e["t"])
        others = [e for e in p.effects if e.get("type") not in ("slowmo", "gif")]
        gifs = [e for e in p.effects if e.get("type") == "gif"]

        def fx_in(a, b, last=False):
            return [x for x in others if a <= x["t"] < b or (last and x["t"] == b)] + _gifs_in(gifs, a, b)
        cur = p.start
        smooth_before = False
        for e in slows:
            sp = e.get("speed", 0.4)
            smooth = e.get("smooth", True)
            a = max(cur, e["t"] - 0.25)                 # start the ramp just before the moment
            b = min(p.end, a + e.get("dur", 1.2))
            ra = max(cur, a - RAMP) if smooth else a
            if ra - cur <= 0.15:                       # too little normal speed left: start easing right away
                ra = cur
            if ra - cur > 0.0:
                out.append(_sub(p, cur, ra, p.speed, fx_in(cur, ra), fade_in=not smooth_before, fade_out=not smooth))
            steps = [p.speed + (sp - p.speed) * (k + 1) / (RAMP_STEPS + 1) for k in range(RAMP_STEPS)]
            if smooth and a - ra > 0.05:              # ease in
                seg = (a - ra) / RAMP_STEPS
                for k in range(RAMP_STEPS):
                    x0, x1 = ra + k * seg, ra + (k + 1) * seg
                    out.append(_sub(p, x0, x1, steps[k], fx_in(x0, x1), False, False))
            out.append(_sub(p, a, b, sp, fx_in(a, b), fade_in=not smooth, fade_out=not smooth))
            rb = min(p.end, b + RAMP) if smooth else b
            if smooth and rb - b > 0.05:              # ease out
                seg = (rb - b) / RAMP_STEPS
                for k in range(RAMP_STEPS):
                    x0, x1 = b + k * seg, b + (k + 1) * seg
                    out.append(_sub(p, x0, x1, steps[RAMP_STEPS - 1 - k], fx_in(x0, x1), False, False))
            cur = rb
            smooth_before = smooth
        if p.end - cur > 0.15 or not out or out[-1].path != p.path:
            out.append(_sub(p, cur, p.end, p.speed, fx_in(cur, p.end, last=True), fade_in=not smooth_before))
        elif out and smooth_before:
            out[-1].fade_out = True
    return out


def _gifs_in(gifs: list, a: float, b: float) -> list:
    """A GIF that spans a speed-ramp cut keeps showing in every part it overlaps."""
    return [g for g in gifs if g["t"] < b and g["t"] + g.get("dur", 2.0) > a]


@dataclass
class Preset:
    key: str
    label: str
    w: int
    h: int
    fps: int
    bitrate_m: float
    vertical: bool
    max_mb: float = 0       # 0 = no cap
    max_seconds: float = 0  # 0 = no cap


PRESETS = {
    "short": Preset("short", "YouTube Shorts / TikTok / Reels (9:16)", 1080, 1920, 60, 16, True, 70, 180),
    "youtube1080": Preset("youtube1080", "YouTube 1080p60 (16:9)", 1920, 1080, 60, 20, False),
    "youtube1440": Preset("youtube1440", "YouTube 1440p60 (16:9, sharper on YouTube)", 2560, 1440, 60, 32, False),
    "discord": Preset("discord", "Discord / small file (720p, <10 MB)", 1280, 720, 30, 4, False, 9.5),
}


@dataclass
class RenderOptions:
    layout: str = "blur"           # vertical only: blur / crop / zoom / fit
    hook_text: str = ""            # big text for the first seconds (the hook)
    hook_seconds: float = 3.0
    watermark: str = ""            # e.g. your channel @handle, stays on screen
    music: str = ""
    music_volume: float = 0.18
    loudnorm: bool = True
    captions: bool = False
    fps: int | None = None
    fade_ms: int = 60              # tiny audio fades at cuts = no clicks
    facecam: bool = True           # use the webcam track when a clip has one
    voiceover: str = ""            # script mode: narration audio laid over the edit
    game_bed_volume: float = 0.45  # game sound level under a voiceover (ducked further while you talk)
    timed_text: list = field(default_factory=list)  # [(start, end, text)] e.g. script lines
    sfx_dir: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- webcam box
# A box is {"x", "y", "w", "aspect"}: top-left corner and width as fractions of the finished video's frame.
CAM_ASPECTS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:4": 3 / 4}
CAM_SPOTS = ("top-left", "top", "top-right", "left", "right", "bottom-left", "bottom", "bottom-right")


def default_cam_box(vertical: bool) -> dict:
    # Shorts: just under the gameplay, clear of TikTok's buttons; 16:9: the usual bottom-right corner
    return (cam_spot("bottom", 0.46, "16:9", True) if vertical else cam_spot("bottom-right", 0.24, "16:9", False))


def cam_spot(spot: str, w: float, aspect: str, vertical: bool) -> dict:
    """Box for one of the 8 standard spots. Shorts keep a bigger margin at the bottom (captions / buttons)."""
    W, H = (1080, 1920) if vertical else (1920, 1080)
    bw = w * W
    bh = bw / CAM_ASPECTS.get(aspect, 16 / 9)
    hf = bh / H
    m = 0.03
    top = 0.12 if vertical else m        # under the hook text on Shorts
    bottom = (0.86 if vertical else 1 - m) - hf
    xs = {"left": m, "center": (1 - w) / 2, "right": 1 - m - w}
    ys = {"top": top, "middle": (1 - hf) / 2, "bottom": bottom}
    v, _, h = spot.partition("-") if "-" in spot else ((spot, "", "center") if spot in ("top", "bottom")
                                                      else ("middle", "", spot))
    return {"x": round(xs[h], 4), "y": round(ys[v], 4), "w": round(w, 4), "aspect": aspect}


def cam_rect(box: dict, W: int, H: int) -> tuple[int, int, int, int]:
    """Box → pixels (x, y, w, h), kept inside the frame, even sizes for the encoder."""
    w = max(0.08, min(1.0, float(box.get("w", 0.3))))
    cw = int(W * w) // 2 * 2
    ch = int(cw / CAM_ASPECTS.get(box.get("aspect", "16:9"), 16 / 9)) // 2 * 2
    if ch > H:
        ch = H // 2 * 2
    x = int(max(0, min(W - cw, float(box.get("x", 0)) * W)))
    y = int(max(0, min(H - ch, float(box.get("y", 0)) * H)))
    return x, y, cw, ch


def _cam_overlay(base: str, cam: str, out: str, W: int, H: int, box: dict) -> str:
    x, y, cw, ch = cam_rect(box, W, H)
    b = 0 if box.get("border") is False else max(2, W // 300)
    pad = f",pad={cw + 2*b}:{ch + 2*b}:{b}:{b}:white" if b else ""
    return (f"[{cam}]scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch}{pad},setsar=1[{out}c];"
            f"[{base}][{out}c]overlay={max(0, x - b)}:{max(0, y - b)}[{out}]")


# text item looks (drawtext options)
TEXT_STYLES = {
    "white": "fontcolor=white:borderw=6:bordercolor=black",
    "yellow": "fontcolor=0xFFD23F:borderw=6:bordercolor=black",
    "red": "fontcolor=white:box=1:boxcolor=0xE0182D@0.95:boxborderw=18",
    "black": "fontcolor=black:box=1:boxcolor=white@0.95:boxborderw=18",
    "green": "fontcolor=0x3DDC97:borderw=6:bordercolor=black",
}
TEXT_STYLE_NAMES = {"white": "White", "yellow": "Yellow", "red": "White on red box", "black": "Black on white box",
                    "green": "Green"}

# transitions INTO a clip: (label, filter on the clip's first moments)
TRANSITIONS = {
    "flash": ("Flash", "fade=t=in:st=0:d=0.3:color=white"),
    "zoom": ("Zoom punch", "zoompan=z='1+0.35*max(0,1-it/0.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                           "d=1:s={W}x{H}:fps={fps}"),
    "whip": ("Whip pan", "dblur=angle=0:radius=70:enable='lt(t,0.2)'"),
    "glitch": ("Glitch", "rgbashift=rh=24:bh=-24:enable='lt(t,0.26)',noise=alls=45:allf=t:enable='lt(t,0.26)'"),
    "fade": ("Fade from black", "fade=t=in:st=0:d=0.35"),
    "crossfade": ("Crossfade", ""),   # a real blend of the two clips (picture + sound), made when joining
}
XFADE = 0.45          # seconds the two clips overlap in a crossfade
PUNCHY = ["flash", "zoom", "whip", "glitch"]
# ...and the clip BEFORE it leaves the same way ({st} = when its last moments start), so a transition is
# felt on both sides of the cut instead of looking like a plain cut with a blip after it
TRANSITIONS_OUT = {
    "flash": (0.14, "fade=t=out:st={st}:d=0.14:color=white"),
    "zoom": (0.12, "fade=t=out:st={st}:d=0.12:color=white"),
    "whip": (0.14, "dblur=angle=0:radius=70:enable='gte(t,{st})'"),
    "glitch": (0.16, "rgbashift=rh=-24:bh=24:enable='gte(t,{st})',noise=alls=45:allf=t:enable='gte(t,{st})'"),
    "fade": (0.25, "fade=t=out:st={st}:d=0.25"),
}
TRANSITION_AUDIO = (0.15, 0.2)
LOOP_HOOK_MAX = 60.0   # a seamless-loop Short keeps its hook up all the way (hides the seam) — only when it's short   # seconds the sound fades out / back in around a transition (a soft cut)

def add_auto_transitions(pieces: list[dict], seed: int | None = None, style: str = "punchy") -> int:
    """Automatic edits: every clip after the first comes in with a transition — punchy ones (mixed, never the
    same twice in a row), smooth crossfades, or a mix of both. Studio piece dicts are changed in place;
    clips that already have one are left alone."""
    import random
    rnd = random.Random(seed)
    names = {"smooth": ["crossfade"], "mix": PUNCHY + ["crossfade", "crossfade"]}.get(style, PUNCHY)
    last, n = "", 0
    for pc in pieces[1:]:
        if any(e.get("type") == "transition" for e in pc.get("effects", [])):
            continue
        name = rnd.choice([x for x in names if x != last] or names)
        pc.setdefault("effects", []).append({"type": "transition", "name": name, "t": pc["start"]})
        last, n = name, n + 1
    return n


GIF_EXT = (".gif", ".webp", ".png", ".apng", ".mp4", ".webm", ".mov")


def _gif_input(path: str) -> list[str]:
    """GIFs loop for as long as they're on screen; video stickers (.webm/.mp4) loop too."""
    ext = Path(path).suffix.lower()
    if ext == ".gif":
        return ["-ignore_loop", "0", "-i", path]
    if ext in (".png", ".jpg", ".jpeg"):
        return ["-loop", "1", "-i", path]
    dec: list[str] = []
    if ext == ".webm":   # transparent WebM stickers only keep their see-through parts with libvpx
        codec = next((st.get("codec_name") for st in ff.probe(path).get("streams", [])
                      if st.get("codec_type") == "video"), "")
        dec = ["-c:v", "libvpx-vp9" if codec == "vp9" else "libvpx"] if codec in ("vp8", "vp9") else []
    return ["-stream_loop", "-1"] + dec + ["-i", path]


def _gif_rect(g: dict, W: int, H: int) -> tuple[int, int, int, int]:
    """GIF box: {"x", "y", "w"} fractions of the frame (top-left + width); "ratio" = its height / width."""
    b = g.get("box") or {}
    w = max(0.05, min(1.0, float(b.get("w", 0.4))))
    gw = int(W * w) // 2 * 2
    gh = int(gw * float(g.get("ratio", 1.0))) // 2 * 2
    x = int(max(-gw // 2, min(W - gw // 2, float(b.get("x", 0.3)) * W)))
    y = int(max(-gh // 2, min(H - gh // 2, float(b.get("y", 0.3)) * H)))
    return x, y, gw, gh


def _duck_expr(spans: list, level: float, ramp: float = 0.2) -> str:
    """Volume curve: 1.0 normally, `level` during each span, with short smooth ramps in and out."""
    if level >= 0.999 or not spans:
        return ""
    parts = [f"clip((t-{a - ramp:.3f})/{ramp},0,1)*clip(({b + ramp:.3f}-t)/{ramp},0,1)" for a, b in spans]
    cur = parts[0]
    for p in parts[1:]:
        cur = f"max({cur},{p})"
    return f"1-{1 - level:.3f}*{cur}"   # used inside single quotes, so the commas are safe


def _has_audio_tracks(path: str) -> int:
    return ff.audio_stream_count(path)


def _vertical_chain(inp: str, out: str, layout: str, W: int, H: int, cam: str | None = None,
                    xexpr: str | None = None) -> str:
    """Filter graph turning a 16:9 gameplay frame into a 9:16 phone frame (no fps/tail)."""
    if cam and layout == "facecam":   # webcam on top third, gameplay below: the classic gaming Short
        ch = int(H * 0.33) // 2 * 2
        gh = H - ch
        return (f"[{cam}]scale={W}:{ch}:force_original_aspect_ratio=increase,crop={W}:{ch},setsar=1[{out}c];"
                f"[{inp}]scale=-2:{gh}:flags=lanczos,crop={W}:{gh},setsar=1[{out}g];"
                f"[{out}c][{out}g]vstack=inputs=2[{out}]")
    if layout in ("facecam", "facecam_pip"):
        layout = "crop" if layout == "facecam" else "blur"  # clip has no webcam: sensible fallback
    if layout == "smart":   # full-screen 9:16 that follows the action (x moves over time)
        xe = xexpr or "(iw-ow)/2"
        return f"[{inp}]crop=w=ih*9/16:h=ih:x='{xe}':y=0,scale={W}:{H}:flags=lanczos,setsar=1[{out}]"
    if layout == "crop":  # full-screen center crop: best for shooters (crosshair is center)
        return f"[{inp}]scale=-2:{H}:flags=lanczos,crop={W}:{H},setsar=1[{out}]"
    if layout == "fit":   # whole 16:9 frame, black bars
        return f"[{inp}]scale={W}:-2:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{out}]"
    # blur / zoom: sharp gameplay in the middle, blurred copy filling the background
    if layout == "zoom":
        fg = f"crop=ih:ih,scale={W}:{W}:flags=lanczos"                   # 1:1 center
    else:
        fg = f"crop=ih*4/3:ih,scale={W}:{W*3//4}:flags=lanczos"          # 4:3 center
    bg = (f"scale={W//4}:{H//4}:force_original_aspect_ratio=increase,crop={W//4}:{H//4},"
          f"boxblur=10:2,scale={W}:{H},eq=brightness=-0.08")
    return (f"[{inp}]split=2[{out}bgi][{out}fgi];[{out}bgi]{bg}[{out}bg];[{out}fgi]{fg}[{out}fg];"
            f"[{out}bg][{out}fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


def _horizontal_chain(inp: str, out: str, W: int, H: int, cam: str | None = None) -> str:
    return (f"[{inp}]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[{out}]")


def cam_settings(opts: "RenderOptions", vertical: bool) -> tuple[str, dict]:
    """(mode, box). mode: 'box' (anywhere, any size), 'stack' (Shorts: webcam on top, gameplay below), 'off'."""
    c = opts.extra.get("cam") or {}
    if opts.layout == "facecam" and vertical:
        return "stack", {}
    mode = c.get("mode", "box")
    if mode == "stack" and not vertical:
        mode = "box"
    box = c.get("box_v" if vertical else "box_h") or c.get("box") or default_cam_box(vertical)
    return mode, box


def _zoom_expr(effects: list, piece_start: float) -> str | None:
    """Punch-in zoom curve for zoompan: snap in over 0.1 s, hold 0.45 s, ease out 0.4 s."""
    terms = []
    for e in effects:
        if e.get("type") != "zoom":
            continue
        T = e["t"] - piece_start
        A = e.get("amount", 0.3)
        terms.append(f"{A}*(between(it,{T-0.1:.3f},{T:.3f})*(it-{T-0.1:.3f})/0.1"
                     f"+between(it,{T+0.0001:.4f},{T+0.45:.3f})"
                     f"+between(it,{T+0.4501:.4f},{T+0.85:.3f})*(1-(it-{T+0.45:.3f})/0.4))")
    if not terms:
        return None
    return "1+" + "+".join(terms)


def _wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text.strip(), width=width)) or " "


def _inter_venc(encoder: tuple[str, str], fps: int) -> list[str]:
    """Near-lossless intermediate; the final pass does the real (upload) encode."""
    vendor, enc = encoder
    if vendor == "nvenc":
        return ["-c:v", enc, "-preset", "p4", "-rc", "constqp", "-qp", "14", "-g", str(fps)]
    if vendor == "x264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-g", str(fps)]
    return ["-c:v", enc, "-b:v", "60M", "-g", str(fps)]


def _transition_of(p: Piece) -> str:
    return next((e.get("name", "") for e in p.effects if e.get("type") == "transition"), "")


def _crossfade_cmd(a: Path, a_dur: float, pre: Path, b: Path, b_dur: float, x: float, out: Path,
                   encoder: tuple[str, str], fps: int) -> list[str]:
    """Blends the end of part A into part B. B's pre-roll (the x seconds just before it in its clip) plays
    under A's last x seconds, so B itself still starts exactly where it sits on the timeline and the
    video keeps its length (= nothing on the timeline moves)."""
    off = max(0.0, a_dur - x)
    f = (f"[1:v][2:v]concat=n=2:v=1:a=0,settb=AVTB[bv];[0:v]settb=AVTB[av];"
         f"[av][bv]xfade=transition=fade:duration={x:.3f}:offset={off:.3f},format=yuv420p[v];"
         f"[1:a][2:a]concat=n=2:v=0:a=1[ba];"
         f"[0:a]afade=t=out:st={off:.3f}:d={x:.3f}[aa];"
         f"[ba]afade=t=in:d={x:.3f},adelay={int(off * 1000)}:all=1[bb];"
         f"[aa][bb]amix=inputs=2:duration=longest:normalize=0,atrim=0:{a_dur + b_dur:.3f}[a]")
    return ([ff.ffmpeg_bin(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-v", "error",
             "-i", str(a), "-i", str(pre), "-i", str(b), "-filter_complex", f, "-map", "[v]", "-map", "[a]"]
            + _inter_venc(encoder, fps) +
            ["-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", "-t", f"{a_dur + b_dur:.3f}", str(out)])


def _next_transition(pieces: list[Piece], i: int) -> str:
    if i + 1 >= len(pieces):
        return ""
    return next((e.get("name", "") for e in pieces[i + 1].effects if e.get("type") == "transition"), "")


def _piece_cmd(i: int, p: Piece, preset: Preset, opts: RenderOptions, fps: int, out: Path,
               encoder: tuple[str, str], next_trans: str = "") -> list[str]:
    """One piece -> an intermediate file at final size/fps with its own layout, zoom, speed and audio."""
    W, H = preset.w, preset.h
    info = ff.probe(p.path)
    vstreams = [st for st in info.get("streams", []) if st.get("codec_type") == "video"]
    n_audio = sum(1 for st in info.get("streams", []) if st.get("codec_type") == "audio")
    cam = "0:v:1" if (opts.facecam and len(vstreams) >= 2) else None
    pre: list[str] = []
    if cam and cam_settings(opts, preset.vertical)[0] == "off":
        cam = None
    if cam and opts.extra.get("reaction_zoom", True):
        # you laugh / yell -> the facecam punches in on your face
        from .highlights import mic_peaks
        peaks = mic_peaks(p.path, p.start, p.end)
        zc = _zoom_expr([{"type": "zoom", "t": p.start + t, "amount": 0.4} for t in peaks], p.start)
        if zc:
            pre.append(f"[0:v:1]scale=1280:720,zoompan=z='{zc}':x='iw/2-(iw/zoom/2)':y='ih/3-(ih/zoom/3)':"
                       f"d=1:s=1280x720:fps=30[camz]")
            cam = "camz"
    filters = list(pre)
    src = "0:v:0"
    zexpr = _zoom_expr(p.effects, p.start)
    if zexpr:  # zoom on the gameplay before the layout, at 1080p for speed
        filters.append(f"[{src}]scale=1920:1080,zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':"
                       f"y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={fps}[z]")
        src = "z"
    alt_in: list[str] = []
    if p.alt_path:  # Friends' POV: both angles at once
        a0 = max(0.0, p.start + p.alt_offset)
        alt_in = ["-ss", f"{a0:.3f}", "-t", f"{p.dur:.3f}", "-i", p.alt_path]
        lab = []
        for i, (name, which) in enumerate(((p.label, "m"), (p.alt_label, "a"))):
            if name and (out.parent / "font.ttf").exists():
                (out.parent / f"lab_{which}.txt").write_text(name.upper(), encoding="utf-8")
                lab.append((which, f",drawtext=fontfile=font.ttf:textfile=lab_{which}.txt:fontsize=44:"
                                   f"fontcolor=white:borderw=4:bordercolor=black:x=30:y=24"))
        labs = dict(lab)
        if preset.vertical:
            hh = H // 2
            filters.append(f"[{src}]scale=-2:{hh}:flags=lanczos,crop={W}:{hh},setsar=1{labs.get('m', '')}[lm];"
                           f"[1:v:0]scale=-2:{hh}:flags=lanczos,crop={W}:{hh},setsar=1{labs.get('a', '')}[la];"
                           f"[lm][la]vstack=inputs=2[l]")
        else:
            hw = W // 2
            filters.append(f"[{src}]scale=-2:{H}:flags=lanczos,crop={hw}:{H},setsar=1{labs.get('m', '')}[lm];"
                           f"[1:v:0]scale=-2:{H}:flags=lanczos,crop={hw}:{H},setsar=1{labs.get('a', '')}[la];"
                           f"[lm][la]hstack=inputs=2[l]")
    else:
        mode, box = cam_settings(opts, preset.vertical)
        layout = opts.layout if opts.layout != "facecam_pip" else "blur"
        if preset.vertical and cam and mode == "stack":
            filters.append(_vertical_chain(src, "l", "facecam", W, H, cam))
        else:
            if layout == "facecam":
                layout = "crop"
            xexpr = None
            if layout == "smart" and preset.vertical:
                try:
                    from .analysis import action_track, crop_x_expr
                    xexpr = crop_x_expr(action_track(p.path, round(p.start, 2), round(p.end, 2)))
                except Exception as e:
                    print("smart framing:", e)
            base = (_vertical_chain(src, "lb", layout, W, H, xexpr=xexpr) if preset.vertical
                    else _horizontal_chain(src, "lb", W, H))
            if cam and mode == "box":
                filters.append(base + ";" + _cam_overlay("lb", cam, "l", W, H, box))
            else:
                filters.append(base.rsplit("[lb]", 1)[0] + "[l]")
    speed = max(0.1, p.speed)
    sp = f"setpts=(PTS-STARTPTS)/{speed:.4f}," if abs(speed - 1) > 1e-3 else "setpts=PTS-STARTPTS,"
    gif_in: list[str] = []
    gifs = [e for e in p.effects if e.get("type") == "gif" and e.get("path") and Path(e["path"]).exists()]
    if gifs:
        filters.append(f"[l]{sp}fps={fps}[gl0]")
        first = 1 + (1 if alt_in else 0)
        for k, g in enumerate(gifs):
            gif_in += _gif_input(g["path"])
            T = (g["t"] - p.start) / speed               # when it appears, in this piece's output time
            D = float(g.get("dur", 2.0))
            x, y, gw, _ = _gif_rect(g, W, H)
            filters.append(f"[{first + k}:v]fps={fps},scale={gw}:-2:flags=lanczos,format=rgba,"
                           f"setpts=PTS-STARTPTS+({T:.3f})/TB[gi{k}];"
                           f"[gl{k}][gi{k}]overlay={x}:{y}:eof_action=pass:"
                           f"enable='between(t,{T:.3f},{T + D:.3f})'[gl{k + 1}]")
        filters.append(f"[gl{len(gifs)}]format=yuv420p[v]")
    else:
        filters.append(f"[l]{sp}fps={fps},format=yuv420p[v]")
    trans = next((e.get("name") for e in p.effects if e.get("type") == "transition"), "")
    if trans == "crossfade":
        trans = ""
    if next_trans == "crossfade":
        next_trans = ""
    tf = TRANSITIONS.get(trans, ("", ""))[1].format(W=W, H=H, fps=fps) if trans else ""
    if tf:   # the clip comes in with a flash / zoom / whip / glitch
        filters[-1] = filters[-1][:filters[-1].rfind("[v]")] + "[vt]"
        filters.append(f"[vt]{tf}[v]")
    dur_out = p.out_dur
    out_tr = TRANSITIONS_OUT.get(next_trans)
    if out_tr and dur_out > out_tr[0] * 3:   # ...and leaves with the start of the next clip's transition
        filters[-1] = filters[-1][:filters[-1].rfind("[v]")] + "[vo]"
        filters.append(f"[vo]{out_tr[1].format(st=f'{dur_out - out_tr[0]:.3f}')}[v]")

    fd = opts.fade_ms / 1000
    fd_in = max(fd, TRANSITION_AUDIO[1]) if trans and dur_out > 1 else fd
    fd_out = max(fd, TRANSITION_AUDIO[0]) if next_trans and dur_out > 1 else fd
    fades = ([f"afade=t=in:d={fd_in}"] if p.fade_in else []) + \
        ([f"afade=t=out:st={max(0, dur_out - fd_out):.3f}:d={fd_out}"] if p.fade_out else [])
    fade = ",".join(fades) or "anull"
    norm = "aresample=48000,aformat=channel_layouts=stereo"
    if abs(speed - 1) < 1e-3:
        tempo = ""
    elif speed < 1:  # slow-mo audio drops in pitch (cinematic)
        tempo = f"asetrate={int(48000*speed)},aresample=48000,"
    else:            # fast-forward keeps pitch
        t_left, chain = speed, []
        while t_left > 2.0:
            chain.append("atempo=2.0")
            t_left /= 2.0
        chain.append(f"atempo={t_left:.4f}")
        tempo = ",".join(chain) + ","
    if n_audio >= 2 and p.mic_volume > 0.001:
        # game + mic tracks. Your voice gets a clean-up (rumble cut, noise gate, levelled so quiet and loud
        # speech come out equally clear) and the game dips a little while you talk, so you're never buried.
        filters.append(f"[0:a:1]{norm},highpass=f=90,agate=threshold=0.004:ratio=6:attack=3:release=350,"
                       f"dynaudnorm=f=200:g=11:p=0.7:m=12,volume={p.mic_volume * 1.2:.3f},asplit=2[m][msc];"
                       f"[0:a:0]{norm},volume={p.game_volume}[g0];"
                       f"[g0][msc]sidechaincompress=threshold=0.06:ratio=3:attack=15:release=450:makeup=1[g];"
                       f"[g][m]amix=inputs=2:duration=first:normalize=0,{tempo}{fade},apad[a]")
    elif n_audio >= 1:   # game sound only (or the mic muted in the Studio)
        filters.append(f"[0:a:0]{norm},volume={p.game_volume},{tempo}{fade},apad[a]")
    else:
        filters.append("anullsrc=r=48000:cl=stereo[a]")
    venc = _inter_venc(encoder, fps)
    return ([ff.ffmpeg_bin(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-v", "error",
             "-ss", f"{p.start:.3f}", "-t", f"{p.dur:.3f}", "-i", p.path] + alt_in + gif_in +
            ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]"] + venc +
            ["-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", "-t", f"{dur_out:.3f}", str(out)])


def build_final(parts: list[Path], pieces: list[Piece], preset: Preset, opts: RenderOptions, out: Path,
                workdir: Path, encoder: tuple[str, str], ass_file: str | None = None) -> tuple[list[str], float]:
    """Joins the intermediates and adds everything that spans the whole video."""
    fps = opts.fps or preset.fps
    footage = sum(p.out_dur for p in pieces)
    total = footage
    if opts.voiceover:
        total = max(total, ff.duration(opts.voiceover))
    if preset.max_seconds and total > preset.max_seconds:
        total = preset.max_seconds
    lst = workdir / "parts.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    args = [ff.ffmpeg_bin(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", lst.name]
    next_input = 1
    if opts.extra.get("loop") and footage > 2 and not opts.voiceover:
        # seamless loop: the last half second dissolves into the first frame, so the replay feels continuous
        X = 0.45
        filters = [f"[0:v]split=2[vm][vh];[vh]trim=0:0.5,setpts=PTS-STARTPTS[vhead];"
                   f"[vm][vhead]xfade=transition=fade:duration={X}:offset={footage - X:.3f}[vc]",
                   # audio: fade the end out while the first half second fades in on top (a crossfade that
                   # doesn't stall the rest of the mix the way acrossfade does)
                   f"[0:a]asplit=2[am][ah];[ah]atrim=0:0.5,asetpts=PTS-STARTPTS,afade=t=in:d={X},"
                   f"adelay={int((footage - X) * 1000)}:all=1[ahead];"
                   f"[am]afade=t=out:st={footage - X:.3f}:d={X}[amf];"
                   f"[amf][ahead]amix=inputs=2:duration=longest:normalize=0,"
                   f"atrim=0:{footage - X + 0.5:.3f}[ac]"]
        footage += 0.5 - X
        total = max(total, footage)
        if not opts.extra.get("hook_fixed") and total <= LOOP_HOOK_MAX:
            opts.hook_seconds = max(opts.hook_seconds, total + 1)  # same text on both sides of the seam
    else:
        filters = ["[0:v]null[vc]", "[0:a]anull[ac]"]
    vlast, alast = "vc", "ac"
    tiktok = opts.extra.get("platform") == "tiktok"

    sfx_events: list[tuple[float, str]] = []
    out_t = 0.0
    for p in pieces:
        for e in p.effects:
            if e.get("type") == "sfx" and p.start <= e["t"] <= p.end:
                sfx_events.append((out_t + (e["t"] - p.start) / max(0.1, p.speed), e.get("name", "boom")))
        out_t += p.out_dur

    if opts.voiceover and total > footage + 0.05:  # narration longer than the footage: hold the last frame
        filters.append(f"[{vlast}]tpad=stop_mode=clone:stop_duration={total - footage + 0.1:.3f}[vp];"
                       f"[{alast}]apad[ap]")
        vlast, alast = "vp", "ap"
    font = ff.windows_font()
    if font:
        shutil.copyfile(font, workdir / "font.ttf")
    # blur boxes: hide chat, names, pop-ups (x, y, w, h are fractions of the frame)
    W, H = preset.w, preset.h
    for k, b in enumerate(opts.extra.get("blurs") or []):
        bw, bh = max(8, int(W * float(b.get("w", 0.3))) // 2 * 2), max(8, int(H * float(b.get("h", 0.1))) // 2 * 2)
        bx = int(max(0, min(W - bw, float(b.get("x", 0)) * W)))
        by = int(max(0, min(H - bh, float(b.get("y", 0)) * H)))
        a0, a1 = float(b["t"]), float(b["t"]) + float(b.get("dur", 2.0))
        filters.append(f"[{vlast}]split=2[bm{k}][bs{k}];[bs{k}]crop={bw}:{bh}:{bx}:{by},"
                       f"boxblur={max(6, min(bw, bh) // 8)}:4[bb{k}];"
                       f"[bm{k}][bb{k}]overlay={bx}:{by}:enable='between(t,{a0:.3f},{a1:.3f})'[bo{k}]")
        vlast = f"bo{k}"
    mutes = opts.extra.get("mutes") or []
    if mutes:   # silence (or bleep) the game + mic sound in these stretches
        cond = "+".join(f"between(t,{float(m['t']):.3f},{float(m['t']) + float(m.get('dur', 0.5)):.3f})"
                        for m in mutes)
        filters.append(f"[{alast}]volume=0:enable='{cond}'[amu]")
        alast = "amu"
        beeps = [m for m in mutes if m.get("bleep", True)]
        if beeps:
            bc = "+".join(f"between(t,{float(m['t']):.3f},{float(m['t']) + float(m.get('dur', 0.5)):.3f})"
                          for m in beeps)
            filters.append(f"sine=f=1000:sample_rate=48000,aformat=channel_layouts=stereo,"
                           f"volume=0.25,volume=0:enable='not({bc})',atrim=0:{total + 1:.3f}[beep];"
                           f"[{alast}][beep]amix=inputs=2:duration=first:normalize=0[abp]")
            alast = "abp"
    if opts.hook_text and font:
        (workdir / "hook.txt").write_text(_wrap(opts.hook_text.upper(), 16 if preset.vertical else 30),
                                          encoding="utf-8")
        size = 84 if preset.vertical else 72
        y = ("h*0.36" if opts.layout == "facecam" else "h*0.10") if preset.vertical else "h*0.08"
        if tiktok and preset.vertical and opts.layout != "facecam":
            y = "h*0.14"  # below TikTok's "Following | For You" tabs
        y = opts.extra.get("hook_y", y)
        x = "(w-text_w)/2"
        pos = opts.extra.get("hook_pos")          # moved by hand in the Studio: centre of the text block
        if pos:
            x = f"max(10\\,min(w-text_w-10\\,w*{float(pos.get('cx', 0.5)):.4f}-text_w/2))"
            y = f"max(10\\,min(h-text_h-10\\,h*{float(pos.get('cy', 0.15)):.4f}-text_h/2))"
        size = int(size * float(opts.extra.get("hook_scale", 1.0)))
        t0 = float(opts.extra.get("hook_start", 0.0))
        filters.append(
            f"[{vlast}]drawtext=fontfile=font.ttf:textfile=hook.txt:fontsize={size}:fontcolor=white:"
            f"borderw=7:bordercolor=black:line_spacing=10:text_align=C:x={x}:y={y}:"
            f"enable='between(t,{t0:.3f},{t0 + opts.hook_seconds:.3f})'[vh]")
        vlast = "vh"
    # your own text anywhere: {"t", "dur", "text", "cx", "cy", "scale", "style"}
    for k, tx in enumerate(opts.extra.get("texts") or []):
        if not font or not str(tx.get("text", "")).strip():
            continue
        (workdir / f"tx{k}.txt").write_text(_wrap(str(tx["text"]), 18 if preset.vertical else 34), encoding="utf-8")
        base = 76 if preset.vertical else 64
        size = int(base * float(tx.get("scale", 1.0)))
        st = TEXT_STYLES.get(tx.get("style", "white"), TEXT_STYLES["white"])
        cx, cy = float(tx.get("cx", 0.5)), float(tx.get("cy", 0.5))
        a0, a1 = float(tx.get("t", 0)), float(tx.get("t", 0)) + float(tx.get("dur", 2.0))
        pop = f"if(lt(t-{a0:.3f},0.12),{size}*(0.6+3.3*(t-{a0:.3f})),{size})" if tx.get("pop", True) else str(size)
        filters.append(
            f"[{vlast}]drawtext=fontfile=font.ttf:textfile=tx{k}.txt:fontsize='{pop}':{st}:line_spacing=8:"
            f"text_align=C:x='max(10,min(w-text_w-10,w*{cx:.4f}-text_w/2))':"
            f"y='max(10,min(h-text_h-10,h*{cy:.4f}-text_h/2))':enable='between(t,{a0:.3f},{a1:.3f})'[tx{k}]")
        vlast = f"tx{k}"
    if opts.watermark and font:
        (workdir / "wm.txt").write_text(opts.watermark, encoding="utf-8")
        y = ("h*0.29" if opts.layout == "facecam" else "h*0.235") if preset.vertical else "h-th-40"
        filters.append(
            f"[{vlast}]drawtext=fontfile=font.ttf:textfile=wm.txt:fontsize={44 if preset.vertical else 34}:"
            f"fontcolor=white@0.75:borderw=3:bordercolor=black@0.6:x=(w-text_w)/2:y={y}[vw]")
        vlast = "vw"
    if ass_file:
        filters.append(f"[{vlast}]subtitles=filename={ass_file}:fontsdir=.[vs]")
        vlast = "vs"

    if sfx_events:  # sound effects on the output timeline
        from .sfx import all_sfx
        lib = all_sfx(opts.sfx_dir)
        labels = []
        for k, (t, name) in enumerate(sfx_events):
            f = lib.get(name)
            if not f:
                continue
            args += ["-i", str(f)]
            ms = max(0, int(t * 1000) - 30)
            filters.append(f"[{next_input}:a:0]aresample=48000,aformat=channel_layouts=stereo,volume=0.9,"
                           f"adelay={ms}|{ms}[sx{k}]")
            labels.append(f"[sx{k}]")
            next_input += 1
        if labels:
            filters.append(f"[{alast}]{''.join(labels)}amix=inputs={len(labels)+1}:duration=first:normalize=0[asx]")
            alast = "asx"
    caster = [c for c in (opts.extra.get("caster_lines") or []) if c.get("wav") and Path(c["wav"]).exists()]
    if caster:
        # AI caster: each line is placed where it sits on the timeline. The game keeps its full volume and only
        # dips (smoothly, as much as you chose) while he's actually talking.
        cvol = max(0.0, float(opts.extra.get("caster_volume", 1.0)))
        labels = []
        for k, c in enumerate(caster):
            args += ["-i", c["wav"]]
            ms = max(0, int(float(c["t"]) * 1000))
            filters.append(f"[{next_input}:a:0]aresample=48000,aformat=channel_layouts=stereo,highpass=f=90,"
                           f"acompressor=threshold=-18dB:ratio=4,volume={1.6 * cvol:.3f},adelay={ms}|{ms}[cl{k}]")
            labels.append(f"[cl{k}]")
            next_input += 1
        duck = max(0.0, min(1.0, float(opts.extra.get("caster_duck", 0.7))))
        spans = [(float(c["t"]), float(c["t"]) + float(c.get("dur", 1.5))) for c in caster]
        env = _duck_expr(spans, duck) if cvol > 0 else ""
        bed = f"volume='{env}':eval=frame" if env else "anull"
        mix = "".join(labels)
        filters.append(f"{mix}amix=inputs={len(labels)}:duration=longest:normalize=0[cmix];"
                       f"[{alast}]{bed}[bed];[bed][cmix]amix=inputs=2:duration=first:normalize=0[avo]")
        alast = "avo"
    if opts.voiceover:  # script mode: game sound ducks under your voice
        args += ["-i", opts.voiceover]
        vi = next_input
        next_input += 1
        filters.append(f"[{vi}:a:0]aresample=48000,aformat=channel_layouts=stereo,asplit=2[vo1][vo2];"
                       f"[{alast}]volume={opts.game_bed_volume}[bed];"
                       f"[bed][vo1]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=350[duck];"
                       f"[duck][vo2]amix=inputs=2:duration=longest:normalize=0[avo]")
        alast = "avo"
    if opts.music:
        mi = next_input
        next_input += 1
        args += ["-stream_loop", "-1", "-i", opts.music]
        filters.append(f"[{mi}:a:0]aresample=48000,aformat=channel_layouts=stereo,volume={opts.music_volume},"
                       f"afade=t=out:st={max(0, total-1.5):.3f}:d=1.5[mus];"
                       f"[{alast}][mus]amix=inputs=2:duration=first:normalize=0[am]")
        alast = "am"
    if opts.loudnorm:  # -14 LUFS: what YouTube and TikTok normalize to
        filters.append(f"[{alast}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[an]")
        alast = "an"

    vendor, enc = encoder
    bitrate = preset.bitrate_m
    if preset.max_mb and total > 0:
        budget = (preset.max_mb * 8 / total) - 0.35  # minus audio
        bitrate = max(1.5, min(bitrate, budget * 0.92))
    args += ["-filter_complex", ";".join(filters), "-map", f"[{vlast}]", "-map", f"[{alast}]"]
    args += ff.export_video_args(vendor, enc, bitrate, fps)
    args += ["-c:a", "aac", "-b:a", "320k" if not preset.max_mb or preset.max_mb > 20 else "160k",
             "-ar", "48000", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]
    return args, total


def render(pieces: list[Piece], preset_key: str, opts: RenderOptions, out: Path,
           progress: Callable[[float, str], None] | None = None,
           cancel: Callable[[], bool] | None = None) -> Path:
    """Blocking render, one piece at a time (memory stays flat for long edits). progress(fraction, msg)."""
    preset = PRESETS[preset_key]
    fps = opts.fps or preset.fps
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clipforge_"))
    pieces = expand_speed(pieces)
    if preset.vertical and any(p.alt_path for p in pieces) and "hook_y" not in opts.extra:
        opts.extra["hook_y"] = "h*0.5-text_h/2"   # split-screen: hook sits on the seam
    if preset.max_seconds:  # drop what would be cut anyway
        kept, acc = [], 0.0
        for p in pieces:
            if acc >= preset.max_seconds:
                break
            kept.append(p)
            acc += p.out_dur
        pieces = kept
    try:
        ass = None
        if opts.timed_text:
            from .captions import ass_from_lines
            ass = ass_from_lines(opts.timed_text, work, vertical=preset.vertical)
        elif opts.captions:
            if progress:
                progress(0.0, "Transcribing speech for captions…")
            from .captions import caster_words, make_ass
            ass = make_ass(pieces, work, vertical=preset.vertical, style=opts.extra.get("caption_style", "pop3"),
                           extra_words=caster_words(opts.extra.get("caster_lines") or []),
                           cy=opts.extra.get("caption_y"))
        if ass and opts.extra.get("platform") == "tiktok":
            f = work / ass
            f.write_text(_tiktok_safe_ass(f.read_text(encoding="utf-8")), encoding="utf-8")
        encoder = ff.pick_encoder("h264")
        font = ff.windows_font()
        if font:
            shutil.copyfile(font, work / "font.ttf")
        total_out = sum(p.out_dur for p in pieces) or 1.0
        # pieces take ~60% of the time, the final pass ~40%
        parts, done = [], 0.0
        part_first: list[int] = []           # index of the first piece in each part (crossfades merge parts)
        for i, p in enumerate(pieces):
            part = work / f"part_{i:03d}.mkv"
            frac0, span = 0.6 * done / total_out, 0.6 * p.out_dur / total_out

            def prog(f, m, frac0=frac0, span=span, i=i):
                if progress:
                    progress(frac0 + span * f, f"Cutting piece {i+1}/{len(pieces)}…")
            ok, err = _run_with_progress(_piece_cmd(i, p, preset, opts, fps, part, encoder, _next_transition(pieces, i)), p.out_dur,
                                         work, prog, cancel)
            if not ok and encoder[0] != "x264":
                encoder = ("x264", "libx264")  # GPU encoder hiccup -> software fallback
                ok, err = _run_with_progress(_piece_cmd(i, p, preset, opts, fps, part, encoder, _next_transition(pieces, i)), p.out_dur,
                                             work, prog, cancel)
            if not ok:
                raise RuntimeError(f"piece {i+1}: " + (err[-1000:] or "failed"))
            if (_transition_of(p) == "crossfade" and parts and p.start > 0.1 and p.out_dur > XFADE * 2
                    and pieces[i - 1].out_dur > XFADE * 2):
                x = min(XFADE, p.start)
                pre_p = Piece(p.path, p.start - x, p.start, p.mic_volume, p.game_volume, 1.0, [],
                              p.alt_path, p.alt_offset, p.label, p.alt_label, fade_in=False, fade_out=False)
                pre = work / f"pre_{i:03d}.mkv"
                merged = work / f"xf_{i:03d}.mkv"
                a_dur = sum(q.out_dur for q in pieces[part_first[-1]:i])
                ok, err = _run_with_progress(_piece_cmd(i, pre_p, preset, opts, fps, pre, encoder), x, work,
                                             lambda f, m: None, cancel)
                if ok:
                    ok, err = _run_with_progress(_crossfade_cmd(parts[-1], a_dur, pre, part, p.out_dur, x, merged,
                                                                encoder, fps), a_dur + p.out_dur, work,
                                                 lambda f, m: None, cancel)
                if ok:
                    parts[-1] = merged                 # A+B are one part now (same total length)
                    done += p.out_dur
                    continue
                print("crossfade skipped:", err[-300:])
            parts.append(part)
            part_first.append(i)
            done += p.out_dur

        def fprog(f, m):
            if progress:
                progress(0.6 + 0.4 * f, "Finishing (text, sound, loudness)…")
        cmd, total = build_final(parts, pieces, preset, opts, out, work, encoder, ass)
        ok, err = _run_with_progress(cmd, total, work, fprog, cancel)
        if not ok and encoder[0] != "x264":
            cmd, total = build_final(parts, pieces, preset, opts, out, work, ("x264", "libx264"), ass)
            ok, err = _run_with_progress(cmd, total, work, fprog, cancel)
        if not ok:
            raise RuntimeError(err[-1200:] or "render failed")
        if progress:
            progress(1.0, "Done")
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _tiktok_safe_ass(text: str) -> str:
    """TikTok's like/comment/share buttons sit on the right: keep captions out from under them."""
    out = []
    for line in text.splitlines():
        if line.startswith("Style: Pop,") or line.startswith("Style: Line,"):
            parts = line.split(",")
            parts[-3] = "170"                                   # MarginR
            parts[-2] = str(int(int(parts[-2]) * 1.15))         # a bit higher too
            line = ",".join(parts)
        out.append(line)
    return "\n".join(out)


def _run_with_progress(cmd, total, cwd, progress, cancel) -> tuple[bool, str]:
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                            **ff.popen_flags("low"))
    import threading
    err_lines: list[str] = []
    t = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
    t.start()
    for line in proc.stdout:
        if cancel and cancel():
            proc.kill()
            return False, "cancelled"
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
                if progress and total:
                    progress(min(0.99, us / 1e6 / total), "Rendering…")
            except ValueError:
                pass
    proc.wait()
    t.join(2)
    return proc.returncode == 0, "".join(err_lines)


def quick_trim(src: Path, start: float, end: float, out: Path) -> Path:
    """Lossless trim, takes a second. Cuts land on the nearest keyframe (every 2 s)."""
    r = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                "-i", str(src), "-map", "0", "-c", "copy", "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero", str(out)], timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return out


def post_text(game: str, hook: str, vertical: bool) -> str:
    """Ready-to-paste title, description and hashtags for the upload."""
    tag = "".join(ch for ch in game.title() if ch.isalnum())
    tags = ["#gaming", f"#{tag}" if tag else "", "#shorts" if vertical else "", "#fyp" if vertical else "",
            "#funny", "#gamingclips"]
    title = (hook or f"{game} moments").strip()
    return (f"TITLE:\n{title[:90]}\n\nDESCRIPTION:\n{title} 🎮\nFollow for more {game} clips!\n\n"
            f"HASHTAGS:\n{' '.join(t for t in tags if t)}\n")
