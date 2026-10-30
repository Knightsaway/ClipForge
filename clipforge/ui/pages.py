from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .. import editor, ff
from ..autoshort import suggest_hook
from ..config import IS_WINDOWS, Settings, save_settings
from ..edit_history import EditHistory
from ..editor import PRESETS, Piece
from ..highlights import find_moments
from ..library import Clip, Library
from . import theme
from .player import PlayerPanel
from .studio import (AIMixin, AutoEditMixin, DraftMixin, EffectsMixin, HistoryMixin, KeyboardMixin, OverlayMixin,
                     RenderMixin, SquadMixin, StyleMixin)
from .widgets import ClipCard, HotkeyEdit, day_label, fmt_dur


def show_in_folder(path: str):
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


class Jobs(QObject):
    """Background work with results delivered on the UI thread."""
    progress = Signal(str, float, str)
    done = Signal(str, object)
    failed = Signal(str, str)

    def run(self, job_id: str, fn, *args):
        def work():
            try:
                res = fn(*args, progress=lambda f, m: self.progress.emit(job_id, f, m))
                self.done.emit(job_id, res)
            except Exception as e:
                self.failed.emit(job_id, str(e))
        threading.Thread(target=work, daemon=True).start()


# ====================================================================== library
class LibraryPage(QWidget):
    PAGE = 48
    makeToday = Signal()
    makeSeries = Signal(list)
    openClip = Signal(object)
    toStudio = Signal(list)
    montage = Signal(list)
    autoShort = Signal(list)
    thumbReady = Signal(object, object)
    scanned = Signal(list)

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.clips: list[Clip] = []
        self.cards: dict[str, ClipCard] = {}
        self.selected: list[Clip] = []
        self.thumbReady.connect(self._set_thumb)
        self.scanned.connect(self._on_scanned)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 0)
        head = QHBoxLayout()
        self.title = QLabel("ALL CLIPS (0)")
        self.title.setObjectName("h1")
        head.addWidget(self.title)
        self.montage_btn = QPushButton("🎬  Create Montage")
        self.montage_btn.setObjectName("primary")
        self.montage_btn.setToolTip("Put the selected clips (Ctrl+click) in the Studio to cut and join")
        self.montage_btn.clicked.connect(lambda: self.montage.emit(self._selection_or_hint()))
        self.auto_btn = QPushButton("⚡  Auto Short")
        self.auto_btn.setObjectName("green")
        self.auto_btn.setToolTip("Builds a 9:16 Short/TikTok from the selected clips — or, if none are selected,\n"
                                 "from your highlights and marked clips of the last 3 days")
        self.auto_btn.clicked.connect(lambda: self.autoShort.emit(self.selected[:] or self._auto_pool()))
        self.sel_lbl = QPushButton("")
        self.sel_lbl.setObjectName("icon")
        self.sel_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self.sel_lbl.clicked.connect(self.clear_selection)
        self.today_btn = QPushButton("🌅  Make today's content")
        self.today_btn.setToolTip("3 Shorts (+ TikTok versions) and 1 long video from the last 24 h of clips —\n"
                                  "they wait for your OK (here or on your phone) before anything is posted")
        self.today_btn.clicked.connect(self.makeToday)
        head.addWidget(self.today_btn)
        head.addWidget(self.montage_btn)
        head.addWidget(self.auto_btn)
        head.addWidget(self.sel_lbl)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Search clips or #tag")
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(lambda: self._debounce.start(200))
        self.game_filter = QComboBox()
        self.game_filter.setFixedWidth(160)
        self.game_filter.currentIndexChanged.connect(self._refilter)
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(["All", "★ Favorites", "⚑ Highlights, kills & markers", "Sessions"])
        self.kind_filter.currentIndexChanged.connect(self._refilter)
        self.sort = QComboBox()
        self.sort.addItems(["Newest", "Oldest", "Longest"])
        self.sort.currentIndexChanged.connect(self._refilter)
        refresh = QPushButton("⟳")
        refresh.setToolTip("Rescan folders")
        refresh.clicked.connect(self.refresh)
        for w in (self.search, self.game_filter, self.kind_filter, self.sort, refresh):
            head.addWidget(w)
        lay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.grid_lay = QVBoxLayout(self.inner)
        self.grid_lay.setContentsMargins(0, 8, 0, 20)
        self.grid_lay.setSpacing(6)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.empty = QLabel("No clips yet.\n\nStart a game and press your Save Clip hotkey "
                            "(PageUp by default).\nPress the Marker key (Home) the moment something cool happens.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("muted")
        self.empty.setStyleSheet("font-size:14px;")
        lay.addWidget(self.empty)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._refilter)
        self._cols = 0
        # cards are built a page at a time (more appear as you scroll): each one holds a thumbnail picture,
        # and building all of them made a big library cost hundreds of MB
        self._limit = self.PAGE
        self._shown_total = 0
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._relayout_if_needed)

    # ----- data
    def refresh(self):
        threading.Thread(target=lambda: self.scanned.emit(self.lib.scan()), daemon=True).start()

    def _on_scanned(self, clips):
        from ..library import _key
        uniq, seen = [], set()
        for c in clips:
            if _key(c.path) not in seen:
                seen.add(_key(c.path))
                uniq.append(c)
        self.clips = uniq
        games = sorted({c.game for c in clips})
        cur = self.game_filter.currentText()
        self.game_filter.blockSignals(True)
        self.game_filter.clear()
        self.game_filter.addItem("All games")
        self.game_filter.addItems(games)
        idx = self.game_filter.findText(cur)
        self.game_filter.setCurrentIndex(max(0, idx))
        self.game_filter.blockSignals(False)
        self.rebuild()
        threading.Thread(target=self._thumb_worker, args=(list(clips),), daemon=True).start()

    def add_clip(self, clip: Clip):
        from ..library import _key
        k = _key(clip.path)
        self.clips = [c for c in self.clips if _key(c.path) != k]   # already listed by a refresh: replace it
        self.clips.insert(0, clip)
        if self.game_filter.findText(clip.game) < 0:
            self.game_filter.addItem(clip.game)
        self.rebuild()

    def _thumb_worker(self, clips):
        for c in clips:
            t = c.thumb if c.thumb.exists() else self.lib.make_thumb(c)
            self.thumbReady.emit(c.path, t)

    def _set_thumb(self, path, thumb):
        card = self.cards.get(path)
        if card:
            card.set_thumb(thumb)

    def _filtered(self) -> list[Clip]:
        q = self.search.text().lower().strip()
        game = self.game_filter.currentText()
        kind = self.kind_filter.currentIndex()
        out = []
        for c in self.clips:
            if game and game != "All games" and c.game != game:
                continue
            if kind == 1 and not c.favorite:
                continue
            if kind == 2 and not (c.highlight or c.markers or c.events):
                continue
            if kind == 3 and c.kind != "Session":
                continue
            if q:
                tags = " ".join("#" + t.lower() for t in (c.tags or []))
                hay = (c.display_title + " " + c.game + " " + c.p.name + " " + tags).lower()
                # "#funny #clutch" = clips with ALL those tags; other words search names, games and tags
                want_tags = [w for w in q.split() if w.startswith("#")]
                words = [w for w in q.split() if not w.startswith("#")]
                if any(t not in tags.split() for t in want_tags) or any(w not in hay for w in words):
                    continue
            out.append(c)
        s = self.sort.currentIndex()
        if s == 1:
            out.sort(key=lambda c: c.created)
        elif s == 2:
            out.sort(key=lambda c: c.duration, reverse=True)
        return out

    def _filter_active(self) -> bool:
        return bool(self.search.text().strip()) or self.game_filter.currentIndex() > 0 or \
            self.kind_filter.currentIndex() > 0

    def _auto_pool(self) -> list[Clip]:
        if self._filter_active():            # e.g. search "#fail" → Auto Short from your fails
            got = self._filtered()
            if got:
                return got[:40]
        recent = [c for c in self.clips if time.time() - c.created < 3 * 86400]
        pool = [c for c in recent if c.highlight or c.markers or c.favorite or c.events]
        return pool or recent[:12] or self.clips[:8]

    def _selection_or_hint(self) -> list[Clip]:
        if not self.selected and self._filter_active() and self._filtered():
            got = self._filtered()
            if QMessageBox.question(self, "Create Montage", f"Make a montage of the {min(len(got), 30)} clips "
                                    f"shown (your search / filter)?") == QMessageBox.Yes:
                return list(reversed(got[:30]))      # oldest first reads like a story
            return []
        if not self.selected:
            QMessageBox.information(self, "Create Montage",
                                    "Ctrl+click clips to select them (in the order you want), "
                                    "then press Create Montage.\nOr just press ⚡ Auto Short and let it pick.")
        return self.selected[:]

    # ----- view
    def _columns(self) -> int:
        w = self.scroll.viewport().width()
        return max(1, (w + 16) // (ClipCard.W + 16))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._resize_timer.start(120)

    def _relayout_if_needed(self):
        if self._columns() != self._cols:
            self.rebuild()

    def _refilter(self, *_):
        self._limit = self.PAGE
        self.rebuild()

    def _maybe_more(self, value: int):
        bar = self.scroll.verticalScrollBar()
        if value >= bar.maximum() - 400 and len(self.cards) < self._shown_total:
            self._show_more()

    def _show_more(self):
        bar = self.scroll.verticalScrollBar()
        keep = bar.value()
        self._limit += self.PAGE
        self.rebuild()
        bar.setValue(keep)

    def rebuild(self):
        while self.grid_lay.count():
            item = self.grid_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
        self.cards.clear()
        clips = self._filtered()
        self.title.setText(f"ALL CLIPS ({len(clips)})")
        self._shown_total = len(clips)
        rest = max(0, len(clips) - self._limit)
        clips = clips[:self._limit]
        self.empty.setVisible(not self.clips)
        self.scroll.setVisible(bool(self.clips))
        self._cols = cols = self._columns()
        groups: dict[str, list[Clip]] = {}
        for c in clips:
            groups.setdefault(day_label(c.created) if self.sort.currentIndex() != 2 else "LONGEST FIRST", []).append(c)
        sel_paths = {c.path for c in self.selected}
        for label, items in groups.items():
            games = sorted({c.game for c in items})
            h = QLabel(f"<span style='font-weight:600;letter-spacing:1px'>{label}</span>"
                       f"&nbsp;&nbsp;<span style='color:{theme.MUTED}'>{' & '.join(games[:3])}</span>")
            self.grid_lay.addWidget(h)
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(16)
            for i, c in enumerate(items):
                card = ClipCard(c)
                card.clicked.connect(self._card_clicked)
                card.favorite.connect(self._toggle_fav)
                card.menuRequested.connect(self._menu)
                card.set_selected(c.path in sel_paths)
                if c.thumb.exists():
                    card.set_thumb(c.thumb)
                self.cards[c.path] = card
                grid.addWidget(card, i // cols, i % cols, Qt.AlignLeft | Qt.AlignTop)
            grid.setColumnStretch(cols, 1)
            self.grid_lay.addLayout(grid)
            self.grid_lay.addSpacing(14)
        if rest:
            more = QPushButton(f"Show more ({rest} older clip{'s' if rest != 1 else ''})")
            more.clicked.connect(self._show_more)
            self.grid_lay.addWidget(more, 0, Qt.AlignHCenter)
        self.grid_lay.addStretch(1)
        self._update_sel_label()

    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())

    def _card_clicked(self, clip: Clip, mods):
        if mods & (Qt.ControlModifier | Qt.ShiftModifier):
            if any(c.path == clip.path for c in self.selected):
                self.selected = [c for c in self.selected if c.path != clip.path]
            else:
                self.selected.append(clip)
            card = self.cards.get(clip.path)
            if card:
                card.set_selected(any(c.path == clip.path for c in self.selected))
            self._update_sel_label()
        else:
            self.openClip.emit(clip)

    def clear_selection(self):
        self.selected = []
        for card in self.cards.values():
            card.set_selected(False)
        self._update_sel_label()

    def _update_sel_label(self):
        n = len(self.selected)
        self.sel_lbl.setText(f"{n} selected  ✕" if n else "Ctrl+click clips to select")

    def _toggle_fav(self, clip: Clip):
        clip.favorite = not clip.favorite
        clip.save()
        self.rebuild()

    def _menu(self, clip: Clip, pos):
        m = QMenu(self)
        m.addAction("Open / trim", lambda: self.openClip.emit(clip))
        m.addAction("⚡ Make a Short from this clip", lambda: self.autoShort.emit([clip]))
        m.addAction("🎬 Add to Studio", lambda: self.toStudio.emit([clip]))
        m.addAction("📺 Make a series (Part 1, 2, 3…)",
                    lambda: self.makeSeries.emit(self.selected[:] if clip in self.selected and len(self.selected) > 1
                                                 else [clip]))
        m.addSeparator()
        m.addAction("★ Favorite" if not clip.favorite else "☆ Unfavorite", lambda: self._toggle_fav(clip))
        m.addAction("Rename…", lambda: self._rename(clip))
        m.addAction("🏷 Tags…", lambda: self._tags(clip))
        m.addAction("Change game name…", lambda: self._regame(clip))
        m.addAction("Show in folder", lambda: show_in_folder(clip.path))
        m.addSeparator()
        m.addAction("🗑 Delete permanently", lambda: self._delete(clip))
        m.exec(pos)

    def _rename(self, clip):
        t, ok = QInputDialog.getText(self, "Rename clip", "Title:", text=clip.title or clip.display_title)
        if ok:
            clip.title = t.strip()
            clip.save()
            self.rebuild()

    def _tags(self, clip):
        targets = self.selected[:] if clip in self.selected and len(self.selected) > 1 else [clip]
        cur = " ".join(clip.tags or [])
        known = sorted({t for c in self.clips for t in (c.tags or [])})
        hint = ("Your tags: " + ", ".join(known[:12]) + "\n") if known else ""
        t, ok = QInputDialog.getText(self, "Tags", f"{hint}Tags for {len(targets)} clip{'s' if len(targets) > 1 else ''} "
                                     "(space-separated, e.g. funny fail clutch).\nSearch #funny to find them, then "
                                     "⚡ Auto Short or Create Montage uses what's shown:", text=cur)
        if not ok:
            return
        tags = sorted({w.strip("#,").lower() for w in t.split() if w.strip("#,")})
        for c in targets:
            c.tags = tags
            c.save()
        self.rebuild()

    def _regame(self, clip):
        t, ok = QInputDialog.getText(self, "Game name", "Game:", text=clip.game)
        if ok and t.strip():
            clip.game = t.strip()
            clip.save()
            self._on_scanned(self.clips)

    def release_file(self, path: str):
        pass   # replaced by the main window: closes any player that has this clip open

    def _delete(self, clip):
        if QMessageBox.question(self, "Delete clip", f"Delete “{clip.display_title}”?\n\n"
                                "It skips the Recycle Bin. For 24 hours you can still get it back in "
                                "Settings → Upkeep → Recently deleted; after that it's gone for good.") \
                == QMessageBox.Yes:
            self.release_file(clip.path)
            if not self.lib.delete(clip):
                QMessageBox.warning(self, "Delete clip", "Windows wouldn't let go of the video file — it's probably "
                                    "open in another program. Close it and try again.")
                return
            self.clips = [c for c in self.clips if c.path != clip.path]
            self.selected = [c for c in self.selected if c.path != clip.path]
            self.rebuild()


# ====================================================================== clip view
class ClipPage(QWidget):
    back = Signal()
    toStudio = Signal(list)
    exportClip = Signal(object, str)   # clip, preset key
    saved = Signal(object)
    momentsReady = Signal(str, list)
    eventsReady = Signal(str, list)
    scanGame = Signal(str)

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.clip: Clip | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)
        top = QHBoxLayout()
        b = QPushButton("←  Back to clips")
        b.clicked.connect(self._back)
        self.name = QLabel()
        self.name.setObjectName("h1")
        top.addWidget(b)
        top.addSpacing(10)
        top.addWidget(self.name, 1)
        lay.addLayout(top)

        body = QHBoxLayout()
        self.player = PlayerPanel()
        body.addWidget(self.player, 1)
        side = QFrame()
        side.setObjectName("panel")
        side.setFixedWidth(270)
        s = QVBoxLayout(side)
        s.setSpacing(8)
        s.addWidget(self._h("QUICK EDIT"))
        hint = QLabel("Drag the purple handles or press I / O.\nYellow flags = your markers. Red = kills.\n"
                      "Grey ticks = loud moments found automatically.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        s.addWidget(hint)
        keep = QPushButton("✂  Keep only the selection")
        keep.setToolTip("Lossless — saves a new trimmed clip instantly (cuts snap to ~2 s)")
        keep.clicked.connect(self._save_trim)
        s.addWidget(keep)
        studio = QPushButton("🎬  Add selection to Studio")
        studio.clicked.connect(self._to_studio)
        s.addWidget(studio)
        s.addSpacing(10)
        s.addWidget(self._h("EXPORT"))
        for key, label in (("short", "📱  Short / TikTok (9:16)"), ("youtube1080", "▶  YouTube 1080p60"),
                           ("youtube1440", "▶  YouTube 1440p60"), ("discord", "💬  Discord (<10 MB)")):
            btn = QPushButton(label)
            if key == "short":
                btn.setObjectName("primary")
            btn.clicked.connect(lambda _=False, k=key: self._export(k))
            s.addWidget(btn)
        s.addSpacing(10)
        s.addWidget(self._h("KILL DETECTION"))
        self.kills_lbl = QLabel("")
        self.kills_lbl.setObjectName("muted")
        self.kills_lbl.setWordWrap(True)
        s.addWidget(self.kills_lbl)
        teach = QPushButton("💀  Teach kill icon (at playhead)")
        teach.setToolTip("Pause on a frame showing the kill icon or medal, then draw a box around it")
        teach.clicked.connect(self._teach)
        s.addWidget(teach)
        self.scan_btn = QPushButton("🔍  Scan this clip for kills")
        self.scan_btn.clicked.connect(self._scan)
        s.addWidget(self.scan_btn)
        s.addSpacing(10)
        s.addWidget(self._h("CLIP"))
        self.fav = QPushButton()
        self.fav.clicked.connect(self._fav)
        s.addWidget(self.fav)
        folder = QPushButton("📁  Show in folder")
        folder.clicked.connect(lambda: self.clip and show_in_folder(self.clip.path))
        s.addWidget(folder)
        s.addStretch(1)
        body.addWidget(side)
        lay.addLayout(body, 1)
        self.player.rangeChanged.connect(self._range)
        self.momentsReady.connect(self._moments)
        self.eventsReady.connect(self._events)

    def _h(self, t):
        l = QLabel(t)
        l.setObjectName("h2")
        return l

    def show_clip(self, clip: Clip):
        self.clip = clip
        self.name.setText(f"{clip.game}  ·  {clip.display_title}")
        self.fav.setText("☆  Unfavorite" if clip.favorite else "★  Favorite")
        self.player.load(clip.path, clip.duration, clip.trim_in, clip.trim_out, clip.markers,
                         events=[e["t"] for e in clip.events])
        self.player.setFocus()
        self._kills_text()
        path = clip.path
        threading.Thread(target=lambda: self.momentsReady.emit(path, [t for t, _ in find_moments(path)]),
                         daemon=True).start()

    def _moments(self, path, ts):
        if self.clip and self.clip.path == path:
            self.player.timeline.moments = ts
            self.player.timeline.update()

    def _kills_text(self):
        from .. import vision
        c = self.clip
        if not c:
            return
        n_det = len(vision.load(c.game).detectors)
        if not n_det:
            self.kills_lbl.setText(f"No kill icons taught for {c.game} yet.")
        elif not c.scanned:
            self.kills_lbl.setText(f"{n_det} icon(s) taught. This clip hasn't been scanned yet.")
        else:
            cl = vision.clusters(c.events)
            best = max((x["count"] for x in cl), default=0)
            self.kills_lbl.setText(f"{len(c.events)} kills found" +
                                   (f" · best: {vision.label_for(best)}" if best > 1 else ""))

    def _events(self, path, events):
        if self.clip and self.clip.path == path:
            self.player.timeline.events = [e["t"] for e in events]
            self.player.timeline.update()
            self.scan_btn.setEnabled(True)
            self._kills_text()

    def _scan(self):
        from .. import vision
        c = self.clip
        if not c:
            return
        if not vision.has_detectors(c.game):
            QMessageBox.information(self, "Kill detection", "Teach a kill icon first (button above).")
            return
        self.scan_btn.setEnabled(False)
        self.kills_lbl.setText("Scanning…")

        def work():
            try:
                c.events = vision.detect(c.path, c.game)
                c.scanned = True
                c.save()
            except Exception as e:
                print("scan:", e)
            self.eventsReady.emit(c.path, c.events)
        threading.Thread(target=work, daemon=True).start()

    def _teach(self):
        from .extra import TeachDialog
        c = self.clip
        if not c:
            return
        self.player.player.pause()
        dlg = TeachDialog(c, self.player.position, self)
        if dlg.exec():
            if QMessageBox.question(self, "Kill detection",
                                    f"Icon saved ✔\n\nScan all your {c.game} clips for it now? "
                                    "(runs in the background at low priority)") == QMessageBox.Yes:
                self.scanGame.emit(c.game)
            self._scan()

    def _back(self):
        self.player.unload()
        self.back.emit()

    def _range(self, a, b):
        if self.clip:
            self.clip.trim_in, self.clip.trim_out = a, (0.0 if b >= self.clip.duration - 0.05 else b)
            self.clip.save()

    def _fav(self):
        if self.clip:
            self.clip.favorite = not self.clip.favorite
            self.clip.save()
            self.fav.setText("☆  Unfavorite" if self.clip.favorite else "★  Favorite")

    def _save_trim(self):
        c = self.clip
        if not c:
            return
        a, b = self.player.timeline.t_in, self.player.timeline.t_out
        out = c.p.with_name(c.p.stem + f" trim {datetime.now().strftime('%H%M%S')}.mp4")
        try:
            editor.quick_trim(c.p, a, b, out)
            new = self.lib.add_clip(out, game=c.game, created=c.created + a, kind="Clip",
                                    markers=[m - a for m in c.markers if a <= m <= b], title=c.title)
            self.saved.emit(new)
        except Exception as e:
            QMessageBox.warning(self, "Trim failed", str(e))

    def _to_studio(self):
        if self.clip:
            self.toStudio.emit([self.clip])

    def _export(self, key):
        if self.clip:
            self.exportClip.emit(self.clip, key)


# ====================================================================== clip picker
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QStackedWidget  # noqa: E402


class ClipPicker(QDialog):
    """Pick clips from the library to add to the Studio (in the order you click them)."""

    def __init__(self, clips: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add clips to the Studio")
        self.clips = clips
        self.chosen: list = []
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Search")
        self.search.textChanged.connect(self._fill)
        self.game = QComboBox()
        self.game.addItem("All games")
        self.game.addItems(sorted({c.game for c in clips}))
        self.game.currentIndexChanged.connect(self._fill)
        top.addWidget(self.search, 1)
        top.addWidget(self.game)
        v.addLayout(top)
        hint = QLabel("Click clips to select them (Ctrl/Shift for several). They're added in the order shown.")
        hint.setObjectName("muted")
        v.addWidget(hint)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QPixmap(240, 135).size())
        self.list.setGridSize(QPixmap(260, 190).size())
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._count)
        self.list.itemDoubleClicked.connect(lambda _: self._ok())
        v.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = QPushButton("Add")
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._ok)
        bb.addButton(self.ok, QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(1150, 720)
        self._fill()

    def _fill(self):
        q = self.search.text().lower().strip()
        g = self.game.currentText()
        self.list.clear()
        for c in self.clips:
            if g != "All games" and c.game != g:
                continue
            if q and q not in (c.display_title + " " + c.game).lower():
                continue
            it = QListWidgetItem(f"{c.game}\n{c.display_title}  ·  {fmt_dur(c.duration)}")
            if c.thumb.exists():
                it.setIcon(QPixmap(str(c.thumb)))
            it.setData(Qt.UserRole, c)
            self.list.addItem(it)
        self._count()

    def _count(self):
        n = len(self.list.selectedItems())
        self.ok.setEnabled(n > 0)
        self.ok.setText(f"Add {n} clip{'s' if n != 1 else ''}" if n else "Add")

    def _ok(self):
        items = sorted(self.list.selectedItems(), key=lambda it: self.list.row(it))
        self.chosen = [it.data(Qt.UserRole) for it in items]
        if self.chosen:
            self.accept()


# ====================================================================== studio
class StudioPage(HistoryMixin, DraftMixin, StyleMixin, KeyboardMixin, EffectsMixin, OverlayMixin, RenderMixin,
                 AIMixin, AutoEditMixin, SquadMixin, QWidget):
    rendered = Signal(object)
    anotherTake = Signal(dict)     # "Try another edit": the main window makes it in the background
    # Results of background work, delivered on the UI thread. They're used by the parts in ui/studio/ but declared
    # here, on the QWidget: signals on a plain mixin class are only known to work on newer PySide6 (checked: 6.12).
    aiReady = Signal(object)        # studio/ai.py: titles and hooks from the AI writer
    casterReady = Signal(object)    # studio/ai.py: the caster's lines
    lineReady = Signal(object)      # studio/ai.py: a re-worded caster line
    alignReady = Signal(object)     # studio/squad.py: a friend's angle lined up
    autoEditReady = Signal(object)  # studio/autoedit.py
    deadReady = Signal(object)      # studio/autoedit.py: dead air and music found
    beatsReady = Signal(object)     # studio/autoedit.py
    bleepReady = Signal(object)     # studio/overlays.py: swear words found
    queued = Signal(str)            # studio/saving.py: a draft went into the render queue

    def __init__(self, s: Settings, lib: Library, jobs: Jobs):
        super().__init__()
        self.s, self.lib, self.jobs = s, lib, jobs
        self.pieces: list[dict] = []
        self._current = -1
        # the hook text: when it shows and where it sits (both editable on the timeline / Layout tab)
        self.hook_start = 0.0
        self.hook_secs = 3.0
        self.hook_whole = False
        self.hook_pos: dict | None = None
        self.hook_scale = 1.0
        self.caster_lines: list[dict] = []     # [{"t", "text", "wav", "dur"}] — the AI caster's lines
        self.items: list[dict] = []           # your text, blur boxes and mutes (times in the finished video)
        self._play_all = False
        self.draft_id = ""                       # this edit is autosaved as a draft (Drafts page)
        self.draft_origin = "studio"
        self.thumb_t: float | None = None        # thumbnail moment picked on the timeline
        self.dead: dict = {}                     # (path, start, end) -> dead-air spans, for the timeline
        self.music_scores: dict = {}             # (path, start, end) -> how much the game sound is music
        self.history = EditHistory()               # undo / redo snapshots
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.setInterval(350)          # a drag = one undo step, not hundreds
        self._hist_timer.timeout.connect(self._commit)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        # left: the cut list
        left = QWidget()
        l = QVBoxLayout(left)
        l.setContentsMargins(0, 0, 0, 0)
        t = QLabel("STUDIO")
        t.setObjectName("h1")
        l.addWidget(t)
        tip = QLabel("Drag to reorder. Click a piece to trim it in the preview or on the timeline.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        l.addWidget(tip)
        self.adv = QCheckBox("Advanced: show the timeline")
        self.adv.setToolTip("Off = simple view (just the clip list and the preview)")
        self.adv.setChecked(s.studio_advanced)
        self.adv.toggled.connect(self._advanced)
        l.addWidget(self.adv)
        add = QPushButton("＋  Add clips")
        add.setObjectName("primary")
        add.setToolTip("Pick clips from your library to put in this video")
        add.clicked.connect(self.pick_clips)
        l.addWidget(add)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.currentRowChanged.connect(self._select)
        self.list.model().rowsMoved.connect(self._reordered)
        # empty state: what to do when nothing is in the Studio yet
        self.empty = QFrame()
        ev = QVBoxLayout(self.empty)
        ev.addStretch(1)
        t1 = QLabel("Nothing here yet")
        t1.setObjectName("h2")
        t1.setAlignment(Qt.AlignCenter)
        ev.addWidget(t1)
        t2 = QLabel("Press  ＋ Add clips  to pick clips from your library.\n\n"
                    "Tip: to use only part of a clip, open it on the Clips page, mark the part with "
                    "the purple handles (or I / O keys) and press  Add selection to Studio.\n\n"
                    "Or press  ⚡ Auto Short  on the Clips page and it does everything for you.")
        t2.setObjectName("muted")
        t2.setWordWrap(True)
        t2.setAlignment(Qt.AlignCenter)
        ev.addWidget(t2)
        big = QPushButton("＋  Add clips")
        big.setObjectName("primary")
        big.setMinimumHeight(40)
        big.clicked.connect(self.pick_clips)
        ev.addWidget(big)
        ev.addStretch(2)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.empty)
        self.list_stack.addWidget(self.list)
        l.addWidget(self.list_stack, 1)
        row = QHBoxLayout()
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        dup = QPushButton("Duplicate")
        dup.clicked.connect(self._dup)
        clr = QPushButton("Clear all")
        clr.clicked.connect(self.clear)
        for w in (rm, dup, clr):
            row.addWidget(w)
        l.addLayout(row)
        self.total = QLabel("Total 00:00")
        self.total.setObjectName("h2")
        l.addWidget(self.total)
        split.addWidget(left)

        # middle: preview  |  webcam placement
        self.player = PlayerPanel()
        self.player.rangeChanged.connect(self._range)
        from .camedit import CamEditor
        from PySide6.QtWidgets import QTabWidget
        self.cam_edit = CamEditor(s)
        self.mid_tabs = QTabWidget()
        self.mid_tabs.addTab(self.player, "▶  Preview")
        self.mid_tabs.addTab(self.cam_edit, "📱  Final look (live)")
        self.mid_tabs.setTabToolTip(1, "The finished video as it will look — press play to watch it with the text, "
                                       "GIFs and caster.\nDrag the text, webcam and GIFs to place them.")
        self.player.gameFrame.connect(self.cam_edit.live_game)
        self.player.camFrame.connect(self.cam_edit.live_cam)
        self.cam_edit.playhead = lambda: self.player.position
        self.cam_edit.gifsChanged.connect(self._gifs_changed)
        self.mid_tabs.currentChanged.connect(self._tab_changed)
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.addWidget(self.mid_tabs, 1)
        from .timeline import EditTimeline
        self.tl_bar = QWidget()
        tb = QHBoxLayout(self.tl_bar)
        tb.setContentsMargins(0, 4, 0, 0)
        self.play_all_btn = QPushButton("▶  Play whole edit")
        self.play_all_btn.setToolTip("Plays every clip one after the other, like the finished video\n"
                                     "(GIFs, text and the caster are only in the rendered video)")
        self.play_all_btn.clicked.connect(self._toggle_play_all)
        tl_tip = QLabel("Drag to trim / move · right-click for more · Ctrl+scroll zooms")
        tl_tip.setObjectName("muted")
        zin = QPushButton("+")
        zin.setFixedWidth(30)
        zin.setToolTip("Zoom in")
        zout = QPushButton("−")
        zout.setFixedWidth(30)
        zout.setToolTip("Zoom out")
        zfit = QPushButton("Fit")
        zfit.setFixedWidth(40)
        zfit.setToolTip("Fit the whole edit")
        tb.addWidget(self.play_all_btn)
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setToolTip("Undo (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QPushButton("Redo")
        self.redo_btn.setToolTip("Redo (Ctrl+Y)")
        self.redo_btn.clicked.connect(self.redo)
        tb.addWidget(self.undo_btn)
        tb.addWidget(self.redo_btn)
        from PySide6.QtGui import QKeySequence, QShortcut
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.redo)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self.boring_btn = QPushButton("✂ Cut boring parts")
        self.boring_btn.setToolTip("Removes the stretches where nothing happens (marked red on the timeline)")
        self.boring_btn.clicked.connect(self._cut_boring)
        self.beat_btn = QPushButton("🎵 Sync to beat")
        self.beat_btn.setToolTip("Moves the cuts and zooms onto the beat of your music")
        self.beat_btn.clicked.connect(self._beat_sync)
        tb.addWidget(self.boring_btn)
        tb.addWidget(self.beat_btn)
        tb.addStretch(1)
        self.timeline_tip = tl_tip       # the timeline's own tooltips explain everything
        for w in (zout, zin, zfit):
            w.setStyleSheet("padding: 4px 2px;")
            tb.addWidget(w)
        mv.addWidget(self.tl_bar)
        self.timeline = EditTimeline(self)
        zin.clicked.connect(lambda: self.timeline.zoom_by(1.5))
        zout.clicked.connect(lambda: self.timeline.zoom_by(1 / 1.5))
        zfit.clicked.connect(self.timeline.fit)
        self.timeline.seek.connect(self._seek_out)
        self.timeline.pieceSelected.connect(lambda i: self.list.setCurrentRow(i) if i != self._current else None)
        self.timeline.changed.connect(self._timeline_changed)
        self.timeline.gifSelected.connect(self._timeline_gif)
        self.timeline.casterEdit.connect(self._caster_reword)
        self.timeline.pieceAction.connect(self._piece_action)
        mv.addWidget(self.timeline)
        self.player.player.positionChanged.connect(self._player_pos)
        self.player.player.playbackStateChanged.connect(lambda *_: self._player_pos(self.player.player.position()))
        self.player.player.mediaStatusChanged.connect(self._media_status)
        self.cam_edit.hookChanged.connect(self._hook_moved)
        self.cam_edit.itemsChanged.connect(self._items_changed)
        self.cam_edit.playhead_out = lambda: self.timeline.playhead
        self.cam_edit.set_items(self.items)
        self.timeline.itemSelected.connect(self._timeline_item)
        split.addWidget(mid)

        # right: export options
        right = QFrame()
        right.setObjectName("panel")
        right.setMinimumWidth(330)
        r = QVBoxLayout(right)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.preset = QComboBox()
        for k, p in PRESETS.items():
            self.preset.addItem(p.label, k)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.layout_box = QComboBox()
        for k, label in (("blur", "Gameplay + blurred background"), ("smart", "Full-screen, follows the action"),
                         ("crop", "Full-screen center crop (FPS)"), ("zoom", "Square zoom + blur"),
                         ("fit", "Whole frame, black bars")):
            self.layout_box.addItem(label, k)
        self.layout_box.setToolTip("How the gameplay fills a 9:16 Short. Place your webcam in the "
                                   "📱 Final look tab next to the preview.")
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(s.short_layout)))
        self.layout_box.currentIndexChanged.connect(lambda *_: self._refresh_cam_edit(frames=False))
        hook_row = QHBoxLayout()
        self.hook = QLineEdit()
        self.hook.textChanged.connect(self._hook_text_changed)
        self.hook.setPlaceholderText("Hook text for the first 3 s")
        dice = QPushButton("🎲")
        dice.setFixedWidth(36)
        dice.setToolTip("Suggest a hook")
        dice.clicked.connect(lambda: self.hook.setText(suggest_hook(self._game(), self.hook_weights())))
        self.ai_btn = QPushButton("🤖")
        self.ai_btn.setFixedWidth(36)
        self.ai_btn.setToolTip("Local AI writes 3 hooks + title + hashtags (click again to cycle hooks)")
        self.ai_btn.clicked.connect(self._ai_hooks)
        hook_row.addWidget(self.hook)
        hook_row.addWidget(dice)
        hook_row.addWidget(self.ai_btn)
        self.watermark = QLineEdit(s.watermark_text)
        self.watermark.setPlaceholderText("@yourchannel")
        music_row = QHBoxLayout()
        self.music = QLineEdit(s.music_file)
        self.music.setPlaceholderText("none")
        pick = QPushButton("…")
        pick.setFixedWidth(30)
        pick.clicked.connect(self._pick_music)
        music_row.addWidget(self.music)
        music_row.addWidget(pick)
        dice_m = QPushButton("🎲")
        dice_m.setFixedWidth(34)
        dice_m.setToolTip("A random track from your music folder (Settings → Music)")
        dice_m.clicked.connect(self._random_music)
        music_row.addWidget(dice_m)
        self.music_vol = QSpinBox()
        self.music_vol.setRange(0, 100)
        self.music_vol.setSuffix(" %")
        self.music_vol.setValue(round(s.music_volume * 100))
        self.mic_vol = QSpinBox()
        self.mic_vol.setRange(0, 300)
        self.mic_vol.setSuffix(" %")
        self.mic_vol.setValue(100)
        self.game_vol = QSpinBox()
        self.game_vol.setRange(0, 300)
        self.game_vol.setSuffix(" %")
        self.game_vol.setValue(100)
        self.captions = QCheckBox("Captions (what you and the caster say)")
        self.captions.setChecked(s.captions)
        self.cap_style = QComboBox()
        from ..captions import CAPTION_STYLES
        for k, label in CAPTION_STYLES.items():
            self.cap_style.addItem(label, k)
        self.cap_style.setCurrentIndex(max(0, self.cap_style.findData(s.caption_style)))
        self.cap_style.setToolTip("Word-by-word captions keep people watching. Needs the captions add-on from "
                                  "setup.bat for your own voice;\nthe caster's lines are always captioned.")
        # "My style" presets
        style_row = QHBoxLayout()
        self.style_box = QComboBox()
        self.style_box.setToolTip("Your saved looks. Automatic edits use the default (or the one set for the game).")
        self.style_box.activated.connect(self._style_chosen)
        st_save = QPushButton("Save…")
        st_save.setToolTip("Save the current look (layout, webcam, text, captions, caster, music, GIFs) as a style")
        st_save.clicked.connect(self._style_save)
        st_menu = QPushButton("⋯")
        st_menu.setFixedWidth(30)
        st_menu.clicked.connect(lambda: self._style_menu(st_menu))
        style_row.addWidget(self.style_box, 1)
        style_row.addWidget(st_save)
        style_row.addWidget(st_menu)
        self.loud = QCheckBox("Normalize loudness (-14 LUFS)")
        self.loud.setChecked(s.loudness_normalize)
        form.addRow("My style", style_row)
        form.addRow("Format", self.preset)
        form.addRow("Layout", self.layout_box)
        form.addRow("Hook", hook_row)
        form.addRow("Watermark", self.watermark)
        form.addRow("Music", music_row)
        self.music_warn = QLabel("")
        self.music_warn.setWordWrap(True)
        self.music_warn.setStyleSheet(f"color:{theme.YELLOW}; font-size:11px;")
        self.music_warn.setVisible(False)
        form.addRow("", self.music_warn)
        form.addRow("Music volume", self.music_vol)
        form.addRow("Game volume", self.game_vol)
        form.addRow("Mic volume", self.mic_vol)
        r.addLayout(form)
        r.addWidget(self.captions)
        r.addWidget(self.cap_style)
        r.addWidget(self.loud)
        self.loop = QCheckBox("Seamless loop (Shorts): end flows into the start")
        self.loop.setChecked(s.loop_shorts)
        self.tiktok = QCheckBox("Also make a TikTok version (own hook, TikTok-safe text)")
        self.tiktok.setChecked(s.tiktok_versions)
        r.addWidget(self.loop)
        r.addWidget(self.tiktok)
        self.caster_btn = QPushButton("🎙  Add AI caster voice")
        self.caster_btn.setToolTip("A hyped esports caster reacts to each big moment (game sound ducks under it)")
        self.caster_btn.clicked.connect(self._caster)
        r.addWidget(self.caster_btn)
        duck_row = QHBoxLayout()
        duck_lbl = QLabel("Game while the caster talks")
        duck_lbl.setObjectName("muted")
        self.caster_duck = QSpinBox()
        self.caster_duck.setRange(0, 100)
        self.caster_duck.setSuffix(" %")
        self.caster_duck.setValue(int(round(getattr(s, "caster_game_volume", 0.7) * 100)))
        self.caster_duck.setToolTip("100 % = the game stays at full volume. Lower = the game dips while he talks.\n"
                                    "Between his lines the game is always at full volume.")
        duck_row.addWidget(duck_lbl, 1)
        duck_row.addWidget(self.caster_duck)
        r.addLayout(duck_row)
        cv_row = QHBoxLayout()
        cv_lbl = QLabel("Caster volume")
        cv_lbl.setObjectName("muted")
        self.caster_vol = QSpinBox()
        self.caster_vol.setRange(0, 200)
        self.caster_vol.setSuffix(" %")
        self.caster_vol.setValue(100)
        self.caster_vol.setToolTip("How loud the caster is. 0 % = muted (his lines stay on the timeline)")
        self.caster_vol.valueChanged.connect(lambda *_: self.timeline.update() if hasattr(self, "timeline") else None)
        cv_row.addWidget(cv_lbl, 1)
        cv_row.addWidget(self.caster_vol)
        r.addLayout(cv_row)

        fxg = QGroupBox("Effects on the selected piece")
        fv = QVBoxLayout(fxg)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Speed"))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 3.0)
        self.speed.setSingleStep(0.25)
        self.speed.setValue(1.0)
        self.speed.setSuffix(" ×")
        self.speed.valueChanged.connect(self._speed_changed)
        srow.addWidget(self.speed)
        srow.addStretch(1)
        fv.addLayout(srow)
        brow = QHBoxLayout()
        zb = QPushButton("＋ Zoom")
        zb.setToolTip("Punch-in zoom at the playhead")
        zb.clicked.connect(lambda: self._add_fx({"type": "zoom", "amount": 0.3}))
        sb = QPushButton("＋ Slow-mo")
        sb.setToolTip("1 s of 0.4× slow motion starting at the playhead (speed ramp)")
        sb.clicked.connect(lambda: self._add_fx({"type": "slowmo", "dur": 1.0, "speed": 0.4}))
        brow.addWidget(zb)
        brow.addWidget(sb)
        fv.addLayout(brow)
        xrow = QHBoxLayout()
        self.sfx_box = QComboBox()
        self.sfx_box.setToolTip("Built-in sounds + anything you drop in your SFX folder")
        self._fill_sfx()
        xb = QPushButton("＋ Sound")
        xb.clicked.connect(lambda: self._add_fx({"type": "sfx", "name": self.sfx_box.currentData()}))
        xrow.addWidget(self.sfx_box, 1)
        xrow.addWidget(xb)
        fv.addLayout(xrow)
        tools = QHBoxLayout()
        for label, tip, fn in (("T+ Text", "Your own text at the playhead (T)", self._add_text),
                               ("▦ Blur", "Blur box to hide chat / names / pop-ups (B)", self._add_blur),
                               ("🔇 Mute", "Silence a moment, with or without a bleep (M)", self._add_mute)):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tools.addWidget(b)
        fv.addLayout(tools)
        bleep = QPushButton("🤬 Bleep swearing")
        bleep.setToolTip("Finds swear words in what you say and bleeps them (YouTube limits ads on swearing).\n"
                         "Needs the captions add-on from setup.bat.")
        bleep.clicked.connect(self._auto_bleep)
        fv.addWidget(bleep)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Transition in"))
        self.trans_box = QComboBox()
        self.trans_box.addItem("Cut (none)", "")
        from ..editor import TRANSITIONS
        for k, (label, _f) in TRANSITIONS.items():
            self.trans_box.addItem(label, k)
        self.trans_box.setToolTip("How this clip starts: a hard cut, or a flash / zoom / whip / glitch / fade")
        self.trans_box.activated.connect(self._set_transition)
        trow.addWidget(self.trans_box, 1)
        fv.addLayout(trow)
        gb = QPushButton("＋ GIF / sticker")
        gb.setToolTip("Put a GIF or sticker on the video at the playhead — your own or from GIPHY")
        gb.clicked.connect(self._add_gif)
        fv.addWidget(gb)
        crow = QHBoxLayout()
        clr_fx = QPushButton("Clear piece effects")
        clr_fx.clicked.connect(self._clear_fx)
        auto_fx = QPushButton("✨ Auto FX (all pieces)")
        auto_fx.setToolTip("Zoom on every kill / marker, boom on multi-kills, slow-mo on the opening moment")
        auto_fx.clicked.connect(self.auto_fx_all)
        crow.addWidget(clr_fx)
        crow.addWidget(auto_fx)
        fv.addLayout(crow)
        self.friend_btn = QPushButton("👥 Friend's angle…")
        self.friend_btn.setToolTip("Find a squad member's clip of the same moment: split-screen or replay")
        self.friend_btn.clicked.connect(self._friend_angle)
        fv.addWidget(self.friend_btn)
        r.addWidget(fxg)
        self.script_lbl = QPushButton("")
        self.script_lbl.setObjectName("icon")
        self.script_lbl.setStyleSheet(f"color:{theme.YELLOW}; text-align:left; font-size:12px;")
        self.script_lbl.setToolTip("Click to drop the voiceover and go back to a normal edit")
        self.script_lbl.clicked.connect(self._drop_voice)
        self.script_lbl.setVisible(False)
        r.addWidget(self.script_lbl)
        self.script: dict | None = None
        r.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        self.render_btn = QPushButton("⬇  Render video")
        self.render_btn.setObjectName("primary")
        self.render_btn.setMinimumHeight(40)
        self.render_btn.clicked.connect(self.render)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setWidget(right)
        right_col = QWidget()           # options scroll; status + Render always stay visible underneath
        rc = QVBoxLayout(right_col)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.addWidget(right_scroll, 1)
        rc.addWidget(self.status)
        rc.addWidget(self.bar)
        self.another_btn = QPushButton("🎲  Try another edit")
        self.another_btn.setToolTip("Same clips, a different take: other moments, another hook, other transitions,\n"
                                    "effects and GIFs. This edit stays in Drafts so you can pick the best one.")
        self.another_btn.clicked.connect(self._another_take)
        rc.addWidget(self.another_btn)
        rrow = QHBoxLayout()
        rrow.addWidget(self.render_btn, 1)
        self.queue_btn = QPushButton("＋ Queue")
        self.queue_btn.setMinimumHeight(40)
        self.queue_btn.setToolTip("Put it in the render queue and keep editing something else.\n"
                                  "Queued videos render one after another — never while you're in a game.")
        self.queue_btn.clicked.connect(self.queue_render)
        rrow.addWidget(self.queue_btn)
        rc.addLayout(rrow)
        right_col.setMinimumWidth(350)
        split.addWidget(right_col)
        split.setSizes([280, 760, 360])
        self._advanced(s.studio_advanced)

        jobs.progress.connect(self._progress)
        jobs.done.connect(self._done)
        jobs.failed.connect(self._failed)
        self.aiReady.connect(self._ai_done)
        self.casterReady.connect(self._caster_done)
        self.lineReady.connect(self._line_done)
        self.deadReady.connect(self._dead_done)
        self._dead_timer = QTimer(self)
        self._dead_timer.setSingleShot(True)
        self._dead_timer.setInterval(900)
        self._dead_timer.timeout.connect(self._scan_dead)
        self._style_refresh(s.default_style)
        self._watch_opts()
        self._shortcuts()
        self.alignReady.connect(self._aligned)
        self.ai_post = None
        self._preset_changed()

    def all_clips(self) -> list:
        return []   # replaced by the main window (library clips incl. squad folders)

    def hook_weights(self) -> dict:
        return {}   # replaced by the main window with what's learned from your channel

    # ----- pieces
    def _game(self) -> str:
        return self.pieces[0]["clip"].game if self.pieces else ""

    def add_clips(self, clips: list[Clip]):
        for c in clips:
            end = c.trim_out or c.duration
            self.pieces.append({"clip": c, "start": c.trim_in, "end": end, "effects": [], "speed": 1.0})
        self._refresh_list(len(self.pieces) - 1)

    def load_plan(self, plan: list[Piece], clips: list[Clip], preset: str = "short", script: dict | None = None):
        by_path = {c.path: c for c in clips}
        self.pieces = [{"clip": by_path[p.path], "start": p.start, "end": p.end, "effects": list(p.effects),
                        "speed": p.speed} for p in plan if p.path in by_path]
        self.preset.setCurrentIndex(self.preset.findData(preset))
        self.set_script(script)
        if not self.hook.text() and not script:
            self.hook.setText(suggest_hook(self._game(), self.hook_weights()))
        self._refresh_list(0)
        self.timeline.fit()

    def set_script(self, script: dict | None):
        """Script mode: a voiceover + timed lines ride along with the pieces."""
        self.script = script
        if script:
            if script.get("caster"):
                self.script_lbl.setText("🎙 AI caster voice on   ✕")
            else:
                self.script_lbl.setText(f"🎙 Script mode: voiceover {fmt_dur(ff.duration(script['voiceover']))}, "
                                        f"{len(script['timed_text'])} lines   ✕")
            if not script.get("caster"):
                self.hook.setText("")
        self.script_lbl.setVisible(bool(script) or bool(self.caster_lines))
        if hasattr(self, "timeline"):
            self.timeline.update()

    def _fill_sfx(self):
        from ..sfx import DESCRIPTIONS, all_sfx
        self.sfx_box.clear()
        try:
            for name in sorted(all_sfx(self.s.sfx_dir)):
                self.sfx_box.addItem(f"{name} — {DESCRIPTIONS.get(name, 'your sound')}", name)
        except Exception as e:
            print("sfx:", e)

    def _cur(self) -> dict | None:
        return self.pieces[self._current] if 0 <= self._current < len(self.pieces) else None

    # drafts, styles, undo / redo, shortcuts, effects, text / blur / mutes, rendering, the AI caster and writer,
    # automatic edits and friend's angle: see ui/studio/

    # ----- thumbnail
    def set_thumb(self, out_t: float):
        self.thumb_t = round(out_t, 2)
        self.timeline.update()
        self._mark()
        self.status.setText(f"🖼 The thumbnail will be made from {fmt_dur(out_t)} into the video")

    # ----- timeline / advanced editing
    def _advanced(self, on: bool):
        self.timeline.setVisible(on)
        self.tl_bar.setVisible(on)
        if self.s.studio_advanced != on:
            self.s.studio_advanced = on
            save_settings(self.s)

    def hook_secs_eff(self) -> float:
        """How long the hook shows: the whole video with a seamless loop (so the seam is invisible)."""
        from .. import timeline_model as tm
        tot = tm.total(self.pieces)
        from ..editor import LOOP_HOOK_MAX
        if self.hook_whole or (self.loop.isChecked() and self._is_short() and not self.script and tot <= LOOP_HOOK_MAX
                               and not getattr(self, "hook_user", False)):
            return max(0.5, tot - self.hook_start)
        return self.hook_secs

    def _hook_text_changed(self, *_):
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "cam_edit"):
            self.cam_edit.set_hook(self.hook.text(), self.hook_pos, self.hook_scale)
        self._mark()

    def _hook_moved(self):
        self.hook_pos = self.cam_edit.canvas.hook_pos
        self.hook_scale = self.cam_edit.canvas.hook_scale
        self._mark()

    def _seek_out(self, out_t: float):
        """Timeline click: show that moment in the preview."""
        from .. import timeline_model as tm
        if not self.pieces:
            return
        i, t = tm.locate(self.pieces, out_t)
        if i < 0:
            return
        if i != self._current:
            self._autoplay_next = False
            self.list.setCurrentRow(i)
        self.player.player.setPosition(int(t * 1000))
        self.timeline.set_playhead(out_t)

    def _media_status(self, st):
        """A clip played right to the end of its file: in Play-whole-edit, carry on with the next one."""
        if self._play_all and st == self.player.player.MediaStatus.EndOfMedia and \
                not getattr(self, "_advancing", False):
            self._advancing = True
            QTimer.singleShot(400, lambda: setattr(self, "_advancing", False))
            if self._current + 1 < len(self.pieces):
                self.list.setCurrentRow(self._current + 1)
                self.player.player.play()
            else:
                self._toggle_play_all(stop=True)

    def _tab_changed(self, i: int):
        self.player.live_frames = i == 1
        if i == 1:
            self._refresh_cam_edit()

    def _live_tick(self, out_t: float, src_t: float, playing: bool):
        """Final-look preview: text / GIFs at their moments, the caster's lines play and the game dips."""
        prev = getattr(self, "_last_out", -1.0)
        self._last_out = out_t
        speaking = [c for c in self.caster_lines if c["t"] <= out_t <= c["t"] + c.get("dur", 1.5)]
        vol = self.caster_volume()
        if playing and vol > 0:
            for c in self.caster_lines:
                if prev < c["t"] <= out_t and out_t - prev < 0.6:
                    self._say(c, vol)
        if not playing:
            self._stop_say()
        self.player.duck(self.caster_duck.value() / 100 if (playing and speaking and vol > 0) else 1.0)
        a = self.hook_start
        self.cam_edit.set_live(playing, src_t, out_t, (a, a + self.hook_secs_eff()),
                               speaking[0].get("text", "") if speaking else "")

    def _player_pos(self, ms: int):
        from .. import timeline_model as tm
        pc = self._cur()
        if not pc:
            return
        lay = tm.layout(self.pieces)
        if self._current < len(lay):
            out_t = lay[self._current][0] + tm.src_to_out(pc, ms / 1000)
            self.timeline.set_playhead(out_t)
            playing = self.player.player.playbackState() == self.player.player.PlaybackState.PlayingState
            self._live_tick(out_t, ms / 1000, playing)
        end = min(pc["end"], (pc["clip"].duration or pc["end"]))
        if self._play_all and ms / 1000 >= end - 0.06 and not getattr(self, "_advancing", False):
            self._advancing = True
            QTimer.singleShot(400, lambda: setattr(self, "_advancing", False))
            if self._current + 1 < len(self.pieces):
                self.list.setCurrentRow(self._current + 1)
                self.player.player.play()
            else:
                self._toggle_play_all(stop=True)

    def _toggle_play_all(self, *_, stop: bool = False):
        if not self.pieces:
            return
        self._play_all = not self._play_all and not stop
        self.player.loop_range = not self._play_all
        self.play_all_btn.setText("⏹  Stop" if self._play_all else "▶  Play whole edit")
        if self._play_all:
            self.mid_tabs.setCurrentIndex(1)       # watch it the way it will look
            if self._current != 0:
                self.list.setCurrentRow(0)
            pc = self.pieces[0]
            self.player.player.setPosition(int(pc["start"] * 1000))
            self.player.player.play()
        else:
            self.player.player.pause()

    def _timeline_changed(self):
        self.cam_edit.set_items(self.items)
        cur = max(0, self._current)
        pc = self._cur()
        pos = self.player.player.position()
        was_playing = self.player.player.playbackState() == self.player.player.PlaybackState.PlayingState
        self._autoplay_next = was_playing
        self._refresh_list(cur)
        if pc and pc["start"] * 1000 <= pos <= pc["end"] * 1000:
            self.player.player.setPosition(pos)
        if pc:   # the clip may have been trimmed on the timeline: update the preview's handles
            self.player.timeline.t_in, self.player.timeline.t_out = pc["start"], pc["end"]
            self.player.timeline.update()
        self._show_fx()
        self.timeline.update()

    def _timeline_gif(self, i: int, gi: int):
        if i != self._current:
            self.list.setCurrentRow(i)
        self.mid_tabs.setCurrentIndex(1)
        self.cam_edit.select_gif(gi)

    def _piece_action(self, what: str, i: int, t: float):
        from .. import timeline_model as tm
        if not (0 <= i < len(self.pieces)):
            return
        if what == "thumb":
            from .. import timeline_model as tm
            self.set_thumb(tm.layout(self.pieces)[i][0] + tm.src_to_out(self.pieces[i], t))
            return
        self._autoplay_next = False
        if what == "split":
            parts = tm.split_piece(self.pieces[i], t)
            if not parts:
                self.status.setText("Can't split that close to the edge of the clip.")
                return
            self.pieces[i:i + 1] = list(parts)
            self._refresh_list(i + 1)
        elif what == "duplicate":
            pc = dict(self.pieces[i])
            pc["effects"] = [dict(e) for e in pc.get("effects", [])]
            self.pieces.insert(i + 1, pc)
            self._refresh_list(i + 1)
        elif what == "move":
            to = int(t)
            pc = self.pieces.pop(i)
            self.pieces.insert(max(0, min(len(self.pieces), to)), pc)
            self._refresh_list(self.pieces.index(pc))
            self.status.setText(f"Moved clip {i + 1} to position {self.pieces.index(pc) + 1}")
        elif what == "remove":
            self.pieces.pop(i)
            self._refresh_list(min(i, len(self.pieces) - 1))
        self.timeline.update()

    def clear(self):
        self.pieces = []
        self.ai_post = None
        self.caster_lines = []
        self.items = []
        if hasattr(self, "cam_edit"):
            self.cam_edit.set_items(self.items)
        self.draft_id = ""
        self.draft_origin = "studio"
        self.thumb_t = None
        self.hook_start, self.hook_secs, self.hook_whole = 0.0, 3.0, False
        self.hook_pos, self.hook_scale = None, 1.0
        self.hook_user = False
        if self._play_all:
            self._toggle_play_all(stop=True)
        self.hook.setText("")
        self.set_script(None)
        self.player.unload()
        self._refresh_list(-1)

    def pick_clips(self):
        clips = list(self.all_clips())
        if not clips:
            QMessageBox.information(self, "Add clips", "You don't have any clips yet — play a game and press "
                                                       "your Save Clip key (PageUp) first.")
            return
        dlg = ClipPicker(clips, self)
        if dlg.exec() and dlg.chosen:
            self.add_clips(dlg.chosen)

    def _refresh_list(self, select=-1):
        if hasattr(self, "list_stack"):
            self.list_stack.setCurrentIndex(1 if self.pieces else 0)
        self.list.blockSignals(True)
        self.list.clear()
        for i, pc in enumerate(self.pieces):
            c = pc["clip"]
            fx = pc.get("effects", [])
            tags = "".join({"zoom": " Z", "slowmo": " S", "sfx": " ♪"}.get(e["type"], "") for e in fx)
            spd = f"  {pc.get('speed', 1.0):g}×" if abs(pc.get("speed", 1.0) - 1) > 1e-3 else ""
            if pc.get("alt"):
                spd += f"  👥 +{pc['alt']['alt_label']}"
            it = QListWidgetItem(f"{i+1}.  {c.game} · {c.display_title}\n      "
                                 f"{fmt_dur(pc['start'])} → {fmt_dur(pc['end'])}   ({pc['end']-pc['start']:.1f} s)"
                                 f"{spd}{'   FX:' + tags if tags else ''}")
            if c.thumb.exists():
                it.setIcon(QPixmap(str(c.thumb)))
            self.list.addItem(it)
        self.list.setIconSize(QPixmap(96, 54).size())
        self.list.blockSignals(False)
        from .. import timeline_model as tm
        tot = tm.total(self.pieces)             # the real length: speed, slow-mo and its ramps, like the render
        warn = "  ⚠ over 60 s — shorter Shorts usually do better" if tot > 60 and self._is_short() else ""
        self.total.setText(f"Total {fmt_dur(tot)}  ·  {len(self.pieces)} pieces{warn}")
        if 0 <= select < len(self.pieces):
            self.list.setCurrentRow(select)
        if hasattr(self, "timeline"):
            self.timeline.update()
        if hasattr(self, "_dead_timer") and self.pieces:
            self._dead_timer.start()
        self._mark()

    def _select(self, row):
        self._current = row
        if 0 <= row < len(self.pieces):
            pc = self.pieces[row]
            c = pc["clip"]
            autoplay = self._play_all or getattr(self, "_autoplay_next", True)
            self._autoplay_next = True
            self.player.load(c.path, c.duration, pc["start"], pc["end"], c.markers,
                             events=[e["t"] for e in c.events], effects=pc.get("effects", []), autoplay=autoplay)
            self.speed.blockSignals(True)
            self.speed.setValue(pc.get("speed", 1.0))
            self.speed.blockSignals(False)
            self.cam_edit.set_gifs(pc.get("effects", []))
            self._sync_transition_box()
            if self.mid_tabs.currentIndex() == 1:
                self._refresh_cam_edit()

    def _range(self, a, b):
        if 0 <= self._current < len(self.pieces):
            self.pieces[self._current]["start"] = a
            self.pieces[self._current]["end"] = b
            self._refresh_list(self._current)

    def _reordered(self, *_):
        # QListWidget moved the rows: rebuild our list in the new order from the item text index
        order = []
        for i in range(self.list.count()):
            num = int(self.list.item(i).text().split(".", 1)[0]) - 1
            order.append(self.pieces[num])
        self.pieces = order
        QTimer.singleShot(0, lambda: self._refresh_list(self.list.currentRow()))

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.pieces):
            self.pieces.pop(r)
            self._refresh_list(min(r, len(self.pieces) - 1))

    def _dup(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.pieces):
            pc = dict(self.pieces[r])
            pc["effects"] = [dict(e) for e in pc.get("effects", [])]
            self.pieces.insert(r + 1, pc)
            self._refresh_list(r + 1)

    def _random_music(self):
        import random
        d = Path(self.s.music_dir) if self.s.music_dir else None
        files = [f for f in d.rglob("*") if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")] \
            if d and d.exists() else []
        if not files:
            QMessageBox.information(self, "Music", "Set your music folder first: Settings → Music (there's a link "
                                    "to free music you're allowed to use on YouTube).")
            return
        cur = self.music.text()
        pick = random.choice([f for f in files if str(f) != cur] or files)
        self.music.setText(str(pick))
        self.status.setText(f"🎵 {pick.stem} — press 🎵 Sync to beat to put the cuts on the beat")

    def _pick_music(self):
        f, _ = QFileDialog.getOpenFileName(self, "Background music", self.s.music_dir or "",
                                           "Audio (*.mp3 *.wav *.m4a *.ogg *.flac)")
        if f:
            self.music.setText(f)

    def _is_short(self):
        return self.preset.currentData() == "short"

    def _preset_changed(self):
        self._refresh_list(self.list.currentRow())
        self._refresh_cam_edit(frames=False)

    def _refresh_cam_edit(self, frames: bool = True):
        if not hasattr(self, "cam_edit"):
            return
        self.cam_edit.set_format(PRESETS[self.preset.currentData()].vertical, self.layout_box.currentData())
        self.cam_edit.set_hook(self.hook.text(), self.hook_pos, self.hook_scale)
        if frames and 0 <= self._current < len(self.pieces):
            c = self.pieces[self._current]["clip"]
            if c.has_cam < 0:
                from ..library import _video_tracks
                c.has_cam = int(_video_tracks(c.path) >= 2)
            pc = self.pieces[self._current]
            t = self.player.position
            if not (pc["start"] <= t <= pc["end"]):
                t = min(pc["end"], pc["start"] + 1)
            self.cam_edit.load_piece(c.path, t, bool(c.has_cam > 0))


# ====================================================================== exports
class ExportsPage(QWidget):
    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 14)
        head = QHBoxLayout()
        t = QLabel("READY TO POST")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        folder = QPushButton("📁  Open exports folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib.exports_dir))))
        head.addWidget(folder)
        lay.addLayout(head)
        tip = QLabel("Each export has a ready-made title, description and hashtags. Copy them, then hit upload — "
                     "drag the video file into the upload page.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.v = QVBoxLayout(self.inner)
        self.v.setSpacing(10)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)

    def refresh(self):
        while self.v.count():
            it = self.v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        files = self.lib.exports()
        if not files:
            e = QLabel("Nothing exported yet. Make one with ⚡ Auto Short or the Studio.")
            e.setObjectName("muted")
            self.v.addWidget(e)
        for f in files[:60]:
            self.v.addWidget(self._row(f))
        self.v.addStretch(1)

    def _row(self, f: Path) -> QFrame:
        row = QFrame()
        row.setObjectName("card")
        h = QHBoxLayout(row)
        info = QVBoxLayout()
        name = QLabel(f.stem)
        name.setObjectName("cardtitle")
        size = f.stat().st_size / 1e6
        meta = QLabel(f"{datetime.fromtimestamp(f.stat().st_mtime).strftime('%b %d %H:%M')}  ·  {size:.1f} MB")
        meta.setObjectName("muted")
        info.addWidget(name)
        info.addWidget(meta)
        h.addLayout(info, 1)
        txt = Path(str(f) + ".txt")
        from ..library import is_vertical_export
        vertical = is_vertical_export(f)

        def btn(label, fn, primary=False):
            b = QPushButton(label)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            h.addWidget(b)
        btn("▶ Play", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(f))))
        btn("📁", lambda: show_in_folder(str(f)))
        if txt.exists():
            btn("📋 Copy title & tags", lambda: QGuiApplication.clipboard().setText(txt.read_text(encoding="utf-8")))
        btn("YouTube ↑", lambda: QDesktopServices.openUrl(QUrl("https://www.youtube.com/upload")), True)
        if vertical:
            btn("TikTok ↑", lambda: QDesktopServices.openUrl(QUrl("https://www.tiktok.com/tiktokstudio/upload")), True)
        return row


