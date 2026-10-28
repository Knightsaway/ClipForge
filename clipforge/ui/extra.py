"""Screens for the v2 features: teaching kill icons, Script mode, and posting/scheduling."""
from __future__ import annotations

import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import ff, vision, youtube
from ..config import Settings, app_data_dir, save_settings
from ..library import Clip, Library
from . import theme
from .widgets import fmt_dur


# ====================================================================== teach a kill icon
class FrameBoxPicker(QWidget):
    """Shows a video frame; drag a box around the kill icon."""
    changed = Signal()

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.pm = pixmap
        self.box: QRectF | None = None   # normalized
        self._start: QPointF | None = None
        self.setMinimumSize(960, 540)
        self.setCursor(Qt.CrossCursor)

    def _img_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        ar = self.pm.width() / max(1, self.pm.height())
        if w / h > ar:
            iw, ih = h * ar, h
        else:
            iw, ih = w, w / ar
        return QRectF((w - iw) / 2, (h - ih) / 2, iw, ih)

    def _norm(self, pos) -> QPointF:
        r = self._img_rect()
        return QPointF(min(1, max(0, (pos.x() - r.left()) / r.width())),
                       min(1, max(0, (pos.y() - r.top()) / r.height())))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        r = self._img_rect()
        p.drawPixmap(r.toRect(), self.pm)
        if self.box:
            b = QRectF(r.left() + self.box.left() * r.width(), r.top() + self.box.top() * r.height(),
                       self.box.width() * r.width(), self.box.height() * r.height())
            p.setPen(QPen(QColor(theme.RED), 2))
            p.drawRect(b)
        p.end()

    def mousePressEvent(self, e):
        self._start = self._norm(e.position())
        self.box = None

    def mouseMoveEvent(self, e):
        if self._start is None:
            return
        cur = self._norm(e.position())
        self.box = QRectF(self._start, cur).normalized()
        self.update()

    def mouseReleaseEvent(self, e):
        self._start = None
        self.changed.emit()


class TeachDialog(QDialog):
    def __init__(self, clip: Clip, t: float, parent=None):
        super().__init__(parent)
        self.clip, self.t = clip, t
        self.setWindowTitle(f"Teach ClipForge a kill icon — {clip.game}")
        lay = QVBoxLayout(self)
        tip = QLabel("Pause on a frame where the kill icon / hitmarker skull / medal is clearly visible, "
                     "then drag a <b>tight</b> box around just the icon (not the background). "
                     "Teach a few: one for normal kills, one per medal (Double Kill, Headshot…).")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        tmp = Path(tempfile.mkdtemp()) / "frame.png"
        ff.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", clip.path, "-frames:v", "1",
                "-vf", "scale=1600:-2", str(tmp)], timeout=30)
        self.picker = FrameBoxPicker(QPixmap(str(tmp)))
        lay.addWidget(self.picker, 1)
        row = QHBoxLayout()
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(["kill", "headshot", "double kill", "triple kill", "medal", "win", "knock / down"])
        self.weight = QDoubleSpinBox()
        self.weight.setRange(0.2, 5)
        self.weight.setValue(1.0)
        self.weight.setSingleStep(0.5)
        self.weight.setToolTip("How exciting this event is. Medals 2-3, normal kill 1.")
        self.thresh = QDoubleSpinBox()
        self.thresh.setRange(0.4, 0.98)
        self.thresh.setSingleStep(0.02)
        self.thresh.setValue(0.72)
        self.thresh.setToolTip("Higher = stricter. Raise it if you get false kills, lower it if kills are missed.")
        for w in (QLabel("Event name"), self.name, QLabel("Excitement"), self.weight,
                  QLabel("Strictness"), self.thresh):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.resize(1100, 760)

    def _save(self):
        b = self.picker.box
        if not b or b.width() < 0.005 or b.height() < 0.005:
            QMessageBox.information(self, "Teach", "Drag a box around the icon first.")
            return
        try:
            det = vision.teach(self.clip.game, self.name.currentText().strip() or "kill", self.clip.path, self.t,
                               [b.left(), b.top(), b.width(), b.height()], weight=self.weight.value())
            gd = vision.load(self.clip.game)
            for d in gd.detectors:
                if d.template == det.template:
                    d.threshold = self.thresh.value()
            vision.save(gd)
        except Exception as e:
            QMessageBox.warning(self, "Teach", str(e))
            return
        self.accept()


