"""The AI in the Studio: the caster's voice lines (write, re-word, drop, preview) and the AI writer for titles and hooks."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QInputDialog, QMessageBox

from ...editor import PRESETS, Piece


class AIMixin:
    def _ai_hooks(self):
        post = getattr(self, "ai_post", None)
        if post and post.get("hooks"):          # cycle through the hooks we already have
            hooks = post["hooks"]
            cur = self.hook.text()
            self.hook.setText(hooks[(hooks.index(cur) + 1) % len(hooks)] if cur in hooks else hooks[0])
            return
        if not self.pieces:
            return
        from ... import ai
        clips = [p["clip"] for p in self.pieces]
        vertical = PRESETS[self.preset.currentData()].vertical
        self.ai_btn.setEnabled(False)
        self.status.setText("🤖 Writing…")

        def work():
            try:
                from ...analytics import winning_hooks
                res = ai.write_post(self._game(), ai.clip_context(clips), vertical, self.s.ai_model,
                                    winning_hooks(getattr(self, "queue_items", lambda: [])()))
            except Exception as e:
                res = {"error": str(e)}
            self.aiReady.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def _ai_done(self, res):
        self.ai_btn.setEnabled(True)
        if "error" in res:
            self.status.setText(f"AI not available: {res['error'][:120]}")
            return
        self.ai_post = res
        if res.get("hooks"):
            self.hook.setText(res["hooks"][0])
        self.status.setText(f"🤖 Title: {res.get('title', '')}")

    def _caster(self):
        if not self.pieces:
            QMessageBox.information(self, "AI caster", "Add some clips first (＋ Add clips).")
            return
        from ... import commentator
        if not commentator.available():
            QMessageBox.information(self, "AI caster", "No voice engine found. Run setup.bat and say yes to the "
                                    "caster voice step (or use Windows' built-in voice).")
            return
        self.caster_btn.setEnabled(False)
        self.caster_btn.setText("🎙  The caster is watching your clips…")
        self.status.setText("🎙 Finding the big moments and writing lines…")
        game = self._game()
        snapshot = [dict(p, effects=[dict(e) for e in p.get("effects", [])]) for p in self.pieces]

        def work():
            import tempfile
            try:
                new_fx = None
                if not any(e["type"] in ("zoom", "slowmo") for p in snapshot for e in p["effects"]):
                    new_fx, _ = self._auto_fx_for(snapshot)       # react to kills/markers or loud moments
                    for p, fx in zip(snapshot, new_fx):
                        p["effects"] = fx
                pieces = [Piece(p["clip"].path, p["start"], p["end"], speed=p.get("speed", 1.0),
                                effects=p["effects"]) for p in snapshot]
                lines = commentator.build_lines(pieces, game, Path(tempfile.mkdtemp(prefix="caster_")),
                                                self.s.ai_enabled, self.s.ai_model, self.s.caster_voice)
                self.casterReady.emit({"caster_lines": lines, "fx": new_fx})
            except Exception as e:
                self.casterReady.emit({"error": str(e)})
        threading.Thread(target=work, daemon=True).start()

    def _caster_done(self, res):
        self.caster_btn.setEnabled(True)
        self.caster_btn.setText("🎙  Add AI caster voice")
        if res.get("fx") and len(res["fx"]) == len(self.pieces):
            for pc, fx in zip(self.pieces, res["fx"]):    # the zooms the caster reacts to
                pc["effects"] = fx + [e for e in pc["effects"] if e.get("type") == "gif"]
            self._show_fx()
            self._refresh_list(max(0, self._current))
        lines = res.get("caster_lines") or []
        if res.get("error") or not lines:
            why = res.get("error") or ("no exciting moments were found to react to — add a Zoom or Slow-mo "
                                       "at the moments you want commented, then try again")
            self.status.setText("🎙 Caster failed")
            QMessageBox.warning(self, "AI caster", f"The caster couldn't make a voice track:\n\n{why}")
            if getattr(self, "_render_after_caster", False):
                self._render_after_caster = False
                self.render()
            return
        self.caster_lines = lines
        self.script_lbl.setText(f"🎙 AI caster: {len(lines)} lines (on the timeline)   ✕")
        self.script_lbl.setVisible(True)
        self.timeline.update()
        self.status.setText(f"🎙 Caster added {len(lines)} line{'s' if len(lines) != 1 else ''}: "
                            + " · ".join(f"“{l['text']}”" for l in lines[:3]) + ("…" if len(lines) > 3 else "")
                            + "  — move, re-word or delete them on the 🎙 track")
        if getattr(self, "_render_after_caster", False):
            self._render_after_caster = False
            self.render()

    def _caster_reword(self, k: int):
        if not (0 <= k < len(self.caster_lines)):
            return
        line = self.caster_lines[k]
        text, ok = QInputDialog.getText(self, "Caster line", "What should the caster say?", text=line["text"])
        if not ok or not text.strip() or text.strip() == line["text"]:
            return
        self.status.setText("🎙 Recording the new line…")

        def work():
            import tempfile
            from ... import commentator
            try:
                wav, dur = commentator.speak_line(text.strip(), Path(tempfile.gettempdir()) / "ClipForge" / "caster",
                                                  self.s.caster_voice)
            except Exception:                # shown as "couldn't say that line" instead of waiting forever
                wav, dur = "", 0.0
            self.lineReady.emit({"k": k, "text": text.strip(), "wav": wav, "dur": dur})
        threading.Thread(target=work, daemon=True).start()

    def _line_done(self, res):
        k = res["k"]
        if not res["wav"]:
            self.status.setText("🎙 The voice engine couldn't say that line.")
            return
        if 0 <= k < len(self.caster_lines):
            self.caster_lines[k].update(text=res["text"], wav=res["wav"], dur=res["dur"])
            self.status.setText(f"🎙 New line: “{res['text']}”")
            self.timeline.update()
            self._mark()

    def _drop_voice(self):
        self.caster_lines = []
        self.set_script(None)
        self.timeline.update()

    def _say(self, line: dict, vol: float):
        if not line.get("wav") or not Path(line["wav"]).exists():
            return
        if not hasattr(self, "_say_player"):
            from ..player import make_audio_player
            self._say_player, self._say_out = make_audio_player(self)
        self._say_out.setVolume(min(1.0, 0.9 * vol))
        self._say_player.setSource(QUrl.fromLocalFile(line["wav"]))
        self._say_player.play()

    def _stop_say(self):
        if hasattr(self, "_say_player"):
            self._say_player.stop()

    def caster_volume(self) -> float:
        return self.caster_vol.value() / 100
