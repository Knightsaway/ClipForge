"""First-start setup: hotkeys, mic, webcam, your look and the after-session autopilot in a few screens."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import ff
from ..config import Settings, save_settings
from . import theme
from .widgets import HotkeyEdit


class _Bridge(QObject):
    mic = Signal(float, str)
    level = Signal(float)
    cam = Signal(str, str)


class SetupWizard(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Set up ClipForge")
        self.resize(640, 520)
        self.b = _Bridge()
        v = QVBoxLayout(self)
        self.title = QLabel("")
        self.title.setObjectName("h1")
        v.addWidget(self.title)
        self.pages = QStackedWidget()
        v.addWidget(self.pages, 1)
        nav = QHBoxLayout()
        self.step = QLabel("")
        self.step.setObjectName("muted")
        skip = QPushButton("Skip setup")
        skip.clicked.connect(self._finish)
        self.back = QPushButton("← Back")
        self.back.clicked.connect(lambda: self._go(-1))
        self.next = QPushButton("Next →")
        self.next.setObjectName("primary")
        self.next.clicked.connect(lambda: self._go(1))
        nav.addWidget(self.step)
        nav.addStretch(1)
        nav.addWidget(skip)
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        v.addLayout(nav)
        self._titles = []
        self._build()
        self._go(0)

    def _page(self, title: str) -> QFormLayout:
        w = QWidget()
        f = QFormLayout(w)
        f.setLabelAlignment(Qt.AlignLeft)
        self.pages.addWidget(w)
        self._titles.append(title)
        return f

    @staticmethod
    def _text(t: str) -> QLabel:
        l = QLabel(t)
        l.setWordWrap(True)
        return l

    def _build(self):
        s = self.s
        # 1 welcome + skin
        f = self._page("👋 Welcome to ClipForge")
        f.addRow(self._text("ClipForge records the last few minutes of your game all the time. Press a key when "
                            "something cool happens, and after your session it turns the best moments into "
                            "Shorts and TikToks — you just check them and press ✔.<br><br>"
                            "This takes a minute. You can change everything later in Settings."))
        self.skin = QComboBox()
        for k, sk in theme.SKINS.items():
            self.skin.addItem(sk["label"], k)
        self.skin.setCurrentIndex(max(0, self.skin.findData(s.skin)))
        f.addRow("Look", self.skin)
        # 2 hotkeys
        f = self._page("⌨ Your keys")
        f.addRow(self._text("Click a box and press the key you want. Quit SteelSeries GG / NVIDIA Instant Replay so "
                            "they don't grab the same keys."))
        self.hk_save = HotkeyEdit(s.hotkeys.save_clip)
        self.hk_mark = HotkeyEdit(s.hotkeys.marker)
        self.hk_rec = HotkeyEdit(s.hotkeys.toggle_record)
        f.addRow("Save the last moments", self.hk_save)
        f.addRow("Marker: “that was good”", self.hk_mark)
        f.addRow("Start / stop recording", self.hk_rec)
        # 3 mic
        f = self._page("🎤 Your microphone")
        self.mic_on = QCheckBox("Record my microphone (its own track, so it can be turned up or down later)")
        self.mic_on.setChecked(s.mic_audio or True)
        f.addRow(self.mic_on)
        self.mic = QComboBox()
        self.mic.addItem("Default microphone", "")
        try:
            from ..audio import list_microphones
            for name in list_microphones():
                self.mic.addItem(name, name)
        except Exception:
            pass
        self.mic.setCurrentIndex(max(0, self.mic.findData(s.mic_device)))
        f.addRow("Microphone", self.mic)
        mt = QPushButton("🎤 Test — talk for 4 seconds")
        mt.clicked.connect(self._mic_test)
        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setTextVisible(False)
        self.mic_res = self._text("")
        f.addRow(mt)
        f.addRow(self.mic_bar)
        f.addRow(self.mic_res)
        self.b.level.connect(lambda v: self.mic_bar.setValue(int(min(1.0, v * 3) * 100)))
        self.b.mic.connect(self._mic_done)
        # 4 webcam
        f = self._page("📷 Webcam (optional)")
        self.cam = QComboBox()
        self.cam.addItem("No webcam", "")
        for name in ff.list_webcams():
            self.cam.addItem(name, name)
        self.cam.setCurrentIndex(max(0, self.cam.findData(s.webcam)))
        f.addRow("Webcam", self.cam)
        ct = QPushButton("🎥 Test")
        ct.clicked.connect(self._cam_test)
        self.cam_pic = QLabel()
        self.cam_res = self._text("With a webcam your videos get a facecam box — you can place it anywhere later "
                                  "(Studio → 📱 Final look).")
        f.addRow(ct)
        f.addRow(self.cam_pic)
        f.addRow(self.cam_res)
        self.b.cam.connect(self._cam_done)
        # 5 look of the videos
        f = self._page("🎬 How your Shorts look")
        self.layout_box = QComboBox()
        for k, label in (("blur", "Gameplay + blurred background"), ("smart", "Full-screen, follows the action"),
                         ("crop", "Full-screen center crop (shooters)")):
            self.layout_box.addItem(label, k)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(s.short_layout)))
        f.addRow("Layout", self.layout_box)
        self.caps = QCheckBox("Word-by-word captions")
        self.caps.setChecked(s.captions)
        self.caster = QCheckBox("AI caster reacts to big moments")
        self.caster.setChecked(s.caster_auto)
        self.gifs = QCheckBox("A few small GIF stickers on big moments")
        self.gifs.setChecked(s.auto_gifs)
        self.trans = QCheckBox("Quick transitions between clips")
        self.trans.setChecked(s.auto_transitions)
        for w in (self.caps, self.caster, self.gifs, self.trans):
            f.addRow(w)
        # 6 autopilot
        f = self._page("📥 After you play")
        self.inbox = QCheckBox("Make the session's best Shorts for me (they wait in Drafts for a quick ✔)")
        self.inbox.setChecked(s.session_inbox)
        self.review = QCheckBox("Let me check Auto Shorts in the Studio before they render")
        self.review.setChecked(s.auto_review)
        self.clean = QComboBox()
        for days, label in ((0, "Never delete old clips"), (14, "Delete unused clips after 2 weeks"),
                            (30, "Delete unused clips after a month"), (90, "Delete unused clips after 3 months")):
            self.clean.addItem(label, days)
        self.clean.setCurrentIndex(max(0, self.clean.findData(s.auto_clean_days)))
        f.addRow(self.inbox)
        f.addRow(self.review)
        f.addRow("Disk space", self.clean)
        f.addRow(self._text("Favorites, clips in drafts and clips used in videos are never deleted.<br><br>"
                            "To upload to YouTube automatically, connect your channel in Settings → Uploads. "
                            "For a ping on your phone when Shorts are ready, see Settings → Phone notifications."))

    def _go(self, d: int):
        i = self.pages.currentIndex() + d if d else 0
        if i >= self.pages.count():
            self._finish()
            return
        i = max(0, i)
        self.pages.setCurrentIndex(i)
        self.title.setText(self._titles[i])
        self.step.setText(f"Step {i + 1} of {self.pages.count()}")
        self.back.setEnabled(i > 0)
        self.next.setText("Done ✔" if i == self.pages.count() - 1 else "Next →")

    def _mic_test(self):
        from ..audio import test_microphone
        name = self.mic.currentData() or ""
        self.mic_res.setText("Talk now…")

        def work():
            peak, dev = test_microphone(name, 4.0, self.b.level.emit)
            self.b.mic.emit(peak, dev)
        threading.Thread(target=work, daemon=True).start()

    def _mic_done(self, peak, dev):
        if peak < 0:
            self.mic_res.setText(f"❌ Couldn't open it: {dev}")
        elif peak == 0:
            self.mic_res.setText("❌ Only silence. Windows may be blocking it: Settings → Privacy & security → "
                                 "Microphone → allow desktop apps.")
        elif peak < 0.02:
            self.mic_res.setText(f"⚠ Very quiet on “{dev}” — raise it in Windows sound settings if you can.")
        else:
            self.mic_res.setText(f"✔ Hearing you on “{dev}”.")

    def _cam_test(self):
        name = self.cam.currentData() or ""
        if not name:
            self.cam_res.setText("Pick a webcam first (or keep “No webcam”).")
            return
        self.cam_res.setText("Opening the camera…")

        def work():
            from ..capture import find_webcam_mode, webcam_hint
            args, err = find_webcam_mode(name, self.s.webcam_res, self.s.webcam_fps)
            if not args:
                self.b.cam.emit("", f"❌ {webcam_hint(err)}")
                return
            pic = str(Path(tempfile.gettempdir()) / "clipforge_wizard_cam.jpg")
            ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y"] + args +
                   ["-frames:v", "15", "-update", "1", "-vf", "scale=320:-2", pic], timeout=20)
            self.b.cam.emit(pic, "✔ The camera works.")
        threading.Thread(target=work, daemon=True).start()

    def _cam_done(self, pic, msg):
        self.cam_res.setText(msg)
        pm = QPixmap(pic) if pic else QPixmap()
        if not pm.isNull():
            self.cam_pic.setPixmap(pm)

    def _finish(self):
        s = self.s
        s.skin = self.skin.currentData() or "legacy"
        s.hotkeys.save_clip = self.hk_save.text() or s.hotkeys.save_clip
        s.hotkeys.marker = self.hk_mark.text() or s.hotkeys.marker
        s.hotkeys.toggle_record = self.hk_rec.text() or s.hotkeys.toggle_record
        s.mic_audio = self.mic_on.isChecked()
        s.mic_device = self.mic.currentData() or ""
        s.webcam = self.cam.currentData() or ""
        s.short_layout = self.layout_box.currentData()
        s.captions = self.caps.isChecked()
        s.caster_auto = self.caster.isChecked()
        s.auto_gifs = self.gifs.isChecked()
        s.auto_transitions = self.trans.isChecked()
        s.session_inbox = self.inbox.isChecked()
        s.auto_review = self.review.isChecked()
        s.auto_clean_days = self.clean.currentData()
        s.wizard_done = True
        save_settings(s)
        self.accept()