# ====================================================================== script mode
SCRIPT_HELP = """Write one beat per line. Optional tags:
  [kill]            show a kill-streak moment here
  [clip: tilted]    use a clip whose name/game contains "tilted"
  [same]            keep showing the previous clip
Lines starting with # are notes and are skipped."""

EXAMPLE = """So I dropped into ranked with the worst loadout in the game [kill]
Everyone in the lobby was laughing at me
Then this happened
# pause for effect
And honestly I still don't know how [same]
Follow for part two"""


class ScriptPage(QWidget):
    build = Signal(dict)   # {"script", "voice", "clips_mode", "game", "preset", "word_captions", "polish"}

    def __init__(self, s: Settings, lib: Library):
        super().__init__()
        self.s, self.lib = s, lib
        self.voice_path = ""
        self.rec = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)

        left = QVBoxLayout()
        t = QLabel("SCRIPT & VOICEOVER")
        t.setObjectName("h1")
        left.addWidget(t)
        help_ = QLabel(SCRIPT_HELP)
        help_.setObjectName("muted")
        left.addWidget(help_)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(EXAMPLE)
        self.editor.setStyleSheet(f"background:{theme.CARD}; border:1px solid {theme.BORDER}; border-radius:6px;"
                                  "font-size:15px; padding:10px;")
        draft = app_data_dir() / "script_draft.txt"
        if draft.exists():
            self.editor.setPlainText(draft.read_text(encoding="utf-8"))
        self.editor.textChanged.connect(lambda: draft.write_text(self.editor.toPlainText(), encoding="utf-8"))
        left.addWidget(self.editor, 1)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.editor.textChanged.connect(self._count)
        left.addWidget(self.count)
        lay.addLayout(left, 3)

        right = QFrame()
        right.setObjectName("panel")
        right.setFixedWidth(360)
        r = QVBoxLayout(right)
        h = QLabel("1 · VOICEOVER")
        h.setObjectName("h2")
        r.addWidget(h)
        rec_row = QHBoxLayout()
        self.rec_btn = QPushButton("●  Record")
        self.rec_btn.setObjectName("record")
        self.rec_btn.clicked.connect(self._toggle_rec)
        imp = QPushButton("Import…")
        imp.clicked.connect(self._import)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self._play)
        rec_row.addWidget(self.rec_btn, 1)
        rec_row.addWidget(imp)
        rec_row.addWidget(self.play_btn)
        r.addLayout(rec_row)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        r.addWidget(self.meter)
        self.voice_lbl = QLabel("No voiceover yet — read your script out loud, with a short pause between lines.")
        self.voice_lbl.setObjectName("muted")
        self.voice_lbl.setWordWrap(True)
        r.addWidget(self.voice_lbl)
        self.polish = QCheckBox("Polish my voice (noise reduction, level)")
        self.polish.setChecked(True)
        r.addWidget(self.polish)
        r.addSpacing(10)
        h = QLabel("2 · FOOTAGE")
        h.setObjectName("h2")
        r.addWidget(h)
        f = QFormLayout()
        self.clips_mode = QComboBox()
        self.clips_mode.addItems(["Highlights & favorites (last 14 days)", "All clips (last 7 days)",
                                  "Selected clips on the Clips page"])
        self.game = QComboBox()
        f.addRow("Use", self.clips_mode)
        f.addRow("Game", self.game)
        r.addLayout(f)
        r.addSpacing(10)
        h = QLabel("3 · VIDEO")
        h.setObjectName("h2")
        r.addWidget(h)
        f2 = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItem("YouTube video (16:9)", "youtube1080")
        self.preset.addItem("Short / TikTok (9:16)", "short")
        self.captions = QComboBox()
        self.captions.addItem("Script lines on screen", False)
        self.captions.addItem("Word-by-word pop captions", True)
        f2.addRow("Format", self.preset)
        f2.addRow("Captions", self.captions)
        r.addLayout(f2)
        r.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        r.addWidget(self.status)
        go = QPushButton("🎬  Build video from script")
        go.setObjectName("primary")
        go.setMinimumHeight(42)
        go.clicked.connect(self._go)
        r.addWidget(go)
        lay.addWidget(right)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._tick)
        self._count()

    def refresh_games(self, games: list[str]):
        cur = self.game.currentText()
        self.game.clear()
        self.game.addItem("Any game")
        self.game.addItems(games)
        i = self.game.findText(cur)
        self.game.setCurrentIndex(max(0, i))

    def _count(self):
        from ..script_mode import parse_script
        lines = parse_script(self.editor.toPlainText())
        words = sum(len(l.text.split()) for l in lines)
        self.count.setText(f"{len(lines)} lines · {words} words · about {fmt_dur(words / 2.6)} spoken")

    def _toggle_rec(self):
        from ..script_mode import VoiceRecorder
        if self.rec:
            path = self.rec.stop()
            err = self.rec.error
            self.rec = None
            self._meter_timer.stop()
            self.meter.setValue(0)
            self.rec_btn.setText("●  Record")
            if err:
                QMessageBox.warning(self, "Recording", f"Microphone problem: {err}")
                return
            self._set_voice(str(path))
        else:
            out = app_data_dir() / f"voiceover_{datetime.now():%Y%m%d_%H%M%S}.wav"
            self.rec = VoiceRecorder(out, self.s.mic_device)
            self.rec.start()
            self.rec_btn.setText("■  Stop")
            self._meter_timer.start(80)

    def _tick(self):
        if self.rec:
            self.meter.setValue(int(min(1, self.rec.level * 1.6) * 100))
            self.voice_lbl.setText(f"Recording… {fmt_dur(time.time() - self.rec.started)}")

    def _import(self):
        f, _ = QFileDialog.getOpenFileName(self, "Voiceover", "", "Audio (*.wav *.mp3 *.m4a *.ogg *.flac)")
        if f:
            self._set_voice(f)

    def _set_voice(self, path: str):
        self.voice_path = path
        self.voice_lbl.setText(f"✔ {Path(path).name}  ({fmt_dur(ff.duration(path))})")

    def _play(self):
        if not self.voice_path:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.stop()
        else:
            self.player.setSource(QUrl.fromLocalFile(self.voice_path))
            self.player.play()

    def _go(self):
        if not self.editor.toPlainText().strip():
            QMessageBox.information(self, "Script", "Write your script first (one line per beat).")
            return
        if not self.voice_path:
            QMessageBox.information(self, "Script", "Record or import a voiceover first.")
            return
        self.player.stop()
        self.build.emit({"script": self.editor.toPlainText(), "voice": self.voice_path,
                         "clips_mode": self.clips_mode.currentIndex(), "game": self.game.currentText(),
                         "preset": self.preset.currentData(), "word_captions": self.captions.currentData(),
                         "polish": self.polish.isChecked()})


