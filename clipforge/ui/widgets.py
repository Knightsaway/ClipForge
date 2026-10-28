from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget, QLineEdit)

from . import theme


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("white"))
    path = QPainterPath()
    path.moveTo(25, 18)
    path.lineTo(47, 32)
    path.lineTo(25, 46)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor(theme.RED))
    p.drawEllipse(QPointF(48, 16), 7, 7)
    p.end()
    return QIcon(pm)


def fmt_dur(sec: float) -> str:
    sec = max(0, int(round(sec)))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def rel_time(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return "JUST NOW"
    if d < 3600:
        return f"{int(d // 60)} MINUTES AGO"
    if d < 86400:
        h = int(d // 3600)
        return f"{h} HOUR{'S' if h > 1 else ''} AGO"
    days = int(d // 86400)
    if days < 31:
        return f"{days} DAY{'S' if days > 1 else ''} AGO"
    mo = days // 30
    return f"{mo} MONTH{'S' if mo > 1 else ''} AGO"


def day_label(ts: float) -> str:
    d = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if d == today:
        return "TODAY"
    if (today - d).days == 1:
        return "YESTERDAY"
    return datetime.fromtimestamp(ts).strftime("%a, %b %d").upper()


class Toggle(QCheckBox):
    """iOS-style switch like the GG auto-clipping toggle."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else "#3a434e"))
        p.drawRoundedRect(QRectF(0, 2, 38, 18), 9, 9)
        p.setBrush(QColor("white"))
        x = 21 if on else 3
        p.drawEllipse(QRectF(x, 4, 14, 14))
        p.end()


class HotkeyEdit(QLineEdit):
    """Click, then press the key combo you want."""

    NAMES = {Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown", Qt.Key_Home: "Home", Qt.Key_End: "End",
             Qt.Key_Insert: "Insert", Qt.Key_Delete: "Delete", Qt.Key_Pause: "Pause",
             Qt.Key_ScrollLock: "ScrollLock", Qt.Key_Print: "PrintScreen", Qt.Key_Space: "Space"}

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click and press keys…")

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        if k == Qt.Key_Escape:
            self.clearFocus()
            return
        mods = []
        m = e.modifiers()
        if m & Qt.ControlModifier:
            mods.append("Ctrl")
        if m & Qt.AltModifier:
            mods.append("Alt")
        if m & Qt.ShiftModifier:
            mods.append("Shift")
        if k in self.NAMES:
            name = self.NAMES[k]
        elif Qt.Key_F1 <= k <= Qt.Key_F24:
            name = f"F{k - Qt.Key_F1 + 1}"
        elif (Qt.Key_A <= k <= Qt.Key_Z) or (Qt.Key_0 <= k <= Qt.Key_9):
            name = chr(k)
            if m & Qt.KeypadModifier and name.isdigit():
                name = "Num" + name
        else:
            return
        self.setText("+".join(mods + [name]))
        self.clearFocus()


class Timeline(QWidget):
    """Scrub bar with draggable in/out handles, marker flags and the playhead."""
    seek = Signal(float)
    rangeChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(54)
        self.setMouseTracking(True)
        self.duration = 1.0
        self.pos_s = 0.0
        self.t_in = 0.0
        self.t_out = 1.0
        self.markers: list[float] = []
        self.moments: list[float] = []
        self.events: list[float] = []      # detected kills
        self.effects: list[dict] = []      # zoom / slowmo / sfx on the current piece
        self._drag = None

    def set_clip(self, duration: float, t_in=0.0, t_out=0.0, markers=None, moments=None, events=None,
                 effects=None):
        self.duration = max(0.1, duration)
        self.t_in = t_in
        self.t_out = t_out or self.duration
        self.markers = markers or []
        self.moments = moments or []
        self.events = events or []
        self.effects = effects or []
        self.update()

    def set_pos(self, s: float):
        self.pos_s = s
        self.update()

    def _x(self, t):
        return 10 + (self.width() - 20) * (t / self.duration)

    def _t(self, x):
        return max(0.0, min(self.duration, (x - 10) / max(1, self.width() - 20) * self.duration))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        bar = QRectF(10, h / 2 - 9, self.width() - 20, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2b333d"))
        p.drawRoundedRect(bar, 4, 4)
        sel = QRectF(self._x(self.t_in), bar.top(), self._x(self.t_out) - self._x(self.t_in), bar.height())
        p.setBrush(QColor(91, 95, 240, 110))
        p.drawRect(sel)
        # suggested moments (audio peaks) as faint ticks
        p.setPen(QPen(QColor(255, 255, 255, 60), 2))
        for m in self.moments:
            x = self._x(m)
            p.drawLine(QPointF(x, bar.top() + 3), QPointF(x, bar.bottom() - 3))
        # detected kills: red ticks
        p.setPen(QPen(QColor(theme.RED), 3))
        for m in self.events:
            x = self._x(m)
            p.drawLine(QPointF(x, bar.top() + 2), QPointF(x, bar.bottom() - 2))
        # effects below the bar: Z = zoom, S = slow-mo, ♪ = sound
        f2 = QFont(self.font())
        f2.setPointSize(8)
        f2.setBold(True)
        p.setFont(f2)
        for e in self.effects:
            x = self._x(e.get("t", 0))
            if e.get("type") == "gif":   # GIFs / stickers: a pink bar for as long as they're on screen
                x2 = self._x(e.get("t", 0) + e.get("dur", 2.0))
                p.fillRect(QRectF(x, bar.bottom() + 12, max(3.0, x2 - x), 3), QColor("#ff6bd6"))
            sym, col = {"zoom": ("Z", "#4fc3ff"), "slowmo": ("S", "#ff9f43"), "sfx": ("♪", "#3ddc97"), "gif": ("G", "#ff6bd6")}.get(
                e.get("type"), ("•", "white"))
            p.setPen(QColor(col))
            p.drawText(QRectF(x - 6, bar.bottom() + 1, 12, 12), Qt.AlignCenter, sym)
        # markers you pressed in-game
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.YELLOW))
        for m in self.markers:
            x = self._x(m)
            tri = QPainterPath()
            tri.moveTo(x - 6, bar.top() - 10)
            tri.lineTo(x + 6, bar.top() - 10)
            tri.lineTo(x, bar.top() - 2)
            tri.closeSubpath()
            p.drawPath(tri)
        # handles
        for t in (self.t_in, self.t_out):
            x = self._x(t)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(x - 4, bar.top() - 4, 8, bar.height() + 8), 3, 3)
        # playhead
        x = self._x(self.pos_s)
        p.setPen(QPen(QColor("white"), 2))
        p.drawLine(QPointF(x, 4), QPointF(x, h - 4))
        p.setPen(QColor(theme.MUTED))
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(QRectF(10, h - 14, 200, 14), Qt.AlignLeft, f"In {fmt_dur(self.t_in)}  •  Out {fmt_dur(self.t_out)}"
                   f"  •  Length {fmt_dur(self.t_out - self.t_in)}")
        p.end()

    def mousePressEvent(self, e):
        x = e.position().x()
        if abs(x - self._x(self.t_in)) < 8:
            self._drag = "in"
        elif abs(x - self._x(self.t_out)) < 8:
            self._drag = "out"
        else:
            self._drag = "seek"
            self.seek.emit(self._t(x))

    def mouseMoveEvent(self, e):
        x = e.position().x()
        near = abs(x - self._x(self.t_in)) < 8 or abs(x - self._x(self.t_out)) < 8
        self.setCursor(Qt.SizeHorCursor if near or self._drag in ("in", "out") else Qt.PointingHandCursor)
        if not self._drag:
            return
        t = self._t(x)
        if self._drag == "in":
            self.t_in = min(t, self.t_out - 0.5)
            self.seek.emit(self.t_in)
        elif self._drag == "out":
            self.t_out = max(t, self.t_in + 0.5)
            self.seek.emit(self.t_out)
        else:
            self.seek.emit(t)
        self.update()

    def mouseReleaseEvent(self, _):
        if self._drag in ("in", "out"):
            self.rangeChanged.emit(self.t_in, self.t_out)
        self._drag = None


class ClipCard(QFrame):
    clicked = Signal(object, object)   # clip, modifiers
    favorite = Signal(object)
    menuRequested = Signal(object, object)  # clip, global pos

    W = 360

    def __init__(self, clip, parent=None):
        super().__init__(parent)
        self.clip = clip
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(self.W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.setSpacing(4)
        self.thumb = QLabel()
        self.thumb.setFixedSize(self.W, self.W * 9 // 16)
        self.thumb.setStyleSheet("background:#0e1115; border-top-left-radius:6px; border-top-right-radius:6px;")
        self.thumb.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.thumb)
        self.badge = QLabel(("✂ " if clip.trim_out else "") + fmt_dur(clip.duration), self.thumb)
        self.badge.setObjectName("badge")
        self.badge.adjustSize()
        self.badge.move(self.W - self.badge.width() - 10, 10)
        if clip.events or clip.highlight or clip.markers:
            if clip.events:
                n = len(clip.events)
                text = f"💀 {clip.title.upper()}" if clip.title and clip.highlight else f"💀 {n} KILL{'S' if n > 1 else ''}"
            elif clip.highlight:
                text = "⚑ HIGHLIGHT"
            else:
                text = f"⚑ {len(clip.markers)} MARKER" + ("S" if len(clip.markers) > 1 else "")
            flag = QLabel(text, self.thumb)
            flag.setObjectName("badge")
            flag.setStyleSheet(f"color:{theme.YELLOW};")
            flag.adjustSize()
            flag.move(10, 10)
        if getattr(clip, "has_cam", 0) == 1:
            cam = QLabel("📷", self.thumb)
            cam.setObjectName("badge")
            cam.setToolTip("Webcam recorded in this clip")
            cam.adjustSize()
            cam.move(self.W - self.badge.width() - cam.width() - 18, 10)
        self.star = QPushButton("★" if clip.favorite else "☆", self.thumb)
        self.star.setObjectName("icon")
        self.star.setStyleSheet(f"color:{theme.YELLOW if clip.favorite else 'white'}; font-size:18px;"
                                "background:rgba(20,24,30,0.7); border-radius:14px;")
        self.star.setFixedSize(30, 30)
        self.star.move(self.W - 40, self.thumb.height() - 40)
        self.star.clicked.connect(lambda: self.favorite.emit(self.clip))

        body = QHBoxLayout()
        body.setContentsMargins(14, 6, 8, 0)
        col = QVBoxLayout()
        col.setSpacing(2)
        g = QLabel(clip.game.upper())
        g.setObjectName("game")
        t = QLabel(clip.display_title)
        t.setObjectName("cardtitle")
        t.setMaximumWidth(self.W - 90)
        tags = getattr(clip, "tags", None) or []
        a = QLabel("◷ " + rel_time(clip.created) + ("   " + " ".join("#" + x for x in tags[:3]) if tags else ""))
        a.setObjectName("muted")
        col.addWidget(g)
        col.addWidget(t)
        col.addWidget(a)
        body.addLayout(col, 1)
        more = QPushButton("⋮")
        more.setObjectName("icon")
        more.setFixedSize(34, 34)
        more.clicked.connect(lambda: self.menuRequested.emit(self.clip, more.mapToGlobal(more.rect().bottomLeft())))
        body.addWidget(more, 0, Qt.AlignVCenter)
        lay.addLayout(body)
        self.set_selected(False)

    def set_thumb(self, path: Path | None):
        if path and Path(path).exists():
            pm = QPixmap(str(path))
            if not pm.isNull():
                self.thumb.setPixmap(pm.scaled(self.thumb.size(), Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation))
                self.badge.raise_()
                self.star.raise_()

    def set_selected(self, on: bool):
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.clip, e.modifiers())
        elif e.button() == Qt.RightButton:
            self.menuRequested.emit(self.clip, e.globalPosition().toPoint())


class Toast(QWidget):
    """Small always-on-top notification. Shows over borderless/windowed games without stealing focus."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        lay = QHBoxLayout(self)
        self.lbl = QLabel()
        self._base = ("background: rgba(22,27,33,0.92); color: white; border-radius: 6px; padding: 12px 18px;"
                      "font-size: 14px; font-weight: 600;")
        lay.addWidget(self.lbl)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, color: str | None = None, ms: int = 2200):
        self.lbl.setText(text)
        self.lbl.setStyleSheet(self._base + f"border-left: 4px solid {color or theme.ACCENT};")
        self.adjustSize()
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.top() + 24)
        self.show()
        self._timer.start(ms)
