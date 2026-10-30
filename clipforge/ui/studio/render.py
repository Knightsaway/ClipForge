"""Rendering the Studio's edit: saves it as a draft and renders it in the background."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ...config import save_settings


class RenderMixin:
    def render(self):
        if not self.pieces:
            QMessageBox.information(self, "Studio", "Add some clips first (Ctrl+click clips → Create Montage, "
                                                     "or press ⚡ Auto Short).")
            return
        self.player.player.pause()
        if self._play_all:
            self._toggle_play_all(stop=True)
        # remember the look for next time
        self.s.watermark_text = self.watermark.text().strip()
        self.s.music_file = self.music.text().strip()
        self.s.music_volume = self.music_vol.value() / 100
        self.s.short_layout = self.layout_box.currentData()
        self.s.captions = self.captions.isChecked()
        self.s.caption_style = self.cap_style.currentData()
        if self.caster_lines:
            self.s.caster_game_volume = self.caster_duck.value() / 100
        save_settings(self.s)
        d = self.save_draft(status="rendering")
        self.render_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setValue(0)
        from ... import drafts
        s, exports = self.s, self.lib.exports_dir

        def job(progress):
            try:
                outs = drafts.render(d, s, exports, progress=progress)
            except Exception as e:
                drafts.update(d["id"], status="failed", error=str(e))
                raise
            drafts.update(d["id"], status="done", output=str(outs[0]), outputs=[str(o) for o in outs])
            return outs[0]
        self._rendering_draft = d["id"]
        self.jobs.run("studio", job)

    def _progress(self, job, f, msg):
        if job == "studio":
            self.bar.setValue(int(f * 1000))
            self.status.setText(msg)

    def _done(self, job, path):
        if job == "studio":
            self.render_btn.setEnabled(True)
            self.bar.setVisible(False)
            self.status.setText(f"✔ Saved {Path(path).name}")
            self.rendered.emit(path)

    def _failed(self, job, err):
        if job == "studio":
            self.render_btn.setEnabled(True)
            self.bar.setVisible(False)
            self.status.setText("Render failed")
            QMessageBox.warning(self, "Render failed", err)
