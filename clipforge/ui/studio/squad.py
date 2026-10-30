"""Friend's angle in the Studio: a squad mate's recording of the same moment, as split-screen or a replay, lined up by audio."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtWidgets import QMenu, QMessageBox


class SquadMixin:
    def owner(self, clip) -> str:
        """Whose recording a clip is: squad clips live in <squad>/<name>/clips/."""
        p = clip.p
        if p.parent.name == "clips" and self.s.squad_dir and str(p).startswith(str(Path(self.s.squad_dir))):
            return p.parent.parent.name
        return self.s.squad_name or "Me"

    def _friend_angle(self):
        from ...squad import same_moment
        pc = self._cur()
        if not pc:
            QMessageBox.information(self, "Friend's angle", "Select a piece first.")
            return
        mine = pc["clip"]
        cands = [c for c in self.all_clips() if c.path != mine.path and self.owner(c) != self.owner(mine)
                 and same_moment(mine, c)]
        if not cands:
            QMessageBox.information(self, "Friend's angle", "No squad clips of this moment yet.\n\n"
                                    "Set up Settings → Squad with your friends; when anyone presses Marker, "
                                    "everyone's angle is saved and shows up here.")
            return
        m = QMenu(self)
        for c in cands[:8]:
            who = self.owner(c)
            m.addAction(f"Split-screen with {who}", lambda c=c: self._align(pc, c, "split"))
            m.addAction(f"Replay from {who}'s view after this", lambda c=c: self._align(pc, c, "replay"))
        m.exec(self.friend_btn.mapToGlobal(self.friend_btn.rect().bottomLeft()))

    def _align(self, pc: dict, other, mode: str):
        from ...squad import align
        mine = pc["clip"]
        self.status.setText("👥 Lining up the two angles (audio sync)…")

        def work():
            try:
                off, conf = align(mine.path, mine.created, other.path, other.created)
            except Exception as e:           # e.g. FFmpeg missing or timed out reading one of them
                off, conf = None, str(e)
            self.alignReady.emit((pc, other, mode, off, conf))
        threading.Thread(target=work, daemon=True).start()

    def _aligned(self, res):
        pc, other, mode, off, conf = res
        if pc not in self.pieces:
            return
        if off is None:
            self.status.setText(f"👥 Couldn't line up the two angles: {conf[:120]}")
            return
        sync = f"synced by audio ({conf:.0%})" if conf >= 0.25 else "synced by clock (audio match was weak)"
        if mode == "split":
            pc["alt"] = {"path": other.path, "offset": off, "label": self.owner(pc["clip"]),
                         "alt_label": self.owner(other)}
            self.status.setText(f"👥 Split-screen with {self.owner(other)} — {sync}")
        else:
            a = max(0.0, pc["start"] + off)
            b = min(other.duration, pc["end"] + off)
            i = self.pieces.index(pc)
            fx = [{"type": "sfx", "t": a + 0.05, "name": "whoosh"}]
            self.pieces.insert(i + 1, {"clip": other, "start": a, "end": b, "effects": fx, "speed": 1.0})
            self.status.setText(f"👥 Added {self.owner(other)}'s view as a replay — {sync}")
        self._refresh_list(self._current)
