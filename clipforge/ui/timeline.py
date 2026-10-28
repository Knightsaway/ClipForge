"""Studio timeline: the whole edit on tracks, like a normal video editor.

Tracks: 🎬 clips · T text · ✨ effects · 🎞 GIFs · 🎙 caster. Everything can be dragged:
clip edges trim, effects / GIFs / caster lines / the hook text move in time, GIF and text blocks resize.
Click anywhere to jump there; right-click an item for more (split, remove, change the caster's words…).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QMenu, QWidget

from .. import timeline_model as tm
from . import theme
from .widgets import fmt_dur

GUTTER = 74
ROWS = [("video", "🎬 Clips", 40), ("text", "T  Text", 22), ("fx", "✨ Effects", 22), ("gif", "🎞 GIFs", 22),
        ("caster", "🎙 Caster", 22), ("blur", "▦ Blur/Mute", 30)]
RULER = 20
GAP = 4
FX_COL = {"zoom": "#4fc3ff", "slowmo": "#ff9f43", "sfx": "#3ddc97"}
FX_SYM = {"zoom": "Z", "slowmo": "S", "sfx": "♪"}
PIECE_COLS = ["#5b5ff0", "#7a5bf0", "#5b8ff0", "#8f5bf0"]


class EditTimeline(QWidget):
    seek = Signal(float)              # output time
    pieceSelected = Signal(int)
    changed = Signal()                # something was edited (after the mouse is released)
    gifSelected = Signal(int, int)    # piece index, GIF index within that piece's GIFs
    casterEdit = Signal(int)          # change the words of caster line k
    pieceAction = Signal(str, int, float)   # "split" / "remove" / "duplicate", piece index, clip time
    itemSelected = Signal(int)              # one of your text / blur / mute items

    def __init__(self, studio, parent=None):
        super().__init__(parent)
        self.studio = studio
        self.setMouseTracking(True)
        self.setMinimumHeight(RULER + sum(h + GAP for _, _, h in ROWS) + 8)
        self.setFocusPolicy(Qt.ClickFocus)
        self.playhead = 0.0
        self.zoom = 1.0
        self.offset = 0.0              # seconds scrolled off the left edge
        self._items: list[tuple[QRectF, tuple]] = []
        self._drag = None
        self._reorder_x: float | None = None   # x of a clip being dragged to a new place
        self.sel = None                          # selected item (keyboard: arrows nudge, Delete removes)
        self._thumbs: dict[str, QPixmap] = {}
        self.setToolTip("")

    # ---------- geometry
    def _rows(self) -> dict[str, tuple[float, float]]:
        y, out = RULER + 2, {}
        for key, _, h in ROWS:
            out[key] = (y, h)
            y += h + GAP
        return out

    def _total(self) -> float:
        return max(1.0, tm.total(self.studio.pieces), self._caster_end())

    def _caster_end(self) -> float:
        return max([c["t"] + c.get("dur", 1.5) for c in self.studio.caster_lines] or [0.0])

    def _scale(self) -> float:
        return (self.width() - GUTTER - 10) / self._total() * self.zoom

    def _x(self, t: float) -> float:
        return GUTTER + (t - self.offset) * self._scale()

    def _t(self, x: float) -> float:
        return (x - GUTTER) / self._scale() + self.offset

    def set_playhead(self, t: float):
        self.playhead = t
        # keep the playhead in view when zoomed in
        if self.zoom > 1.0:
            vis = (self.width() - GUTTER - 10) / self._scale()
            if t < self.offset or t > self.offset + vis:
                self.offset = max(0.0, t - vis * 0.2)
        self.update()

    def fit(self):
        self.zoom, self.offset = 1.0, 0.0
        self.update()

    def zoom_by(self, f: float):
        center = self.playhead
        self.zoom = max(1.0, min(40.0, self.zoom * f))
        vis = (self.width() - GUTTER - 10) / self._scale()
        self.offset = max(0.0, min(self._total() - vis, center - vis / 2)) if self.zoom > 1 else 0.0
        self.update()

    # ---------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        rows = self._rows()
        self._items = []
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        # track backgrounds + labels
        for key, label, h in ROWS:
            y, hh = rows[key]
            p.fillRect(QRectF(GUTTER, y, self.width() - GUTTER, hh), QColor(theme.CARD))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(6, y, GUTTER - 8, hh), Qt.AlignVCenter | Qt.AlignLeft, label)
        p.setClipRect(QRectF(GUTTER, 0, self.width() - GUTTER, self.height()))
        self._paint_ruler(p)
        lay = tm.layout(self.studio.pieces)
        self._paint_pieces(p, rows["video"], lay)
        self._paint_text(p, rows["text"])
        self._paint_fx(p, rows["fx"], lay)
        self._paint_gifs(p, rows["gif"], lay)
        self._paint_caster(p, rows["caster"])
        self._paint_items(p, rows)
        # thumbnail moment
        tt = getattr(self.studio, "thumb_t", None)
        if tt is not None:
            x = self._x(tt)
            p.setPen(QPen(QColor(theme.YELLOW), 2))
            p.drawLine(QPointF(x, RULER - 2), QPointF(x, rows["video"][0] + rows["video"][1]))
            p.setPen(QColor(theme.YELLOW))
            p.drawText(QRectF(x + 3, 0, 80, RULER - 2), Qt.AlignLeft | Qt.AlignVCenter, "🖼 thumbnail")
        # clip being moved: ghost + where it will land
        if self._reorder_x is not None and self._drag:
            i = self._drag[1][1]
            vy, vh = rows["video"]
            to = self._drop_index(self._reorder_x, i)
            others = [ad for k, ad in enumerate(lay) if k != i]       # (start, length) as they are now
            land_t = (others[to - 1][0] + others[to - 1][1]) if to > 0 and others else \
                (others[0][0] if others else 0.0)
            lx = self._x(land_t)
            p.setPen(QPen(QColor(theme.YELLOW), 3))
            p.drawLine(QPointF(lx, vy - 2), QPointF(lx, vy + vh + 2))
            ghost = QRectF(self._reorder_x - 40, vy + 4, 80, vh - 8)
            p.setPen(QPen(QColor("white"), 1, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRoundedRect(ghost, 4, 4)
            p.drawText(ghost, Qt.AlignCenter, f"clip {i + 1}")
        # playhead
        x = self._x(self.playhead)
        p.setPen(QPen(QColor(theme.RED), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(QColor(theme.RED))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))
        p.end()

    def _paint_ruler(self, p: QPainter):
        total = self._total()
        sc = self._scale()
        step = next(s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300) if s * sc >= 60)
        p.setPen(QColor(theme.MUTED))
        t = int(self.offset / step) * step
        while t <= total + step:
            x = self._x(t)
            p.drawLine(QPointF(x, RULER - 6), QPointF(x, RULER))
            p.drawText(QRectF(x + 3, 0, 60, RULER - 4), Qt.AlignLeft | Qt.AlignVCenter, fmt_dur(t))
            t += step

    def _paint_pieces(self, p: QPainter, row, lay):
        y, h = row
        cur = self.studio._current
        for i, (pc, (a, d)) in enumerate(zip(self.studio.pieces, lay)):
            r = QRectF(self._x(a) + 1, y, max(4.0, d * self._scale() - 2), h)
            col = QColor(theme.ACCENT).darker(100 + 18 * (i % 3))
            col.setAlpha(255 if i == cur else 170)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, 4, 4)
            th = self._thumb(pc["clip"])
            if th is not None and r.width() > 70:
                p.drawPixmap(QRectF(r.x() + 2, r.y() + 2, (h - 4) * 16 / 9, h - 4), th, QRectF(th.rect()))
                text_x = r.x() + (h - 4) * 16 / 9 + 6
            else:
                text_x = r.x() + 4
            p.setPen(QColor("white"))
            sp = pc.get("speed", 1.0)
            label = f"{i + 1}. {pc['clip'].display_title}"
            sub = f"{fmt_dur(pc['start'])}–{fmt_dur(pc['end'])}" + (f"  {sp:g}×" if abs(sp - 1) > 1e-3 else "")
            p.drawText(QRectF(text_x, r.y() + 2, r.right() - text_x - 4, h / 2), Qt.AlignLeft | Qt.AlignVCenter,
                       label)
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QRectF(text_x, r.y() + h / 2, r.right() - text_x - 4, h / 2 - 2),
                       Qt.AlignLeft | Qt.AlignVCenter, sub)
            # boring stretches (no action, nobody talking): red stripes
            for a0, b0 in getattr(self.studio, "dead_for", lambda _p: [])(pc):
                x0 = self._x(a + tm.src_to_out(pc, a0))
                x1 = self._x(a + tm.src_to_out(pc, b0))
                band = QRectF(x0, r.y() + h - 9, max(2.0, x1 - x0), 7)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(255, 80, 90, 220), Qt.BDiagPattern))
                p.drawRect(band)
                p.setBrush(QColor(255, 80, 90, 70))
                p.drawRect(band)
            if i == cur:
                p.setPen(QPen(QColor("white"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r, 4, 4)
            # trim handles
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRect(QRectF(r.x(), r.y() + 8, 3, h - 16))
            p.drawRect(QRectF(r.right() - 3, r.y() + 8, 3, h - 16))
            self._items.append((r, ("piece", i)))

    def _paint_text(self, p: QPainter, row):
        y, h = row
        st = self.studio
        hook = st.hook.text().strip()
        if hook:
            a = st.hook_start
            r = QRectF(self._x(a), y + 2, max(6.0, st.hook_secs_eff() * self._scale()), h - 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e6e6e6"))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor("#111"))
            p.drawText(r.adjusted(4, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, "T  " + hook)
            p.setBrush(QColor("#888"))
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(r.right() - 3, r.y() + 3, 3, r.height() - 6))
            self._items.append((r, ("hook",)))
        for k, tt in enumerate((st.script or {}).get("timed_text", []) if st.script else []):
            r = QRectF(self._x(tt[0]), y + 4, max(4.0, (tt[1] - tt[0]) * self._scale()), h - 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#b0b0b0"))
            p.drawRoundedRect(r, 2, 2)
            self._items.append((r, ("timed", k)))

    def _paint_fx(self, p: QPainter, row, lay):
        y, h = row
        for i, (pc, (a, d)) in enumerate(zip(self.studio.pieces, lay)):
            for e in pc.get("effects", []):
                kind = e.get("type")
                if kind not in FX_COL:
                    continue
                t = a + tm.src_to_out(pc, e["t"])
                x = self._x(t)
                if kind == "slowmo":
                    dur = e.get("dur", 1.0) / max(0.1, e.get("speed", 0.4))
                    band = QRectF(x, y + 4, max(4.0, dur * self._scale()), h - 8)
                    c = QColor(FX_COL[kind])
                    c.setAlpha(70)
                    p.fillRect(band, c)
                r = QRectF(x - 8, y + 2, 16, h - 4)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(FX_COL[kind]))
                p.drawRoundedRect(r, 3, 3)
                p.setPen(QColor("#111"))
                p.drawText(r, Qt.AlignCenter, FX_SYM[kind])
                self._items.append((r, ("fx", i, e)))

    def _paint_gifs(self, p: QPainter, row, lay):
        y, h = row
        for i, (pc, (a, d)) in enumerate(zip(self.studio.pieces, lay)):
            gi = 0
            for e in pc.get("effects", []):
                if e.get("type") != "gif":
                    continue
                t0 = a + tm.src_to_out(pc, e["t"])
                t1 = a + tm.src_to_out(pc, min(pc["end"], e["t"] + e.get("dur", 2.0)))
                r = QRectF(self._x(t0), y + 2, max(8.0, (t1 - t0) * self._scale()), h - 4)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#ff6bd6"))
                p.drawRoundedRect(r, 3, 3)
                p.setPen(QColor("#111"))
                from pathlib import Path
                p.drawText(r.adjusted(4, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, Path(e.get("path", "")).stem)
                p.setBrush(QColor("#a0337f"))
                p.setPen(Qt.NoPen)
                p.drawRect(QRectF(r.right() - 3, r.y() + 3, 3, r.height() - 6))
                self._items.append((r, ("gif", i, e, gi)))
                gi += 1

    def _paint_caster(self, p: QPainter, row):
        y, h = row
        vol = self.studio.caster_volume()
        for k, c in enumerate(self.studio.caster_lines):
            r = QRectF(self._x(c["t"]), y + 2, max(8.0, c.get("dur", 1.5) * self._scale()), h - 4)
            col = QColor("#ffd23f")
            col.setAlpha(int(110 + 145 * min(1.0, vol)))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor("#111"))
            p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, "“" + c.get("text", "") + "”")
            self._items.append((r, ("caster", k)))

    def _paint_items(self, p: QPainter, rows):
        """Your text (Text track), blur boxes and mutes (Blur/Mute track)."""
        for k, it in enumerate(self.studio.items):
            kind = it.get("kind")
            y, h = rows["text" if kind == "text" else "blur"]
            if kind == "blur":              # blur boxes on the top half, mutes on the bottom half
                y, h = y, h / 2 + 1
            elif kind == "mute":
                y, h = y + h / 2 - 1, h / 2 + 1
            r = QRectF(self._x(float(it["t"])), y + 1, max(8.0, float(it.get("dur", 1.0)) * self._scale()), h - 2)
            col = {"text": "#9ad0ff", "blur": "#9aa4b2", "mute": "#ff8a5c"}.get(kind, "#ccc")
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor("#111"))
            label = {"text": "T  " + str(it.get("text", "")), "blur": "▦ blur",
                     "mute": "🔇 bleep" if it.get("bleep", True) else "🔇 mute"}.get(kind, kind)
            p.drawText(r.adjusted(4, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
            p.setBrush(QColor(0, 0, 0, 90))
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(r.right() - 3, r.y() + 3, 3, r.height() - 6))
            if self.sel == ("item", k):
                p.setPen(QPen(QColor("white"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 3, 3)
            self._items.append((r, ("item", k)))

    # ---------- keyboard helpers (called by the Studio)
    def delete_selected(self):
        st, sel = self.studio, self.sel
        if not sel:
            return
        if sel[0] == "item" and 0 <= sel[1] < len(st.items):
            del st.items[sel[1]]
        elif sel[0] == "caster" and 0 <= sel[1] < len(st.caster_lines):
            del st.caster_lines[sel[1]]
        elif sel[0] in ("fx", "gif"):
            pc = st.pieces[sel[1]] if sel[1] < len(st.pieces) else None
            if pc:
                pc["effects"] = [e for e in pc["effects"] if e is not sel[2]]
        elif sel[0] == "piece":
            self.pieceAction.emit("remove", sel[1], 0.0)
        elif sel[0] == "hook":
            st.hook.setText("")
        self.sel = None
        self.changed.emit()

    def nudge_selected(self, dt: float):
        st, sel = self.studio, self.sel
        if not sel:
            return
        if sel[0] == "item" and 0 <= sel[1] < len(st.items):
            it = st.items[sel[1]]
            it["t"] = round(max(0.0, float(it["t"]) + dt), 2)
        elif sel[0] == "caster" and 0 <= sel[1] < len(st.caster_lines):
            c = st.caster_lines[sel[1]]
            c["t"] = round(max(0.0, c["t"] + dt), 2)
        elif sel[0] in ("fx", "gif"):
            pc = st.pieces[sel[1]]
            e = sel[2]
            o = tm.src_to_out(pc, e["t"]) + dt
            e["t"] = round(max(pc["start"], min(pc["end"] - 0.05, tm.out_to_src(pc, max(0.0, o)))), 2)
        elif sel[0] == "hook":
            st.hook_start = round(max(0.0, st.hook_start + dt), 2)
        self.update()
        self.changed.emit()

    def _thumb(self, clip) -> QPixmap | None:
        key = str(clip.thumb)
        if key not in self._thumbs:
            pm = QPixmap(key) if clip.thumb.exists() else QPixmap()
            self._thumbs[key] = pm if not pm.isNull() else None
        return self._thumbs[key]

    # ---------- hit testing
    def _hit(self, pos: QPointF):
        for r, item in reversed(self._items):
            if item[0] == "piece":
                # grab zones sit just inside each edge, so two touching clips never steal each other's edge
                if r.left() - 1 <= pos.x() <= r.left() + 7 and r.top() <= pos.y() <= r.bottom():
                    return item, "trim_l"
                if r.right() - 7 <= pos.x() <= r.right() + 1 and r.top() <= pos.y() <= r.bottom():
                    return item, "trim_r"
            if item[0] in ("gif", "hook", "item") and abs(pos.x() - r.right()) <= 5 and r.top() <= pos.y() <= r.bottom():
                return item, "resize"
            if r.contains(pos):
                return item, "move"
        return None, None

    def _row_at(self, y: float) -> str:
        for key, (ry, h) in self._rows().items():
            if ry <= y <= ry + h:
                return key
        return ""

    # ---------- mouse
    def mousePressEvent(self, e):
        pos = e.position()
        item, part = self._hit(pos)
        if e.button() == Qt.RightButton:
            self._menu(item, pos, e.globalPosition().toPoint())
            return
        if item is None:
            if pos.x() > GUTTER:
                self._drag = ("scrub", None, pos, None)
                self.seek.emit(max(0.0, min(self._t(pos.x()), tm.total(self.studio.pieces) - 0.01)))
            return
        kind = item[0]
        st = self.studio
        self.sel = (item[0], item[1], item[2]) if item[0] in ("fx", "gif") else tuple(item[:2])
        self.setFocus()
        if kind == "item":
            it = st.items[item[1]]
            self._drag = (part, item, pos, (float(it["t"]), float(it.get("dur", 1.0))))
            self.itemSelected.emit(item[1])
            self.update()
            return
        if kind == "piece":
            i = item[1]
            pc = st.pieces[i]
            self._drag = (part, item, pos, (pc["start"], pc["end"]))
            if part == "move":
                self.pieceSelected.emit(i)
                self.seek.emit(self._t(pos.x()))
        elif kind == "fx":
            self._drag = (part, item, pos, item[2]["t"])
            self.pieceSelected.emit(item[1])
        elif kind == "gif":
            self._drag = (part, item, pos, (item[2]["t"], item[2].get("dur", 2.0)))
            self.gifSelected.emit(item[1], item[3])
        elif kind == "caster":
            c = st.caster_lines[item[1]]
            self._drag = (part, item, pos, c["t"])
        elif kind == "hook":
            self._drag = (part, item, pos, (st.hook_start, st.hook_secs_eff()))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            item, part = self._hit(pos)
            self.setCursor(Qt.SizeHorCursor if part in ("trim_l", "trim_r", "resize") else
                           Qt.OpenHandCursor if item and item[0] in ("fx", "gif", "caster", "hook", "item") else
                           Qt.PointingHandCursor if item else Qt.ArrowCursor)
            self.setToolTip(self._tip(item, part))
            return
        part, item, start, orig = self._drag
        if part == "scrub":
            self.seek.emit(max(0.0, min(self._t(pos.x()), tm.total(self.studio.pieces) - 0.01)))
            return
        dt = (pos.x() - start.x()) / self._scale()
        st = self.studio
        kind = item[0]
        if kind == "piece" and part == "move":
            # dragging a clip sideways = moving it to another place in the edit
            if self._reorder_x is not None or abs(pos.x() - start.x()) > 10:
                self._reorder_x = pos.x()
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
            return
        if kind == "piece":
            pc = st.pieces[item[1]]
            sp = max(0.1, pc.get("speed", 1.0))
            dur = pc["clip"].duration or (orig[1] + 60)
            if part == "trim_l":
                pc["start"] = round(max(0.0, min(orig[1] - 0.5, orig[0] + dt * sp)), 2)
            elif part == "trim_r":
                pc["end"] = round(max(orig[0] + 0.5, min(dur, orig[1] + dt * sp)), 2)
        elif kind in ("fx", "gif"):
            i, eff = item[1], item[2]
            pc = st.pieces[i]
            if part == "resize" and kind == "gif":
                eff["dur"] = round(max(0.3, orig[1] + dt), 2)
            else:
                t0 = orig[0] if kind == "gif" else orig
                o = tm.src_to_out(pc, t0) + dt
                eff["t"] = round(max(pc["start"], min(pc["end"] - 0.05, tm.out_to_src(pc, max(0.0, o)))), 2)
        elif kind == "caster":
            c = st.caster_lines[item[1]]
            c["t"] = round(max(0.0, min(tm.total(st.pieces) - 0.2, orig + dt)), 2)
        elif kind == "item":
            it = st.items[item[1]]
            if part == "resize":
                it["dur"] = round(max(0.2, orig[1] + dt), 2)
            else:
                it["t"] = round(max(0.0, min(tm.total(st.pieces) - 0.2, orig[0] + dt)), 2)
        elif kind == "hook":
            if part == "resize":
                st.hook_secs = round(max(0.5, orig[1] + dt), 2)
                st.hook_whole = False
                st.hook_user = True
            else:
                st.hook_start = round(max(0.0, min(tm.total(st.pieces) - 0.5, orig[0] + dt)), 2)
        self.update()

    def _drop_index(self, x: float, moving: int) -> int:
        """Where a dragged clip lands: before the first other clip whose middle is right of the mouse."""
        t = self._t(x)
        lay = tm.layout(self.studio.pieces)
        idx = 0
        for i, (a, d) in enumerate(lay):
            if i == moving:
                continue
            if a + d / 2 < t:
                idx += 1
        return idx

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] == "move" and self._drag[1][0] == "piece" and self._reorder_x is not None:
            i = self._drag[1][1]
            to = self._drop_index(self._reorder_x, i)
            self._reorder_x = None
            self._drag = None
            self.setCursor(Qt.ArrowCursor)
            if to != i:
                self.pieceAction.emit("move", i, float(to))
            self.update()
            return
        self._reorder_x = None
        if self._drag and self._drag[0] != "scrub":
            moved = (e.position() - self._drag[2]).manhattanLength() > 2
            if moved or self._drag[0] in ("trim_l", "trim_r"):
                self.changed.emit()
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        item, _ = self._hit(e.position())
        if item and item[0] == "caster":
            self.casterEdit.emit(item[1])

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.25 if dy > 0 else 0.8)
        elif self.zoom > 1.0:
            vis = (self.width() - GUTTER - 10) / self._scale()
            self.offset = max(0.0, min(self._total() - vis, self.offset - dy / 120 * vis * 0.15))
            self.update()

    def _tip(self, item, part) -> str:
        if item is None:
            return "Click to jump there · Ctrl+scroll to zoom"
        k = item[0]
        if k == "piece":
            return ("Drag to trim this clip" if part in ("trim_l", "trim_r") else
                    "Click to select · drag sideways to move it · right-click: split / duplicate / remove")
        if k == "fx":
            return f"{item[2]['type']}: drag to move · right-click to remove"
        if k == "gif":
            return "GIF: drag to move, drag its right edge to change how long it shows · right-click for more"
        if k == "caster":
            c = self.studio.caster_lines[item[1]]
            return f"“{c.get('text', '')}” — drag to move · double-click to change the words · right-click to remove"
        if k == "hook":
            return "Hook text: drag to move in time, drag the right edge to change how long it shows"
        if k == "item":
            it = self.studio.items[item[1]]
            return {"text": "Your text: drag to move, drag the right edge to change how long it shows",
                    "blur": "Blur box: drag to move in time; place it on the video in Final look",
                    "mute": "Mute: drag / stretch it over the sound to hide · right-click: bleep or silence"
                    }.get(it.get("kind"), "") + " · Delete removes"
        return ""

    def _menu(self, item, pos, gpos):
        if item is None:
            return
        st = self.studio
        m = QMenu(self)
        k = item[0]
        if k == "piece":
            i = item[1]
            t_clip = tm.out_to_src(st.pieces[i], self._t(pos.x()) - tm.layout(st.pieces)[i][0])
            m.addAction("✂ Split here", lambda: self.pieceAction.emit("split", i, t_clip))
            m.addAction("🖼 Use this moment as the thumbnail", lambda: self.pieceAction.emit("thumb", i, t_clip))
            m.addAction("Duplicate", lambda: self.pieceAction.emit("duplicate", i, 0.0))
            m.addAction("🗑 Remove clip", lambda: self.pieceAction.emit("remove", i, 0.0))
        elif k == "fx":
            i, eff = item[1], item[2]
            if eff.get("type") == "slowmo":
                smooth = eff.get("smooth", True)
                m.addAction("Snap into slow-mo (no easing)" if smooth else "Ease in and out (smooth)",
                            lambda: (eff.update(smooth=not smooth), self.changed.emit()))
            m.addAction(f"🗑 Remove {eff['type']}", lambda: self._remove_effect(i, eff))
        elif k == "gif":
            i, eff = item[1], item[2]
            m.addAction("🖼 Place / resize it", lambda: self.gifSelected.emit(i, item[3]))
            m.addAction("🗑 Remove GIF", lambda: self._remove_effect(i, eff))
        elif k == "caster":
            kk = item[1]
            m.addAction("✏ Change the words…", lambda: self.casterEdit.emit(kk))
            m.addAction("🗑 Remove this line", lambda: self._remove_caster(kk))
        elif k == "item":
            kk = item[1]
            it = st.items[kk]
            if it.get("kind") == "mute":
                m.addAction("Switch the bleep off (just silence)" if it.get("bleep", True) else "Bleep instead of silence",
                            lambda: (it.update(bleep=not it.get("bleep", True)), self.changed.emit()))
            elif it.get("kind") in ("text", "blur"):
                m.addAction("✏ Edit on the video", lambda: self.itemSelected.emit(kk))
            m.addAction("🗑 Remove", lambda: (st.items.pop(kk), self.changed.emit()))
        elif k == "hook":
            m.addAction("Show the whole video", self._hook_whole)
            m.addAction("🗑 Remove the text", lambda: (st.hook.setText(""), self.changed.emit()))
        m.exec(gpos)

    def _remove_effect(self, i, eff):
        pc = self.studio.pieces[i]
        pc["effects"] = [e for e in pc["effects"] if e is not eff]
        self.changed.emit()

    def _remove_caster(self, k):
        del self.studio.caster_lines[k]
        self.changed.emit()

    def _hook_whole(self):
        st = self.studio
        st.hook_start = 0.0
        st.hook_whole = True
        st.hook_user = False
        self.changed.emit()
