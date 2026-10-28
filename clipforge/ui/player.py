from __future__ import annotations

import time

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
                               QWidget)

from .widgets import Timeline, fmt_dur


def make_audio_player(parent):
    """A small player for sounds (the caster's lines in the preview)."""
    pl = QMediaPlayer(parent)
    out = QAudioOutput(parent)
    pl.setAudioOutput(out)
    return pl, out


class _Stage(QWidget):
    """Video with the webcam laid over the bottom-right of the actual picture (scaled with it)."""

    def __init__(self, video, cam):
        super().__init__()
        self.video, self.cam = video, cam
        video.setParent(self)
        cam.setParent(self)
        self.setMinimumHeight(260)

    def _content(self):
        """Where the picture really is inside the video widget (it keeps its shape, adding bars)."""
        w, h = self.width(), self.height()
        vw, vh = 16, 9
        try:
            sz = self.video.videoSink().videoSize()
            if sz.width() > 0 and sz.height() > 0:
                vw, vh = sz.width(), sz.height()
        except Exception:
            pass
        sc = min(w / vw, h / vh)
        cw, ch = vw * sc, vh * sc
        return (w - cw) / 2, (h - ch) / 2, cw, ch

    def relayout(self, *_):
        self.video.setGeometry(0, 0, self.width(), self.height())
        x, y, cw, ch = self._content()
        pw = max(96, int(cw * 0.24))
        ph = int(pw * 9 / 16)
        m = max(6, int(cw * 0.015))
        self.cam.setGeometry(int(x + cw - pw - m), int(y + ch - ph - m), pw, ph)
        self.cam.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()


