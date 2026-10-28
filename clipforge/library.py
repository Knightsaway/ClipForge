"""Clip library: plain .mp4 files in Videos/ClipForge/<Game>/ plus a small .json next
to each one. No database to corrupt - delete or move files in Explorer and the app
just picks it up."""
from __future__ import annotations

import json
import shutil
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import ff

VIDEO_EXT = {".mp4", ".mkv", ".mov"}


@dataclass
class Clip:
    path: str
    game: str = "Desktop"
    created: float = 0.0
    duration: float = 0.0
    favorite: bool = False
    highlight: bool = False
    kind: str = "Clip"
    title: str = ""
    markers: list = field(default_factory=list)   # seconds from clip start
    events: list = field(default_factory=list)    # detected kills etc: {"t", "name", "weight", "score"}
    scanned: bool = False                          # visual detectors already run on this clip
    squad: dict = field(default_factory=dict)      # {"by": who marked it, "t": wall time} for multi-angle edits
    has_cam: int = -1                              # 1 = webcam track inside, 0 = none, -1 = not checked yet
    tags: list = field(default_factory=list)       # your own labels: funny, fail, clutch…
    trim_in: float = 0.0
    trim_out: float = 0.0                          # 0 = end
    exported: list = field(default_factory=list)

    @property
    def p(self) -> Path:
        return Path(self.path)

    @property
    def sidecar(self) -> Path:
        return self.p.with_suffix(".json")

    @property
    def thumb(self) -> Path:
        return self.p.parent / ".thumbs" / (self.p.stem + ".jpg")

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        from datetime import datetime
        d = datetime.fromtimestamp(self.created or self.p.stat().st_mtime)
        return f"{self.kind} from {d.strftime('%b %d, %Y %H:%M')}"

    def save(self):
        data = asdict(self)
        data.pop("path", None)
        tmp = self.sidecar.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.sidecar)