# ====================================================================== posting
class PostPage(QWidget):
    """Ready to post: exports, one-click YouTube scheduling, the upload queue and how videos perform."""
    changed = Signal()

    def __init__(self, s: Settings, lib: Library, queue: youtube.UploadQueue, uploader: youtube.Uploader):
        super().__init__()
        self.s, self.lib, self.q, self.up = s, lib, queue, uploader
        self.changed.connect(self.refresh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)
        head = QHBoxLayout()
        t = QLabel("READY TO POST")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        self.conn = QLabel("")
        self.conn.setObjectName("muted")
        head.addWidget(self.conn)
        backlog = QPushButton("📅  Schedule all unposted Shorts (1 per slot)")
        backlog.setObjectName("primary")
        backlog.clicked.connect(self._schedule_backlog)
        stats = QPushButton("📈  Update views")
        stats.clicked.connect(self._stats)
        folder = QPushButton("📁")
        folder.setToolTip("Open exports folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib.exports_dir))))
        for w in (backlog, stats, folder):
            head.addWidget(w)
        lay.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.v = QVBoxLayout(self.inner)
        self.v.setSpacing(8)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._live_progress)
        self._timer.start(1000)
        self._bars: dict[str, QProgressBar] = {}

    # ---------- helpers
    def _queued_paths(self) -> dict[str, youtube.QueueItem]:
        return {i.path: i for i in self.q.items if i.status != "failed"}

    def _meta(self, f: Path) -> tuple[str, str, list[str]]:
        txt = Path(str(f) + ".txt")
        if txt.exists():
            return youtube.parse_post_text(txt.read_text(encoding="utf-8"))
        return f.stem, "", ["#gaming"]

    def _enqueue(self, f: Path, schedule: bool, title_b: str = "", title_a: str = ""):
        if not youtube.connected():
            QMessageBox.information(self, "YouTube", "Connect your YouTube channel first: Settings → Uploads.")
            return
        if youtube.needs_reconnect() and (title_b or Path(str(f) + ".thumb.jpg").exists()):
            QMessageBox.information(self, "YouTube", "Title tests and thumbnails need one more permission: "
                                    "Settings → Uploads → Disconnect, then Connect YouTube again.")
            return
        title, desc, tags = self._meta(f)
        title = title_a or title
        from ..library import is_vertical_export
        if is_vertical_export(f) and "#shorts" not in [t.lower() for t in tags]:
            tags.append("#shorts")
        thumb = str(Path(str(f) + ".thumb.jpg")) if Path(str(f) + ".thumb.jpg").exists() else ""
        it = self.q.add(str(f), title, desc, tags, self.s.yt_privacy, schedule, self.s.post_times,
                        title_b=title_b, thumb=thumb)
        self.up.wake.set()
        self.refresh()
        when = datetime.fromisoformat(it.publish_at).strftime("%a %d %b %H:%M") if it.publish_at else "now"
        self.window().toast.show_message(f"📅 Uploading — goes live {when}", theme.GREEN)

    def _thumbs(self, f: Path):
        from .v3 import ThumbDialog
        from ..analytics import read_meta
        title, _, _ = self._meta(f)
        dlg = ThumbDialog(f, read_meta(f).get("hook") or title, self)
        if dlg.exec():
            self.refresh()

    def _ab(self, f: Path):
        from .v3 import ABDialog
        from ..analytics import read_meta
        title, _, _ = self._meta(f)
        dlg = ABDialog(title, read_meta(f).get("game", ""), self.s, self)
        if dlg.exec() and dlg.b.text().strip():
            self._enqueue(f, True, title_b=dlg.b.text().strip(), title_a=dlg.a.text().strip())

    def _approval_row(self, it) -> QFrame:
        from ..producer import Approvals
        row = QFrame()
        row.setObjectName("card")
        h = QHBoxLayout(row)
        col = QVBoxLayout()
        name = QLabel(it.title or Path(it.path).stem)
        name.setObjectName("cardtitle")
        when = (" · " + it.publish_at.replace("T", " ")) if it.publish_at else ""
        meta = QLabel(f"{it.platform.upper()} · {it.kind}{when}")
        meta.setObjectName("muted")
        col.addWidget(name)
        col.addWidget(meta)
        h.addLayout(col, 1)
        play = QPushButton("▶")
        play.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(it.path)))
        no = QPushButton("✖ Skip")
        ok = QPushButton("✔ Schedule" if it.platform == "youtube" else "✔ Approve")
        ok.setObjectName("primary")

        def decide(approve):
            msg = Approvals().decide(it.id, approve, self.q, self.s, self.up)
            self.window().toast.show_message(msg, theme.GREEN if approve else theme.MUTED)
            self.refresh()
        no.clicked.connect(lambda: decide(False))
        ok.clicked.connect(lambda: decide(True))
        for w in (play, no, ok):
            h.addWidget(w)
        return row

    def _tiktok_row(self, it, ap) -> QFrame:
        row = QFrame()
        row.setObjectName("card")
        h = QHBoxLayout(row)
        name = QLabel(it.title)
        name.setObjectName("cardtitle")
        h.addWidget(name, 1)
        post = QPushButton("TikTok ↑")
        post.setObjectName("primary")
        post.clicked.connect(lambda: self._tiktok(Path(it.path), Path(it.path + ".txt")))
        done = QPushButton("✔ Posted")

        def mark():
            it.status = "posted"
            ap.save()
            self.refresh()
        done.clicked.connect(mark)
        h.addWidget(post)
        h.addWidget(done)
        return row

    def _tiktok(self, f: Path, txt: Path):
        from .pages import show_in_folder
        if txt.exists():
            QGuiApplication.clipboard().setText(txt.read_text(encoding="utf-8"))
        QDesktopServices.openUrl(QUrl("https://www.tiktok.com/tiktokstudio/upload"))
        show_in_folder(str(f))

    def _schedule_backlog(self):
        queued = self._queued_paths()
        from ..library import is_tiktok_export, is_vertical_export
        shorts = [f for f in reversed(self.lib.exports())
                  if is_vertical_export(f) and not is_tiktok_export(f) and str(f) not in queued]
        if not shorts:
            QMessageBox.information(self, "Schedule", "Every Short is already queued or posted.")
            return
        if QMessageBox.question(self, "Schedule", f"Schedule {len(shorts)} Shorts, one per posting slot "
                                f"({', '.join(self.s.post_times)} daily)?") != QMessageBox.Yes:
            return
        for f in shorts:
            self._enqueue(f, True)

    def _stats(self):
        def work():
            try:
                self.up.refresh_stats()
            except Exception as e:
                print("stats:", e)
            self.changed.emit()
        threading.Thread(target=work, daemon=True).start()

    def _live_progress(self):
        for item_id, bar in list(self._bars.items()):
            try:
                bar.setValue(int(self.up.progress.get(item_id, 0) * 1000))
            except RuntimeError:
                self._bars.pop(item_id, None)

    # ---------- view
    def refresh(self):
        while self.v.count():
            it = self.v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._bars = {}
        self.conn.setText("YouTube: connected ✔" if youtube.connected() else "YouTube: not connected (Settings)")
        queued = self._queued_paths()
        from ..producer import Approvals
        ap = Approvals()
        pend = ap.pending()
        if pend:
            h = QLabel(f"WAITING FOR YOUR OK ({len(pend)})  —  or approve from your phone")
            h.setObjectName("h2")
            self.v.addWidget(h)
            for it in pend:
                self.v.addWidget(self._approval_row(it))
            self.v.addSpacing(12)
        tik = [i for i in ap.items if i.platform == "tiktok" and i.status == "approved" and Path(i.path).exists()]
        if tik:
            h = QLabel(f"POST TO TIKTOK ({len(tik)})")
            h.setObjectName("h2")
            self.v.addWidget(h)
            for it in tik[-10:]:
                self.v.addWidget(self._tiktok_row(it, ap))
            self.v.addSpacing(12)

        if self.q.items:
            h = QLabel("UPLOAD QUEUE")
            h.setObjectName("h2")
            self.v.addWidget(h)
            items = sorted(self.q.items, key=lambda i: (i.publish_at or "0", i.added), reverse=True)
            for it in items[:40]:
                self.v.addWidget(self._queue_row(it))
            self.v.addSpacing(12)
        h = QLabel("EXPORTS")
        h.setObjectName("h2")
        self.v.addWidget(h)
        files = self.lib.exports()
        if not files:
            e = QLabel("Nothing exported yet. Make one with ⚡ Auto Short, the Studio or Script mode.")
            e.setObjectName("muted")
            self.v.addWidget(e)
        for f in files[:60]:
            self.v.addWidget(self._export_row(f, queued.get(str(f))))
        self.v.addStretch(1)

    def _queue_row(self, it: youtube.QueueItem) -> QFrame:
        row = QFrame()
        row.setObjectName("card")
        h = QHBoxLayout(row)
        col = QVBoxLayout()
        name = QLabel(it.title)
        name.setObjectName("cardtitle")
        when = datetime.fromisoformat(it.publish_at).strftime("%a %d %b %H:%M") if it.publish_at else "on upload"
        icon = {"queued": "⏳", "uploading": "⬆", "scheduled": "📅", "published": "✔", "failed": "⚠"}[it.status]
        stats = ""
        if it.stats:
            stats = (f"  ·  👁 {it.stats.get('viewCount', 0):,}  ♥ {it.stats.get('likeCount', 0):,}"
                     f"  💬 {it.stats.get('commentCount', 0):,}")
        ab = ""
        if it.title_b:
            ab = {"": "  ·  🅰🅱 test: A first", "b": "  ·  🅰🅱 test: B running",
                  "done": f"  ·  🅰🅱 winner {it.ab_winner}"}.get(it.ab_state, "")
        meta = QLabel(f"{icon} {it.status.upper()}  ·  live {when}{stats}{ab}" + (f"  ·  {it.error}" if it.error else ""))
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        col.addWidget(name)
        col.addWidget(meta)
        if it.status == "uploading":
            bar = QProgressBar()
            bar.setRange(0, 1000)
            self._bars[it.id] = bar
            col.addWidget(bar)
        h.addLayout(col, 1)
        if it.video_id:
            b = QPushButton("Open on YouTube")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://youtu.be/{it.video_id}")))
            h.addWidget(b)
        if it.status == "failed":
            b = QPushButton("Retry")
            b.clicked.connect(lambda: (setattr(it, "status", "queued"), self.q.save(), self.up.wake.set(),
                                       self.refresh()))
            h.addWidget(b)
        if it.status in ("queued", "failed", "published"):
            b = QPushButton("✕")
            b.setToolTip("Remove from this list (doesn't delete anything on YouTube)")
            b.clicked.connect(lambda: (self.q.remove(it.id), self.refresh()))
            h.addWidget(b)
        return row

    def _export_row(self, f: Path, queued: youtube.QueueItem | None) -> QFrame:
        row = QFrame()
        row.setObjectName("card")
        h = QHBoxLayout(row)
        info = QVBoxLayout()
        name = QLabel(f.stem)
        name.setObjectName("cardtitle")
        size = f.stat().st_size / 1e6
        state = f"  ·  {queued.status}" if queued else ""
        meta = QLabel(f"{datetime.fromtimestamp(f.stat().st_mtime).strftime('%b %d %H:%M')}  ·  {size:.1f} MB{state}")
        meta.setObjectName("muted")
        info.addWidget(name)
        info.addWidget(meta)
        h.addLayout(info, 1)
        txt = Path(str(f) + ".txt")
        from ..library import is_vertical_export
        vertical = is_vertical_export(f)

        def btn(label, fn, primary=False, tip=""):
            b = QPushButton(label)
            if primary:
                b.setObjectName("primary")
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
        btn("▶", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(f))), tip="Play")
        if txt.exists():
            btn("📋", lambda: QGuiApplication.clipboard().setText(txt.read_text(encoding="utf-8")),
                tip="Copy title, description & hashtags")
        if not vertical:
            has = Path(str(f) + ".thumb.jpg").exists()
            btn("🖼 ✔" if has else "🖼", lambda: self._thumbs(f), tip="Pick an auto thumbnail")
        if not queued:
            btn("📅 Schedule", lambda: self._enqueue(f, True), True, "Upload now, goes public at your next posting slot")
            if not vertical and self.s.ab_titles:
                btn("🅰🅱", lambda: self._ab(f), tip="Schedule with a title A/B test")
            btn("⬆ Post now", lambda: self._enqueue(f, False))
        if vertical:
            btn("TikTok ↑", lambda: self._tiktok(f, txt),
                tip="Copies the caption, opens TikTok's uploader and shows the file — drag it in")
        return row


