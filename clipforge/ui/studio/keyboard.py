"""Keyboard shortcuts in the Studio (S split, Delete, J/K/L, arrows nudge, T/B/M add)."""
from __future__ import annotations

from PySide6.QtCore import Qt


class KeyboardMixin:
    def _shortcuts(self):
        from PySide6.QtGui import QKeySequence, QShortcut
        keys = {"S": self._key_split, "Delete": self._key_delete, "Backspace": self._key_delete,
                "J": lambda: self._key_seek(-2.0), "K": self._key_playpause, "L": self._key_play,
                "Left": lambda: self._key_nudge(-0.1), "Right": lambda: self._key_nudge(0.1),
                "Shift+Left": lambda: self._key_nudge(-1.0), "Shift+Right": lambda: self._key_nudge(1.0),
                "Ctrl+D": lambda: self._piece_action("duplicate", self._current, 0.0),
                "T": self._add_text, "B": self._add_blur, "M": self._add_mute, "Space": self._key_playpause}
        for k, fn in keys.items():
            sc = QShortcut(QKeySequence(k), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _key_split(self):
        pc = self._cur()
        if pc:
            self._piece_action("split", self._current, self.player.position)

    def _key_delete(self):
        sel = getattr(self.timeline, "sel", None)
        if sel:
            self.timeline.delete_selected()
        elif self._cur():
            self._piece_action("remove", self._current, 0.0)

    def _key_seek(self, dt: float):
        self.player.player.setPosition(max(0, int((self.player.position + dt) * 1000)))

    def _key_playpause(self):
        self.player.toggle()

    def _key_play(self):
        if self.player.player.playbackState() != self.player.player.PlaybackState.PlayingState:
            self.player.toggle()

    def _key_nudge(self, dt: float):
        if getattr(self.timeline, "sel", None):
            self.timeline.nudge_selected(dt)
        else:
            self._key_seek(dt)
