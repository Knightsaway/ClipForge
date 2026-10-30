"""Settings: one JSON file in %APPDATA%/ClipForge, written atomically so a crash can never corrupt it."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "ClipForge"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_note(text: str) -> None:
    """One line in clipforge.log (shown in Diagnostics) for problems that are handled but worth knowing."""
    try:
        with open(app_data_dir() / "clipforge.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


def default_library_dir() -> Path:
    vids = Path.home() / "Videos"
    if not vids.exists():
        vids = Path.home()
    return vids / APP_NAME


@dataclass
class Hotkeys:
    save_clip: str = "PageUp"          # save the replay buffer (like your GG setup)
    marker: str = "Home"               # drop a "that was good" marker while playing
    toggle_record: str = "Ctrl+End"    # start/stop a full session recording
    save_long: str = "Ctrl+PageUp"     # save the whole buffer, ignoring clip length


@dataclass
class Settings:
    # --- capture -----------------------------------------------------------
    library_dir: str = str(default_library_dir())
    buffer_seconds: int = 180          # how much history is kept (3:00 like GG)
    clip_seconds: int = 60             # length of a normal "save clip"
    marker_pre: int = 12               # seconds before a marker used by auto-edit
    marker_post: int = 4               # seconds after a marker used by auto-edit
    quality: str = "High"              # Low / Medium / High / Ultra
    resolution: str = "Native"         # Native / 1440p / 1080p / 720p
    fps: int = 60
    codec: str = "h264"                # h264 / hevc / av1
    encoder: str = "auto"              # auto / nvenc / amf / qsv / x264
    monitor: int = 0
    follow_game: bool = True           # keep recording the game (not the desktop) when you alt-tab
    capture_method: str = "auto"       # auto / window (Windows Graphics Capture) / desktop (Desktop Duplication) / gdi
    method_by_game: dict = field(default_factory=dict)   # what turned out smooth for each game
    capture_cursor: bool = False
    desktop_audio: bool = True
    mic_audio: bool = False
    mic_device: str = ""               # empty = default microphone
    audio_offset_ms: int = 0
    webcam: str = ""                   # DirectShow camera name; empty = no facecam
    use_webcam: bool = True            # off = ClipForge never opens the camera (the choice above is kept)
    webcam_res: str = "1280x720"
    webcam_fps: int = 30
    only_while_gaming: bool = True     # pause the buffer when no fullscreen game is focused
    start_buffer_on_launch: bool = True
    start_minimized: bool = False
    auto_clip: bool = False            # auto-save loud/hype moments (audio based)
    sound_on_save: bool = True
    game_aliases: dict = field(default_factory=dict)   # exe -> nice name
    extra_folders: list = field(default_factory=list)  # e.g. old GG Moments / ShadowPlay folders
    # --- export ------------------------------------------------------------
    short_target_seconds: int = 30
    short_layout: str = "blur"         # blur / crop / zoom / fit / facecam (webcam on top)
    cam_layout: dict = field(default_factory=dict)   # webcam in exports: {"mode", "box_v", "box_h"}
    gif_dir: str = ""                  # your GIFs / stickers (empty = AppData/ClipForge/gifs)
    caster_game_volume: float = 0.7   # game level while the AI caster talks (full volume between lines)
    giphy_key: str = ""
    auto_gifs: bool = True             # Auto Short / Montage may put a few small GIF stickers on big moments
    auto_review: bool = True           # Auto Short opens in the Studio for you to check before rendering
    montage_auto_edit: bool = True     # Create Montage adds effects (+ GIFs) for you, ready to tweak
    studio_advanced: bool = True       # Studio shows the full timeline (off = simple view)
    caption_style: str = "pop3"        # pop3 / word / karaoke / boxed
    styles: dict = field(default_factory=dict)          # "My style" presets: name -> look
    style_by_game: dict = field(default_factory=dict)   # game -> style name
    default_style: str = ""
    session_inbox: bool = True         # after a session: the best Shorts wait as drafts in Drafts → Inbox
    session_inbox_count: int = 3
    no_repeats: bool = True            # automatic edits skip moments already used in a video
    auto_cut_boring: bool = True       # Create Montage cuts dead air for you (undo brings it back)
    mic_moments: bool = True           # your laughing / yelling on the mic counts as a highlight
    transition_style: str = "punchy"   # automatic edits: punchy (flash/zoom/whip/glitch), smooth (crossfades), mix
    auto_transitions: bool = True      # automatic edits put quick transitions between clips
    auto_clean_days: int = 0           # delete unused clips older than this (0 = never)
    disk_warn_gb: int = 20             # warn when the clips drive has less free space than this
    update_check: bool = True          # look for a new release when ClipForge starts
    update_url: str = "github:Knightsaway/ClipForge"   # where new versions come from (GitHub repo or a version.json)
    ntfy_topic: str = ""               # phone push notifications via the free ntfy app
    wizard_done: bool = False
    game_overrides: dict = field(default_factory=dict)   # game -> {"fps", "quality", "buffer_seconds"}
    music_dir: str = ""                # your royalty-free music folder (for styles / auto edits)
    quick_trim: bool = True            # right after saving: Ctrl+→ / Ctrl+← move the clip's start by 5 s
    weekly_summary: bool = True        # Sunday evening: your week in numbers (toast + phone ping)
    summary_week: str = ""
    text_templates: dict = field(default_factory=dict)   # name -> text look (style, size, place, length)
    text_default: str = ""
    skin: str = "legacy"               # legacy / halflife / control / lastjedi / eldenring
    short_fps: int = 60
    loudness_normalize: bool = True
    music_file: str = ""
    music_volume: float = 0.18
    captions: bool = False             # needs faster-whisper (optional)
    auto_fx: bool = True               # Auto Short adds zooms / slow-mo / sound effects on kills & markers
    sfx_dir: str = ""                  # your own sound effects folder
    # --- uploads -----------------------------------------------------------
    yt_client_secret: str = ""         # path to Google OAuth client JSON
    yt_privacy: str = "public"
    post_times: list = field(default_factory=lambda: ["17:30"])  # daily slots for scheduled posts
    # --- channel growth -------------------------------------------------------------------
    ai_enabled: bool = True            # use local AI (Ollama) for titles/hooks when it's running
    ai_model: str = "llama3.1:8b"
    learn: bool = True                 # lean towards what performs on your channel
    bestof_enabled: bool = True
    bestof_day: int = 6                # 0=Mon … 6=Sun
    bestof_hour: int = 12
    bestof_count: int = 10
    bestof_autopost: bool = True       # schedule it on YouTube when connected
    squad_dir: str = ""                # shared Drive/OneDrive/Dropbox folder for Friends' POV
    squad_name: str = ""
    ab_titles: bool = True             # A/B test titles on scheduled long videos (AI writes title B)
    # --- automation -------------------------------------------------------------------
    loop_shorts: bool = True           # Shorts end by dissolving into their first frame (replays)
    tiktok_versions: bool = True       # every Short also gets a TikTok version with its own hook
    reaction_zoom: bool = True         # facecam punches in when you laugh / yell
    caster_auto: bool = False          # put the AI caster voice on Auto Shorts (off by default: still rough)
    caster_reset_030: bool = False     # 0.3.x switched the caster off once for everyone
    caster_voice: str = "en_US-ryan-high"
    today_shorts: int = 3
    recap_enabled: bool = True         # 60 s recap after each gaming session (waits for approval)
    recap_min_minutes: int = 20
    monthly_enabled: bool = True       # 8+ min best of the month with chapters (waits for approval)
    series_parts: int = 3
    phone_enabled: bool = False        # opt-in: local HTTP page for couch approvals
    phone_lan: bool = False            # False = 127.0.0.1 only; True = LAN (0.0.0.0)
    phone_port: int = 8765
    phone_token: str = ""
    watermark_text: str = ""          # your @handle shown on exports
    hotkeys: Hotkeys = field(default_factory=Hotkeys)

    # quality -> (cq, maxrate Mbps) at 1080p60; scaled by resolution in capture.py
    QUALITY_TABLE = {
        "Low": (28, 15),
        "Medium": (23, 30),
        "High": (19, 50),
        "Ultra": (16, 80),
    }

    @property
    def library(self) -> Path:
        p = Path(self.library_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def buffer_dir(self) -> Path:
        p = app_data_dir() / "buffer"
        p.mkdir(parents=True, exist_ok=True)
        return p


SETTINGS_FILE = app_data_dir() / "settings.json"


def keep_damaged(path: Path, why: str) -> None:
    """Move an unreadable file aside so the next save can't overwrite what the user had."""
    keep = path.with_name(f"{path.name}.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(path, keep)
        log_note(f"{path.name} could not be read ({why}); kept as {keep.name}, starting from defaults")
    except OSError as e:
        log_note(f"{path.name} could not be read ({why}) and could not be kept aside: {e}")


def _fits(default, value) -> bool:
    """Does a value from settings.json have the same kind as the default (any number for a number)?"""
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float))
    return isinstance(value, type(default))


