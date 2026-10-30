"""Things on top of the whole video in the Studio: text, blur boxes and mutes (with automatic bleeps for swearing)."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox

from ...editor import Piece


class OverlayMixin:
    def _out_now(self) -> float:
        return float(getattr(self.timeline, "playhead", 0.0))

    def _add_item(self, item: dict, place: bool = True):
        self.items.append(item)
        self.timeline.sel = ("item", len(self.items) - 1)
        self.timeline.update()
        self._mark()
        if place:
            self.mid_tabs.setCurrentIndex(1)
            self.cam_edit.set_items(self.items)
            self.cam_edit.select_item(len(self.items) - 1)

    def _add_text(self):
        if not self.pieces:
            return
        text, ok = QInputDialog.getText(self, "Text", "What should it say?", text="")
        if not ok or not text.strip():
            return
        item = {"kind": "text", "t": round(self._out_now(), 2), "dur": 2.5, "text": text.strip(),
                "cx": 0.5, "cy": 0.45, "scale": 1.0, "style": "white", "pop": True}
        tpl = self.s.text_templates.get(self.s.text_default)   # your last-used text look
        if tpl:
            item.update({k: v for k, v in tpl.items() if k in ("style", "scale", "cx", "cy", "pop", "dur")})
        self._add_item(item)
        self.status.setText("T Text added — drag it on the video, change its look on the right of the Final look tab.")

    def _add_blur(self):
        if not self.pieces:
            return
        self._add_item({"kind": "blur", "t": round(self._out_now(), 2), "dur": 3.0,
                        "x": 0.05, "y": 0.75, "w": 0.45, "h": 0.12})
        self.status.setText("▦ Blur box added — drag it over what you want hidden, drag a corner to resize.")

    def _add_mute(self):
        if not self.pieces:
            return
        self._add_item({"kind": "mute", "t": round(self._out_now(), 2), "dur": 1.0, "bleep": True}, place=False)
        self.status.setText("🔇 Mute added at the playhead (with a bleep) — stretch it on the timeline; "
                            "right-click it to switch the bleep off.")

    def _auto_bleep(self):
        from ... import captions
        if not self.pieces:
            return
        if not captions.available():
            QMessageBox.information(self, "Bleep swearing", "This needs the speech-to-text add-on: run setup.bat "
                                    "and say yes to captions.")
            return
        if not getattr(self, "_bleep_wired", False):
            self._bleep_wired = True
            self.bleepReady.connect(self._bleep_done)
        pieces = [Piece(p["clip"].path, p["start"], p["end"], speed=p.get("speed", 1.0), effects=p.get("effects", []))
                  for p in self.pieces]
        self.status.setText("🤬 Listening for swearing…")

        def work():
            import tempfile
            try:
                words = captions.transcribe_pieces(pieces, Path(tempfile.mkdtemp(prefix="bleep_")))
                self.bleepReady.emit(captions.swear_spans(words))
            except Exception as e:
                self.bleepReady.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _bleep_done(self, res):
        if isinstance(res, str):
            self.status.setText(f"🤬 Couldn't listen: {res[:120]}")
            return
        have = {(round(m["t"], 1)) for m in self.items if m.get("kind") == "mute"}
        n = 0
        for a, b in res:
            if round(a, 1) not in have:
                self.items.append({"kind": "mute", "t": a, "dur": round(b - a, 3), "bleep": True, "auto": True})
                n += 1
        self.timeline.update()
        self._mark()
        self.status.setText(f"🤬 Bleeped {n} word{'s' if n != 1 else ''}." if n else "🤬 No swearing found. 😇")

    def _items_changed(self):
        self.timeline.update()
        self._mark()

    def _timeline_item(self, k: int):
        it = self.items[k] if 0 <= k < len(self.items) else None
        if it and it.get("kind") in ("text", "blur"):
            self.cam_edit.set_items(self.items)
            self.cam_edit.select_item(k)
