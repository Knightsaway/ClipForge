"""Studio → 📱 Final look tab: see the finished frame and drag / resize your webcam box and GIFs anywhere on it."""
from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QMovie, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLineEdit, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from .. import ff
from ..editor import CAM_ASPECTS, _gif_rect, cam_rect, cam_spot, default_cam_box
from . import theme

SPOT_ICONS = {"top-left": "↖", "top": "↑", "top-right": "↗", "left": "←", "right": "→",
              "bottom-left": "↙", "bottom": "↓", "bottom-right": "↘"}


def grab_frame(path: str, t: float, video_index: int, out: Path) -> bool:
    """One frame of video track `video_index` (0 = game, 1 = webcam) as a small jpg."""
    try:
        r = subprocess.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}",
                            "-i", path, "-map", f"0:v:{video_index}", "-frames:v", "1", "-vf", "scale=-2:540",
                            "-q:v", "4", str(out)], capture_output=True, timeout=20, **ff.popen_flags("normal"))
        return r.returncode == 0 and out.exists()
    except Exception:
        return False


class CamCanvas(QWidget):
    """The finished video frame (9:16 or 16:9) with the webcam box on top. Drag to move, drag a corner to resize."""
    boxChanged = Signal(dict)
    gifChanged = Signal()
    hookChanged = Signal()
    itemChanged = Signal()
    selectionChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setMouseTracking(True)
        self.vertical = True
        self.layout_key = "blur"
        self.mode = "box"
        self.box = default_cam_box(True)
        self.game: QImage | None = None
        self.cam: QImage | None = None
        self.has_cam = False
        self.gifs: list[dict] = []          # the selected piece's GIF effects (edited in place)
        self.gif_imgs: dict[str, QImage] = {}
        self.selected = "cam"               # "cam", "hook" or the index of a GIF
        self.hook_text = ""
        self.hook_pos: dict | None = None    # {"cx", "cy"} centre of the text; None = the usual spot
        self.hook_scale = 1.0
        # live playback ("Final look"): only show things at their moment, GIFs animate
        self.timed = False
        self.now_src = 0.0          # seconds into the current clip
        self.now_out = 0.0          # seconds into the finished video
        self.hook_window = (0.0, 1e9)
        self.caster_text = ""
        self.movies: dict[str, QMovie] = {}
        self.items: list[dict] = []      # your text / blur boxes / mutes (the Studio's list, edited in place)
        self._drag = None   # (item, "move" | "tl" | "tr" | "bl" | "br", start mouse, start box)

    # ---------- geometry
    def _frame(self) -> QRectF:
        W, H = (1080, 1920) if self.vertical else (1920, 1080)
        avail_w, avail_h = self.width() - 20, self.height() - 20
        sc = min(avail_w / W, avail_h / H)
        w, h = W * sc, H * sc
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _box_rect(self, fr: QRectF) -> QRectF:
        W, H = (1080, 1920) if self.vertical else (1920, 1080)
        x, y, w, h = cam_rect(self.box, W, H)
        k = fr.width() / W
        return QRectF(fr.x() + x * k, fr.y() + y * k, w * k, h * k)

    def _gif_box_rect(self, g: dict, fr: QRectF) -> QRectF:
        W, H = (1080, 1920) if self.vertical else (1920, 1080)
        x, y, w, h = _gif_rect(g, W, H)
        k = fr.width() / W
        return QRectF(fr.x() + x * k, fr.y() + y * k, w * k, h * k)

    def _hook_layout(self, fr: QRectF):
        """(font, lines, block rect) of the hook text as it will look in the video."""
        from ..editor import _wrap
        W = 1080 if self.vertical else 1920
        k = fr.width() / W
        px = (84 if self.vertical else 72) * self.hook_scale * k
        font = QFont("Arial Black")
        font.setBold(True)
        font.setPixelSize(max(6, int(px)))
        lines = _wrap(self.hook_text.upper(), 16 if self.vertical else 30).split("\n")
        fm = QFontMetricsF(font)
        bw = max((fm.horizontalAdvance(l) for l in lines), default=10.0)
        bh = fm.height() * len(lines) + 10 * k * (len(lines) - 1)
        if self.hook_pos:
            cx = fr.x() + float(self.hook_pos.get("cx", 0.5)) * fr.width()
            cy = fr.y() + float(self.hook_pos.get("cy", 0.15)) * fr.height()
            x, y = cx - bw / 2, cy - bh / 2
        else:
            x = fr.x() + (fr.width() - bw) / 2
            y = fr.y() + fr.height() * (0.10 if self.vertical else 0.08)
        x = max(fr.x() + 10 * k, min(fr.right() - bw - 10 * k, x))
        y = max(fr.y() + 10 * k, min(fr.bottom() - bh - 10 * k, y))
        return font, lines, QRectF(x, y, bw, bh)

    def _movie(self, path: str, run: bool) -> QMovie | None:
        """Animated GIF / WebP for live playback (started only while it's on screen)."""
        if not path or Path(path).suffix.lower() not in (".gif", ".webp", ".apng"):
            return None
        mv = self.movies.get(path)
        if mv is None:
            mv = QMovie(path)
            if not mv.isValid():
                self.movies[path] = mv
                return None
            mv.setCacheMode(QMovie.CacheAll)
            mv.frameChanged.connect(lambda *_: self.update() if self.timed else None)
            self.movies[path] = mv
        if not mv.isValid():
            return None
        running = mv.state() == QMovie.Running
        if run and not running:
            mv.start()
        elif not run and running:
            mv.setPaused(True)
        return mv

    def _item_visible(self, k: int) -> bool:
        it = self.items[k]
        if it.get("kind") not in ("text", "blur"):
            return False
        on = float(it["t"]) <= self.now_out <= float(it["t"]) + float(it.get("dur", 1.0))
        return on or (not self.timed and self.selected == ("item", k))

    def _text_layout(self, it: dict, fr: QRectF):
        from ..editor import _wrap
        W = 1080 if self.vertical else 1920
        k = fr.width() / W
        px = (76 if self.vertical else 64) * float(it.get("scale", 1.0)) * k
        font = QFont("Arial Black")
        font.setBold(True)
        font.setPixelSize(max(6, int(px)))
        lines = _wrap(str(it.get("text", "")), 18 if self.vertical else 34).split("\n")
        fm = QFontMetricsF(font)
        bw = max((fm.horizontalAdvance(l) for l in lines), default=10.0)
        bh = fm.height() * len(lines) + 8 * k * (len(lines) - 1)
        cx = fr.x() + float(it.get("cx", 0.5)) * fr.width()
        cy = fr.y() + float(it.get("cy", 0.5)) * fr.height()
        x = max(fr.x() + 10 * k, min(fr.right() - bw - 10 * k, cx - bw / 2))
        y = max(fr.y() + 10 * k, min(fr.bottom() - bh - 10 * k, cy - bh / 2))
        return font, lines, QRectF(x, y, bw, bh)

    def _blur_rect(self, it: dict, fr: QRectF) -> QRectF:
        return QRectF(fr.x() + float(it.get("x", 0)) * fr.width(), fr.y() + float(it.get("y", 0)) * fr.height(),
                      float(it.get("w", 0.3)) * fr.width(), float(it.get("h", 0.1)) * fr.height())

    def _paint_items(self, p: QPainter, fr: QRectF):
        colours = {"white": ("white", None), "yellow": ("#ffd23f", None), "green": ("#3ddc97", None),
                   "red": ("white", "#e0182d"), "black": ("black", "white")}
        for k, it in enumerate(self.items):
            if not self._item_visible(k):
                continue
            if it["kind"] == "blur":
                r = self._blur_rect(it, fr)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(140, 150, 165, 200))
                p.drawRect(r)
                p.setBrush(QBrush(QColor(255, 255, 255, 60), Qt.Dense5Pattern))
                p.drawRect(r)
                p.setPen(QColor("white"))
                p.drawText(r, Qt.AlignCenter, "▦ blurred")
            else:
                font, lines, r = self._text_layout(it, fr)
                fg, box = colours.get(it.get("style", "white"), ("white", None))
                fm = QFontMetricsF(font)
                if box:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(box))
                    pad = font.pixelSize() * 0.22
                    p.drawRect(r.adjusted(-pad, -pad, pad, pad))
                path = QPainterPath()
                yy = r.y() + fm.ascent()
                for line in lines:
                    path.addText(QPointF(r.x() + (r.width() - fm.horizontalAdvance(line)) / 2, yy), font, line)
                    yy += fm.height() + 8 * fr.width() / (1080 if self.vertical else 1920)
                if not box:
                    p.setPen(QPen(QColor("black"), max(2.0, font.pixelSize() / 11), Qt.SolidLine, Qt.RoundCap,
                                  Qt.RoundJoin))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(fg))
                p.drawPath(path)
            if self.selected == ("item", k):
                self._paint_selection(p, self._rect_of(("item", k)))

    def _cam_visible(self) -> bool:
        return self.mode == "box" and self.has_cam

    # ---------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG) if hasattr(theme, "BG") else QColor("#14161c"))
        fr = self._frame()
        p.fillRect(fr, QColor("#000"))
        stack = self.vertical and self.mode == "stack" and self.has_cam
        if self.game is not None and not self.game.isNull():
            self._paint_game(p, fr, stack)
        else:
            p.setPen(QColor("#888"))
            p.drawText(fr, Qt.AlignCenter, "Select a piece\nto see the frame")
        if stack:
            top = QRectF(fr.x(), fr.y(), fr.width(), fr.height() * 0.33)
            self._paint_img(p, self.cam, top)
        elif self._cam_visible():
            br = self._box_rect(fr)
            self._paint_img(p, self.cam, br)
            p.setPen(QPen(QColor("white"), 2))
            p.drawRect(br)
            if self.selected == "cam":
                self._paint_selection(p, br)
        for i, g in enumerate(self.gifs):
            on = g["t"] <= self.now_src <= g["t"] + g.get("dur", 2.0)
            mv = self._movie(g.get("path", ""), self.timed and on)
            if self.timed and not on:
                continue
            gr = self._gif_box_rect(g, fr)
            img = mv.currentImage() if (self.timed and mv is not None) else self.gif_imgs.get(g.get("path", ""))
            if img is not None and not img.isNull():
                p.drawImage(gr, img)
            else:
                p.fillRect(gr, QColor(255, 107, 214, 90))
                p.setPen(QColor("white"))
                p.drawText(gr, Qt.AlignCenter, "GIF")
            if self.selected == i:
                self._paint_selection(p, gr)
            else:
                p.setPen(QPen(QColor("#ff6bd6"), 1, Qt.DotLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(gr)
        self._paint_items(p, fr)
        show_hook = not self.timed or self.hook_window[0] <= self.now_out <= self.hook_window[1]
        if self.hook_text.strip() and show_hook:
            font, lines, hr = self._hook_layout(fr)
            fm = QFontMetricsF(font)
            path = QPainterPath()
            yy = hr.y() + fm.ascent()
            for line in lines:
                path.addText(QPointF(hr.x() + (hr.width() - fm.horizontalAdvance(line)) / 2, yy), font, line)
                yy += fm.height() + 10 * fr.width() / (1080 if self.vertical else 1920)
            p.setPen(QPen(QColor("black"), max(2.0, font.pixelSize() / 10), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("white"))
            p.drawPath(path)
            if self.selected == "hook":
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.DashLine))
                p.drawRect(hr.adjusted(-6, -6, 6, 6))
        if self.timed and self.caster_text:      # what the caster is saying right now
            f2 = QFont(self.font())
            f2.setPointSize(10)
            f2.setBold(True)
            p.setFont(f2)
            bubble = QRectF(fr.x() + 8, fr.bottom() - 40, fr.width() - 16, 30)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(bubble, 8, 8)
            p.setPen(QColor("#ffd23f"))
            p.drawText(bubble, Qt.AlignCenter, "🎙 " + self.caster_text)
        p.setPen(QPen(QColor("#555"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(fr)
        p.end()

    def _paint_selection(self, p: QPainter, r: QRectF):
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT), 2, Qt.DashLine))
        p.drawRect(r.adjusted(-3, -3, 3, 3))
        p.setBrush(QColor(theme.ACCENT))
        p.setPen(Qt.NoPen)
        for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            p.drawEllipse(c, 6, 6)

    def _paint_img(self, p: QPainter, img: QImage | None, target: QRectF):
        """Fill target, cropping the image (like the export does)."""
        if img is None or img.isNull():
            p.fillRect(target, QColor("#2a2d38"))
            p.setPen(QColor("#bbb"))
            p.drawText(target, Qt.AlignCenter, "📷")
            return
        ia = img.width() / img.height()
        ta = target.width() / max(1, target.height())
        if ia > ta:
            sw = img.height() * ta
            src = QRectF((img.width() - sw) / 2, 0, sw, img.height())
        else:
            sh = img.width() / ta
            src = QRectF(0, (img.height() - sh) / 2, img.width(), sh)
        p.drawImage(target, img, src)

    def _paint_game(self, p: QPainter, fr: QRectF, stack: bool):
        img = self.game
        if not self.vertical:
            self._paint_img(p, img, fr)
            return
        if stack:
            self._paint_img(p, img, QRectF(fr.x(), fr.y() + fr.height() * 0.33, fr.width(), fr.height() * 0.67))
            return
        lay = self.layout_key if self.layout_key != "facecam" else "crop"
        if lay == "crop":
            self._paint_img(p, img, fr)
        elif lay == "fit":
            h = fr.width() * img.height() / img.width()
            p.drawImage(QRectF(fr.x(), fr.y() + (fr.height() - h) / 2, fr.width(), h), img)
        else:  # blurred background (darkened) + sharp middle
            self._paint_img(p, img.scaled(48, 48 * img.height() // img.width()), fr)
            p.fillRect(fr, QColor(0, 0, 0, 90))
            h = fr.width() * (1.0 if lay == "zoom" else 0.75)
            self._paint_img(p, img, QRectF(fr.x(), fr.y() + (fr.height() - h) / 2, fr.width(), h))

    # ---------- mouse
    def _rect_of(self, item) -> QRectF | None:
        fr = self._frame()
        if item == "cam":
            return self._box_rect(fr) if self._cam_visible() else None
        if isinstance(item, int) and 0 <= item < len(self.gifs):
            return self._gif_box_rect(self.gifs[item], fr)
        if item == "hook" and self.hook_text.strip():
            return self._hook_layout(fr)[2].adjusted(-6, -6, 6, 6)
        if isinstance(item, tuple) and item[0] == "item" and 0 <= item[1] < len(self.items):
            it = self.items[item[1]]
            if it.get("kind") == "blur":
                return self._blur_rect(it, fr)
            if it.get("kind") == "text":
                return self._text_layout(it, fr)[2].adjusted(-6, -6, 6, 6)
        return None

    def _hit(self, pos: QPointF):
        """(item, part). Corners of the selected item first, then GIFs on top, then the webcam."""
        vis = [("item", k) for k in reversed(range(len(self.items))) if self._item_visible(k)]
        order = [self.selected] + vis + list(reversed(range(len(self.gifs)))) + ["hook", "cam"]
        for item in order:
            r = self._rect_of(item)
            if r is None:
                continue
            text_item = isinstance(item, tuple) and self.items[item[1]].get("kind") == "text"
            if item == self.selected and item != "hook" and not text_item:
                for name, c in (("tl", r.topLeft()), ("tr", r.topRight()), ("bl", r.bottomLeft()),
                                ("br", r.bottomRight())):
                    if (pos - c).manhattanLength() < 14:
                        return item, name
            if r.contains(pos):
                return item, "move"
        return None, None

    def select(self, item):
        self.selected = item
        self.update()
        self.selectionChanged.emit(item)

    def mousePressEvent(self, e):
        item, part = self._hit(e.position())
        if item is None:
            return
        if item != self.selected:
            self.select(item)
        if item == "hook":
            fr = self._frame()
            r = self._hook_layout(fr)[2]
            box = {"cx": (r.center().x() - fr.x()) / fr.width(), "cy": (r.center().y() - fr.y()) / fr.height()}
        elif isinstance(item, tuple):
            box = dict(self.items[item[1]])
        else:
            box = dict(self.box) if item == "cam" else dict(self.gifs[item].get("box") or {})
        self._drag = (item, part, e.position(), box)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            _, part = self._hit(pos)
            self.setCursor({None: Qt.ArrowCursor, "move": Qt.SizeAllCursor, "tl": Qt.SizeFDiagCursor,
                            "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}[part])
            return
        item, kind, start, b0 = self._drag
        fr = self._frame()
        dx = (pos.x() - start.x()) / fr.width()
        dy = (pos.y() - start.y()) / fr.height()
        b = dict(b0)
        frame_aspect = fr.width() / fr.height()
        if isinstance(item, tuple):          # your text / blur box
            it = self.items[item[1]]
            if it.get("kind") == "text":
                it["cx"] = round(max(0.0, min(1.0, b0.get("cx", 0.5) + dx)), 4)
                it["cy"] = round(max(0.0, min(1.0, b0.get("cy", 0.5) + dy)), 4)
            elif kind == "move":
                it["x"] = round(max(0.0, min(1.0 - it["w"], b0["x"] + dx)), 4)
                it["y"] = round(max(0.0, min(1.0 - it["h"], b0["y"] + dy)), 4)
            else:                              # blur boxes resize freely from any corner
                x0, y0, x1, y1 = b0["x"], b0["y"], b0["x"] + b0["w"], b0["y"] + b0["h"]
                if "l" in kind:
                    x0 = min(x1 - 0.03, max(0.0, x0 + dx))
                if "r" in kind:
                    x1 = max(x0 + 0.03, min(1.0, x1 + dx))
                if "t" in kind:
                    y0 = min(y1 - 0.02, max(0.0, y0 + dy))
                if "b" in kind:
                    y1 = max(y0 + 0.02, min(1.0, y1 + dy))
                it.update(x=round(x0, 4), y=round(y0, 4), w=round(x1 - x0, 4), h=round(y1 - y0, 4))
            self.update()
            self.itemChanged.emit()
            return
        if item == "hook":
            self.hook_pos = {"cx": round(max(0.0, min(1.0, b0["cx"] + dx)), 4),
                             "cy": round(max(0.0, min(1.0, b0["cy"] + dy)), 4)}
            self.update()
            self.hookChanged.emit()
            return
        if item == "cam":
            hpw = 1 / CAM_ASPECTS.get(b0.get("aspect", "16:9"), 16 / 9)   # box height / width
        else:
            b.setdefault("x", 0.3)
            b.setdefault("y", 0.3)
            b.setdefault("w", 0.4)
            b0 = dict(b)
            hpw = float(self.gifs[item].get("ratio", 1.0))
        if kind == "move":
            b["x"], b["y"] = b0["x"] + dx, b0["y"] + dy
        else:
            sign = 1 if kind in ("br", "tr") else -1
            w = max(0.06, min(1.0, b0["w"] + sign * dx))
            b["w"] = w
            if kind in ("tl", "bl"):
                b["x"] = b0["x"] + (b0["w"] - w)
            if kind in ("tl", "tr"):
                b["y"] = b0["y"] + (b0["w"] - w) * hpw * frame_aspect
        hfrac = b["w"] * hpw * frame_aspect
        if item == "cam":    # the webcam stays fully inside the frame
            b["x"] = max(0.0, min(1.0 - b["w"], b["x"]))
            b["y"] = max(0.0, min(max(0.0, 1.0 - hfrac), b["y"]))
        else:                # a GIF may hang half off the edge (peeking in)
            b["x"] = max(-b["w"] / 2, min(1.0 - b["w"] / 2, b["x"]))
            b["y"] = max(-hfrac / 2, min(1.0 - hfrac / 2, b["y"]))
        b = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items()}
        if item == "cam":
            self.box = b
            self.update()
            self.boxChanged.emit(self.box)
        else:
            self.gifs[item]["box"] = b
            self.update()
            self.gifChanged.emit()

    def mouseReleaseEvent(self, e):
        self._drag = None


class CamEditor(QWidget):
    """Canvas + quick controls. `changed` fires when the webcam setup changes, `gifsChanged` when a GIF does."""
    changed = Signal()
    gifsChanged = Signal()
    hookChanged = Signal()
    itemsChanged = Signal()
    _framesReady = Signal(object, object)

    def __init__(self, s, parent=None):
        super().__init__(parent)
        self.s = s
        cfg = dict(s.cam_layout or {})
        self._boxes = {True: cfg.get("box_v") or default_cam_box(True),
                       False: cfg.get("box_h") or default_cam_box(False)}
        self.canvas = CamCanvas()
        self.canvas.mode = cfg.get("mode", "box")
        self.canvas.box = dict(self._boxes[True])
        self.canvas.boxChanged.connect(self._box_dragged)
        self.canvas.gifChanged.connect(self._gif_dragged)
        self.canvas.selectionChanged.connect(self._selected)
        self.canvas.hookChanged.connect(self.hookChanged)
        self.canvas.itemChanged.connect(self.itemsChanged)
        self._framesReady.connect(self._frames)
        self.playhead = lambda: 0.0          # set by the Studio
        self.playhead_out = lambda: 0.0      # seconds into the finished video (set by the Studio)

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{theme.YELLOW};")
        v.addWidget(self.note)
        v.addWidget(self.canvas, 1)

        ctl = QFrame()
        ctl.setObjectName("panel")
        g = QHBoxLayout(ctl)
        self.cam_panel = QWidget()
        left = QVBoxLayout(self.cam_panel)
        left.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItem("📷 Webcam box (drag it anywhere)", "box")
        self.mode.addItem("Webcam on top, gameplay below (Shorts)", "stack")
        self.mode.addItem("No webcam in this video", "off")
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.canvas.mode)))
        self.mode.currentIndexChanged.connect(self._mode)
        left.addWidget(self.mode)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size"))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(10, 100)
        self.size.setValue(int(self.canvas.box.get("w", 0.4) * 100))
        self.size.valueChanged.connect(self._size)
        size_row.addWidget(self.size, 1)
        left.addLayout(size_row)
        shape_row = QHBoxLayout()
        shape_row.addWidget(QLabel("Shape"))
        self.shape = QComboBox()
        for k, label in (("16:9", "Wide 16:9"), ("4:3", "4:3"), ("1:1", "Square"), ("3:4", "Tall 3:4")):
            self.shape.addItem(label, k)
        self.shape.setCurrentIndex(max(0, self.shape.findData(self.canvas.box.get("aspect", "16:9"))))
        self.shape.currentIndexChanged.connect(self._shape)
        shape_row.addWidget(self.shape, 1)
        self.border = QCheckBox("White border")
        self.border.setChecked(self.canvas.box.get("border", True) is not False)
        self.border.toggled.connect(self._border)
        shape_row.addWidget(self.border)
        left.addLayout(shape_row)
        hint = QLabel("Drag the box to move it, drag a corner to resize. Saved for Auto Shorts and every "
                      "automatic video too.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        left.addWidget(hint)
        g.addWidget(self.cam_panel, 1)

        # GIF controls (shown when a GIF is selected on the canvas)
        self.gif_panel = QWidget()
        gl = QVBoxLayout(self.gif_panel)
        gl.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.gif_name = QLabel("")
        self.gif_name.setStyleSheet("color:#ff6bd6; font-weight:bold;")
        back = QPushButton("📷 Webcam")
        back.setToolTip("Select the webcam box again")
        back.clicked.connect(lambda: self.canvas.select("cam"))
        top.addWidget(self.gif_name, 1)
        top.addWidget(back)
        gl.addLayout(top)
        gsize_row = QHBoxLayout()
        gsize_row.addWidget(QLabel("Size"))
        self.gif_size = QSlider(Qt.Horizontal)
        self.gif_size.setRange(6, 100)
        self.gif_size.valueChanged.connect(self._gif_size)
        gsize_row.addWidget(self.gif_size, 1)
        gl.addLayout(gsize_row)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Show for"))
        self.gif_dur = QDoubleSpinBox()
        self.gif_dur.setRange(0.3, 60.0)
        self.gif_dur.setSingleStep(0.5)
        self.gif_dur.setSuffix(" s")
        self.gif_dur.valueChanged.connect(self._gif_dur)
        trow.addWidget(self.gif_dur)
        at = QPushButton("⏱ Start at playhead")
        at.setToolTip("Move this GIF so it appears where the preview's playhead is")
        at.clicked.connect(self._gif_at_playhead)
        rm = QPushButton("🗑 Remove")
        rm.clicked.connect(self._gif_remove)
        trow.addWidget(at)
        trow.addWidget(rm)
        gl.addLayout(trow)
        ghint = QLabel("Drag to move (it can peek in from the edge), drag a corner to resize. "
                       "The pink bar on the timeline shows when it's on screen.")
        ghint.setObjectName("muted")
        ghint.setWordWrap(True)
        gl.addWidget(ghint)
        self.gif_panel.setVisible(False)
        g.addWidget(self.gif_panel, 1)

        # hook text controls (shown when the text is selected)
        self.hook_panel = QWidget()
        hl = QVBoxLayout(self.hook_panel)
        hl.setContentsMargins(0, 0, 0, 0)
        htop = QHBoxLayout()
        ht = QLabel("T  Hook text")
        ht.setStyleSheet("font-weight:bold;")
        back2 = QPushButton("📷 Webcam")
        back2.clicked.connect(lambda: self.canvas.select("cam"))
        htop.addWidget(ht, 1)
        htop.addWidget(back2)
        hl.addLayout(htop)
        hs_row = QHBoxLayout()
        hs_row.addWidget(QLabel("Text size"))
        self.hook_size = QSlider(Qt.Horizontal)
        self.hook_size.setRange(40, 200)
        self.hook_size.setValue(100)
        self.hook_size.valueChanged.connect(self._hook_size)
        hs_row.addWidget(self.hook_size, 1)
        hl.addLayout(hs_row)
        reset = QPushButton("↺ Usual spot")
        reset.setToolTip("Put the text back in its normal place at the top")
        reset.clicked.connect(self._hook_reset)
        hl.addWidget(reset)
        hhint = QLabel("Drag the text anywhere on the video. Change the words in the Hook box on the right; "
                       "when it shows is on the timeline's Text track.")
        hhint.setObjectName("muted")
        hhint.setWordWrap(True)
        hl.addWidget(hhint)
        self.hook_panel.setVisible(False)
        g.addWidget(self.hook_panel, 1)

        # your text / blur box controls
        self.item_panel = QWidget()
        il = QVBoxLayout(self.item_panel)
        il.setContentsMargins(0, 0, 0, 0)
        itop = QHBoxLayout()
        self.item_title = QLabel("")
        self.item_title.setStyleSheet("font-weight:bold;")
        back3 = QPushButton("📷 Webcam")
        back3.clicked.connect(lambda: self.canvas.select("cam"))
        itop.addWidget(self.item_title, 1)
        itop.addWidget(back3)
        il.addLayout(itop)
        self.item_text = QLineEdit()
        self.item_text.setPlaceholderText("Text")
        self.item_text.textEdited.connect(lambda t: self._item_set(text=t))
        il.addWidget(self.item_text)
        srow = QHBoxLayout()
        self.item_style = QComboBox()
        from ..editor import TEXT_STYLE_NAMES
        for k, label in TEXT_STYLE_NAMES.items():
            self.item_style.addItem(label, k)
        self.item_style.activated.connect(lambda *_: self._item_set(style=self.item_style.currentData()))
        self.item_size = QSlider(Qt.Horizontal)
        self.item_size.setRange(40, 250)
        self.item_size.valueChanged.connect(lambda v: self._item_set(scale=v / 100))
        self.item_pop = QCheckBox("Pop in")
        self.item_pop.toggled.connect(lambda on: self._item_set(pop=on))
        srow.addWidget(self.item_style)
        srow.addWidget(QLabel("Size"))
        srow.addWidget(self.item_size, 1)
        srow.addWidget(self.item_pop)
        self.item_text_row = QWidget()
        self.item_text_row.setLayout(srow)
        il.addWidget(self.item_text_row)
        trow2 = QHBoxLayout()
        trow2.addWidget(QLabel("Template"))
        self.item_tpl = QComboBox()
        self.item_tpl.setToolTip("Saved text looks — pick one to apply it to this text")
        self.item_tpl.activated.connect(self._tpl_apply)
        tpl_save = QPushButton("Save look…")
        tpl_save.setToolTip("Save this text's look (colour, size, place, pop, length) to reuse with one click.\n"
                            "The last one you save or pick is used for new text (T+ Text).")
        tpl_save.clicked.connect(self._tpl_save)
        trow2.addWidget(self.item_tpl, 1)
        trow2.addWidget(tpl_save)
        self.item_tpl_row = QWidget()
        self.item_tpl_row.setLayout(trow2)
        il.addWidget(self.item_tpl_row)
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Show for"))
        self.item_dur = QDoubleSpinBox()
        self.item_dur.setRange(0.2, 120.0)
        self.item_dur.setSingleStep(0.5)
        self.item_dur.setSuffix(" s")
        self.item_dur.valueChanged.connect(lambda v: self._item_set(dur=round(v, 2)))
        at2 = QPushButton("⏱ Start at playhead")
        at2.clicked.connect(lambda: self._item_set(t=round(self.playhead_out(), 2)))
        rm2 = QPushButton("🗑 Remove")
        rm2.clicked.connect(self._item_remove)
        drow.addWidget(self.item_dur)
        drow.addWidget(at2)
        drow.addWidget(rm2)
        il.addLayout(drow)
        self.item_panel.setVisible(False)
        g.addWidget(self.item_panel, 1)

        spots = QGridLayout()
        spots.setSpacing(4)
        pos = {"top-left": (0, 0), "top": (0, 1), "top-right": (0, 2), "left": (1, 0), "right": (1, 2),
               "bottom-left": (2, 0), "bottom": (2, 1), "bottom-right": (2, 2)}
        for spot, (r, c) in pos.items():
            b = QPushButton(SPOT_ICONS[spot])
            b.setFixedSize(38, 32)
            b.setToolTip(f"Put the selected webcam / GIF {spot.replace('-', ' ')}")
            b.clicked.connect(lambda _=False, sp=spot: self._spot(sp))
            spots.addWidget(b, r, c)
        mid = QLabel("📷")
        mid.setAlignment(Qt.AlignCenter)
        spots.addWidget(mid, 1, 1)
        g.addLayout(spots)
        v.addWidget(ctl)
        self._sync_controls()

    # ---------- public
    def set_format(self, vertical: bool, layout_key: str):
        if vertical != self.canvas.vertical:
            self._boxes[self.canvas.vertical] = dict(self.canvas.box)
            self.canvas.vertical = vertical
            self.canvas.box = dict(self._boxes[vertical])
        self.canvas.layout_key = layout_key
        self._sync_controls()
        self.canvas.update()

    def load_piece(self, path: str, t: float, has_cam: bool):
        self.canvas.has_cam = has_cam
        self._sync_controls()
        work = Path(tempfile.gettempdir()) / "ClipForge" / "camedit"
        work.mkdir(parents=True, exist_ok=True)

        def job():
            g, c = work / "game.jpg", work / "cam.jpg"
            for f in (g, c):
                try:
                    f.unlink()
                except OSError:
                    pass
            gi = QImage(str(g)) if grab_frame(path, t, 0, g) else None
            ci = QImage(str(c)) if has_cam and grab_frame(path, t, 1, c) else None
            self._framesReady.emit(gi, ci)
        threading.Thread(target=job, daemon=True).start()

    def set_live(self, playing: bool, now_src: float, now_out: float, hook_window: tuple, caster_text: str):
        c = self.canvas
        changed = (playing != c.timed or caster_text != c.caster_text or abs(now_out - c.now_out) > 0.03)
        c.timed, c.now_src, c.now_out, c.hook_window, c.caster_text = (playing, now_src, now_out, hook_window,
                                                                      caster_text)
        if not playing:
            for mv in c.movies.values():
                if mv.isValid() and mv.state() == QMovie.Running:
                    mv.setPaused(True)
        if changed:
            c.update()

    def live_game(self, img):
        self.canvas.game = img
        self.canvas.update()

    def live_cam(self, img):
        if self.canvas.has_cam:
            self.canvas.cam = img
            self.canvas.update()

    def set_gifs(self, effects: list[dict]):
        """Show the selected piece's GIFs on the canvas (the dicts are edited in place)."""
        self.canvas.gifs = [e for e in effects if e.get("type") == "gif"]
        for g in self.canvas.gifs:
            pth = g.get("path", "")
            if pth and pth not in self.canvas.gif_imgs:
                self.canvas.gif_imgs[pth] = QImage(pth)     # first frame is enough to place it
        sel = self.canvas.selected
        if isinstance(sel, int) and sel >= len(self.canvas.gifs):
            self.canvas.selected = "cam"
        self._selected(self.canvas.selected)
        self.canvas.update()

    def select_gif(self, index: int):
        if 0 <= index < len(self.canvas.gifs):
            self.canvas.select(index)

    def set_hook(self, text: str, pos: dict | None, scale: float):
        self.canvas.hook_text = text or ""
        self.canvas.hook_pos = pos
        self.canvas.hook_scale = scale
        if self.canvas.selected == "hook" and not text:
            self.canvas.selected = "cam"
        self.hook_size.blockSignals(True)
        self.hook_size.setValue(int(scale * 100))
        self.hook_size.blockSignals(False)
        self._selected(self.canvas.selected)
        self.canvas.update()

    def _hook_size(self, val):
        self.canvas.hook_scale = val / 100
        self.canvas.update()
        self.hookChanged.emit()

    def _hook_reset(self):
        self.canvas.hook_pos = None
        self.canvas.update()
        self.hookChanged.emit()

    def set_items(self, items: list[dict]):
        self.canvas.items = items
        sel = self.canvas.selected
        if isinstance(sel, tuple) and sel[1] >= len(items):
            self.canvas.selected = "cam"
        self._selected(self.canvas.selected)
        self.canvas.update()

    def select_item(self, k: int):
        if 0 <= k < len(self.canvas.items):
            self.canvas.select(("item", k))

    def _cur_item(self) -> dict | None:
        sel = self.canvas.selected
        if isinstance(sel, tuple) and 0 <= sel[1] < len(self.canvas.items):
            return self.canvas.items[sel[1]]
        return None

    def _item_set(self, **kw):
        it = self._cur_item()
        if it is None:
            return
        it.update(kw)
        self.canvas.update()
        self.itemsChanged.emit()

    TPL_KEYS = ("style", "scale", "cx", "cy", "pop", "dur")

    def _tpl_fill(self):
        self.item_tpl.blockSignals(True)
        self.item_tpl.clear()
        self.item_tpl.addItem("(choose…)", "")
        for name in sorted(self.s.text_templates):
            self.item_tpl.addItem(name + ("  ★" if name == self.s.text_default else ""), name)
        self.item_tpl.blockSignals(False)

    def _tpl_apply(self, *_):
        name = self.item_tpl.currentData()
        it = self._cur_item()
        if not name or it is None or name not in self.s.text_templates:
            return
        it.update({k: v for k, v in self.s.text_templates[name].items() if k in self.TPL_KEYS})
        self.s.text_default = name
        from ..config import save_settings
        save_settings(self.s)
        self._selected(self.canvas.selected)
        self.canvas.update()
        self.itemsChanged.emit()

    def _tpl_save(self):
        from PySide6.QtWidgets import QInputDialog
        it = self._cur_item()
        if it is None:
            return
        name, ok = QInputDialog.getText(self, "Text template", "Name this look (e.g. 'Red box, top'):")
        if not ok or not name.strip():
            return
        self.s.text_templates[name.strip()] = {k: it[k] for k in self.TPL_KEYS if k in it}
        self.s.text_default = name.strip()
        from ..config import save_settings
        save_settings(self.s)
        self._tpl_fill()

    def _item_remove(self):
        sel = self.canvas.selected
        if isinstance(sel, tuple) and 0 <= sel[1] < len(self.canvas.items):
            del self.canvas.items[sel[1]]
            self.canvas.select("cam")
            self.itemsChanged.emit()

    def _selected(self, item):
        is_gif = isinstance(item, int) and 0 <= item < len(self.canvas.gifs)
        is_hook = item == "hook"
        it = self._cur_item() if isinstance(item, tuple) else None
        self.item_panel.setVisible(it is not None)
        if it is not None:
            text = it.get("kind") == "text"
            self.item_title.setText("T  Your text" if text else "▦  Blur box")
            self.item_text.setVisible(text)
            self.item_text_row.setVisible(text)
            self.item_tpl_row.setVisible(text)
            self._tpl_fill()
            for w in (self.item_text, self.item_style, self.item_size, self.item_pop, self.item_dur):
                w.blockSignals(True)
            self.item_text.setText(str(it.get("text", "")))
            self.item_style.setCurrentIndex(max(0, self.item_style.findData(it.get("style", "white"))))
            self.item_size.setValue(int(float(it.get("scale", 1.0)) * 100))
            self.item_pop.setChecked(bool(it.get("pop", True)))
            self.item_dur.setValue(float(it.get("dur", 2.0)))
            for w in (self.item_text, self.item_style, self.item_size, self.item_pop, self.item_dur):
                w.blockSignals(False)
        self.gif_panel.setVisible(is_gif)
        self.hook_panel.setVisible(is_hook)
        self.cam_panel.setVisible(not is_gif and not is_hook and it is None)
        if is_gif:
            g = self.canvas.gifs[item]
            self.gif_name.setText("🎞 " + Path(g.get("path", "")).stem[:40])
            for w, val in ((self.gif_size, int((g.get("box") or {}).get("w", 0.4) * 100)),
                           (self.gif_dur, float(g.get("dur", 3.0)))):
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)

    def _cur_gif(self) -> dict | None:
        i = self.canvas.selected
        return self.canvas.gifs[i] if isinstance(i, int) and 0 <= i < len(self.canvas.gifs) else None

    def _gif_dragged(self):
        self._selected(self.canvas.selected)
        self.gifsChanged.emit()

    def _gif_size(self, val):
        g = self._cur_gif()
        if not g:
            return
        b = dict(g.get("box") or {"x": 0.3, "y": 0.3, "w": 0.4})
        cx, w_old = b.get("x", 0.3) + b.get("w", 0.4) / 2, b.get("w", 0.4)
        W, H = (1080, 1920) if self.canvas.vertical else (1920, 1080)
        ratio = float(g.get("ratio", 1.0))
        cy = b.get("y", 0.3) + w_old * ratio * W / H / 2
        b["w"] = val / 100
        b["x"] = round(cx - b["w"] / 2, 4)
        b["y"] = round(cy - b["w"] * ratio * W / H / 2, 4)
        g["box"] = b
        self.canvas.update()
        self.gifsChanged.emit()

    def _gif_dur(self, val):
        g = self._cur_gif()
        if g:
            g["dur"] = round(val, 2)
            self.gifsChanged.emit()

    def _gif_at_playhead(self):
        g = self._cur_gif()
        if g:
            g["t"] = round(self.playhead(), 2)
            self.gifsChanged.emit()

    def _gif_remove(self):
        g = self._cur_gif()
        if g:
            g["_remove"] = True
            self.canvas.selected = "cam"
            self.gifsChanged.emit()

    def set_config(self, cfg: dict):
        """Webcam setup from a draft or style."""
        cfg = dict(cfg or {})
        self._boxes = {True: cfg.get("box_v") or default_cam_box(True), False: cfg.get("box_h") or default_cam_box(False)}
        self.canvas.mode = cfg.get("mode", "box")
        self.canvas.box = dict(self._boxes[self.canvas.vertical])
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.canvas.mode)))
        self.mode.blockSignals(False)
        self._sync_controls()
        self.canvas.update()
        self.s.cam_layout = self.config()

    def config(self) -> dict:
        self._boxes[self.canvas.vertical] = dict(self.canvas.box)
        return {"mode": self.canvas.mode, "box_v": dict(self._boxes[True]), "box_h": dict(self._boxes[False])}

    # ---------- internals
    def _frames(self, gi, ci):
        self.canvas.game = gi
        self.canvas.cam = ci
        self.canvas.update()

    def _sync_controls(self):
        box = self.canvas.box
        for w, val in ((self.size, int(box.get("w", 0.4) * 100)),):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self.shape.blockSignals(True)
        self.shape.setCurrentIndex(max(0, self.shape.findData(box.get("aspect", "16:9"))))
        self.shape.blockSignals(False)
        self.border.blockSignals(True)
        self.border.setChecked(box.get("border", True) is not False)
        self.border.blockSignals(False)
        boxy = self.canvas.mode == "box"
        for w in (self.size, self.shape, self.border):
            w.setEnabled(boxy)
        if not self.canvas.has_cam:
            self.note.setText("⚠ This clip has no webcam recording, so there's nothing to place. Pick your camera in "
                              "Settings → Audio & webcam and press 🎥 Test there; new clips will carry it.")
        elif self.canvas.mode == "stack" and not self.canvas.vertical:
            self.note.setText("'Webcam on top' is for Shorts; 16:9 videos use the box.")
        else:
            self.note.setText("")

    def _save(self):
        self.s.cam_layout = self.config()
        from ..config import save_settings
        save_settings(self.s)
        self.changed.emit()

    def _box_dragged(self, box):
        self._sync_controls()
        self._save()

    def _set_box(self, box):
        self.canvas.box = box
        self.canvas.update()
        self._sync_controls()
        self._save()

    def _mode(self):
        self.canvas.mode = self.mode.currentData()
        self._sync_controls()
        self.canvas.update()
        self._save()

    def _spot(self, spot):
        it = self._cur_item()
        if it is not None:
            cx = {"left": 0.22, "right": 0.78}.get(spot.split("-")[-1], 0.5)
            cy = 0.15 if spot.startswith("top") else 0.8 if spot.startswith("bottom") else 0.5
            if it.get("kind") == "text":
                it.update(cx=cx, cy=cy)
            else:
                it.update(x=round(max(0.0, min(1 - it["w"], cx - it["w"] / 2)), 4),
                          y=round(max(0.0, min(1 - it["h"], cy - it["h"] / 2)), 4))
            self.canvas.update()
            self.itemsChanged.emit()
            return
        if self.canvas.selected == "hook":
            cx = {"left": 0.25, "right": 0.75}.get(spot.split("-")[-1], 0.5)
            cy = 0.12 if spot.startswith("top") else 0.82 if spot.startswith("bottom") else 0.5
            self.canvas.hook_pos = {"cx": cx, "cy": cy}
            self.canvas.update()
            self.hookChanged.emit()
            return
        g = self._cur_gif()
        if g:   # same 8 spots for a GIF, using its own shape
            b = g.get("box") or {}
            w = b.get("w", 0.4)
            ratio = float(g.get("ratio", 1.0))
            nb = cam_spot(spot, w, "16:9", self.canvas.vertical)
            W, H = (1080, 1920) if self.canvas.vertical else (1920, 1080)
            hf = w * ratio * W / H
            if "bottom" in spot:
                nb["y"] = round((0.86 if self.canvas.vertical else 0.97) - hf, 4)
            elif spot in ("left", "right"):
                nb["y"] = round((1 - hf) / 2, 4)
            g["box"] = {"x": nb["x"], "y": nb["y"], "w": w}
            self.canvas.update()
            self.gifsChanged.emit()
            return
        if self.canvas.mode != "box":
            self.mode.setCurrentIndex(self.mode.findData("box"))
        b = self.canvas.box
        nb = cam_spot(spot, b.get("w", 0.4), b.get("aspect", "16:9"), self.canvas.vertical)
        if b.get("border") is False:
            nb["border"] = False
        self._set_box(nb)

    def _size(self, val):
        b = dict(self.canvas.box)
        cx = b["x"] + b["w"] / 2
        b["w"] = val / 100
        b["x"] = max(0.0, min(1 - b["w"], cx - b["w"] / 2))
        self._set_box(b)

    def _shape(self):
        b = dict(self.canvas.box)
        b["aspect"] = self.shape.currentData()
        self._set_box(b)

    def _border(self, on):
        b = dict(self.canvas.box)
        b["border"] = bool(on)
        self._set_box(b)
