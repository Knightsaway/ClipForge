"""📝 Drafts: the session inbox (Shorts ClipForge made from your last session), the render queue,
your saved Studio edits, and what was rendered lately."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import drafts
from .widgets import fmt_dur

ORIGIN = {"session": "📥 from your session", "auto": "⚡ Auto Short", "montage": "🎬 Montage", "studio": "✏ Studio"}


class QueueBridge(QObject):
    """Render-queue callbacks (worker thread) → UI thread."""
    progress = Signal(str, float, str)
    done = Signal(str, object)
    failed = Signal(str, str)


def _when(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d")


class DraftsPage(QWidget):
    openDraft = Signal(dict)
    queueChanged = Signal()

    def __init__(self):
        super().__init__()
        self._bars: dict[str, tuple[QProgressBar, QLabel]] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)
        head = QHBoxLayout()
        t = QLabel("DRAFTS")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        self.all_btn = QPushButton("▶  Render everything in the inbox")
        self.all_btn.setObjectName("primary")
        self.all_btn.setToolTip("Queues every inbox Short (with its TikTok version). They render in the background.")
        self.all_btn.clicked.connect(self._queue_inbox)
        head.addWidget(self.all_btn)
        lay.addLayout(head)
        tip = QLabel("Every Studio edit is saved here automatically. The render queue works through your videos "
                     "one by one in the background — and waits while you're in a game.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.v = QVBoxLayout(self.inner)
        self.v.setSpacing(8)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)

    # ---------- data
    def inbox(self) -> list[dict]:
        return [d for d in drafts.all_drafts() if d.get("origin") == "session" and d.get("status") == "draft"]

    def refresh(self):
        while self.v.count():
            it = self.v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._bars = {}
        alld = drafts.all_drafts()
        inbox = [d for d in alld if d.get("origin") == "session" and d.get("status") == "draft"]
        queue = sorted([d for d in alld if d.get("status") in ("queued", "rendering")],
                       key=lambda d: (d.get("status") != "rendering", d.get("queued_at", 0)))
        mine = [d for d in alld if d.get("status") in ("draft", "failed") and d.get("origin") != "session"]
        done = [d for d in alld if d.get("status") == "done"][:10]
        self.all_btn.setVisible(bool(inbox))
        self._section(f"📥  INBOX — FROM YOUR LAST SESSION ({len(inbox)})", inbox,
                      "Nothing new. After you play for a while and stop, the best Shorts of the session wait here.")
        self._section(f"⏳  RENDER QUEUE ({len(queue)})", queue, "Empty. Press ＋ Queue in the Studio, or ▶ Queue here.")
        self._section(f"📝  YOUR DRAFTS ({len(mine)})", mine, "No drafts. Anything you edit in the Studio shows up here.")
        if done:
            self._section("✔  RENDERED LATELY", done, "")
        self.v.addStretch(1)

    def _section(self, title: str, items: list[dict], empty: str):
        h = QLabel(title)
        h.setObjectName("h2")
        self.v.addWidget(h)
        if not items and empty:
            e = QLabel(empty)
            e.setObjectName("muted")
            e.setWordWrap(True)
            self.v.addWidget(e)
        for d in items:
            self.v.addWidget(self._row(d))
        self.v.addSpacing(10)

    def _row(self, d: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("card")
        h = QHBoxLayout(row)
        pic = QLabel()
        pm = QPixmap(d.get("thumb", "")) if d.get("thumb") else QPixmap()
        if not pm.isNull():
            pic.setPixmap(pm.scaled(112, 63, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        pic.setFixedSize(112, 63)
        h.addWidget(pic)
        info = QVBoxLayout()
        title = QLabel(d.get("title") or "Untitled")
        title.setObjectName("cardtitle")
        st = d.get("status", "draft")
        bits = [d.get("game") or "", fmt_dur(d.get("length", 0)), ORIGIN.get(d.get("origin"), ""),
                _when(d.get("updated", time.time()))]
        if d.get("schedule"):
            bits.append("📅 will be scheduled")
        meta = QLabel("  ·  ".join(b for b in bits if b))
        meta.setObjectName("muted")
        info.addWidget(title)
        info.addWidget(meta)
        if st == "failed":
            err = QLabel("❌ " + (d.get("error") or "Render failed")[:160])
            err.setStyleSheet("color:#ff5c6c;")
            err.setWordWrap(True)
            info.addWidget(err)
        if st in ("queued", "rendering"):
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(0)
            lbl = QLabel("Rendering…" if st == "rendering" else "Waiting for its turn")
            lbl.setObjectName("muted")
            info.addWidget(bar)
            info.addWidget(lbl)
            self._bars[d["id"]] = (bar, lbl)
        h.addLayout(info, 1)

        def btn(label, fn, primary=False, tip=""):
            b = QPushButton(label)
            if primary:
                b.setObjectName("primary")
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
        if st in ("draft", "failed"):
            btn("✏ Open", lambda: self.openDraft.emit(drafts.load(d["id"]) or d), tip="Open it in the Studio")
            if d.get("origin") == "session":
                btn("✔ Render & schedule", lambda: self._queue(d, schedule=True), True,
                    "Renders it (+ TikTok version) and schedules it on YouTube at your next free slot")
                btn("✖ Skip", lambda: self._skip(d), tip="Not this one")
            else:
                btn("▶ Queue", lambda: self._queue(d), True, "Render it in the background")
                btn("🗑", lambda: self._delete(d), tip="Delete this draft")
        elif st == "queued":
            btn("Remove from queue", lambda: self._unqueue(d))
        elif st == "done":
            out = d.get("output", "")
            if out and Path(out).exists():
                btn("▶ Play", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(out)))
                btn("📁", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out).parent))),
                    tip="Show the files")
            btn("✏ Edit again", lambda: self.openDraft.emit(drafts.load(d["id"]) or d),
                tip="Opens a copy in the Studio; the rendered video stays as it is")
        return row

    # ---------- actions
    def _queue(self, d: dict, schedule: bool = False):
        drafts.update(d["id"], status="queued", queued_at=time.time(), schedule=schedule or d.get("schedule", False))
        self.queueChanged.emit()
        self.refresh()

    def _queue_inbox(self):
        for d in self.inbox():
            drafts.update(d["id"], status="queued", queued_at=time.time(), schedule=True)
        self.queueChanged.emit()
        self.refresh()

    def _unqueue(self, d: dict):
        drafts.update(d["id"], status="draft")
        self.refresh()

    def _skip(self, d: dict):
        drafts.update(d["id"], status="skipped")
        self.refresh()

    def _delete(self, d: dict):
        if QMessageBox.question(self, "Delete draft", f"Delete the draft “{d.get('title', '')}”?\n"
                                "Your clips aren't touched.") == QMessageBox.Yes:
            drafts.delete(d["id"])
            self.refresh()

    # ---------- queue progress
    def on_progress(self, did: str, f: float, msg: str):
        if did in self._bars:
            bar, lbl = self._bars[did]
            bar.setValue(int(f * 1000))
            lbl.setText(msg)
        elif self.isVisible():
            self.refresh()