# ====================================================================== settings
class SettingsPage(QWidget):
    applied = Signal()
    micLevel = Signal(float)
    micDone = Signal(float, str)
    camDone = Signal(str, str, str)    # picture path, message, args

    def __init__(self, s: Settings):
        super().__init__()
        self.s = s
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 10, 18, 14)
        t = QLabel("SETTINGS")
        t.setObjectName("h1")
        outer.addWidget(t)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        cols = QHBoxLayout(inner)
        left, right = QVBoxLayout(), QVBoxLayout()
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # look
        g = QGroupBox("Look")
        f = QFormLayout(g)
        self.skin = QComboBox()
        for key, sk in theme.SKINS.items():
            self.skin.addItem(sk["label"], key)
        self.skin.setCurrentIndex(max(0, self.skin.findData(s.skin)))
        self.skin.setToolTip("Legacy is the original look. The others are inspired by the feel of those games.")
        self.skin.currentIndexChanged.connect(self._skin_preview)
        self.swatch = QLabel()
        self.swatch.setMinimumHeight(26)
        self.skin_note = QLabel("")
        self.skin_note.setObjectName("muted")
        self.skin_note.setWordWrap(True)
        self.skin_restart = QPushButton("⟳  Restart ClipForge to finish switching")
        self.skin_restart.setVisible(False)
        self.skin_restart.clicked.connect(self._skin_restart)
        f.addRow("Skin", self.skin)
        f.addRow("", self.swatch)
        f.addRow("", self.skin_note)
        f.addRow("", self.skin_restart)
        self._skin_swatch(s.skin)
        left.addWidget(g)

        # capture
        g = QGroupBox("Capture")
        f = QFormLayout(g)
        self.buffer = QSpinBox(); self.buffer.setRange(15, 1200); self.buffer.setSuffix(" s"); self.buffer.setValue(s.buffer_seconds)
        self.clip_len = QSpinBox(); self.clip_len.setRange(5, 1200); self.clip_len.setSuffix(" s"); self.clip_len.setValue(s.clip_seconds)
        self.quality = QComboBox(); self.quality.addItems(list(Settings.QUALITY_TABLE)); self.quality.setCurrentText(s.quality)
        self.res = QComboBox(); self.res.addItems(["Native", "2160p", "1440p", "1080p", "720p"]); self.res.setCurrentText(s.resolution)
        self.fps = QComboBox(); self.fps.addItems(["30", "60", "120", "144"]); self.fps.setCurrentText(str(s.fps))
        self.codec = QComboBox(); self.codec.addItems(["h264", "hevc", "av1"]); self.codec.setCurrentText(s.codec)
        self.codec.setToolTip("h264 plays everywhere. hevc/av1 = same quality at ~half the file size (RTX 40/50).")
        self.encoder = QComboBox(); self.encoder.addItems(["auto", "nvenc", "amf", "qsv", "x264"]); self.encoder.setCurrentText(s.encoder)
        self.monitor = QSpinBox(); self.monitor.setRange(0, 5); self.monitor.setValue(s.monitor)
        self.method = QComboBox()
        for k, label in (("auto", "Auto — picks what's smooth per game (recommended)"),
                         ("window", "Game window (Windows Graphics Capture)"),
                         ("desktop", "Whole screen (Desktop Duplication)"), ("gdi", "Compatibility (slow)")):
            self.method.addItem(label, k)
        self.method.setCurrentIndex(max(0, self.method.findData(s.capture_method)))
        self.method.setToolTip("Game window capture records only the game — alt-tabbing won't record your desktop.")
        self.follow = QCheckBox("Keep recording the game when I alt-tab (never record my desktop/browser)")
        self.follow.setChecked(s.follow_game)
        self.follow.setToolTip("On: only the game window is recorded, even while you're tabbed out.\n"
                               "Off: the whole screen is recorded, so alt-tabbing shows what you tabbed to.")
        self.cursor = QCheckBox("Show mouse cursor"); self.cursor.setChecked(s.capture_cursor)
        self.only_game = QCheckBox("Only buffer while a game is focused (saves power)"); self.only_game.setChecked(s.only_while_gaming)
        f.addRow("Replay buffer", self.buffer)
        f.addRow("Save-clip length", self.clip_len)
        f.addRow("Quality", self.quality)
        f.addRow("Resolution", self.res)
        f.addRow("Frame rate", self.fps)
        f.addRow("Codec", self.codec)
        f.addRow("Encoder", self.encoder)
        f.addRow(self.follow)
        f.addRow("Capture method", self.method)
        f.addRow("Monitor #", self.monitor)
        f.addRow(self.cursor)
        f.addRow(self.only_game)
        left.addWidget(g)

        g = QGroupBox("Audio && webcam")
        f = QFormLayout(g)
        self.desk = QCheckBox("Record game / desktop sound (track 1)"); self.desk.setChecked(s.desktop_audio)
        self.mic = QCheckBox("Record microphone on its own track (track 2)"); self.mic.setChecked(s.mic_audio)
        self.mic_dev = QComboBox()
        self.mic_dev.addItem("Default microphone", "")
        from ..audio import list_microphones
        for name in list_microphones():
            self.mic_dev.addItem(name, name)
        self.mic_dev.setCurrentIndex(max(0, self.mic_dev.findData(s.mic_device)))
        self.offset = QSpinBox(); self.offset.setRange(-1000, 1000); self.offset.setSuffix(" ms"); self.offset.setValue(s.audio_offset_ms)
        self.offset.setToolTip("If sound is early/late compared to the picture, nudge it here")
        f.addRow(self.desk)
        f.addRow(self.mic)
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.mic_dev, 1)
        self.mic_test = QPushButton("🎤 Test")
        self.mic_test.setToolTip("Talk for 4 seconds — shows whether ClipForge can hear this mic")
        self.mic_test.clicked.connect(self._test_mic)
        mic_row.addWidget(self.mic_test)
        f.addRow("Mic device", mic_row)
        self.mic_meter = QProgressBar()
        self.mic_meter.setRange(0, 100)
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(8)
        self.mic_meter.setVisible(False)
        self.mic_result = QLabel("")
        self.mic_result.setWordWrap(True)
        self.mic_result.setVisible(False)
        f.addRow("", self.mic_meter)
        f.addRow("", self.mic_result)
        f.addRow("Audio sync offset", self.offset)
        self.cam = QComboBox()
        self.cam.addItem("No facecam", "")
        for name in ff.list_webcams():
            self.cam.addItem(name, name)
        if s.webcam and self.cam.findData(s.webcam) < 0:
            self.cam.addItem(s.webcam, s.webcam)
        self.cam.setCurrentIndex(max(0, self.cam.findData(s.webcam)))
        self.cam.setToolTip("Records your webcam as a separate track so Shorts can use facecam layouts")
        self.cam_res = QComboBox()
        self.cam_res.addItems(["1920x1080", "1280x720", "640x480"])
        self.cam_res.setCurrentText(s.webcam_res)
        cam_row = QHBoxLayout()
        cam_row.addWidget(self.cam, 1)
        self.cam_test = QPushButton("🎥 Test")
        self.cam_test.setToolTip("Opens the camera for a moment and shows a picture from it")
        self.cam_test.clicked.connect(self._test_cam)
        cam_row.addWidget(self.cam_test)
        self.use_cam = QCheckBox("Record my webcam")
        self.use_cam.setToolTip("Off = ClipForge never opens your camera (its light stays off). Your camera choice\n"
                                "below is remembered for when you switch it back on.")
        self.use_cam.setChecked(getattr(s, "use_webcam", True))
        self.use_cam.toggled.connect(lambda on: (self.cam.setEnabled(on), self.cam_res.setEnabled(on),
                                                 self.cam_test.setEnabled(on)))
        f.addRow(self.use_cam)
        f.addRow("Webcam (facecam)", cam_row)
        f.addRow("Webcam size", self.cam_res)
        self.cam_pic = QLabel("")
        self.cam_pic.setVisible(False)
        self.cam_result = QLabel("")
        self.cam_result.setWordWrap(True)
        self.cam_result.setVisible(False)
        f.addRow("", self.cam_pic)
        f.addRow("", self.cam_result)
        left.addWidget(g)

        g = QGroupBox("Edits && effects")
        f = QFormLayout(g)
        self.auto_fx = QCheckBox("Auto Short adds zooms, slow-mo and sounds on kills / markers")
        self.auto_fx.setChecked(s.auto_fx)
        row3 = QHBoxLayout()
        self.sfx_dir = QLineEdit(s.sfx_dir)
        self.sfx_dir.setPlaceholderText("folder with your own .wav/.mp3 sound effects")
        b3 = QPushButton("…"); b3.setFixedWidth(30)
        b3.clicked.connect(lambda: self._pick_dir(self.sfx_dir))
        row3.addWidget(self.sfx_dir); row3.addWidget(b3)
        f.addRow(self.auto_fx)
        self.auto_gifs = QCheckBox("Auto edits add small GIF stickers on big moments")
        self.auto_gifs.setChecked(s.auto_gifs)
        self.auto_gifs.setToolTip("Uses GIFs in your GIF folder named like 'lets go', 'lol', 'wow', 'gg' — or GIPHY\n"
                                  "stickers if you've saved a GIPHY key (Studio → ＋ GIF / sticker)")
        self.auto_review = QCheckBox("Check Auto Shorts in the Studio before they render")
        self.auto_review.setChecked(s.auto_review)
        self.montage_auto = QCheckBox("Create Montage adds effects (and GIFs) for me")
        self.montage_auto.setChecked(s.montage_auto_edit)
        f.addRow(self.auto_gifs)
        f.addRow(self.auto_review)
        f.addRow(self.montage_auto)
        self.cut_boring = QCheckBox("Create Montage cuts out dead air")
        self.cut_boring.setChecked(s.auto_cut_boring)
        self.mic_moments = QCheckBox("Laughing or yelling on my mic counts as a highlight")
        self.mic_moments.setToolTip("Auto Short and session drafts also pick the moments you reacted to —\n"
                                    "with a second or two before it, so the video shows what was so funny.")
        self.mic_moments.setChecked(getattr(s, "mic_moments", True))
        self.no_repeats = QCheckBox("Never reuse a moment that's already in another video")
        self.no_repeats.setChecked(s.no_repeats)
        inbox_row = QHBoxLayout()
        self.inbox = QCheckBox("When I stop playing, make my session's best")
        self.inbox.setToolTip("2 minutes after the game closes, ClipForge edits your session's best moments into\n"
                              "Shorts in the background. They wait as drafts — nothing renders or gets posted.")
        self.inbox.setChecked(s.session_inbox)
        self.inbox_n = QSpinBox()
        self.inbox_n.setRange(1, 6)
        self.inbox_n.setValue(s.session_inbox_count)
        inbox_row.addWidget(self.inbox)
        inbox_row.addWidget(self.inbox_n)
        inbox_row.addWidget(QLabel("Shorts, ready as drafts"))
        inbox_row.addStretch(1)
        self.auto_trans = QCheckBox("Auto edits add transitions between clips")
        self.auto_trans.setChecked(s.auto_transitions)
        f.addRow(self.auto_trans)
        self.trans_style = QComboBox()
        for key, label in (("punchy", "Punchy — flash, zoom, whip, glitch"), ("smooth", "Smooth — crossfades"),
                           ("mix", "Mix of both")):
            self.trans_style.addItem(label, key)
        self.trans_style.setCurrentIndex(max(0, self.trans_style.findData(getattr(s, "transition_style", "punchy"))))
        self.trans_style.setEnabled(s.auto_transitions)
        self.auto_trans.toggled.connect(self.trans_style.setEnabled)
        f.addRow("Transition style", self.trans_style)
        f.addRow(self.cut_boring)
        f.addRow(self.mic_moments)
        f.addRow(self.no_repeats)
        f.addRow(inbox_row)
        f.addRow("My sound effects", row3)
        left.addWidget(g)
        from .extra import DetectorsBox, UploadSettingsBox
        self.detectors = DetectorsBox()
        left.addWidget(self.detectors)
        left.addStretch(1)

        g = QGroupBox("Hotkeys (work inside games)")
        f = QFormLayout(g)
        self.hk_save = HotkeyEdit(s.hotkeys.save_clip)
        self.hk_mark = HotkeyEdit(s.hotkeys.marker)
        self.hk_rec = HotkeyEdit(s.hotkeys.toggle_record)
        self.hk_long = HotkeyEdit(s.hotkeys.save_long)
        f.addRow("Save clip", self.hk_save)
        f.addRow("Marker / instant highlight", self.hk_mark)
        f.addRow("Start/stop recording", self.hk_rec)
        f.addRow("Save whole buffer", self.hk_long)
        self.quick_trim = QCheckBox("Quick trim: Ctrl+→ / Ctrl+← right after saving")
        self.quick_trim.setChecked(s.quick_trim)
        f.addRow(self.quick_trim)
        right.addWidget(g)

        g = QGroupBox("Highlights && auto")
        f = QFormLayout(g)
        self.pre = QSpinBox(); self.pre.setRange(2, 60); self.pre.setSuffix(" s before"); self.pre.setValue(s.marker_pre)
        self.post = QSpinBox(); self.post.setRange(0, 30); self.post.setSuffix(" s after"); self.post.setValue(s.marker_post)
        self.auto = QCheckBox("Auto-clip hype moments (yelling/laughing, big audio spikes)"); self.auto.setChecked(s.auto_clip)
        self.target = QSpinBox(); self.target.setRange(10, 180); self.target.setSuffix(" s"); self.target.setValue(s.short_target_seconds)
        self.sound = QCheckBox("Play a sound when a clip is saved"); self.sound.setChecked(s.sound_on_save)
        f.addRow("Marker highlight", self.pre)
        f.addRow("", self.post)
        f.addRow(self.auto)
        f.addRow("Auto Short length", self.target)
        f.addRow(self.sound)
        right.addWidget(g)

        g = QGroupBox("Library")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.lib_dir = QLineEdit(s.library_dir)
        b = QPushButton("…"); b.setFixedWidth(30)
        b.clicked.connect(lambda: self._pick_dir(self.lib_dir))
        row.addWidget(self.lib_dir); row.addWidget(b)
        f.addRow("Save clips to", row)
        row2 = QHBoxLayout()
        self.extra = QLineEdit("; ".join(s.extra_folders))
        self.extra.setPlaceholderText("e.g. your SteelSeries Moments folder")
        b2 = QPushButton("+"); b2.setFixedWidth(30)
        b2.clicked.connect(self._add_extra)
        row2.addWidget(self.extra); row2.addWidget(b2)
        f.addRow("Also show clips from", row2)
        self.minimized = QCheckBox("Start minimized to tray"); self.minimized.setChecked(s.start_minimized)
        self.autostart = QCheckBox("Start with Windows (as admin if ClipForge is running as admin now)")
        self.autostart.setChecked(self._autostart_enabled())
        f.addRow(self.minimized)
        f.addRow(self.autostart)
        right.addWidget(g)
        self.uploads = UploadSettingsBox(s)
        right.addWidget(self.uploads)
        from .v3 import V3SettingsBox, V4SettingsBox
        self.v3 = V3SettingsBox(s)
        right.addWidget(self.v3)
        self.v4 = V4SettingsBox(s, lambda: self.phone_url())
        left.insertWidget(0, self.v4)
        from .maintenance import MaintenanceBox
        self.lib = None
        self.clips_fn = lambda: []
        self.maint = MaintenanceBox(s, lambda: self.lib, lambda: self.clips_fn(),
                                    lambda **kw: getattr(self, "restart_app", lambda **k: None)(**kw),
                                    lambda: self.phone_url())
        right.addWidget(self.maint)
        right.addStretch(1)
        self._make_tabs(scroll, outer)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.msg = QLabel("")
        self.msg.setObjectName("muted")
        bottom.addWidget(self.msg)
        apply = QPushButton("Save settings")
        apply.setObjectName("primary")
        apply.clicked.connect(self.apply)
        bottom.addWidget(apply)
        outer.addLayout(bottom)


    def _test_mic(self):
        import threading
        from ..audio import test_microphone
        name = self.mic_dev.currentData() or ""
        self.mic_test.setEnabled(False)
        self.mic_test.setText("Talk now…")
        self.mic_meter.setVisible(True)
        self.mic_meter.setValue(0)
        self.mic_result.setVisible(False)

        if not getattr(self, "_mic_wired", False):
            self._mic_wired = True
            self.micLevel.connect(lambda v: self.mic_meter.setValue(int(min(1.0, v * 3) * 100)))
            self.micDone.connect(self._mic_tested)

        def work():
            peak, dev = test_microphone(name, 4.0, self.micLevel.emit)
            self.micDone.emit(peak, dev)
        threading.Thread(target=work, daemon=True).start()

    def _test_cam(self):
        import tempfile
        import threading
        name = self.cam.currentData() or ""
        if not name:
            self.cam_result.setText("Pick your camera in the list first.")
            self.cam_result.setVisible(True)
            return
        eng = getattr(self, "engine", None)
        busy = eng is not None and any(sp.kind == "cam" and sp.proc and sp.proc.poll() is None for sp in eng._sides)
        if busy and name == self.s.webcam:
            self.cam_result.setText("✔ ClipForge is recording this camera right now — new clips include it. "
                                    "Open a new clip to see it in the corner of the player.")
            self.cam_result.setVisible(True)
            return
        if not getattr(self, "_cam_wired", False):
            self._cam_wired = True
            self.camDone.connect(self._cam_tested)
        self.cam_test.setEnabled(False)
        self.cam_test.setText("Opening…")
        res = self.cam_res.currentText()

        def work():
            from ..capture import find_webcam_mode, webcam_hint
            args, err = find_webcam_mode(name, res, self.s.webcam_fps)
            if not args:
                self.camDone.emit("", f"❌ Couldn't open '{name}': {webcam_hint(err)}\nFFmpeg said: {err}", "")
                return
            pic = str(Path(tempfile.gettempdir()) / "clipforge_camtest.jpg")
            ff.run([ff.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y"] + args +
                   ["-frames:v", "15", "-update", "1", "-vf", "scale=320:-2", pic], timeout=20)
            mode = " ".join(a for a in args if not a.startswith("video=") and a not in ("-f", "dshow", "-rtbufsize",
                                                                                         "256M", "-i"))
            self.camDone.emit(pic, f"✔ Camera works ({mode or 'its default mode'}). Press Save — new clips will "
                                   "carry your webcam, and you place it in Studio → 📱 Final look.", "")
        threading.Thread(target=work, daemon=True).start()

    def _cam_tested(self, pic: str, msg: str, _args: str):
        self.cam_test.setEnabled(True)
        self.cam_test.setText("🎥 Test")
        self.cam_result.setText(msg)
        self.cam_result.setVisible(True)
        pm = QPixmap(pic) if pic else QPixmap()
        self.cam_pic.setVisible(not pm.isNull())
        if not pm.isNull():
            self.cam_pic.setPixmap(pm)

    def _mic_tested(self, peak: float, dev: str):
        self.mic_test.setEnabled(True)
        self.mic_test.setText("🎤 Test")
        self.mic_result.setVisible(True)
        if peak < 0:
            self.mic_result.setText(f"❌ Couldn't open the microphone: {dev}")
        elif peak == 0.0:
            self.mic_result.setText(f"❌ '{dev}' sends complete silence. Windows is probably blocking it: Settings → "
                                    "Privacy & security → Microphone → turn on 'Let desktop apps access your "
                                    "microphone'. With SteelSeries Sonar, also check that your real mic is selected "
                                    "as Sonar's input.")
        elif peak < 0.02:
            self.mic_result.setText(f"⚠ '{dev}' works but is very quiet (peak {peak:.3f}). If you were talking, "
                                    "raise its level in Windows sound settings or in Sonar. Exports boost your voice, "
                                    "but a louder source sounds better.")
        else:
            self.mic_result.setText(f"✔ Hearing you on '{dev}' (peak {peak:.2f}). Press Save to use it.")
    def _pick_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if d:
            edit.setText(d)

    def _add_extra(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with existing clips")
        if d:
            cur = [x.strip() for x in self.extra.text().split(";") if x.strip()]
            self.extra.setText("; ".join(cur + [d]))

    def phone_url(self) -> str:
        return ""   # replaced by the main window

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _autostart_enabled(self) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            r = subprocess.run(["schtasks", "/Query", "/TN", "ClipForge"], capture_output=True,
                               creationflags=0x08000000, timeout=15)
            if r.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as k:
                winreg.QueryValueEx(k, "ClipForge")
                return True
        except OSError:
            return False

    def _set_autostart(self, on: bool):
        if not IS_WINDOWS:
            return
        import sys
        import winreg
        from ..capture import is_admin
        from ..paths import app_root, is_frozen
        exe = sys.executable
        root = app_root()
        if not is_frozen() and exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        nowin = 0x08000000
        # remove both kinds first, then add the right one
        try:
            subprocess.run(["schtasks", "/Delete", "/TN", "ClipForge", "/F"], capture_output=True, creationflags=nowin,
                           timeout=15)
        except subprocess.TimeoutExpired:
            pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, "ClipForge")
            except OSError:
                pass
            if not on:
                return
            if is_frozen():
                cmd = f'"{exe}" --minimized'
            else:
                launcher = root / "ClipForge.vbs"
                cmd = (f'wscript.exe "{launcher}" --minimized' if launcher.exists()
                       else f'"{exe}" -m clipforge --minimized')
            if is_admin():
                # Logon task with highest privileges → elevated without a UAC prompt → GPU priority
                try:
                    r = subprocess.run(["schtasks", "/Create", "/TN", "ClipForge", "/SC", "ONLOGON", "/RL",
                                        "HIGHEST", "/TR", cmd, "/F"], capture_output=True, creationflags=nowin,
                                       timeout=15)
                    if r.returncode == 0:
                        return
                except subprocess.TimeoutExpired:
                    pass                    # fall back to the Run key below
            winreg.SetValueEx(k, "ClipForge", 0, winreg.REG_SZ, cmd)

    TABS = [("🎥  Recording", ["Capture", "Audio & webcam", "Hotkeys", "Highlights & auto", "Library",
                               "Per-game recording"]),
            ("🎬  Editing", ["Edits & effects", "Shorts style", "Kill detection", "AI writer", "Music"]),
            ("🤖  Automation", ["Automatic content", "Weekly best-of", "Learning", "Squad"]),
            ("📤  Posting", ["Uploads", "Approve from your phone", "Phone notifications"]),
            ("🧰  Upkeep", ["Storage", "Updates", "Backup & restore"]),
            ("🎨  Look", ["Look"])]

    def _make_tabs(self, scroll, outer):
        """The settings were one long page; each section now lives on a tab (same boxes, same Save button)."""
        from PySide6.QtWidgets import QTabWidget
        inner = scroll.widget()
        boxes = inner.findChildren(QGroupBox)
        tabs = QTabWidget()
        placed = set()
        for label, keys in self.TABS:
            page = QWidget()
            cols = QHBoxLayout(page)
            lc, rc = QVBoxLayout(), QVBoxLayout()
            cols.addLayout(lc, 1)
            cols.addLayout(rc, 1)
            mine = []
            for key in keys:
                for b in boxes:
                    if id(b) not in placed and key.lower() in b.title().replace("&&", "&").lower():
                        mine.append(b)
                        placed.add(id(b))
            for i, b in enumerate(mine):
                (lc if i % 2 == 0 else rc).addWidget(b)
            if label.endswith("Upkeep"):
                wz = next((w for w in self.maint.findChildren(QPushButton) if "wizard" in w.text().lower()), None)
                if wz:
                    rc.addWidget(wz)
            lc.addStretch(1)
            rc.addStretch(1)
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sa.setWidget(page)
            tabs.addTab(sa, label)
        left_over = [b for b in boxes if id(b) not in placed and b.title()]
        if left_over:   # anything new that isn't sorted yet still shows up
            page = QWidget()
            col = QVBoxLayout(page)
            for b in left_over:
                col.addWidget(b)
            col.addStretch(1)
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(page)
            tabs.addTab(sa, "More")
        i = outer.indexOf(scroll)
        outer.removeWidget(scroll)
        scroll.hide()
        outer.insertWidget(i, tabs, 1)
        self.tabs = tabs

    def _skin_swatch(self, key: str):
        sk = theme.SKINS.get(key, theme.SKINS["legacy"])
        cells = "".join(f"<td style='background:{sk[c]};width:34px;height:20px'></td>"
                        for c in ("BG", "PANEL", "CARD", "ACCENT", "TEXT", "GREEN", "RED", "YELLOW"))
        font = sk["FONT"].split(",")[0].strip("'")
        self.swatch.setText(f"<table cellspacing=2><tr>{cells}</tr></table>"
                            f"<span style='font-family:{sk['FONT']};color:{sk['ACCENT']}'>"
                            f"{sk['LOGO']} ClipForge — {font}</span>")

    def _skin_preview(self):
        key = self.skin.currentData() or "legacy"
        self._skin_swatch(key)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(theme.apply(key))   # most of the app changes right away
        for w in QApplication.instance().topLevelWidgets():
            w.update()
        changed = key != self.s.skin
        self.s.skin = key
        save_settings(self.s)
        self.skin_note.setText("Applied. A few details (logo, some labels) switch after a restart." if changed else "")
        self.skin_restart.setVisible(changed)

    def _skin_restart(self):
        fn = getattr(self, "restart_app", None)
        if fn:
            fn()

    def apply(self):
        s = self.s
        s.buffer_seconds = self.buffer.value()
        s.clip_seconds = min(self.clip_len.value(), s.buffer_seconds)
        s.quality = self.quality.currentText()
        s.resolution = self.res.currentText()
        s.fps = int(self.fps.currentText())
        s.codec = self.codec.currentText()
        s.encoder = self.encoder.currentText()
        s.monitor = self.monitor.value()
        if self.method.currentData() != s.capture_method:
            s.method_by_game = {}          # start fresh when you change it by hand
        s.capture_method = self.method.currentData()
        s.follow_game = self.follow.isChecked()
        s.capture_cursor = self.cursor.isChecked()
        s.only_while_gaming = self.only_game.isChecked()
        s.desktop_audio = self.desk.isChecked()
        s.mic_audio = self.mic.isChecked()
        s.mic_device = self.mic_dev.currentData() or ""
        s.audio_offset_ms = self.offset.value()
        s.hotkeys.save_clip = self.hk_save.text()
        s.hotkeys.marker = self.hk_mark.text()
        s.hotkeys.toggle_record = self.hk_rec.text()
        s.hotkeys.save_long = self.hk_long.text()
        s.marker_pre = self.pre.value()
        s.marker_post = self.post.value()
        s.auto_clip = self.auto.isChecked()
        s.short_target_seconds = self.target.value()
        s.sound_on_save = self.sound.isChecked()
        s.library_dir = self.lib_dir.text().strip() or s.library_dir
        s.extra_folders = [x.strip() for x in self.extra.text().split(";") if x.strip()]
        s.start_minimized = self.minimized.isChecked()
        s.webcam = self.cam.currentData() or ""
        s.use_webcam = self.use_cam.isChecked()
        s.webcam_res = self.cam_res.currentText()
        s.auto_fx = self.auto_fx.isChecked()
        s.auto_gifs = self.auto_gifs.isChecked()
        s.auto_review = self.auto_review.isChecked()
        s.montage_auto_edit = self.montage_auto.isChecked()
        s.auto_cut_boring = self.cut_boring.isChecked()
        s.auto_transitions = self.auto_trans.isChecked()
        s.transition_style = self.trans_style.currentData()
        s.quick_trim = self.quick_trim.isChecked()
        s.no_repeats = self.no_repeats.isChecked()
        s.session_inbox = self.inbox.isChecked()
        s.mic_moments = self.mic_moments.isChecked()
        s.session_inbox_count = self.inbox_n.value()
        new_skin = self.skin.currentData() or "legacy"
        if new_skin != s.skin:
            s.skin = new_skin
        s.sfx_dir = self.sfx_dir.text().strip()
        self.maint.apply()
        self.uploads.apply()
        self.detectors.apply()
        self.v3.apply()
        self.v4.apply()
        try:
            self._set_autostart(self.autostart.isChecked())
        except OSError:
            pass
        save_settings(s)
        self.msg.setText("Saved ✔")
        QTimer.singleShot(2500, lambda: self.msg.setText(""))
        self.applied.emit()
