from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from .. import editor, ff
from ..autoshort import plan_short, suggest_hook
from ..capture import CaptureEngine
from ..config import IS_WINDOWS, Settings, save_settings
from ..editor import PRESETS, Piece, RenderOptions
from ..hotkeys import HotkeyManager
from ..library import Library, thumb_work_dir
from . import theme
from .. import youtube
from .. import analytics, bestof
from ..squad import Squad, SquadWatcher
from .extra import PostPage, ScriptPage
from .v3 import InsightsPage
from ..producer import Approvals, produce
from .. import content
from .pages import ClipPage, Jobs, LibraryPage, SettingsPage, StudioPage
from .widgets import Toast, Toggle, app_icon, fmt_dur


class Bridge(QObject):
    """Engine/hotkey threads -> UI thread."""
    event = Signal(str, object)
    hotkey = Signal(str)


INBOX_AFTER = 120   # seconds after the game closes before the session's Shorts are made


class MainWindow(QMainWindow):
    def __init__(self, s: Settings, start_minimized=False):
        super().__init__()
        self.s = s
        self.setWindowTitle("ClipForge")
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.lib = Library(s.library, s.extra_folders)
        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.hotkey.connect(self._on_hotkey)
        self.engine = CaptureEngine(s, lambda k, d: self.bridge.event.emit(k, d), self.lib)
        self.hotkeys = HotkeyManager()
        self.jobs = Jobs()
        self.toast = Toast()
        self._quitting = False
        self._bestof_running = False

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._topbar())
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        v.addLayout(body, 1)

        self.library = LibraryPage(self.lib)
        self.library.release_file = self._release_file
        self.clip_page = ClipPage(self.lib)
        self.studio = StudioPage(s, self.lib, self.jobs)
        self.upload_queue = youtube.UploadQueue()
        try:   # older versions left 3 loose files per export: give each video its own named folder
            busy = {i.path for i in self.upload_queue.items} | {i.path for i in Approvals().items}
            self.lib.tidy_exports(busy)
        except Exception as e:
            print("tidy exports:", e)
        self.uploader = youtube.Uploader(self.upload_queue, on_change=lambda *_: self.exports.changed.emit())
        self.exports = PostPage(s, self.lib, self.upload_queue, self.uploader)
        self.script_page = ScriptPage(s, self.lib)
        self.insights = InsightsPage(s, self.upload_queue, self.uploader)
        self.settings_page = SettingsPage(s)
        self.settings_page.engine = self.engine   # webcam test knows when the camera is already recording
        self.settings_page.restart_app = self._restart_app
        self.settings_page.lib = self.lib
        self.settings_page.clips_fn = lambda: self.library.clips
        self.settings_page.maint.run_wizard = self._run_wizard
        self.settings_page.maint.send_summary = lambda: self._weekly_summary(force=True)
        self.settings_page.maint.on_cleaned = lambda: self.library.refresh()
        QTimer.singleShot(3000, self.settings_page.maint.refresh)
        QTimer.singleShot(1500, self._update_result)
        from .diag import DiagnosticsPage
        self.diag = DiagnosticsPage(self.engine, s)
        from .drafts_page import DraftsPage, QueueBridge
        self.drafts_page = DraftsPage()
        for p in (self.library, self.clip_page, self.studio, self.exports, self.settings_page, self.script_page,
                  self.insights, self.diag, self.drafts_page):
            self.stack.addWidget(p)
        # render queue: drafts render one by one in the background, never while you're in a game
        from .. import drafts as _drafts
        _drafts.cleanup()
        self.qbridge = QueueBridge()
        self.render_queue = _drafts.RenderQueue(
            s, lambda: self.lib.exports_dir,
            busy=lambda: bool(self.engine.state.game) or self.engine.state.recording,
            on_progress=self.qbridge.progress.emit, on_done=self.qbridge.done.emit, on_failed=self.qbridge.failed.emit)
        self.qbridge.progress.connect(self.drafts_page.on_progress)
        self.qbridge.done.connect(self._queue_done)
        self.qbridge.failed.connect(self._queue_failed)
        self.drafts_page.queueChanged.connect(self.render_queue.wake)
        self.drafts_page.openDraft.connect(self._open_draft)
        self.studio.queued.connect(lambda *_: (self.render_queue.wake(), self._update_drafts_badge()))
        QTimer.singleShot(1500, self._update_drafts_badge)
        if getattr(s, "update_check", True):
            QTimer.singleShot(20000, self._check_update_online)
        QTimer.singleShot(60000, self._housekeeping)
        # in the tray / while you play: let go of what only the window needs (see _lighten)
        self._light = False
        self._light_timer = QTimer(self)
        self._light_timer.timeout.connect(self._memory_watch)
        self._light_timer.start(15000)
        QTimer.singleShot(5000, lambda: self.lib.purge_deleted())
        self.setAcceptDrops(True)

        self.library.openClip.connect(self._open_clip)
        self.library.toStudio.connect(self._to_studio)
        self.library.montage.connect(self._montage)
        self.library.autoShort.connect(self._auto_short)
        self.studio.anotherTake.connect(self._another_take)
        self.clip_page.back.connect(lambda: self._nav(0))
        self.clip_page.toStudio.connect(self._to_studio)
        self.clip_page.exportClip.connect(self._quick_export)
        self.clip_page.saved.connect(lambda c: (self.library.add_clip(c), self.toast.show_message("✂ Trimmed clip saved")))
        self.studio.rendered.connect(self._rendered)
        self.settings_page.applied.connect(self._settings_applied)
        self.jobs.progress.connect(self._job_progress)
        self.jobs.done.connect(self._job_done)
        self.jobs.failed.connect(self._job_failed)

        # squad (Friends' POV)
        self.squad = Squad(s.squad_dir, s.squad_name)
        self.engine.squad = self.squad
        self.squad_watcher = SquadWatcher(self.squad, self.engine, s, lambda k, d: self.bridge.event.emit(k, d))
        self.squad_watcher.start()
        self.lib.extra_roots += [Path(r) for r in self.squad.clip_roots()]
        # learning feeds the hook picker / AI
        self.studio.hook_weights = lambda: analytics.hook_weights(self.upload_queue.items) if self.s.learn else {}
        self.studio.queue_items = lambda: self.upload_queue.items
        self.studio.all_clips = lambda: self.library.clips
        self.library.scanned.connect(lambda clips: self.insights.set_games(sorted({c.game for c in clips})))
        self._maint = QTimer(self)
        self._maint.timeout.connect(self._maintenance)
        self._maint.start(10 * 60 * 1000)
        QTimer.singleShot(90 * 1000, self._maintenance)   # catch up shortly after launch
        # v4: phone approvals, daily content, series, session recaps
        self.phone = None
        self.restart_phone()
        self.settings_page.phone_url = lambda: self.phone.url if self.phone else "(phone page is off)"
        self.settings_page.v4.link.setText(self.settings_page.phone_url())
        self.library.makeToday.connect(self._make_today)
        self.library.makeSeries.connect(self._make_series)
        self._session_start = 0.0
        self._last_game_seen = 0.0
        self.clip_page.scanGame.connect(self._scan_game)
        self.script_page.build.connect(self._build_script)
        self.library.scanned.connect(lambda clips: self.script_page.refresh_games(sorted({c.game for c in clips})))
        self.uploader.start()
        self.uploader.wake.set()  # anything left in the queue from last time goes out now
        self._tray()
        self._bind_hotkeys()
        self.library.refresh()
        self._nav(0)
        if s.start_buffer_on_launch:
            QTimer.singleShot(300, self.engine.start)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_status)
        self._tick.start(1000)
        if start_minimized or s.start_minimized:
            QTimer.singleShot(0, self.hide)

    # ------------------------------------------------------------------ chrome
    def _topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 10, 12, 6)
        h.setSpacing(8)

        chip = QFrame(); chip.setObjectName("chip")
        c = QHBoxLayout(chip); c.setContentsMargins(14, 8, 14, 8)
        self.status_lbl = QLabel("STARTING…")
        self.status_lbl.setStyleSheet(f"color:{theme.GREEN}; letter-spacing:1px; font-weight:600;")
        self.status_lbl.setMinimumWidth(280)
        self.buffer_btn = QPushButton("Pause")
        self.buffer_btn.setToolTip("Pause / resume the replay buffer")
        self.buffer_btn.clicked.connect(self._toggle_buffer)
        c.addWidget(QLabel("🖥"))
        c.addWidget(self.status_lbl, 1)
        c.addWidget(self.buffer_btn)
        h.addWidget(chip, 3)

        chip2 = QFrame(); chip2.setObjectName("chip")
        c2 = QHBoxLayout(chip2); c2.setContentsMargins(14, 8, 14, 8)
        self.auto_lbl = QLabel()
        self.auto_toggle = Toggle(self.s.auto_clip)
        self.auto_toggle.toggled.connect(self._auto_toggled)
        c2.addWidget(self.auto_lbl)
        c2.addStretch(1)
        c2.addWidget(self.auto_toggle)
        self._auto_toggled(self.s.auto_clip, save=False)
        h.addWidget(chip2, 2)

        chip3 = QFrame(); chip3.setObjectName("chip")
        c3 = QHBoxLayout(chip3); c3.setContentsMargins(10, 4, 10, 4)
        self.q_buffer = QComboBox()
        for sec in (30, 60, 120, 180, 300, 600):
            self.q_buffer.addItem(fmt_dur(sec), sec)
        self.q_quality = QComboBox(); self.q_quality.addItems(list(Settings.QUALITY_TABLE))
        self.q_res = QComboBox(); self.q_res.addItems(["Native", "1440p", "1080p", "720p"])
        self.q_fps = QComboBox(); self.q_fps.addItems(["30fps", "60fps", "120fps"])
        self._sync_quick()
        for w in (self.q_buffer, self.q_quality, self.q_res, self.q_fps):
            w.currentIndexChanged.connect(self._quick_changed)
            c3.addWidget(w)
        h.addWidget(chip3, 3)

        chip4 = QFrame(); chip4.setObjectName("chip")
        c4 = QHBoxLayout(chip4); c4.setContentsMargins(10, 4, 10, 4)
        self.hk_lbl = QLabel()
        self.hk_lbl.setStyleSheet("background:#e8ecf2; color:#111; border-radius:3px; padding:2px 6px; font-size:12px;")
        self.rec_btn = QPushButton("● REC")
        self.rec_btn.setObjectName("record")
        self.rec_btn.setToolTip("Start/stop a full recording (includes what's already in the buffer)")
        self.rec_btn.clicked.connect(self._toggle_record)
        save_btn = QPushButton("💾 Save clip")
        save_btn.clicked.connect(lambda: self.engine.save_clip())
        c4.addWidget(self.hk_lbl)
        c4.addWidget(save_btn)
        c4.addWidget(self.rec_btn)
        h.addWidget(chip4, 2)
        return bar

    def _sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(176)
        v = QVBoxLayout(side)
        v.setContentsMargins(10, 12, 10, 12)
        logo = QLabel(f"  {theme.LOGO} ClipForge")
        logo.setStyleSheet("font-size:17px; font-weight:700; padding:6px 0 14px 0;")
        v.addWidget(logo)
        self.nav_group = QButtonGroup(self)
        self.nav_btns = []
        for i, (label, page) in enumerate((("🎞  Clips", 0), ("🎬  Studio", 2), ("🗂  Drafts", 8), ("📝  Script", 5),
                                           ("📤  Ready to post", 3), ("📈  Insights", 6), ("⚙  Settings", 4),
                                           ("🩺  Diagnostics", 7))):
            b = QPushButton(label)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, p=page: self._nav(p))
            self.nav_group.addButton(b)
            self.nav_btns.append((page, b))
            v.addWidget(b)
        v.addStretch(1)
        # automatic videos are made in the background; this shows progress, then opens the result
        self.gen_btn = QPushButton("")
        self.gen_btn.setVisible(False)
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.setStyleSheet("text-align:left; padding:8px 10px;")
        self.gen_btn.clicked.connect(self._gen_clicked)
        v.addWidget(self.gen_btn)
        self._gen_running: dict[str, str] = {}    # job id -> "⏳ Generating Short… 40%"
        self._gen_ready: list[dict] = []           # finished drafts waiting to be opened
        self._gen_labels: dict[str, str] = {}
        self.job_lbl = QLabel("")
        self.job_lbl.setObjectName("muted")
        self.job_lbl.setWordWrap(True)
        v.addWidget(self.job_lbl)
        # raw FFmpeg output while something is wrong (handy for troubleshooting)
        self.err_lbl = QLabel("")
        self.err_lbl.setWordWrap(True)
        self.err_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.err_lbl.setStyleSheet(f"color:{theme.RED}; font-family:Consolas; font-size:10px;")
        self.err_lbl.setVisible(False)
        v.addWidget(self.err_lbl)
        self.admin_btn = QPushButton("🛡 Restart as administrator")
        self.admin_btn.setObjectName("primary")
        self.admin_btn.setToolTip("Windows asks for permission; ClipForge then gets top GPU priority for capture")
        self.admin_btn.clicked.connect(self._restart_admin)
        self.admin_btn.setVisible(False)
        v.addWidget(self.admin_btn)
        self.err_btn = QPushButton("📋 Copy error details")
        self.err_btn.setToolTip("Copies a full report for Claude (same as Diagnostics → Copy for Claude)")
        self.err_btn.clicked.connect(self._copy_error)
        self.err_btn.setVisible(False)
        v.addWidget(self.err_btn)
        self.pipe_lbl = QLabel("")
        self.pipe_lbl.setObjectName("muted")
        self.pipe_lbl.setWordWrap(True)
        v.addWidget(self.pipe_lbl)
        return side

    def _restart_admin(self):
        from ..capture import restart_as_admin
        if self.engine.state.recording:
            self.engine.stop_recording()
        if restart_as_admin():
            self._quit_now()
        else:
            self.toast.show_message("Not restarted (permission was declined)", theme.MUTED)

    def _restart_app(self, no_save: bool = False):
        """Start a fresh copy (it waits for this one to close) — after a skin change, update or restore."""
        self._no_save = no_save
        import os
        import subprocess
        import sys
        if self.engine.state.recording:
            self.engine.stop_recording()
        from .. import updater
        from ..paths import app_root, is_frozen
        if is_frozen() and updater.pending_update():
            # the exe can't overwrite itself while running: a helper swaps the files once we've closed
            try:
                updater.start_swap(os.getpid(), getattr(self, "_relaunch_args", ()))
            except OSError as e:
                self.toast.show_message(f"Couldn't start the update: {e}", theme.RED)
                return
            self._quit_now()
            # the helper can only swap the files once this process is really gone: don't let a lingering
            # thread keep it alive
            QTimer.singleShot(2500, lambda: os._exit(0))
            return
        exe = sys.executable
        if IS_WINDOWS and not is_frozen() and exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        args = [exe, "--after", str(os.getpid())] if is_frozen() else \
            [exe, "-m", "clipforge", "--after", str(os.getpid())]
        try:
            subprocess.Popen(args, cwd=str(app_root()),
                             **({"creationflags": 0x08000000} if IS_WINDOWS else {}))
        except OSError as e:
            self.toast.show_message(f"Couldn't restart: {e}", theme.RED)
            return
        self._quit_now()

    def _apply_update_zip(self, path: str, relaunch_args=()):
        """Install an update zip and restart into it — the same path as Settings → Upkeep."""
        from .. import updater
        updater.install_zip(path)
        self._relaunch_args = list(relaunch_args)
        self._restart_app()

    def _update_result(self):
        """After an exe update: say whether the files were swapped in."""
        from .. import updater
        res = updater.take_result()
        if res.startswith("ok"):
            from .. import __version__
            self.toast.show_message(f"⬆ Updated to ClipForge {__version__}", theme.GREEN)
        elif res:
            QMessageBox.warning(self, "Update", "The update couldn't replace every file (another program may "
                                "have had one open). Try Check now again, or download the installer.\n\n" + res)

    def _quit_now(self):
        self._quitting = True
        if self.phone:
            self.phone.stop()
        self.hotkeys.stop()
        self.engine.shutdown()
        self.tray.hide()
        QApplication.quit()

    def _copy_error(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.diag.report())
        self.toast.show_message("📋 Copied — paste it into the chat with Claude", theme.GREEN)

    def _nav(self, page: int):
        if page != 1 and self.stack.currentIndex() == 1:
            self.clip_page.player.unload()
        if page != 2:
            self.studio.player.player.pause()
        self.stack.setCurrentIndex(page)
        for p, b in self.nav_btns:
            b.setChecked(p == page or (page == 1 and p == 0))
        if page == 3:
            self.exports.refresh()
        if page == 8:
            self.drafts_page.refresh()
        if page == 6:
            self.insights.refresh()
        if page == 7:
            self.diag.refresh()

    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        m = QMenu()
        m.addAction("Open ClipForge", self._show)
        m.addAction("Save clip", lambda: self.engine.save_clip())
        m.addAction("Start / stop recording", self._toggle_record)
        m.addSeparator()
        m.addAction("Quit", self._quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self._show() if r in (QSystemTrayIcon.Trigger,
                                                                    QSystemTrayIcon.DoubleClick) else None)
        self.tray.setToolTip("ClipForge")
        self.tray.show()

    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._wake_up()

    def _memory_watch(self):
        away = not self.isVisible() or self.isMinimized()
        gaming = bool(self.engine.state.game) and not self.isActiveWindow()
        if (away or gaming) and not self._light:
            self._lighten()
        elif not (away or gaming) and self._light:
            self._wake_up()

    def _lighten(self):
        """While ClipForge sits in the tray or you're in a game: unload the video players (each keeps a decoder
        and frames of the last clip), the caster's voice model and freed memory, and give that RAM back to
        Windows. Nothing you're working on is lost; opening the window brings it back."""
        self._light = True
        try:
            st = self.studio
            if st.pieces and st.player.player.source().isValid():
                self._studio_resume = (st._current, st.player.player.position())
                st.player.player.pause()
            st.player.unload()
            if self.stack.currentIndex() != 1:
                self.clip_page.player.unload()
            from .. import voices
            voices.unload()
        except Exception as e:
            print("lighten:", e)
        ff.trim_memory()

    def _wake_up(self):
        if not self._light:
            return
        self._light = False
        resume = getattr(self, "_studio_resume", None)
        self._studio_resume = None
        if resume and self.studio.pieces:
            i, pos = resume
            self.studio._autoplay_next = False
            self.studio._refresh_list(min(i, len(self.studio.pieces) - 1))
            QTimer.singleShot(300, lambda: self.studio.player.player.setPosition(pos))

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._wake_up)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange and not self.isMinimized():
            QTimer.singleShot(0, self._wake_up)

    def closeEvent(self, e):
        if self._quitting:
            e.accept()
            return
        e.ignore()
        self.hide()
        QTimer.singleShot(1000, self._memory_watch)
        self.tray.showMessage("ClipForge is still recording",
                              f"Press {self.s.hotkeys.save_clip} in-game to save a clip. Right-click the tray icon to quit.",
                              QSystemTrayIcon.Information, 3000)

    def _quit(self):
        if self.engine.state.recording and QMessageBox.question(
                self, "Quit", "A recording is running. Save it and quit?") != QMessageBox.Yes:
            return
        self._quitting = True
        if self.phone:
            self.phone.stop()
        self.hotkeys.stop()
        self.engine.shutdown()
        self.tray.hide()
        QApplication.quit()

    # ------------------------------------------------------------------ capture controls
    def _bind_hotkeys(self, quick_trim: bool = False):
        hk = self.s.hotkeys
        emit = self.bridge.hotkey.emit
        b = {
            "Save clip": (hk.save_clip, lambda: emit("save")),
            "Marker": (hk.marker, lambda: emit("marker")),
            "Record": (hk.toggle_record, lambda: emit("record")),
            "Save buffer": (hk.save_long, lambda: emit("save_long")),
        }
        if quick_trim:   # only for a few seconds after a save, so the game gets Ctrl+arrows back right away
            b["Trim later"] = ("Ctrl+Right", lambda: emit("trim_fwd"))
            b["Trim earlier"] = ("Ctrl+Left", lambda: emit("trim_back"))
        self.hotkeys.set_bindings(b)
        if quick_trim:
            self.hotkeys.failed = [f for f in self.hotkeys.failed if "Trim" not in f]
            return
        self.hk_lbl.setText(hk.save_clip)
        if self.hotkeys.failed:
            QTimer.singleShot(1500, lambda: QMessageBox.warning(
                self, "Hotkeys", "Some hotkeys couldn't be registered:\n\n" + "\n".join(self.hotkeys.failed) +
                "\n\nIf SteelSeries GG or ShadowPlay is running, close it or pick other keys in Settings."))

    def _quick_trim_open(self, clip):
        """A few seconds after a clip is saved: Ctrl+→ cuts 5 s off its start, Ctrl+← puts 5 s back."""
        if not self.s.quick_trim or clip.duration < 8:
            return
        self._qt_clip = clip
        if not getattr(self, "_qt_timer", None):
            self._qt_timer = QTimer(self)
            self._qt_timer.setSingleShot(True)
            self._qt_timer.timeout.connect(self._quick_trim_close)
        if not self._qt_timer.isActive():
            self._bind_hotkeys(quick_trim=True)
        self._qt_timer.start(8000)

    def _quick_trim_close(self):
        self._qt_clip = None
        self._bind_hotkeys()

    def _quick_trim(self, step: float):
        c = getattr(self, "_qt_clip", None)
        if not c:
            return
        end = c.trim_out or c.duration
        c.trim_in = round(max(0.0, min(end - 3.0, (c.trim_in or 0.0) + step)), 2)
        c.save()
        self._qt_timer.start(8000)
        self.toast.show_message(f"✂ Clip starts at {fmt_dur(c.trim_in)} — {fmt_dur(end - c.trim_in)} long", theme.GREEN)

    def _on_hotkey(self, name: str):
        if name == "trim_fwd":
            self._quick_trim(5.0)
            return
        if name == "trim_back":
            self._quick_trim(-5.0)
            return
        if name == "save":
            self.engine.save_clip()
            self.toast.show_message(f"💾 Saving last {fmt_dur(self.s.clip_seconds)}…")
        elif name == "save_long":
            self.engine.save_clip(self.s.buffer_seconds)
            self.toast.show_message(f"💾 Saving whole buffer ({fmt_dur(self.s.buffer_seconds)})…")
        elif name == "marker":
            self.engine.add_marker()
        elif name == "record":
            self._toggle_record()

    def _toggle_record(self):
        if self.engine.state.recording:
            self.engine.stop_recording()
            self.toast.show_message("■ Recording stopped — saving…", theme.RED)
        else:
            self.engine.start_recording(from_seconds_ago=0)
            self.toast.show_message("● Recording", theme.RED)

    def _toggle_buffer(self):
        if self.engine._want_running:
            self.engine.stop()
        else:
            self.engine.start()
        self._update_status()

    def _auto_toggled(self, on, save=True):
        self.auto_lbl.setText("Auto-clipping: " + (f"<span style='color:{theme.GREEN}'>Enabled</span>" if on
                                                   else f"<span style='color:{theme.RED}'>Disabled</span>"))
        if save:
            self.s.auto_clip = on
            save_settings(self.s)

    def _sync_quick(self):
        for w in (self.q_buffer, self.q_quality, self.q_res, self.q_fps):
            w.blockSignals(True)
        i = self.q_buffer.findData(self.s.buffer_seconds)
        if i < 0:
            self.q_buffer.addItem(fmt_dur(self.s.buffer_seconds), self.s.buffer_seconds)
            i = self.q_buffer.count() - 1
        self.q_buffer.setCurrentIndex(i)
        self.q_quality.setCurrentText(self.s.quality)
        if self.q_res.findText(self.s.resolution) < 0:
            self.q_res.addItem(self.s.resolution)
        self.q_res.setCurrentText(self.s.resolution)
        if self.q_fps.findText(f"{self.s.fps}fps") < 0:
            self.q_fps.addItem(f"{self.s.fps}fps")
        self.q_fps.setCurrentText(f"{self.s.fps}fps")
        for w in (self.q_buffer, self.q_quality, self.q_res, self.q_fps):
            w.blockSignals(False)

    def _quick_changed(self):
        old = (self.s.quality, self.s.resolution, self.s.fps)
        self.s.buffer_seconds = int(self.q_buffer.currentData())
        self.s.clip_seconds = min(self.s.clip_seconds, self.s.buffer_seconds)
        self.s.quality = self.q_quality.currentText()
        self.s.resolution = self.q_res.currentText()
        self.s.fps = int(self.q_fps.currentText().replace("fps", ""))
        save_settings(self.s)
        if old != (self.s.quality, self.s.resolution, self.s.fps):
            self.engine.restart()

    def _settings_applied(self):
        self._sync_quick()
        self.auto_toggle.blockSignals(True)
        self.auto_toggle.setChecked(self.s.auto_clip)
        self.auto_toggle.blockSignals(False)
        self._auto_toggled(self.s.auto_clip, save=False)
        self._bind_hotkeys()
        self.restart_phone()
        self.settings_page.v4.link.setText(self.settings_page.phone_url())
        self.engine.restart()
        self.lib.root = self.s.library
        self.squad.folder = Path(self.s.squad_dir) if self.s.squad_dir else None
        from ..squad import _safe
        self.squad.me = _safe(self.s.squad_name)
        self.lib.extra_roots = [Path(p) for p in self.s.extra_folders if Path(p).exists()] + \
            [Path(r) for r in self.squad.clip_roots()]
        self.library.refresh()

    # ------------------------------------------------------------------ drafts / queue / inbox
    def _open_draft(self, d: dict):
        if d and self.studio.load_draft(d):
            self._nav(2)

    def _update_drafts_badge(self):
        try:
            n = len(self.drafts_page.inbox())
        except Exception:
            n = 0
        for p, b in self.nav_btns:
            if p == 8:
                b.setText("🗂  Drafts" + (f"  ({n} new)" if n else ""))

    def _queue_done(self, did: str, outs):
        from .. import drafts as _drafts
        d = _drafts.load(did) or {}
        main = Path(outs[0])
        msg = f"✔ Rendered: {d.get('title', main.name)}"
        if d.get("schedule"):
            if youtube.connected():
                try:
                    title, desc, tags = youtube.parse_post_text(Path(str(main) + ".txt").read_text(encoding="utf-8"))
                    thumb = str(main) + ".thumb.jpg" if Path(str(main) + ".thumb.jpg").exists() else ""
                    self.upload_queue.add(str(main), title, desc, tags, self.s.yt_privacy, True, self.s.post_times,
                                          **({"thumb": thumb} if thumb else {}))
                    self.uploader.wake.set()
                    msg += " — scheduled on YouTube"
                except Exception as e:
                    msg += f" (couldn't schedule: {e})"
            else:
                msg += " — connect YouTube in Settings to schedule automatically"
        self.toast.show_message(msg, theme.GREEN, 4000)
        self._push("🎬 Video ready", msg.replace("✔ ", ""))
        self.drafts_page.refresh()
        self.exports.changed.emit()
        self._update_drafts_badge()

    def _queue_failed(self, did: str, err: str):
        self.toast.show_message(f"Render failed — see Drafts: {err[:80]}", theme.RED, 5000)
        self.drafts_page.refresh()

    def _session_inbox(self, clips):
        """A session just ended: build its best Shorts as drafts (they wait in Drafts → Inbox)."""
        from .. import drafts as _drafts
        s = self.s

        if "inbox" in self._gen_running:
            return

        def job(progress):
            return _drafts.build_session_drafts(clips, s, s.session_inbox_count, progress)
        self._gen_start("inbox", "Shorts from your session")
        self.jobs.run("inbox", job)

    def _release_file(self, path: str):
        """Before a clip is deleted: close it in the clip view / Studio players and drop it from the Studio."""
        import os
        key = os.path.normcase(os.path.abspath(path))
        for panel in (self.clip_page.player, self.studio.player):
            try:
                src = panel.player.source().toLocalFile()
                if src and os.path.normcase(os.path.abspath(src)) == key:
                    panel.unload()
            except Exception:
                pass
        if any(os.path.normcase(os.path.abspath(p["clip"].path)) == key for p in self.studio.pieces):
            self.studio.pieces = [p for p in self.studio.pieces
                                  if os.path.normcase(os.path.abspath(p["clip"].path)) != key]
            self.studio._current = -1
            self.studio._refresh_list(0 if self.studio.pieces else -1)
        QApplication.processEvents()

    def _update_status(self):
        try:
            self._check_session()
        except Exception as e:
            print("session:", e)
        st = self.engine.state
        if st.recording:
            el = time.time() - st.record_started
            txt, col = f"● RECORDING  {fmt_dur(el)}", theme.RED
        elif not self.engine._want_running:
            txt, col = "BUFFER PAUSED", theme.YELLOW
        elif st.buffering and not st.error:
            rf = self.engine.real_fps
            if st.game and rf is not None and rf < self.s.fps * 0.5 and self.engine.starved_checks:
                txt, col = f"CHOPPY  ·  {st.game}  ·  {rf:.0f} fps", theme.YELLOW
            else:
                fps = f"  ·  {min(rf, self.s.fps):.0f} fps" if (st.game and rf and rf >= self.s.fps * 0.5) else ""
                txt, col = "READY TO CAPTURE" + (f"  ·  {st.game}" if st.game else "") + fps, theme.GREEN
        elif st.error and st.game:
            # only worth bothering you about when you're actually playing
            txt, col = f"NOT RECORDING  ·  {st.error}", theme.RED
        elif st.paused_no_game or not st.game:
            txt, col = "STANDBY  ·  starts when you launch a game", theme.MUTED
        else:
            txt, col = "STARTING…", theme.MUTED
        self.status_lbl.setText(txt)
        self.status_lbl.setStyleSheet(f"color:{col}; letter-spacing:1px; font-weight:600;")
        self.buffer_btn.setText("Pause" if self.engine._want_running else "Resume")
        self.rec_btn.setText("■ STOP" if st.recording else "● REC")
        self.rec_btn.setProperty("active", "true" if st.recording else "false")
        self.rec_btn.style().unpolish(self.rec_btn)
        self.rec_btn.style().polish(self.rec_btn)
        self.pipe_lbl.setText(f"Capture: {st.pipeline}" if st.pipeline and st.buffering else "")
        show_err = bool(st.error) and not st.paused_no_game
        raw = "\n".join(l for l in list(self.engine._stderr_tail)[-6:] if l.strip()) if show_err else ""
        self.err_lbl.setText(raw[-600:])
        self.err_lbl.setVisible(bool(raw))
        self.err_btn.setVisible(show_err)
        self.status_lbl.setToolTip("Full capture details: %APPDATA%\\ClipForge\\capture.log")

    def _on_event(self, kind: str, data):
        if kind == "state":
            self._update_status()
        elif kind == "saved":
            self.library.add_clip(data)
            label = (f"💀 {data.title} saved" if data.title and data.highlight else
                     "⚑ Highlight saved" if data.highlight else f"✔ {data.kind} saved")
            hint = "  ·  Ctrl+→ trims the start" if (self.s.quick_trim and not data.highlight
                                                    and data.duration >= 8) else ""
            self.toast.show_message(f"{label}  ({fmt_dur(data.duration)}){hint}", theme.GREEN)
            if not data.highlight:
                self._quick_trim_open(data)
            if self.s.sound_on_save and IS_WINDOWS:
                import winsound
                winsound.MessageBeep(winsound.MB_OK)
            self.library.thumbReady.emit(data.path, data.thumb)
        elif kind == "updated":
            for i, c in enumerate(self.library.clips):
                if c.path == data.path:
                    self.library.clips[i] = data
            self.library.rebuild()
        elif kind == "marker":
            msg = "⚑ Marker added" if self.engine.state.recording else \
                f"⚑ Marked — saving highlight in {self.s.marker_post}s"
            self.toast.show_message(msg, theme.YELLOW)
        elif kind == "toast":
            self.toast.show_message(str(data))
        elif kind == "error":   # e.g. a save that failed; full details are in the log
            self.toast.show_message(str(data).splitlines()[0][:120], theme.RED)
            self._update_status()
        elif kind == "capture_starved":
            if time.time() - getattr(self, "_last_starve_note", 0) > 900:
                self._last_starve_note = time.time()
                from ..capture import is_admin
                if not is_admin():
                    self.job_lbl.setText(f"⚠ {data['game']} uses so much GPU that recording only gets "
                                         f"~{data['fps']:.0f} fps. Running as administrator lets ClipForge "
                                         "jump the GPU queue (same as OBS).")
                    self.admin_btn.setVisible(True)
                else:
                    self.job_lbl.setText(f"⚠ Even with top GPU priority {data['game']} leaves recording only "
                                         f"~{data['fps']:.0f} fps. Cap the game's FPS a little (e.g. your monitor's "
                                         "Hz minus 3, in the game or NVIDIA Control Panel) to leave room.")
                self.toast.show_message("⚠ Recording is choppy — see the tip in ClipForge", theme.YELLOW, 5000)
        elif kind == "capture_failing":
            if time.time() - getattr(self, "_last_fail_note", 0) > 600:   # at most every 10 min
                self._last_fail_note = time.time()
                self.toast.show_message("⚠ ClipForge isn't recording this game — open the app for details",
                                        theme.RED, 5000)
                self.tray.showMessage("ClipForge isn't recording",
                                      "Capture keeps stopping. Open ClipForge → Settings to check the capture "
                                      "settings; details are in capture.log.", QSystemTrayIcon.Warning, 6000)

    # ------------------------------------------------------------------ editing flows
    def _open_clip(self, clip):
        self.stack.setCurrentIndex(1)
        for p, b in self.nav_btns:
            b.setChecked(p == 0)
        self.clip_page.show_clip(clip)

    def _to_studio(self, clips):
        if not clips:
            return
        self.studio.add_clips(clips)
        self.library.clear_selection()
        self._nav(2)

    def _montage(self, clips):
        """Create Montage: edited in the background (dead air cut, effects, GIFs, transitions), then it waits
        as a draft — click "open in Studio" to tweak it and render."""
        if not clips:
            return
        if self.s.montage_auto_edit and "montage" in self._gen_running:
            self.toast.show_message("⏳ Already making a montage — it'll be ready in a moment", theme.ACCENT, 3000)
            return
        if not self.s.montage_auto_edit:
            self.studio.clear()
            self._to_studio(clips)
            self.studio.draft_origin = "montage"
            return
        preset = self.studio.preset.currentData() or "youtube1080"
        pieces = [{"clip": c, "start": c.trim_in, "end": c.trim_out or c.duration, "effects": [], "speed": 1.0}
                  for c in clips]
        self.library.clear_selection()
        s = self.s

        def job(progress):
            from ..autoedit import generate_draft
            return {"draft": generate_draft(pieces, s, origin="montage", preset=preset, montage=True, add_fx=True,
                                            progress=progress)}
        self._gen_start("montage", "montage")
        self.jobs.run("montage", job)

    def _another_take(self, info: dict):
        if "another" in self._gen_running:
            self.toast.show_message("⏳ Already making another take — one moment", theme.ACCENT, 3000)
            return
        s = self.s

        def job(progress):
            from ..autoedit import another_take
            return {"draft": another_take(info, s, progress)}
        self._gen_start("another", "another take")
        self.jobs.run("another", job)

    def _auto_short(self, clips):
        if not clips:
            QMessageBox.information(self, "Auto Short", "No clips to work with yet — save some clips first!")
            return
        if "plan" in self._gen_running:
            self.toast.show_message("⏳ Already making a Short — it'll be ready in a moment", theme.ACCENT, 3000)
            return
        target = self.s.short_target_seconds
        before, after = max(3.0, self.s.marker_pre * 0.4), max(1.5, float(self.s.marker_post))
        self._auto_clips = clips

        ai_on, model = self.s.ai_enabled, self.s.ai_model
        items = self.upload_queue.items if self.s.learn else []
        from .. import used as used_mod
        no_repeats = self.s.no_repeats

        def job(progress):
            excl = used_mod.exclusions() if no_repeats else []
            plan = plan_short(clips, target=target, before=before, after=after, exclude=excl,
                              use_mic=getattr(self.s, "mic_moments", True),
                              progress=lambda f, m: progress(0.6 * f, m), auto_fx=self.s.auto_fx)
            post = None
            if ai_on and plan:
                try:
                    from .. import ai
                    if ai.available():
                        progress(0.95, "🤖 Writing the hook and title…")
                        picked = {p.path for p in plan}
                        post = ai.write_post(clips[0].game, ai.clip_context([c for c in clips if c.path in picked]),
                                             True, model, analytics.winning_hooks(items))
                except Exception as e:
                    print("ai:", e)
            if not plan:
                return {"draft": None}
            by_path = {c.path: c for c in clips}
            pieces = [{"clip": by_path[p.path], "start": p.start, "end": p.end, "effects": list(p.effects),
                       "speed": p.speed} for p in plan if p.path in by_path]
            from ..autoedit import generate_draft
            d = generate_draft(pieces, self.s, origin="auto", preset="short", ai_post=post,
                               hook=((post or {}).get("hooks") or [""])[0], add_fx=False,
                               progress=lambda f, m: progress(0.6 + 0.4 * f, m))
            return {"draft": d}
        self._gen_start("plan", "Short")
        self.jobs.run("plan", job)

    def _quick_export(self, clip, key):
        a = clip.trim_in
        b = clip.trim_out or clip.duration
        opts = RenderOptions(layout=self.s.short_layout, watermark=self.s.watermark_text,
                             loudnorm=self.s.loudness_normalize, extra={"cam": dict(self.s.cam_layout)},
                             hook_text=suggest_hook(clip.game, self.studio.hook_weights()) if key == "short" else "")
        tag = {"short": "YouTube Short", "discord": "Discord"}.get(key, "YouTube")
        hook = opts.hook_text
        out = self.lib.export_path(clip.game, hook or clip.title, tag)

        def job(progress):
            path = editor.render([Piece(clip.path, a, b)], key, opts, out, progress=progress)
            Path(str(path) + ".txt").write_text(editor.post_text(clip.game, hook, PRESETS[key].vertical),
                                                encoding="utf-8")
            from ..autoshort import hook_template
            analytics.write_meta(path, game=clip.game, hook=hook, hook_template=hook_template(hook, clip.game),
                                 preset=key, vertical=PRESETS[key].vertical, layout=opts.layout, n_pieces=1,
                                 kills=len([e for e in clip.events if a <= e["t"] <= b]), duration=b - a)
            return path
        self.toast.show_message(f"⬇ Exporting {PRESETS[key].label.split('(')[0].strip()}…")
        self.jobs.run("export", job)

    # ------------------------------------------------------------------ v4
    def restart_phone(self):
        from ..phone import PhoneServer, new_token
        if self.phone:
            self.phone.stop()
            self.phone = None
        if not self.s.phone_enabled:
            return
        if not self.s.phone_token:
            self.s.phone_token = new_token()
            save_settings(self.s)

        def decide(item_id, ok):
            msg = Approvals().decide(item_id, ok, self.upload_queue, self.s, self.uploader)
            self.exports.changed.emit()
            return msg
        try:
            lan = bool(getattr(self.s, "phone_lan", False))
            self.phone = PhoneServer(self.s.phone_token, decide, self.s.phone_port, lan=lan)
            self.phone.start()
        except OSError as e:
            print("phone page:", e)
            self.phone = None

    def _short_opts(self, game: str = "") -> RenderOptions:
        from ..styles import apply_to_opts, style_for
        return apply_to_opts(style_for(self.s, game), self._base_short_opts())

    def _base_short_opts(self) -> RenderOptions:
        return RenderOptions(layout=self.s.short_layout, watermark=self.s.watermark_text,
                             loudnorm=self.s.loudness_normalize, sfx_dir=self.s.sfx_dir,
                             extra={"loop": self.s.loop_shorts, "reaction_zoom": self.s.reaction_zoom,
                                    "cam": dict(self.s.cam_layout)})

    def _out(self, game: str, tag: str) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        return self.lib.exports_dir / f"{game} {tag} {stamp}.mp4"

    def _make_today(self):
        clips = [c for c in self.library.clips if time.time() - c.created < 86400]
        if not clips:
            QMessageBox.information(self, "Today's content", "No clips from the last 24 hours yet.")
            return
        s = self.s

        def job(progress):
            plan = content.make_today(clips, s.today_shorts)
            made = 0
            n_jobs = len(plan["shorts"]) + (1 if plan["long"][0] else 0)
            for i, pieces in enumerate(plan["shorts"]):
                used = [c for c in clips if any(p.path == c.path for p in pieces)]
                game = used[0].game if used else clips[0].game
                base = i / max(1, n_jobs)
                produce(pieces, "short", self._short_opts(game), self._out(game, "Short"), game, kind="today",
                        ai_on=s.ai_enabled, ai_model=s.ai_model, clips=used, tiktok=s.tiktok_versions,
                        progress=lambda f, m, b=base, i=i: progress(b + f / n_jobs, f"Short {i+1}: {m}"))
                made += 2 if s.tiktok_versions else 1
            lp, lt, lh = plan["long"]
            if lp:
                game = clips[0].game
                out = self._out(game, "YouTube Today")
                opts = RenderOptions(hook_text=lh, timed_text=lt, watermark=s.watermark_text,
                                     extra={"hook_y": "h*0.42", "cam": dict(s.cam_layout)})
                from .. import bestof
                desc = bestof.chapters(lt)
                out = produce(lp, "youtube1080", opts, out, game, kind="long", ai_on=s.ai_enabled, ai_model=s.ai_model,
                        clips=clips, tiktok=False, title=f"{lh} ({datetime.now():%d %b})",
                        description=(desc + "\n\n" if desc else "") + f"The best of today's {game}.",
                        to_approval=False,
                        progress=lambda f, m: progress((n_jobs - 1 + f) / n_jobs, f"Long video: {m}"))[0]
                self._thumb_for(out)
                self._to_approval(out, "long")
                made += 1
            return made
        self.toast.show_message("🌅 Making today's content — this runs in the background")
        self.jobs.run("today", job)

    def _thumb_for(self, out: Path):
        try:
            from ..thumbnail import make_thumbnails
            from ..youtube import parse_post_text
            title = parse_post_text(Path(str(out) + ".txt").read_text(encoding="utf-8"))[0]
            th = make_thumbnails(str(out), title, thumb_work_dir(out))
            if th:
                import shutil
                shutil.copyfile(th[0], str(out) + ".thumb.jpg")
        except Exception as e:
            print("thumb:", e)

    def _to_approval(self, out: Path, kind: str, publish_at: str = "", series: str = ""):
        from ..producer import PendingItem
        from ..youtube import parse_post_text
        title, desc, tags = parse_post_text(Path(str(out) + ".txt").read_text(encoding="utf-8"))
        thumb = str(out) + ".thumb.jpg" if Path(str(out) + ".thumb.jpg").exists() else ""
        Approvals().add(PendingItem(path=str(out), kind=kind, title=title, description=desc, tags=tags,
                                    thumb=thumb, publish_at=publish_at, series=series))

    def _make_series(self, clips):
        s = self.s
        parts = content.plan_series(clips, parts=s.series_parts, part_len=50)
        if len(parts) < 2:
            QMessageBox.information(self, "Series", "Not enough moments in that for a series — pick a longer "
                                                    "session or select several clips (Ctrl+click).")
            return
        first = self.upload_queue.next_slot(s.post_times)
        dates = content.series_dates(first, len(parts))
        game = clips[0].game
        sid = datetime.now().strftime("%Y%m%d%H%M")

        def job(progress):
            for k, part in enumerate(parts):
                opts = self._short_opts(game)
                opts.extra["loop"] = False          # a series ends on its cliffhanger card, not a loop
                opts.timed_text = part["timed_text"]
                used = [c for c in clips if any(p.path == c.path for p in part["pieces"])]
                produce(part["pieces"], "short", opts, self._out(game, f"Short Part {part['part']}"), game,
                        kind="series", hook=part["hook"], ai_on=s.ai_enabled, ai_model=s.ai_model, clips=used,
                        tiktok=s.tiktok_versions, title=f"{game} — Part {part['part']}/{part['of']}",
                        publish_at=dates[k].isoformat(timespec="minutes"), series=sid,
                        progress=lambda f, m, k=k: progress((k + f) / len(parts), f"Part {k+1}: {m}"))
            return len(parts)
        self.toast.show_message(f"📺 Making a {len(parts)}-part series — Part 1 goes out "
                                f"{first:%a %H:%M} after you approve")
        self.jobs.run("series", job)

    def _check_session(self):
        """Tracks gaming sessions; when one ends, makes the recap (never while you're playing)."""
        st = self.engine.state
        now = time.time()
        if st.game:
            if not self._session_start:
                self._session_start = now
            self._last_game_seen = now
            return
        if not self._session_start:
            return
        start, end = self._session_start, self._last_game_seen
        # 2 minutes after you stop playing: your session's best Shorts get made, ready when you look
        if now - end >= INBOX_AFTER and getattr(self, "_inbox_for", None) != end and end - start >= 5 * 60:
            self._inbox_for = end
            if self.s.session_inbox:
                clips = content.session_clips(self.library.clips, start)
                if clips:
                    self._session_inbox(clips)
        if now - end < 10 * 60:
            return
        self._session_start = 0.0
        if end - start < self.s.recap_min_minutes * 60:
            return
        clips = content.session_clips(self.library.clips, start)
        if not self.s.recap_enabled or len(clips) < 2:
            return
        s = self.s

        def job(progress):
            pieces, hook = content.plan_recap(clips)
            if not pieces:
                return 0
            game = clips[0].game
            produce(pieces, "short", self._short_opts(game), self._out(game, "Short Recap"), game, kind="recap",
                    hook=hook, ai_on=s.ai_enabled, ai_model=s.ai_model, clips=clips, tiktok=s.tiktok_versions,
                    progress=progress)
            return 1
        self.jobs.run("recap", job)

    def _monthly(self):
        from .. import bestof
        s = self.s
        clips = list(self.library.clips)
        self._bestof_running = True

        def job(progress):
            try:
                pieces, texts, hook = bestof.plan_bestof(clips, count=25, days=31, long=True, min_total=8.5 * 60)
                if len(pieces) < 8:
                    bestof.month_done()
                    return 0
                games = sorted({c.game for c in clips if any(p.path == c.path for p in pieces)})
                game = games[0] if len(games) == 1 else "Gaming"
                month = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%B")
                out = self._out(game, f"YouTube Best of {month}")
                title = f"Best {game} moments of {month}".replace("Best Gaming moments", "Best gaming moments")
                desc = bestof.chapters(texts)
                opts = RenderOptions(hook_text=hook.replace("this month", f"of {month}"), timed_text=texts,
                                     watermark=s.watermark_text, extra={"hook_y": "h*0.42", "cam": dict(s.cam_layout)})
                out = produce(pieces, "youtube1080", opts, out, game, kind="monthly", ai_on=s.ai_enabled,
                        ai_model=s.ai_model, clips=clips, tiktok=False, title=title,
                        description=(desc + "\n\n" if desc else "") + f"The best plays of {month}. "
                                    "Which one was your favourite?", to_approval=False, progress=progress)[0]
                self._thumb_for(out)
                self._to_approval(out, "monthly")
                bestof.month_done()
                return 1
            finally:
                self._bestof_running = False
        self.jobs.run("monthly", job)

    def _housekeeping(self):
        """Once a day: tidy old unused clips (if you turned that on) and warn when the drive gets full."""
        from .. import storage
        s = self.s
        today = time.strftime("%Y-%m-%d")
        if getattr(self, "_house_day", "") == today:
            return
        self._house_day = today
        try:
            self.lib.purge_deleted()            # deleted clips older than 24 h are removed for good
            if s.auto_clean_days > 0:
                old = storage.candidates(self.library.clips, s.auto_clean_days)
                if old:
                    n, freed = storage.clean(self.lib, old)
                    if n:
                        self.toast.show_message(f"🧹 Deleted {n} old unused clips ({storage.gb(freed)})", theme.MUTED, 4000)
                        self.library.refresh()
            u = storage.usage(self.lib)
            if s.disk_warn_gb and u["free"] and u["free"] < s.disk_warn_gb * 1e9:
                msg = (f"Only {storage.gb(u['free'])} free on your clips drive — Settings → Storage can clean up "
                       "old clips.")
                self.toast.show_message("⚠ " + msg, theme.YELLOW, 7000)
                self._push("ClipForge: disk almost full", msg)
        except Exception as e:
            print("housekeeping:", e)

    def _push(self, title: str, msg: str):
        if self.s.ntfy_topic:
            from ..notify import send_async
            # Never put the phone secret link in ntfy Click — that would send the token to ntfy.sh.
            send_async(self.s.ntfy_topic, title, msg, "")

    def _check_update_online(self):
        url = self.s.update_url
        if not url:
            return
        import threading

        def work():
            from .. import updater
            try:
                info = updater.check(url)
            except Exception:
                return
            if info:
                self.bridge.event.emit("toast", f"⬆ ClipForge {info['version']} is out — Settings → Updates")
        threading.Thread(target=work, daemon=True).start()

    def _run_wizard(self):
        from .wizard import SetupWizard
        before = (self.s.skin, self.s.hotkeys.save_clip, self.s.mic_device, self.s.webcam, self.s.use_webcam)
        dlg = SetupWizard(self.s, self)
        dlg.exec()
        after = (self.s.skin, self.s.hotkeys.save_clip, self.s.mic_device, self.s.webcam, self.s.use_webcam)
        if after != before:
            QMessageBox.information(self, "ClipForge", "All set! ClipForge restarts once to use your choices.")
            self._restart_app()

    # ---------- drop a ClipForge update zip on the window
    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith(".zip") for u in urls):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            f = u.toLocalFile()
            if f.lower().endswith(".zip"):
                self.settings_page.maint.install_zip(f)
                return

    def _weekly_summary(self, force: bool = False):
        from .. import summary
        if not (force or summary.due(self.s)):
            return
        title, msg = summary.build(self.library.clips, self.lib.exports(), self.upload_queue.items)
        self.s.summary_week = summary.week_key()
        save_settings(self.s)
        self.toast.show_message(title + " — " + msg.splitlines()[0] + " …", theme.GREEN, 7000)
        self.tray.showMessage(title, msg, QSystemTrayIcon.Information, 8000)
        self._push(title, msg)
        return msg

    def _maintenance(self):
        """Runs every 10 min: the weekly best-of (never while you're in a game)."""
        s = self.s
        self._housekeeping()
        self._weekly_summary()
        if self._bestof_running or self.engine.state.game or self.engine.state.recording:
            return  # rendering would cost you frames; try again later
        if s.monthly_enabled and bestof.month_due():
            self._monthly()
            return
        if not s.bestof_enabled or not bestof.due(s.bestof_day, s.bestof_hour):
            return
        clips = list(self.library.clips)
        self._bestof_running = True
        count, model, ai_on = s.bestof_count, s.ai_model, s.ai_enabled

        def job(progress):
            try:
                progress(0.02, "🏆 Picking the week's best plays…")
                pieces, texts, hook = bestof.plan_bestof(clips, count)
                if not pieces:
                    bestof.mark_done()   # not enough clips this week; skip it
                    return None
                games = sorted({c.game for c in clips if any(p.path == c.path for p in pieces)})
                game = games[0] if len(games) == 1 else "Gaming"
                title = f"Top {len(pieces)} {game} plays of the week".replace("Gaming plays", "plays")
                out = self.lib.export_path(game, title, "YouTube")
                opts = RenderOptions(hook_text=hook, timed_text=texts, watermark=s.watermark_text,
                                     loudnorm=True, extra={"hook_y": "h*0.42", "cam": dict(s.cam_layout)})
                editor.render(pieces, "youtube1080", opts, out,
                              progress=lambda f, m: progress(0.05 + 0.9 * f, "🏆 " + m))
                post = None
                if ai_on:
                    try:
                        from .. import ai
                        if ai.available():
                            post = ai.write_post(game, [{"game": game, "title": t[2]} for t in texts
                                                        if len(t) > 3 and t[3] == "Sub"], False, model)
                    except Exception:
                        post = None
                desc = (post or {}).get("description") or f"The best {game} moments from this week. Which was your favourite?"
                tags = (post or {}).get("tags") or ["#gaming", f"#{game.replace(' ', '')}", "#bestof", "#top10"]
                Path(str(out) + ".txt").write_text(f"TITLE:\n{(post or {}).get('title') or title}\n\n"
                                                   f"DESCRIPTION:\n{desc}\n\nHASHTAGS:\n{' '.join(tags)}\n",
                                                   encoding="utf-8")
                analytics.write_meta(out, game=game, hook=hook, preset="youtube1080", vertical=False,
                                     layout="16:9", n_pieces=len(pieces), bestof=True, ai=bool(post),
                                     duration=sum(p.out_dur for p in editor.expand_speed(pieces)))
                try:  # pick a thumbnail automatically for the best-of
                    from ..thumbnail import make_thumbnails
                    th = make_thumbnails(str(out), title, thumb_work_dir(out))
                    if th:
                        import shutil
                        shutil.copyfile(th[0], str(out) + ".thumb.jpg")
                except Exception as e:
                    print("thumb:", e)
                bestof.mark_done()
                return out
            finally:
                self._bestof_running = False
        self.jobs.run("bestof", job)

    def _scan_game(self, game: str):
        clips = [c for c in self.library.clips if c.game == game]

        def job(progress):
            from .. import vision
            gd = vision.load(game)
            for i, c in enumerate(clips):
                progress(i / max(1, len(clips)), f"💀 Scanning {game} clips for kills…")
                try:
                    c.events = vision.detect(c.path, game, gd=gd)
                    c.scanned = True
                    c.save()
                except Exception as e:
                    print("scan:", e)
            return len(clips)
        self.jobs.run("scan", job)

    def _build_script(self, req: dict):
        from .. import script_mode as sm
        sel = self.library.selected[:]
        all_clips = self.library.clips
        game = req["game"]

        def job(progress):
            progress(0.05, "Reading your script…")
            lines = sm.parse_script(req["script"])
            if not lines:
                raise RuntimeError("The script has no lines.")
            voice = req["voice"]
            if req["polish"]:
                progress(0.1, "Polishing your voice…")
                voice = sm.clean_voice(voice, str(Path(voice).with_name(Path(voice).stem + "_clean.wav")))
            progress(0.2, "Finding where each line is spoken…")
            lines = sm.time_lines(lines, voice, use_speech_recognition=True)
            now = time.time()
            if req["clips_mode"] == 2 and sel:
                pool = sel
            elif req["clips_mode"] == 1:
                pool = [c for c in all_clips if now - c.created < 7 * 86400]
            else:
                pool = [c for c in all_clips if now - c.created < 14 * 86400 and (c.highlight or c.markers
                                                                                   or c.favorite or c.events)]
            if game and game != "Any game":
                pool = [c for c in pool if c.game == game] or pool
            pool = pool or all_clips[:20]
            if not pool:
                raise RuntimeError("No clips to use yet — save some gameplay first.")
            progress(0.4, "Matching gameplay to each line…")
            pieces = sm.plan_script(lines, pool, effects=self.s.auto_fx)
            return {"pieces": pieces, "clips": pool, "preset": req["preset"],
                    "script": {"voiceover": voice, "timed_text": sm.timed_text(lines, req["word_captions"])}}
        self.jobs.run("script", job)

    def _job_progress(self, job, f, msg):
        if job in self._gen_running:
            what = self._gen_labels.get(job, "video")
            self._gen_running[job] = f"⏳ Generating {what}… {int(f * 100)}%"
            self.gen_btn.setToolTip(msg)
            self._gen_refresh()
            return
        if job in ("plan", "export", "scan", "script", "bestof", "today", "series", "recap", "monthly", "inbox"):
            self.job_lbl.setText(f"{msg} {int(f*100)}%")

    def _job_done(self, job, res):
        if job in ("plan", "montage", "another"):
            self._gen_running.pop(job, None)
            d = (res or {}).get("draft")
            if not d:
                self._gen_refresh()
                QMessageBox.information(self, {"plan": "Auto Short", "montage": "Montage"}.get(job, "Another take"),
                                        "Couldn't find usable moments in those clips.")
                return
            self._update_drafts_badge()
            if job == "plan" and not self.s.auto_review:     # "render without asking": straight to the queue
                from .. import drafts
                drafts.update(d["id"], status="queued", queued_at=time.time())
                self.render_queue.wake()
                self._gen_refresh()
                self.toast.show_message("⚡ Short edited — rendering in the background (see Drafts)", theme.GREEN, 5000)
                return
            self._gen_ready.append(d)
            self._gen_refresh()
            what = {"plan": "Short", "montage": "Montage"}.get(job, "Another take")
            self.toast.show_message(f"✔ {what} ready — click it on the left to open it in the Studio",
                                    theme.GREEN, 6000)
            if not self.isActiveWindow():
                self.tray.showMessage("ClipForge", f"Your {what.lower()} is edited and ready to check",
                                      QSystemTrayIcon.Information, 5000)
        elif job == "inbox":
            self._gen_running.pop("inbox", None)
            self._gen_ready += list(res or [])
            self._gen_refresh()
            res = len(res or [])
            if res:
                self._update_drafts_badge()
                self.toast.show_message(f"📥 {res} Short{'s' if res != 1 else ''} from your session are waiting in "
                                        "Drafts", theme.GREEN, 6000)
                self.tray.showMessage("ClipForge", f"{res} Shorts from your session are ready to check in Drafts",
                                      QSystemTrayIcon.Information, 5000)
                self._push(f"📥 {res} Shorts are ready", "Your session's best moments are edited and waiting for "
                                                        "your ✔ in ClipForge → Drafts.")
        elif job == "export":
            self._rendered(res)
        elif job == "bestof":
            self.job_lbl.setText("")
            if res:
                self._rendered(res)
                if self.s.bestof_autopost and youtube.connected():
                    title, desc, tags = youtube.parse_post_text(Path(str(res) + ".txt").read_text(encoding="utf-8"))
                    self.upload_queue.add(str(res), title, desc, tags, self.s.yt_privacy, True, self.s.post_times)
                    self.uploader.wake.set()
                    self.toast.show_message("🏆 Weekly best-of made and scheduled", theme.GREEN, 4000)
        elif job in ("today", "series", "recap", "monthly"):
            self.job_lbl.setText("")
            if res:
                what = {"today": "Today's content", "series": "Your series", "recap": "Tonight's recap",
                        "monthly": "Best of the month"}[job]
                where = " — check your phone" if self.phone else ""
                self.toast.show_message(f"✔ {what} is ready for your OK{where}", theme.GREEN, 5000)
                self.tray.showMessage("ClipForge", f"{what} is waiting for your approval{where}",
                                      QSystemTrayIcon.Information, 5000)
                self.exports.changed.emit()
        elif job == "scan":
            self.job_lbl.setText("")
            self.toast.show_message(f"💀 Scanned {res} clips for kills", theme.GREEN)
            self.library.rebuild()
        elif job == "script":
            self.job_lbl.setText("")
            self.studio.clear()
            self.studio.load_plan(res["pieces"], res["clips"], res["preset"], res["script"])
            self._nav(2)
            self.studio.render()

    def _gen_start(self, job: str, what: str):
        self._gen_labels[job] = what
        self._gen_running[job] = f"⏳ Generating {what}…"
        self._gen_refresh()

    def _gen_refresh(self):
        b = self.gen_btn
        if self._gen_running:
            b.setObjectName("")
            texts = list(self._gen_running.values())
            b.setText(texts[0] if len(texts) == 1 else f"⏳ Generating {len(texts)} videos…")
            if self._gen_ready:
                b.setText(b.text() + f"\n✔ {len(self._gen_ready)} ready")
        elif self._gen_ready:
            b.setObjectName("primary")
            d = self._gen_ready[-1]
            b.setText("✔ Video ready — open in Studio" if len(self._gen_ready) == 1
                      else f"✔ {len(self._gen_ready)} videos ready — open")
            b.setToolTip(d.get("title", "") + (f"\n{d['note']}" if d.get("note") else ""))
        b.setVisible(bool(self._gen_running or self._gen_ready))
        b.style().unpolish(b)
        b.style().polish(b)

    def _gen_clicked(self):
        if not self._gen_ready:
            self.toast.show_message("Still editing — it's done in a moment. Keep playing!", theme.ACCENT, 2500)
            return
        if len(self._gen_ready) > 1:          # several waiting: show them all in Drafts
            self._gen_ready.clear()
            self._gen_refresh()
            self._nav(8)
            return
        from .. import drafts
        d = drafts.load(self._gen_ready.pop()["id"])
        self._gen_refresh()
        if d:
            self._open_draft(d)

    def _job_failed(self, job, err):
        if job in self._gen_running:
            self._gen_running.pop(job, None)
            self._gen_refresh()
            if job == "inbox":                  # happened on its own after a session: don't pop up a dialog
                print("session drafts failed:", err)
                return
            QMessageBox.warning(self, "Automatic edit", f"The automatic edit failed: {err}")
            return
        if job == "bestof":
            self.job_lbl.setText("")
            print("best-of failed:", err)
            return
        if job in ("recap", "monthly", "inbox"):
            self.job_lbl.setText("")
            print(job, "failed:", err)
            return
        if job in ("plan", "export", "scan", "script", "today", "series"):
            self.job_lbl.setText("")
            QMessageBox.warning(self, "Something went wrong", err[-1500:])

    def _rendered(self, path):
        self.job_lbl.setText("")
        self.toast.show_message(f"✔ Ready to post: {Path(path).name}", theme.GREEN, 4000)
        self.tray.showMessage("Video ready", Path(path).name, QSystemTrayIcon.Information, 3000)
        if self.stack.currentIndex() == 3:
            self.exports.refresh()
