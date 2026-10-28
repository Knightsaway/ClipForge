"""Auto Short / Montage: a few small, fitting GIF stickers on the big moments.

What happened decides the mood (kill streak → "let's go", a single kill → "too easy", you laughing on the
mic → "lol", anything else loud → "mind blown"). GIFs come from your own GIF folder first (a file whose
name contains one of the mood words, e.g. "lets go.gif", "lol cat.gif"), otherwise from GIPHY stickers if
you've saved a GIPHY key. They're kept small, sit in the corners clear of your webcam and hook text, show
for under 2 seconds, and there are never more than a few per video.
"""
from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from pathlib import Path

from .config import app_data_dir

MOODS = {
    "streak": {"words": ["lets go", "letsgo", "hype", "fire", "insane", "goat"],
               "search": ["lets go", "hype", "on fire", "insane", "goat"]},
    "kill": {"words": ["ez", "easy", "gg", "rekt", "bye", "cool"],
             "search": ["too easy", "get rekt", "bye bye", "deal with it", "gg"]},
    "laugh": {"words": ["lol", "laugh", "lmao", "funny", "haha"],
              "search": ["lol", "laughing", "crying laughing", "lmao"]},
    "hype": {"words": ["wow", "omg", "mind", "shock", "what", "bruh"],
             "search": ["mind blown", "omg", "wow", "shocked", "what"]},
}
EXT = (".gif", ".webp", ".png", ".webm", ".mp4")


def _gif_dir(s) -> Path:
    d = Path(s.gif_dir) if getattr(s, "gif_dir", "") else app_data_dir() / "gifs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _local(s, mood: str) -> list[Path]:
    words = MOODS[mood]["words"]
    out = []
    for p in _gif_dir(s).rglob("*"):
        if p.suffix.lower() in EXT:
            name = p.stem.lower().replace("_", " ").replace("-", " ")
            if any(w in name for w in words):
                out.append(p)
    return out


def _giphy(s, mood: str) -> Path | None:
    """One sticker for this mood from GIPHY (cached, so the same few aren't downloaded again and again)."""
    key = getattr(s, "giphy_key", "")
    if not key:
        return None
    cache_dir = _gif_dir(s) / "auto"
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx_file = cache_dir / "index.json"
    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        idx = {}
    have = [p for p in (cache_dir / f for f in idx.get(mood, [])) if p.exists()]
    if len(have) >= 6 and random.random() < 0.7:     # mostly reuse, sometimes fetch something new
        return random.choice(have)
    term = random.choice(MOODS[mood]["search"])
    url = "https://api.giphy.com/v1/stickers/search?" + urllib.parse.urlencode(
        {"api_key": key, "q": term, "limit": 15, "rating": "pg-13"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ClipForge"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8")).get("data", [])
        if not data:
            return random.choice(have) if have else None
        pick = random.choice(data[:10])
        img = (pick.get("images", {}).get("fixed_height") or pick.get("images", {}).get("original") or {})
        if not img.get("url"):
            return None
        dst = cache_dir / f"{mood} {pick.get('id', 'x')}.gif"
        if not dst.exists():
            req = urllib.request.Request(img["url"], headers={"User-Agent": "ClipForge"})
            dst.write_bytes(urllib.request.urlopen(req, timeout=20).read())
        idx.setdefault(mood, [])
        if dst.name not in idx[mood]:
            idx[mood].append(dst.name)
        idx_file.write_text(json.dumps(idx), encoding="utf-8")
        return dst
    except Exception as e:
        print("auto gif:", e)
        return random.choice(have) if have else None


def gif_ratio(path: str) -> float:
    from . import ff
    for st in ff.probe(path).get("streams", []):
        if st.get("codec_type") == "video" and st.get("width"):
            return round(st["height"] / st["width"], 4)
    return 1.0


def available(s) -> bool:
    return bool(getattr(s, "giphy_key", "")) or any(_local(s, m) for m in MOODS)