class Library:
    def __init__(self, root: Path, extra_roots: list[str] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.extra_roots = [Path(r) for r in (extra_roots or []) if r and Path(r).exists()]
        self._lock = threading.Lock()

    def scan(self) -> list[Clip]:
        """Your ClipForge clips plus any extra folders (e.g. your old GG Moments / ShadowPlay clips)."""
        clips, seen = [], set()
        own = _key(self.root)
        for root in [self.root] + self.extra_roots:
            for p in root.rglob("*"):
                if p.suffix.lower() not in VIDEO_EXT or ".thumbs" in p.parts or ".thumbs_work" in p.parts \
                        or ".deleted" in p.parts:
                    continue
                k = _key(p)
                if k in seen:          # an extra folder can overlap the library (e.g. all of Videos)
                    continue
                if root != self.root and k.startswith(own + os.sep):
                    continue           # ClipForge's own folder is scanned once, as itself
                if k.startswith(_key(self.root / "Exports") + os.sep):
                    continue
                seen.add(k)
                try:
                    clips.append(self.load(p, self.root if k.startswith(own + os.sep) else root))
                except OSError:
                    continue
        clips.sort(key=lambda c: c.created, reverse=True)
        return clips

    def exports(self) -> list[Path]:
        """Every finished video: one folder per video (new) plus loose files from older versions."""
        d = self.root / "Exports"
        if not d.exists():
            return []
        files = [p for p in d.glob("*.mp4")] + [p for p in d.glob("*/*.mp4") if not p.parent.name.startswith(".")]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def tidy_exports(self, skip: set[str] = frozenset()) -> int:
        """Moves loose exports from older versions into one named folder per video (Short + TikTok together).
        Videos waiting in the upload queue / approval list (`skip`) stay where they are."""
        import re
        d = self.exports_dir
        groups: dict[str, list[Path]] = {}
        for p in d.glob("*.mp4"):
            if _key(p) in {_key(x) for x in skip}:
                continue
            key = re.sub(r" (Short|TikTok)( |$)", " ", p.stem).strip()
            groups.setdefault(key, []).append(p)
        moved = 0
        for files in groups.values():
            files.sort(key=lambda p: "TikTok" in p.name)          # YouTube version first
            main = files[0]
            title, game = "", main.stem.split(" ")[0]
            try:
                from .youtube import parse_post_text
                title = parse_post_text(Path(str(main) + ".txt").read_text(encoding="utf-8"))[0]
            except Exception:
                pass
            try:
                game = json.loads(Path(str(main) + ".meta.json").read_text(encoding="utf-8")).get("game") or game
            except Exception:
                pass
            if not title:
                title = re.sub(r"^\S+ ", "", main.stem)
            target = None
            for f in files:
                tiktok = "TikTok" in f.name
                label = "TikTok" if tiktok else ("YouTube Short" if "Short" in f.name else
                                                 "Discord" if "Discord" in f.name else "YouTube")
                if target is None:
                    target = package_path(d, game, title, label, main.stat().st_mtime)
                else:
                    target = sibling_path(target, label)
                try:
                    os.replace(f, target)
                    for ext in (".txt", ".meta.json", ".thumb.jpg"):
                        side = Path(str(f) + ext)
                        if side.exists():
                            os.replace(side, Path(str(target) + ext))
                            if ext == ".meta.json":
                                hide_file(Path(str(target) + ext))
                    moved += 1
                except OSError:
                    continue
        return moved

    def export_path(self, game: str, title: str, label: str) -> Path:
        """A new folder per video, named so you can find it: 'Exports/2026-10-28 AION2 - Insane triple kill/'
        holding 'AION2 - Insane triple kill (YouTube Short).mp4' (+ its TikTok version and post text)."""
        return package_path(self.exports_dir, game, title, label)

    @property
    def exports_dir(self) -> Path:
        d = self.root / "Exports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load(self, p: Path, root: Path | None = None) -> Clip:
        side = p.with_suffix(".json")
        root = root or self.root
        clip = Clip(path=str(p), game=p.parent.name if p.parent != root else "Desktop")
        if side.exists():
            try:
                data = json.loads(side.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(clip, k) and k != "path":
                        setattr(clip, k, v)
            except Exception:
                pass
        if clip.has_cam < 0 and clip.duration:
            clip.has_cam = int(_video_tracks(p) >= 2)
            try:
                clip.save()
            except OSError:
                pass
        if not clip.created:
            clip.created = p.stat().st_mtime
        if not clip.duration:
            clip.duration = ff.duration(p)
            clip.has_cam = int(_video_tracks(p) >= 2)
            try:
                clip.save()
            except OSError:
                pass
        return clip

    def add_clip(self, path: Path, **meta) -> Clip:
        clip = Clip(path=str(path))
        for k, v in meta.items():
            if hasattr(clip, k):
                setattr(clip, k, v)
        clip.duration = ff.duration(path)
        clip.has_cam = int(_video_tracks(path) >= 2)
        clip.save()
        self.make_thumb(clip)
        return clip

    def make_thumb(self, clip: Clip) -> Path | None:
        t = clip.thumb
        if t.exists():
            return t
        t.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            try:  # hide the .thumbs folder
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(t.parent), 0x02)
            except Exception:
                pass
        at = min(2.0, max(0.0, (clip.duration or 1) / 2))
        r = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-ss", f"{at:.2f}",
                    "-i", clip.path, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(t)],
                   timeout=30)
        return t if r.returncode == 0 and t.exists() else None

    # ---------- deleting: kept 24 h in a hidden "Recently deleted" folder, then gone for good
    HOLD_HOURS = 24

    @property
    def trash_dir(self) -> Path:
        return self.root / ".deleted"

    def delete(self, clip: Clip, forever: bool = False) -> bool:
        """Deletes the clip (video, info file, thumbnail). It's kept for 24 hours in Recently deleted (no Recycle
        Bin), unless forever=True. Returns False if Windows wouldn't let go of the video (still open somewhere)."""
        import time as _t
        ok = True
        stamp = _t.strftime("%Y%m%d-%H%M%S")
        hold = None
        if not forever:
            hold = self.trash_dir / f"{stamp} {clip.p.stem}"
            try:
                hold.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    from .library import hide_file
                    hide_file(self.trash_dir)
                (hold / "origin.json").write_text(json.dumps({"path": clip.path, "deleted": _t.time(),
                                                               "game": clip.game, "title": clip.display_title}),
                                                  encoding="utf-8")
            except OSError:
                hold = None
        for f in (clip.p, clip.sidecar, clip.thumb):
            for attempt in range(6):          # a player that was just closed can hold the file a moment
                try:
                    if f.exists():
                        if hold is not None:
                            shutil.move(str(f), str(hold / (("thumb_" if f == clip.thumb else "") + f.name)))
                        else:
                            f.unlink()
                    break
                except OSError:
                    if attempt == 5:
                        ok = ok and f != clip.p
                    else:
                        _t.sleep(0.25)
        for ext in (".mp4.txt", ".mp4.meta.json"):   # leftovers some versions wrote next to clips
            try:
                side = clip.p.with_suffix(ext)
                if side.exists():
                    side.unlink()
            except OSError:
                pass
        if hold is not None and not ok:
            shutil.rmtree(hold, ignore_errors=True)
        return ok

    def recently_deleted(self) -> list[dict]:
        out = []
        if self.trash_dir.exists():
            for d in self.trash_dir.iterdir():
                try:
                    info = json.loads((d / "origin.json").read_text(encoding="utf-8"))
                    info["dir"] = str(d)
                    vids = [f for f in d.iterdir() if f.suffix.lower() in VIDEO_EXT]
                    info["size"] = sum(f.stat().st_size for f in vids)
                    out.append(info)
                except Exception:
                    continue
        return sorted(out, key=lambda i: i.get("deleted", 0), reverse=True)

    def restore_deleted(self, info: dict) -> bool:
        d = Path(info["dir"])
        orig = Path(info["path"])
        try:
            orig.parent.mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():
                if f.name == "origin.json":
                    continue
                if f.name.startswith("thumb_"):
                    dst = orig.parent / ".thumbs" / f.name[len("thumb_"):]
                    dst.parent.mkdir(parents=True, exist_ok=True)
                else:
                    dst = orig.parent / f.name
                if not dst.exists():
                    shutil.move(str(f), str(dst))
            shutil.rmtree(d, ignore_errors=True)
            return orig.exists()
        except OSError:
            return False

    def purge_deleted(self, all_now: bool = False) -> int:
        """Deletes for good what's been in Recently deleted for more than 24 hours."""
        import time as _t
        n = 0
        for info in self.recently_deleted():
            if all_now or _t.time() - info.get("deleted", 0) > self.HOLD_HOURS * 3600:
                shutil.rmtree(info["dir"], ignore_errors=True)
                n += 1
        return n

    def rename_game(self, clip: Clip, new_game: str):
        clip.game = new_game
        clip.save()