# ====================================================================== settings boxes
class UploadSettingsBox(QGroupBox):
    def __init__(self, s: Settings):
        super().__init__("Uploads (YouTube)")
        self.s = s
        f = QFormLayout(self)
        row = QHBoxLayout()
        self.secret = QLineEdit(s.yt_client_secret)
        self.secret.setPlaceholderText("client_secret_….json from Google Cloud")
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._pick)
        row.addWidget(self.secret)
        row.addWidget(b)
        f.addRow("OAuth client file", row)
        self.status = QLabel()
        self.connect_btn = QPushButton()
        self.connect_btn.clicked.connect(self._connect)
        crow = QHBoxLayout()
        crow.addWidget(self.status, 1)
        crow.addWidget(self.connect_btn)
        f.addRow("Channel", crow)
        self.times = QLineEdit(", ".join(s.post_times))
        self.times.setToolTip("Daily posting times, e.g. 12:00, 17:30, 20:00")
        f.addRow("Post at (daily)", self.times)
        self.privacy = QComboBox()
        self.privacy.addItems(["public", "unlisted", "private"])
        self.privacy.setCurrentText(s.yt_privacy)
        f.addRow("'Post now' visibility", self.privacy)
        how = QLabel('<a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com">'
                     "Get a client file (free)</a>: enable YouTube Data API v3 → Credentials → "
                     "Create OAuth client → Desktop app → download JSON. Add yourself as a test user.")
        how.setOpenExternalLinks(True)
        how.setWordWrap(True)
        how.setObjectName("muted")
        f.addRow(how)
        self._update()

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "OAuth client JSON", "", "JSON (*.json)")
        if f:
            self.secret.setText(f)

    def _update(self):
        ok = youtube.connected()
        self.status.setText("Connected ✔" if ok else "Not connected")
        self.connect_btn.setText("Disconnect" if ok else "Connect YouTube")

    def _connect(self):
        if youtube.connected():
            youtube.disconnect()
            self._update()
            return
        if not Path(self.secret.text()).exists():
            QMessageBox.information(self, "YouTube", "Pick your OAuth client JSON file first.")
            return
        self.s.yt_client_secret = self.secret.text()
        save_settings(self.s)
        self.status.setText("Waiting for you in the browser…")
        QGuiApplication.processEvents()
        try:
            name = youtube.connect(self.s.yt_client_secret)
            QMessageBox.information(self, "YouTube", f"Connected to {name} ✔")
        except Exception as e:
            QMessageBox.warning(self, "YouTube", str(e))
        self._update()

    def apply(self):
        self.s.yt_client_secret = self.secret.text().strip()
        times = []
        for t in self.times.text().split(","):
            t = t.strip()
            try:
                hh, mm = t.split(":")
                times.append(f"{int(hh):02d}:{int(mm):02d}")
            except ValueError:
                continue
        self.s.post_times = times or ["17:30"]
        self.s.yt_privacy = self.privacy.currentText()


