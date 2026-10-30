"""Undo / redo in the Studio, and the options on the right that every undo step, draft and style reads and writes."""
from __future__ import annotations


class HistoryMixin:
    @staticmethod
    def _copy_pieces(pieces: list[dict]) -> list[dict]:
        """Pieces that share nothing editable with the originals (the clip itself is shared)."""
        import copy
        return [dict(p, effects=copy.deepcopy(p.get("effects", [])),
                     **({"alt": dict(p["alt"])} if p.get("alt") else {})) for p in pieces]

    def _state(self) -> dict:
        import copy
        return {"pieces": self._copy_pieces(self.pieces),
                "hook": self.hook.text(), "hook_start": self.hook_start, "hook_secs": self.hook_secs,
                "hook_whole": self.hook_whole, "hook_pos": copy.deepcopy(self.hook_pos),
                "hook_scale": self.hook_scale, "hook_user": getattr(self, "hook_user", False),
                "caster": copy.deepcopy(self.caster_lines), "items": copy.deepcopy(self.items),
                "current": self._current,
                "thumb_t": self.thumb_t,
                "opts": self._opts_values(strip=False) if hasattr(self, "caster_vol") else {}}

    # The options on the right: (draft key, widget, kind, default). Drafts, undo steps and styles all use this
    # one list; volumes are 0–100 on screen and 0.0–1.0 everywhere else.
    OPTS = (("preset", "preset", "combo", "short"), ("layout", "layout_box", "combo", "blur"),
            ("watermark", "watermark", "text", ""), ("music", "music", "text", ""),
            ("music_volume", "music_vol", "pct", 0.18), ("loudnorm", "loud", "check", True),
            ("captions", "captions", "check", False), ("caption_style", "cap_style", "combo", "pop3"),
            ("loop", "loop", "check", True), ("tiktok", "tiktok", "check", True),
            ("game_volume", "game_vol", "pct", 1.0), ("mic_volume", "mic_vol", "pct", 1.0),
            ("caster_duck", "caster_duck", "pct", 0.7), ("caster_volume", "caster_vol", "pct", 1.0))

    def _opts_values(self, strip: bool = True) -> dict:
        out = {}
        for key, attr, kind, _ in self.OPTS:
            w = getattr(self, attr)
            if kind == "combo":
                out[key] = w.currentData()
            elif kind == "text":
                out[key] = w.text().strip() if strip else w.text()
            elif kind == "pct":
                out[key] = w.value() / 100
            else:
                out[key] = w.isChecked()
        return out

    def _set_opts(self, o: dict, defaults: bool = False, quiet: bool = False):
        """Put options on screen. Missing ones keep their current value, or get their default (defaults=True).
        quiet: no change signals (undo / redo restores without recording a new step)."""
        for key, attr, kind, default in self.OPTS:
            v = o.get(key)
            if v is None or (kind == "combo" and not v):
                if not defaults:
                    continue
                v = default
            w = getattr(self, attr)
            was = w.blockSignals(True) if quiet else None
            if kind == "combo":
                w.setCurrentIndex(max(0, w.findData(v)))
            elif kind == "text":
                w.setText(str(v))
            elif kind == "pct":
                w.setValue(round(float(v) * 100))
            else:
                w.setChecked(bool(v))
            if quiet:
                w.blockSignals(was)

    def _watch_opts(self):
        """Changing any option on the right is an undo step and is saved in the draft."""
        for w in (self.preset, self.layout_box, self.cap_style):
            w.currentIndexChanged.connect(lambda *_: self._mark())
        for w in (self.watermark, self.music):
            w.textChanged.connect(lambda *_: self._mark())
        for w in (self.music_vol, self.game_vol, self.mic_vol, self.caster_duck, self.caster_vol):
            w.valueChanged.connect(lambda *_: self._mark())
        for w in (self.captions, self.loud, self.loop, self.tiktok):
            w.toggled.connect(lambda *_: self._mark())

    def _mark(self):
        """Something changed: record an undo step once things settle."""
        if hasattr(self, "_hist_timer"):
            self._hist_timer.start()

    def _commit(self):
        if not self.history.record(self._state()):
            return
        self._update_undo_buttons()
        try:
            self.save_draft()          # every change is saved: close the app and nothing is lost
        except Exception as e:
            print("draft save:", e)

    def _update_undo_buttons(self):
        if hasattr(self, "undo_btn"):
            self.undo_btn.setEnabled(self.history.can_undo)
            self.redo_btn.setEnabled(self.history.can_redo)

    def undo(self):
        self._hist_timer.stop()
        self._commit()                      # don't lose the very last change
        st = self.history.undo()
        if st is not None:
            self._restore(st)
            self.status.setText("Undone (Ctrl+Y to redo)")

    def redo(self):
        self._hist_timer.stop()
        st = self.history.redo()
        if st is not None:
            self._restore(st)
            self.status.setText("Redone")

    def _restore(self, st: dict):
        import copy
        self.pieces = self._copy_pieces(st["pieces"])
        self.hook.blockSignals(True)
        self.hook.setText(st["hook"])
        self.hook.blockSignals(False)
        self.hook_start, self.hook_secs, self.hook_whole = st["hook_start"], st["hook_secs"], st["hook_whole"]
        self.hook_pos, self.hook_scale = copy.deepcopy(st["hook_pos"]), st["hook_scale"]
        self.hook_user = st["hook_user"]
        self.caster_lines = copy.deepcopy(st["caster"])
        self.items = copy.deepcopy(st.get("items", []))
        self.cam_edit.set_items(self.items)
        self.thumb_t = st.get("thumb_t")
        self._set_opts(st.get("opts") or {}, quiet=True)
        self.script_lbl.setVisible(bool(self.script) or bool(self.caster_lines))
        if self.caster_lines and not self.script:
            self.script_lbl.setText(f"🎙 AI caster: {len(self.caster_lines)} lines (on the timeline)   ✕")
        self._autoplay_next = False
        cur = st.get("current", 0)
        self._refresh_list(min(max(0, cur if cur is not None else 0), len(self.pieces) - 1))
        self._show_fx()
        self._refresh_cam_edit(frames=False)
        self.timeline.update()
        self._hist_timer.stop()
        self._update_undo_buttons()
