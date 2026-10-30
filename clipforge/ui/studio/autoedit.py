"""Automatic editing in the Studio: another take, the auto edit, dead air (and music that YouTube may claim) and syncing cuts to the beat."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ...autoshort import suggest_hook
from ...editor import PRESETS


class AutoEditMixin:
    def _another_take(self):
        if not self.pieces:
            self.status.setText("Add some clips first — then 🎲 makes a different take of them.")
            return
        self.save_draft()                       # keep this one: it waits in Drafts next to the new take
        clips, seen = [], set()
        for p in self.pieces:
            if p["clip"].path not in seen:
                seen.add(p["clip"].path)
                clips.append(p["clip"])
        self.anotherTake.emit({
            "clips": clips, "origin": self.draft_origin or "auto", "preset": self.preset.currentData(),
            "exclude": [(p["clip"].path, p["start"], p["end"]) for p in self.pieces], "hook": self.hook.text()})
        self.status.setText("🎲 Making another take in the background — it shows up on the left when it's ready. "
                            "This edit is saved in Drafts.")

    def auto_edit(self, add_fx: bool = True, montage: bool = False, then_render: bool = False):
        """The automatic edit, done inside the Studio so you can change anything before rendering:
        effects on the big moments, a few small GIFs (if allowed), the hook text and the AI caster."""
        if not self.pieces:
            return
        if not getattr(self, "_auto_wired", False):
            self._auto_wired = True
            self.autoEditReady.connect(self._auto_edit_done)
        from ...styles import style_for, style_name_for
        st_name = style_name_for(self.s, self._game())
        style = style_for(self.s, self._game())
        if style:
            self.apply_style(style, st_name)
        vertical = PRESETS[self.preset.currentData()].vertical
        snapshot = [dict(p, effects=[dict(e) for e in p.get("effects", [])]) for p in self.pieces]
        s = self.s
        use_gifs = style.get("gifs", s.auto_gifs) if style else s.auto_gifs
        cut = montage and (style.get("cut_boring", s.auto_cut_boring) if style else s.auto_cut_boring)
        self.status.setText("✨ Auto-editing: " + ("cutting dead air, " if cut else "") + "effects" +
                            (", GIFs" if use_gifs else "") + (", caster" if s.caster_auto else "") + "…")

        def work():
            try:
                from ...autoedit import edit_pieces
                new, note = edit_pieces(snapshot, s, style, montage=montage, add_fx=add_fx, vertical=vertical)
            except Exception as e:
                new, note = snapshot, f" (auto edit problem: {e})"
            self.autoEditReady.emit({"pieces": new, "note": note, "render": then_render})
        threading.Thread(target=work, daemon=True).start()

    def _auto_edit_done(self, res):
        new = res["pieces"]
        if new:
            self.pieces = new
        if self.music.text().strip() and (self.current_style().get("beat_sync", True)):
            self._beat_sync(quiet=True)
        if not self.hook.text().strip() and self._is_short() and not self.script:
            self.hook.setText(suggest_hook(self._game(), self.hook_weights()))
        self._refresh_list(max(0, self._current))
        self._show_fx()
        n_fx = sum(1 for p in self.pieces for e in p["effects"] if e.get("type") != "gif")
        msg = f"✨ Auto edit: {n_fx} effects{res['note']}."
        if self.s.caster_auto:
            self._render_after_caster = res["render"]
            self._caster()
            msg += " The caster is writing his lines…"
        elif res["render"]:
            self.render()
            return
        self.status.setText(msg + " Check it on the timeline, change anything, then press Render video.")

    def _scan_dead(self):
        """Finds the boring stretches of every piece in the background (shown red on the timeline)."""
        todo = []
        for p in self.pieces:
            key = (p["clip"].path, round(p["start"], 2), round(p["end"], 2))
            if key not in self.dead:
                ev = tuple(sorted({round(e["t"], 1) for e in p["clip"].events} | set(p["clip"].markers)))
                todo.append((key, ev))
        if not todo or getattr(self, "_dead_busy", False):
            return
        self._dead_busy = True

        def work():
            from ...analysis import dead_spans, music_score
            out = {}
            for key, ev in todo:
                try:
                    out[key] = dead_spans(key[0], key[1], key[2], ev)
                except Exception:
                    out[key] = []
                try:   # game music in the clip = Content ID risk
                    self.music_scores[key] = music_score(key[0], key[1], key[2])
                except Exception:
                    self.music_scores[key] = 0.0
            self.deadReady.emit(out)
        threading.Thread(target=work, daemon=True).start()

    def _dead_done(self, res):
        self._dead_busy = False
        self.dead.update(res)
        flagged = [i + 1 for i, p in enumerate(self.pieces)
                   if self.music_scores.get((p["clip"].path, round(p["start"], 2), round(p["end"], 2)), 0) >= 0.55]
        self.music_warn.setText(f"⚠ Clip{'s' if len(flagged) > 1 else ''} {', '.join(map(str, flagged))} seem to have "
                                "music in the game sound — YouTube may claim the video (Content ID). Turn Game volume "
                                "down and use music from your own folder." if flagged else "")
        self.music_warn.setVisible(bool(flagged))
        n = sum(1 for p in self.pieces
                if self.dead.get((p["clip"].path, round(p["start"], 2), round(p["end"], 2))))
        self.boring_btn.setText("✂ Cut boring parts" + (f" ({n})" if n else ""))
        self.timeline.update()
        if any((p["clip"].path, round(p["start"], 2), round(p["end"], 2)) not in self.dead for p in self.pieces):
            self._dead_timer.start()

    def dead_for(self, pc: dict) -> list:
        return self.dead.get((pc["clip"].path, round(pc["start"], 2), round(pc["end"], 2)), [])

    def _cut_boring(self):
        from ...analysis import cut_spans
        if not self.pieces:
            return
        before = sum(p["end"] - p["start"] for p in self.pieces)
        new = []
        for p in self.pieces:
            new += cut_spans(p, self.dead_for(p))
        gone = before - sum(p["end"] - p["start"] for p in new)
        if gone < 0.3:
            self.status.setText("✂ No boring parts found (or still checking — they show up red on the timeline)")
            return
        self.pieces = new
        self._autoplay_next = False
        self._refresh_list(0)
        self.status.setText(f"✂ Cut {gone:.1f} s of dead air. Ctrl+Z brings it back.")

    def _beat_sync(self, quiet: bool = False):
        music = self.music.text().strip()
        if not music or not Path(music).exists():
            if not quiet:
                QMessageBox.information(self, "Sync to beat", "Pick some background music first (Music on the right).")
            return
        self.status.setText("🎵 Finding the beat…")
        if not getattr(self, "_beat_wired", False):
            self._beat_wired = True
            self.beatsReady.connect(self._beats_done)

        def work():
            from ...analysis import beats
            try:
                self.beatsReady.emit(list(beats(music)))
            except Exception:
                self.beatsReady.emit([])
        threading.Thread(target=work, daemon=True).start()

    def _beats_done(self, bts):
        from ... import timeline_model as tm
        from ...analysis import nearest
        if len(bts) < 4:
            self.status.setText("🎵 Couldn't find a steady beat in that music.")
            return
        moved = 0
        # 1. every cut lands on a beat (clips get a little longer or shorter, never past their footage)
        acc = 0.0
        for i, pc in enumerate(self.pieces):
            d = tm.piece_out_dur(pc)
            end_out = acc + d
            if i < len(self.pieces) - 1:
                b = nearest(bts, end_out, 0.6)
                if b is not None and abs(b - end_out) > 0.03:
                    sp = max(0.1, pc.get("speed", 1.0))
                    new_end = pc["end"] + (b - end_out) * sp
                    new_end = max(pc["start"] + 0.8, min(pc["clip"].duration or new_end, new_end))
                    if abs(new_end - pc["end"]) > 0.01:
                        pc["end"] = round(new_end, 3)
                        moved += 1
            acc += tm.piece_out_dur(pc)
        # 2. zooms and sound effects hit on the beat
        lay = tm.layout(self.pieces)
        for (a, _), pc in zip(lay, self.pieces):
            for e in pc.get("effects", []):
                if e.get("type") in ("zoom", "sfx"):
                    o = a + tm.src_to_out(pc, e["t"])
                    b = nearest(bts, o, 0.3)
                    if b is not None:
                        e["t"] = round(max(pc["start"], min(pc["end"] - 0.05, tm.out_to_src(pc, max(0.0, b - a)))), 3)
                        moved += 1
        self._autoplay_next = False
        self._refresh_list(max(0, self._current))
        self._show_fx()
        self.status.setText(f"🎵 Synced {moved} cuts / effects to the beat ({60 / max(0.2, bts[1] - bts[0]):.0f} BPM)."
                            " Ctrl+Z undoes it.")
