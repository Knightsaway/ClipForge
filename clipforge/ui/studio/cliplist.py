"""The clips in the Studio's edit: adding them, the list on the left (select, trim, drag to reorder, remove, duplicate) and the timeline's move / split / remove."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QListWidgetItem

from ...autoshort import suggest_hook
from ...editor import Piece
from ...library import Clip
from ..widgets import fmt_dur


class ClipListMixin:
    def add_clips(self, clips: list[Clip]):
        for c in clips:
            end = c.trim_out or c.duration
            self.pieces.append({"clip": c, "start": c.trim_in, "end": end, "effects": [], "speed": 1.0})
        self._refresh_list(len(self.pieces) - 1)

    def load_plan(self, plan: list[Piece], clips: list[Clip], preset: str = "short", script: dict | None = None):
        by_path = {c.path: c for c in clips}
        self.pieces = [{"clip": by_path[p.path], "start": p.start, "end": p.end, "effects": list(p.effects),
                        "speed": p.speed} for p in plan if p.path in by_path]
        self.preset.setCurrentIndex(self.preset.findData(preset))
        self.set_script(script)
        if not self.hook.text() and not script:
            self.hook.setText(suggest_hook(self._game(), self.hook_weights()))
        self._refresh_list(0)
        self.timeline.fit()

    def _refresh_list(self, select=-1):
        if hasattr(self, "list_stack"):
            self.list_stack.setCurrentIndex(1 if self.pieces else 0)
        self.list.blockSignals(True)
        self.list.clear()
        for i, pc in enumerate(self.pieces):
            c = pc["clip"]
            fx = pc.get("effects", [])
            tags = "".join({"zoom": " Z", "slowmo": " S", "sfx": " ♪"}.get(e["type"], "") for e in fx)
            spd = f"  {pc.get('speed', 1.0):g}×" if abs(pc.get("speed", 1.0) - 1) > 1e-3 else ""
            if pc.get("alt"):
                spd += f"  👥 +{pc['alt']['alt_label']}"
            it = QListWidgetItem(f"{i+1}.  {c.game} · {c.display_title}\n      "
                                 f"{fmt_dur(pc['start'])} → {fmt_dur(pc['end'])}   ({pc['end']-pc['start']:.1f} s)"
                                 f"{spd}{'   FX:' + tags if tags else ''}")
            if c.thumb.exists():
                it.setIcon(QPixmap(str(c.thumb)))
            self.list.addItem(it)
        self.list.setIconSize(QPixmap(96, 54).size())
        self.list.blockSignals(False)
        from ... import timeline_model as tm
        tot = tm.total(self.pieces)             # the real length: speed, slow-mo and its ramps, like the render
        warn = "  ⚠ over 60 s — shorter Shorts usually do better" if tot > 60 and self._is_short() else ""
        self.total.setText(f"Total {fmt_dur(tot)}  ·  {len(self.pieces)} pieces{warn}")
        if 0 <= select < len(self.pieces):
            self.list.setCurrentRow(select)
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "_dead_timer") and self.pieces:
            self._dead_timer.start()
        self._mark()

    def _select(self, row):
        self._current = row
        if 0 <= row < len(self.pieces):
            pc = self.pieces[row]
            c = pc["clip"]
            autoplay = self._play_all or getattr(self, "_autoplay_next", True)
            self._autoplay_next = True
            self.player.load(c.path, c.duration, pc["start"], pc["end"], c.markers,
                             events=[e["t"] for e in c.events], effects=pc.get("effects", []), autoplay=autoplay)
            self.speed.blockSignals(True)
            self.speed.setValue(pc.get("speed", 1.0))
            self.speed.blockSignals(False)
            self.cam_edit.set_gifs(pc.get("effects", []))
            self._sync_transition_box()
            if self.mid_tabs.currentIndex() == 1:
                self._refresh_cam_edit()

    def _range(self, a, b):
        if 0 <= self._current < len(self.pieces):
            self.pieces[self._current]["start"] = a
            self.pieces[self._current]["end"] = b
            self._refresh_list(self._current)

    def _reordered(self, *_):
        # QListWidget moved the rows: rebuild our list in the new order from the item text index
        order = []
        for i in range(self.list.count()):
            num = int(self.list.item(i).text().split(".", 1)[0]) - 1
            order.append(self.pieces[num])
        self.pieces = order
        QTimer.singleShot(0, lambda: self._refresh_list(self.list.currentRow()))

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.pieces):
            self.pieces.pop(r)
            self._refresh_list(min(r, len(self.pieces) - 1))

    def _dup(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.pieces):
            pc = dict(self.pieces[r])
            pc["effects"] = [dict(e) for e in pc.get("effects", [])]
            self.pieces.insert(r + 1, pc)
            self._refresh_list(r + 1)

    def _piece_action(self, what: str, i: int, t: float):
        from ... import timeline_model as tm
        if not (0 <= i < len(self.pieces)):
            return
        if what == "thumb":
            from ... import timeline_model as tm
            self.set_thumb(tm.layout(self.pieces)[i][0] + tm.src_to_out(self.pieces[i], t))
            return
        self._autoplay_next = False
        if what == "split":
            parts = tm.split_piece(self.pieces[i], t)
            if not parts:
                self.status.setText("Can't split that close to the edge of the clip.")
                return
            self.pieces[i:i + 1] = list(parts)
            self._refresh_list(i + 1)
        elif what == "duplicate":
            pc = dict(self.pieces[i])
            pc["effects"] = [dict(e) for e in pc.get("effects", [])]
            self.pieces.insert(i + 1, pc)
            self._refresh_list(i + 1)
        elif what == "move":
            to = int(t)
            pc = self.pieces.pop(i)
            self.pieces.insert(max(0, min(len(self.pieces), to)), pc)
            self._refresh_list(self.pieces.index(pc))
            self.status.setText(f"Moved clip {i + 1} to position {self.pieces.index(pc) + 1}")
        elif what == "remove":
            self.pieces.pop(i)
            self._refresh_list(min(i, len(self.pieces) - 1))
        self.timeline.update()