class PlayerPanel(QWidget):
    """Video preview + trim timeline. Keys: Space play, I/O set in/out, ←/→ 2 s, ,/. 1 frame."""
    rangeChanged = Signal(float, float)
    gameFrame = Signal(object)      # QImage, ~15 per second, only while live_frames is on
    camFrame = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(260)
        self.video.setStyleSheet("background:black;")
        self.player.setVideoOutput(self.video)
        # webcam: the same file played a second time with its 2nd video track, muted and kept in sync
        self.cam_player = QMediaPlayer(self)
        self.cam_video = QVideoWidget()
        self.cam_video.setMinimumSize(96, 54)
        from . import theme
        self.cam_video.setStyleSheet(f"background:black; border:2px solid {theme.ACCENT};")
        self.cam_player.setVideoOutput(self.cam_video)
        self.cam_video.setVisible(False)
        self.live_frames = False        # the Studio's "Final look" view wants frames
        self._last_frame = {"game": 0.0, "cam": 0.0}
        try:
            self.video.videoSink().videoFrameChanged.connect(lambda f: self._frame("game", f))
            self.cam_video.videoSink().videoFrameChanged.connect(lambda f: self._frame("cam", f))
        except Exception:
            pass
        self.cam_on = True
        self.has_cam = False
        self.timeline = Timeline()
        self.loop_range = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # the webcam sits INSIDE the game picture (bottom-right corner), not in the black bars around it
        self.stage = _Stage(self.video, self.cam_video)
        lay.addWidget(self.stage, 1)
        try:
            self.video.videoSink().videoSizeChanged.connect(self.stage.relayout)
        except Exception:
            pass
        lay.addWidget(self.timeline)
        ctl = QHBoxLayout()
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(46)
        self.play_btn.clicked.connect(self.toggle)
        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setObjectName("muted")
        set_in = QPushButton("[  Set In (I)")
        set_in.clicked.connect(self.set_in)
        set_out = QPushButton("Set Out (O)  ]")
        set_out.clicked.connect(self.set_out)
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset_range)
        self.track = QComboBox()
        self.track.addItems(["Hear: Game audio", "Hear: Mic"])
        self.track.currentIndexChanged.connect(self._track)
        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(80)
        vol.setFixedWidth(90)
        self.base_volume = 0.8
        self._duck = 1.0
        vol.valueChanged.connect(self._vol)
        for w in (self.play_btn, self.time_lbl):
            ctl.addWidget(w)
        ctl.addStretch(1)
        self.cam_btn = QPushButton("📷 Webcam")
        self.cam_btn.setCheckable(True)
        self.cam_btn.setChecked(True)
        self.cam_btn.setToolTip("Show the webcam recorded with this clip (picture-in-picture)")
        self.cam_btn.toggled.connect(self._cam_toggle)
        self.cam_btn.setVisible(False)
        for w in (set_in, set_out, reset, self.cam_btn, self.track, QLabel("🔊"), vol):
            ctl.addWidget(w)
        lay.addLayout(ctl)

        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(self._dur)
        self.player.playbackStateChanged.connect(self._state)
        self.cam_player.mediaStatusChanged.connect(self._cam_status)
        self.timeline.seek.connect(lambda s: self.player.setPosition(int(s * 1000)))
        self.timeline.rangeChanged.connect(self.rangeChanged)

    def _frame(self, which: str, frame):
        if not self.live_frames:
            return
        now = time.monotonic()
        if now - self._last_frame[which] < 0.066:
            return
        self._last_frame[which] = now
        try:
            img = frame.toImage()
            if img.isNull():
                return
            if img.width() > 720:
                img = img.scaledToWidth(720)
            (self.gameFrame if which == "game" else self.camFrame).emit(img)
        except Exception:
            pass

    def _vol(self, v):
        self.base_volume = v / 100
        self.audio.setVolume(self.base_volume * self._duck)

    def duck(self, factor: float):
        """Turn the game down to `factor` (the AI caster is talking), 1.0 = back to normal."""
        self._duck = factor
        self.audio.setVolume(self.base_volume * factor)

    def load(self, path: str, duration: float, t_in=0.0, t_out=0.0, markers=None, moments=None, autoplay=True,
             events=None, effects=None):
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.has_cam = False
        self.cam_video.setVisible(False)
        self.cam_btn.setVisible(False)
        self.cam_player.stop()
        self.cam_player.setSource(QUrl.fromLocalFile(path))
        self.timeline.set_clip(duration, t_in, t_out, markers, moments, events, effects)
        self.player.setPosition(int(t_in * 1000))
        self.track.setEnabled(True)
        if autoplay:
            self.player.play()

    @property
    def position(self) -> float:
        return self.player.position() / 1000

    def unload(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.cam_player.stop()
        self.cam_player.setSource(QUrl())

    # ---- webcam picture-in-picture
    def _cam_status(self, status):
        if status not in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) or self.has_cam:
            return
        try:
            tracks = self.cam_player.videoTracks()
        except Exception:
            tracks = []
        if len(tracks) < 2:
            return          # this clip has no webcam
        self.cam_player.setActiveVideoTrack(1)
        self.has_cam = True
        self.cam_btn.setVisible(True)
        self.cam_video.setVisible(self.cam_on)
        self.stage.relayout()
        self._sync_cam(force=True)

    def _cam_toggle(self, on):
        self.cam_on = on
        self.cam_video.setVisible(on and self.has_cam)
        self._sync_cam(force=True)

    def _sync_cam(self, force=False):
        if not (self.has_cam and self.cam_on):
            if self.cam_player.playbackState() == QMediaPlayer.PlayingState:
                self.cam_player.pause()
            return
        main = self.player.position()
        if force or abs(self.cam_player.position() - main) > 150:
            self.cam_player.setPosition(main)
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        if playing and self.cam_player.playbackState() != QMediaPlayer.PlayingState:
            self.cam_player.play()
        elif not playing and self.cam_player.playbackState() == QMediaPlayer.PlayingState:
            self.cam_player.pause()

    def _state(self, st):
        self.play_btn.setText("❚❚" if st == QMediaPlayer.PlayingState else "▶")
        self._sync_cam(force=True)

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.timeline.pos_s >= self.timeline.t_out - 0.05:
                self.player.setPosition(int(self.timeline.t_in * 1000))
            self.player.play()

    def _pos(self, ms):
        s = ms / 1000
        self.timeline.set_pos(s)
        self.time_lbl.setText(f"{fmt_dur(s)} / {fmt_dur(self.timeline.duration)}")
        self._sync_cam()
        if self.loop_range and self.player.playbackState() == QMediaPlayer.PlayingState \
                and s > self.timeline.t_out + 0.05:
            self.player.setPosition(int(self.timeline.t_in * 1000))

    def _dur(self, ms):
        if ms > 0 and abs(ms / 1000 - self.timeline.duration) > 0.5:
            out = self.timeline.t_out if self.timeline.t_out < self.timeline.duration - 0.01 else ms / 1000
            self.timeline.set_clip(ms / 1000, self.timeline.t_in, out, self.timeline.markers, self.timeline.moments,
                                   self.timeline.events, self.timeline.effects)

    def _track(self, idx):
        try:
            tracks = self.player.audioTracks()
            if idx < len(tracks):
                self.player.setActiveAudioTrack(idx)
        except Exception:
            pass

    def set_in(self):
        self.timeline.t_in = min(self.timeline.pos_s, self.timeline.t_out - 0.5)
        self.timeline.update()
        self.rangeChanged.emit(self.timeline.t_in, self.timeline.t_out)

    def set_out(self):
        self.timeline.t_out = max(self.timeline.pos_s, self.timeline.t_in + 0.5)
        self.timeline.update()
        self.rangeChanged.emit(self.timeline.t_in, self.timeline.t_out)

    def reset_range(self):
        self.timeline.t_in, self.timeline.t_out = 0.0, self.timeline.duration
        self.timeline.update()
        self.rangeChanged.emit(self.timeline.t_in, self.timeline.t_out)

    def keyPressEvent(self, e):
        k = e.key()
        pos = self.player.position()
        if k == Qt.Key_Space:
            self.toggle()
        elif k == Qt.Key_I:
            self.set_in()
        elif k == Qt.Key_O:
            self.set_out()
        elif k == Qt.Key_Left:
            self.player.setPosition(max(0, pos - 2000))
        elif k == Qt.Key_Right:
            self.player.setPosition(pos + 2000)
        elif k == Qt.Key_Comma:
            self.player.pause()
            self.player.setPosition(max(0, pos - 17))
        elif k == Qt.Key_Period:
            self.player.pause()
            self.player.setPosition(pos + 17)
        else:
            super().keyPressEvent(e)