class DetectorsBox(QGroupBox):
    """Kill icons you've taught, per game."""

    def __init__(self):
        super().__init__("Kill detection (taught icons)")
        v = QVBoxLayout(self)
        tip = QLabel("Teach icons from a clip: open a clip, pause on a kill, press 💀 Teach kill icon.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.list = QListWidget()
        self.list.setMaximumHeight(140)
        v.addWidget(self.list)
        row = QHBoxLayout()
        self.live = QCheckBox("Watch live while playing (auto-save multi-kills)")
        self.live.setChecked(True)
        self.min_k = QSpinBox()
        self.min_k.setRange(1, 6)
        self.min_k.setPrefix("Highlight from ")
        self.min_k.setSuffix(" kills")
        rm = QPushButton("Remove selected")
        rm.clicked.connect(self._remove)
        row.addWidget(self.live)
        row.addWidget(self.min_k)
        row.addWidget(rm)
        v.addLayout(row)
        self._rows: list[tuple[str, int]] = []
        self.refresh()

    def refresh(self):
        self.list.clear()
        self._rows = []
        d = vision._dir()
        for f in sorted(d.glob("*.json")):
            gd = vision.load(f.stem)
            try:
                import json
                gd = vision.load(json.loads(f.read_text(encoding="utf-8")).get("game", f.stem))
            except Exception:
                pass
            for i, det in enumerate(gd.detectors):
                self.list.addItem(f"{gd.game}  ·  {det.name}  (excitement {det.weight:g}, strictness {det.threshold:.2f})")
                self._rows.append((gd.game, i))
            self.live.setChecked(gd.live)
            self.min_k.setValue(gd.min_kills_for_highlight)

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self._rows):
            game, i = self._rows[r]
            gd = vision.load(game)
            gd.detectors.pop(i)
            vision.save(gd)
            self.refresh()

    def apply(self):
        for f in vision._dir().glob("*.json"):
            import json
            try:
                game = json.loads(f.read_text(encoding="utf-8")).get("game", f.stem)
            except Exception:
                continue
            gd = vision.load(game)
            gd.live = self.live.isChecked()
            gd.min_kills_for_highlight = self.min_k.value()
            vision.save(gd)
