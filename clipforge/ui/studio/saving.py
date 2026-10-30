"""The Studio's edit as a draft: autosaved on every change, reopened exactly as left, queued for rendering."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QMessageBox

from ... import drafts


class DraftMixin:
    def edit_state(self) -> dict:
        """Everything needed to rebuild / render this edit later (saved as a draft)."""
        import copy
        return {
            "pieces": drafts.pieces_to_json(self.pieces),
            "hook": self.hook.text(), "hook_start": self.hook_start, "hook_secs": self.hook_secs,
            "hook_secs_eff": self.hook_secs_eff(), "hook_whole": self.hook_whole,
            "hook_pos": copy.deepcopy(self.hook_pos), "hook_scale": self.hook_scale,
            "hook_user": getattr(self, "hook_user", False), "caster": copy.deepcopy(self.caster_lines),
            "items": copy.deepcopy(self.items),
            "script": copy.deepcopy(self.script), "ai_post": self.ai_post, "thumb_t": self.thumb_t,
            "game": self._game() or "Clips",
            "opts": {**self._opts_values(), "cam": self.cam_edit.config()},
        }

    def save_draft(self, **fields) -> dict | None:
        if not self.pieces:
            return None
        d = drafts.for_saving(drafts.load(self.draft_id) if self.draft_id else None, self.draft_origin)
        d["edit"] = self.edit_state()
        d["title"] = self.hook.text().strip() or f"{self._game() or 'Clips'} · {len(self.pieces)} clips"
        d["game"] = self._game()
        from ... import timeline_model as tm
        d["length"] = round(tm.total(self.pieces), 1)
        d["thumb"] = str(self.pieces[0]["clip"].thumb)
        d.update(fields)
        d = drafts.save(d)
        self.draft_id = d["id"]
        return d

    def load_draft(self, d: dict) -> bool:
        """Open a saved draft in the Studio exactly as it was left."""
        edit = d.get("edit", {})
        pieces = drafts.pieces_from_json(edit.get("pieces", []), self.lib.load)
        if not pieces:
            QMessageBox.information(self, "Draft", "The clips in this draft were deleted or moved.")
            return False
        self.clear()
        o = edit.get("opts", {})
        self._set_opts(o, defaults=True)
        if o.get("cam"):
            self.cam_edit.set_config(o["cam"])
        self.pieces = pieces
        self.hook.blockSignals(True)
        self.hook.setText(edit.get("hook", ""))
        self.hook.blockSignals(False)
        self.hook_start = edit.get("hook_start", 0.0)
        self.hook_secs = edit.get("hook_secs", 3.0)
        self.hook_whole = edit.get("hook_whole", False)
        self.hook_pos = edit.get("hook_pos")
        self.hook_scale = edit.get("hook_scale", 1.0)
        self.hook_user = edit.get("hook_user", False)
        self.caster_lines = [dict(c) for c in edit.get("caster", [])]
        self.items = [dict(x) for x in edit.get("items", [])]
        self.cam_edit.set_items(self.items)
        self.ai_post = edit.get("ai_post")
        self.thumb_t = edit.get("thumb_t")
        self.set_script(edit.get("script"))
        if self.caster_lines and not edit.get("script"):
            self.script_lbl.setText(f"🎙 AI caster: {len(self.caster_lines)} lines (on the timeline)   ✕")
            self.script_lbl.setVisible(True)
        self.draft_id = d["id"] if d.get("status") != "done" else ""
        self.draft_origin = d.get("origin", "studio")
        self.history.reset()
        self._autoplay_next = False
        self._refresh_list(0)
        self.timeline.fit()
        self._refresh_cam_edit(frames=False)
        self.status.setText(f"📝 Opened draft “{d.get('title', '')}”")
        return True

    def queue_render(self):
        if not self.pieces:
            return
        d = self.save_draft(status="queued", queued_at=time.time())
        if d:
            self.queued.emit(d["id"])
            self.status.setText("＋ Added to the render queue (Drafts page). It renders in the background — "
                                "never while you're in a game. Press Clear all to start the next edit.")
