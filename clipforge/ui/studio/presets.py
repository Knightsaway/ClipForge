""""My style" presets in the Studio: save the current look, apply one, make it the default or a game's style."""
from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMenu

from ... import styles
from ...config import save_settings


class StyleMixin:
    STYLE_OPTS = ("layout", "watermark", "music", "music_volume", "loop", "tiktok", "captions", "caption_style",
                  "caster_duck", "caster_volume")      # the options on the right that a style remembers

    def current_style(self) -> dict:
        o = self._opts_values()
        return {**{k: o[k] for k in self.STYLE_OPTS}, "cam": self.cam_edit.config(),
                "hook_pos": self.hook_pos, "hook_scale": self.hook_scale,
                "caster": bool(self.caster_lines) or self.s.caster_auto,
                "gifs": self.s.auto_gifs, "cut_boring": self.s.auto_cut_boring, "beat_sync": True}

    def apply_style(self, st: dict, name: str = ""):
        if st.get("cam"):
            self.cam_edit.set_config(st["cam"])
        if "hook_pos" in st:
            self.hook_pos = st.get("hook_pos")
        if st.get("hook_scale"):
            self.hook_scale = float(st["hook_scale"])
        self._set_opts({k: st[k] for k in self.STYLE_OPTS if k in st})
        self._refresh_cam_edit(frames=False)
        self._style_refresh(name)
        self._mark()

    def _style_refresh(self, select: str = ""):
        self.style_box.blockSignals(True)
        self.style_box.clear()
        self.style_box.addItem("(none — normal settings)", "")
        for name in sorted(self.s.styles):
            self.style_box.addItem(styles.label(self.s, name), name)
        self.style_box.setCurrentIndex(max(0, self.style_box.findData(select)))
        self.style_box.blockSignals(False)

    def _style_chosen(self, i):
        name = self.style_box.itemData(i)
        if name and name in self.s.styles:
            self.apply_style(self.s.styles[name], name)
            self.status.setText(f"🎨 Style “{name}” applied")

    def _style_save(self):
        cur = self.style_box.currentData() or ""
        name, ok = QInputDialog.getText(self, "Save style", "Name this look (e.g. 'Shorts – facecam right'):",
                                        text=cur)
        if not ok or not name.strip():
            return
        name = name.strip()
        styles.save(self.s, name, self.current_style())
        save_settings(self.s)
        self._style_refresh(name)
        self.status.setText(f"🎨 Saved style “{name}”" + (" — it's the default for automatic edits"
                                                         if self.s.default_style == name else ""))

    def _style_menu(self, btn):
        name = self.style_box.currentData() or ""
        m = QMenu(self)
        game = self._game()
        if name:
            m.addAction("⭐ Default for all automatic edits", lambda: self._style_set(default=name))
            if game:
                m.addAction(f"🎮 Use for {game}", lambda: self._style_set(game=game, name=name))
            m.addAction("💾 Update with the current look", lambda: self._style_update(name))
            m.addAction("🗑 Delete this style", lambda: self._style_delete(name))
        if game and game in self.s.style_by_game:
            m.addAction(f"No special style for {game}", lambda: self._style_set(game=game, name=""))
        if self.s.default_style:
            m.addAction("No default style", lambda: self._style_set(default=""))
        if m.isEmpty():
            m.addAction("Save a style first (Save…)").setEnabled(False)
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _style_set(self, default=None, game=None, name=None):
        if default is not None:
            self.s.default_style = default
        if game is not None:
            styles.use_for_game(self.s, game, name)
        save_settings(self.s)
        self._style_refresh(self.style_box.currentData() or "")

    def _style_update(self, name):
        self.s.styles[name] = self.current_style()
        save_settings(self.s)
        self.status.setText(f"🎨 Updated style “{name}”")

    def _style_delete(self, name):
        styles.delete(self.s, name)
        save_settings(self.s)
        self._style_refresh("")