def load_settings() -> Settings:
    s = Settings()
    data = None
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except (OSError, UnicodeDecodeError, ValueError) as e:
            if not isinstance(e, OSError):          # locked/unreadable: leave it alone, it may be fine
                keep_damaged(SETTINGS_FILE, str(e) or type(e).__name__)
            data = None
    if data is None:
        from . import secrets_store
        secrets_store.apply_to_settings(s)
        return s
    hk = data.pop("hotkeys", None)
    known = {f.name for f in fields(Settings)} - {"hotkeys"}
    for k, v in data.items():
        if k not in known:
            continue
        if _fits(getattr(s, k), v):
            setattr(s, k, v)
        else:
            log_note(f"settings.json: ignored {k}={v!r} (expected {type(getattr(s, k)).__name__})")
    if s.short_layout == "facecam_pip":       # older versions: now the movable webcam box
        s.short_layout = "blur"
    if not s.caster_reset_030:                  # the caster isn't good enough yet: off for everyone, once
        s.caster_auto = False
        for st in (s.styles or {}).values():
            if isinstance(st, dict) and st.get("caster"):
                st["caster"] = False
        s.caster_reset_030 = True
    if not s.update_url:                        # everyone gets updates from the ClipForge GitHub repo
        s.update_url = Settings.update_url
    if not isinstance(s.cam_layout, dict):
        s.cam_layout = {}
    if s.short_layout == "facecam":          # older versions: "webcam on top" is now a webcam option
        s.short_layout = "crop"
        s.cam_layout.setdefault("mode", "stack")
    if isinstance(hk, dict):
        for k, v in hk.items():
            if hasattr(s.hotkeys, k) and isinstance(v, str):
                setattr(s.hotkeys, k, v)
    from . import secrets_store
    secrets_store.apply_to_settings(s)
    return s


def save_settings(s: Settings) -> None:
    from . import secrets_store
    secrets_store.persist_from_settings(s)
    data = secrets_store.redact_for_json(asdict(s))
    fd, tmp = tempfile.mkstemp(dir=str(SETTINGS_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


IS_WINDOWS = sys.platform == "win32"