_BAD = '<>:"/\\|?*'


def clean_name(text: str, limit: int = 60) -> str:
    """Title → something Windows accepts as a file name (no emojis, no reserved characters)."""
    import re
    text = re.sub(r"(\d+)\s*/\s*(\d+)", r"\1 of \2", str(text)).replace("%", " percent").replace("&", "and")
    out = "".join(" " if (ch in _BAD or ord(ch) < 32) else ch for ch in str(text))
    out = "".join(ch for ch in out if ord(ch) < 0x2000 or ch.isalnum())   # drop emojis / symbols
    out = " ".join(out.split()).strip(" .")
    out = re.sub(r"\s+([!,.])", r"\1", out)
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].strip(" .,-") or out[:limit]
    return out


def package_path(exports_dir: Path, game: str, title: str, label: str, when: float | None = None) -> Path:
    game_s = clean_name(game or "", 30)
    if game_s.lower() in ("desktop", "clips", "gaming", ""):
        game_s = ""            # game wasn't recognised: just use the title
    title_s = clean_name(title or "", 60)
    if game_s and title_s.lower().startswith(game_s.lower()):
        title_s = title_s[len(game_s):].strip(" -:|,")
    name = " - ".join(x for x in (game_s, title_s) if x) or "Clip"
    from datetime import datetime
    base = f"{datetime.fromtimestamp(when) if when else datetime.now():%Y-%m-%d} {name}"
    folder = Path(exports_dir) / base
    n = 2
    while folder.exists() and any(folder.iterdir()):
        folder = Path(exports_dir) / f"{base} ({n})"
        n += 1
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{name} ({label}).mp4"


def sibling_path(video: Path, label: str) -> Path:
    """Another version of the same video in its folder, e.g. the TikTok one."""
    stem = video.stem
    if stem.endswith(")") and " (" in stem:
        stem = stem[: stem.rfind(" (")]
    return video.with_name(f"{stem} ({label}){video.suffix}")


def thumb_work_dir(video) -> Path:
    """Scratch space for thumbnail candidates — outside the export folder so it stays tidy."""
    import tempfile
    return Path(tempfile.gettempdir()) / "ClipForge" / "thumbs" / Path(video).stem


def hide_file(path) -> None:
    """Keeps ClipForge's bookkeeping files out of sight in Explorer."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def is_vertical_export(path) -> bool:
    try:
        meta = json.loads(Path(str(path) + ".meta.json").read_text(encoding="utf-8"))
        if "vertical" in meta:
            return bool(meta["vertical"])
    except Exception:
        pass
    name = Path(path).name
    return "Short" in name or "TikTok" in name


def is_tiktok_export(path) -> bool:
    try:
        meta = json.loads(Path(str(path) + ".meta.json").read_text(encoding="utf-8"))
        if meta.get("platform"):
            return meta["platform"] == "tiktok"
    except Exception:
        pass
    return "TikTok" in Path(path).name


def _key(p) -> str:
    """Same file = same key, however the path was written (case, slashes, ..)."""
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(p)))


def _video_tracks(path) -> int:
    return sum(1 for st in ff.probe(path).get("streams", []) if st.get("codec_type") == "video")


def _send_to_recycle_bin(path: Path):
    """Deleted clips go to the Recycle Bin, never gone for good by accident."""
    if os.name != "nt":
        path.unlink()
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = str(path.resolve()) + "\0\0"
    op.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004  # ALLOWUNDO|NOCONFIRMATION|NOERRORUI|SILENT
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:
        path.unlink()
