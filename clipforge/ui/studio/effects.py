"""Effects on the selected clip in the Studio: zooms, slow-mo, sounds, GIFs, its transition and speed, and automatic effects."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from ...editor import PRESETS


class EffectsMixin:
    def _add_fx(self, fx: dict):
        pc = self._cur()
        if not pc:
            QMessageBox.information(self, "Effects", "Select a piece in the list first.")
            return
        t = self.player.position
        if not (pc["start"] <= t <= pc["end"]):
            t = (pc["start"] + pc["end"]) / 2
        fx = dict(fx, t=round(t, 2))
        if fx["type"] == "sfx" and not fx.get("name"):
            return
        pc["effects"].append(fx)
        self._show_fx()
        self._refresh_list(self._current)

    def _clear_fx(self):
        pc = self._cur()
        if pc:
            pc["effects"] = []
            self._show_fx()
            self._refresh_list(self._current)

    def _add_gif(self):
        pc = self._cur()
        if not pc:
            QMessageBox.information(self, "GIFs", "Select a piece in the list first, then move the playhead "
                                                  "to where the GIF should pop up.")
            return
        from ..gifs import GifPicker, gif_ratio
        t = self.player.position
        self.player.player.pause()
        dlg = GifPicker(self.s, self)
        if dlg.exec() != QDialog.Accepted or not dlg.chosen:
            return
        if not (pc["start"] <= t <= pc["end"]):
            t = pc["start"]
        vertical = PRESETS[self.preset.currentData()].vertical
        ratio = gif_ratio(dlg.chosen)
        w = 0.45 if vertical else 0.28
        box = {"x": round((1 - w) / 2, 4), "y": 0.16 if vertical else 0.12, "w": w}
        pc["effects"].append({"type": "gif", "t": round(t, 2), "dur": round(dlg.seconds, 2), "path": dlg.chosen,
                              "ratio": ratio, "box": box})
        self._show_fx()
        self._refresh_list(self._current)
        self.mid_tabs.setCurrentIndex(1)           # straight to placing it
        self._refresh_cam_edit()
        gifs = [e for e in pc["effects"] if e.get("type") == "gif"]
        self.cam_edit.select_gif(len(gifs) - 1)
        self.status.setText("🎞 GIF added — drag it where you want it, drag a corner to resize.")

    def _gifs_changed(self):
        pc = self._cur()
        if not pc:
            return
        if any(e.get("_remove") for e in pc["effects"]):
            pc["effects"] = [e for e in pc["effects"] if not e.get("_remove")]
            self.cam_edit.set_gifs(pc["effects"])
        self._show_fx()
        self._mark()

    def _set_transition(self, *_):
        pc = self._cur()
        if not pc:
            return
        name = self.trans_box.currentData() or ""
        pc["effects"] = [e for e in pc.get("effects", []) if e.get("type") != "transition"]
        if name:
            pc["effects"].append({"type": "transition", "name": name, "t": pc["start"]})
        self._refresh_list(self._current)

    def _sync_transition_box(self):
        pc = self._cur()
        name = next((e.get("name") for e in (pc or {}).get("effects", []) if e.get("type") == "transition"), "")
        self.trans_box.blockSignals(True)
        self.trans_box.setCurrentIndex(max(0, self.trans_box.findData(name or "")))
        self.trans_box.blockSignals(False)

    def _speed_changed(self, v):
        pc = self._cur()
        if pc and abs(pc.get("speed", 1.0) - v) > 1e-3:
            pc["speed"] = v
            self._refresh_list(self._current)

    def _show_fx(self):
        pc = self._cur()
        self.player.timeline.effects = pc["effects"] if pc else []
        self.player.timeline.update()
        if hasattr(self, "cam_edit"):
            self.cam_edit.set_gifs(pc["effects"] if pc else [])

    @staticmethod
    def _auto_fx_for(pieces: list[dict]) -> tuple[list[list], bool]:
        from ...autoedit import auto_fx_for
        return auto_fx_for(pieces)

    def auto_fx_all(self):
        fxs, used_sound = self._auto_fx_for(self.pieces)
        for pc, fx in zip(self.pieces, fxs):
            pc["effects"] = fx + [e for e in pc["effects"] if e.get("type") == "gif"]   # keep your GIFs
        self._show_fx()
        self._refresh_list(max(0, self._current))
        n_fx = sum(len(p["effects"]) for p in self.pieces)
        self.status.setText((f"✨ Added {n_fx} effects" + (" (on the loudest moments)" if used_sound else ""))
                            if n_fx else "Couldn't find any exciting moments — add effects by hand at the playhead.")