def _mood(clip, t: float, effects: list, mic_hits: list[float]) -> str:
    from .vision import clusters
    near = [c for c in clusters(getattr(clip, "events", []) or []) if abs(c["t"] - t) < 2.0]
    if near and near[0]["count"] >= 2:
        return "streak"
    if any(abs(e["t"] - t) < 2.0 for e in getattr(clip, "events", []) or []):
        return "kill"
    if any(abs(m - t) < 2.5 for m in mic_hits):
        return "laugh"
    return "hype"


def _overlaps(a, b) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def spots(vertical: bool, cam_box: dict | None, has_cam: bool, w: float) -> list[tuple[float, float]]:
    """Corner spots (top-left x, y as fractions) that stay clear of the webcam box and the hook text."""
    if vertical:
        cand = [(0.05, 0.22), (0.95 - w, 0.22), (0.05, 0.56), (0.95 - w, 0.56)]
        aspect = 1080 / 1920
    else:
        cand = [(0.03, 0.08), (0.97 - w, 0.08), (0.03, 0.62), (0.97 - w, 0.62)]
        aspect = 1920 / 1080
    hgt = w * aspect           # treat the sticker as square-ish when checking for overlaps
    if has_cam and cam_box:
        from .editor import CAM_ASPECTS
        cw = float(cam_box.get("w", 0.3))
        cbox = (float(cam_box.get("x", 0)), float(cam_box.get("y", 0)), cw,
                cw * aspect / CAM_ASPECTS.get(cam_box.get("aspect", "16:9"), 16 / 9))
        cand = [c for c in cand if not _overlaps((c[0], c[1], w, hgt), cbox)] or cand
    return cand


def add_gifs(s, pieces: list[dict], vertical: bool, montage: bool = False, progress=None) -> int:
    """Adds GIF effects to Studio pieces (in place) on their key moments. Returns how many were added."""
    from . import timeline_model as tm
    from .editor import default_cam_box
    total = tm.total(pieces)
    limit = min(6, len(pieces)) if montage else max(1, min(3, int(total / 10)))
    w = 0.26 if vertical else 0.16
    cam = dict(getattr(s, "cam_layout", {}) or {})
    cam_box = cam.get("box_v" if vertical else "box_h") or default_cam_box(vertical)
    added, last_out, spot_i = 0, -99.0, 0
    lay = tm.layout(pieces)
    # strongest moments first: zooms the edit already put on kills / markers / loud moments
    moments = []
    for i, pc in enumerate(pieces):
        keys = sorted({round(e["t"], 2) for e in pc.get("effects", []) if e.get("type") == "zoom"})
        for k in keys:
            moments.append((i, k))
    for i, k in moments:
        if added >= limit:
            break
        pc = pieces[i]
        if any(e.get("type") == "gif" for e in pc.get("effects", [])) and montage:
            continue
        out_t = lay[i][0] + tm.src_to_out(pc, k)
        if out_t - last_out < 6.0 or out_t < 1.0:       # not on top of the hook, and spaced out
            continue
        if progress:
            progress(added / max(1, limit), "🎞 Picking GIFs…")
        try:
            from .highlights import mic_peaks
            mic = [pc["start"] + m for m in mic_peaks(pc["clip"].path, pc["start"], pc["end"])]
        except Exception:
            mic = []
        mood = _mood(pc["clip"], k, pc.get("effects", []), mic)
        local = _local(s, mood)
        path = random.choice(local) if local else _giphy(s, mood)
        if not path:
            continue
        has_cam = getattr(pc["clip"], "has_cam", 0) > 0 and cam.get("mode", "box") == "box"
        cands = spots(vertical, cam_box, has_cam, w)
        x, y = cands[spot_i % len(cands)]
        spot_i += 1
        t = round(min(pc["end"] - 0.4, k + 0.2), 2)
        dur = round(max(0.6, min(1.8, pc["end"] - t)), 2)
        pc.setdefault("effects", []).append({"type": "gif", "t": t, "dur": dur, "path": str(path),
                                             "ratio": gif_ratio(str(path)), "box": {"x": x, "y": y, "w": w},
                                             "auto": True, "mood": mood})
        added += 1
        last_out = out_t
    return added
