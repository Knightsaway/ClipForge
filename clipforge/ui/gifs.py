"""Studio → ＋ GIF / sticker: pick one of your own GIFs or search GIPHY, then place it on the video."""
from __future__ import annotations

import json
import shutil
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import ff
from ..config import app_data_dir, save_settings
from ..editor import GIF_EXT

GIPHY_SIGNUP = "https://developers.giphy.com/dashboard/?create=true"


def gif_dir(s) -> Path:
    d = Path(s.gif_dir) if s.gif_dir else app_data_dir() / "gifs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def gif_ratio(path: str) -> float:
    """Height / width of a GIF or sticker (so its box keeps the right shape)."""
    for st in ff.probe(path).get("streams", []):
        if st.get("codec_type") == "video" and st.get("width"):
            return round(st["height"] / st["width"], 4)
    return 1.0


def _get(url: str, timeout=15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class GifPicker(QDialog):
    """Returns the chosen file in .chosen and how long to show it in .seconds."""
    _results = Signal(object)
    _thumb = Signal(int, bytes)
    _downloaded = Signal(str, str)

    def __init__(self, s, parent=None):
        super().__init__(parent)
        self.s = s
        self.chosen = ""
        self.setWindowTitle("Add a GIF or sticker")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        # --- my GIFs
        mine = QWidget()
        mv = QVBoxLayout(mine)
        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter your GIFs by name…")
        self.filter.textChanged.connect(self._fill_mine)
        browse = QPushButton("📂 Add from file…")
        browse.setToolTip("Any .gif, .webp, .png, or a .webm/.mp4 sticker — it's copied into your GIF folder")
        browse.clicked.connect(self._browse)
        folder = QPushButton("📁 Open folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(gif_dir(self.s)))))
        for w in (self.filter, browse, folder):
            row.addWidget(w)
        mv.addLayout(row)
        self.mine = self._grid()
        self.mine.itemDoubleClicked.connect(lambda it: self._pick(it.data(Qt.UserRole)))
        mv.addWidget(self.mine, 1)
        tip = QLabel("Double-click to add. Drop GIFs into your GIF folder and they show up here. "
                     "GIFs you add from GIPHY are kept here too.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        mv.addWidget(tip)
        self.tabs.addTab(mine, "⭐ My GIFs")

        # --- GIPHY
        gp = QWidget()
        gv = QVBoxLayout(gp)
        self.key_row = QWidget()
        kr = QHBoxLayout(self.key_row)
        kr.setContentsMargins(0, 0, 0, 0)
        self.key = QLineEdit(self.s.giphy_key)
        self.key.setPlaceholderText("Paste your free GIPHY API key")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        get_key = QPushButton("Get a free key ↗")
        get_key.setToolTip("Sign in at GIPHY → Create an App → API → copy the key. Takes 2 minutes.")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GIPHY_SIGNUP)))
        save_key = QPushButton("Save key")
        save_key.clicked.connect(self._save_key)
        for w in (QLabel("GIPHY key:"), self.key, get_key, save_key):
            kr.addWidget(w)
        gv.addWidget(self.key_row)
        srow = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search GIPHY… (e.g. 'mind blown', 'bruh', 'dance')")
        self.q.returnPressed.connect(self._search)
        self.kind_stickers = QRadioButton("Stickers (see-through)")
        self.kind_gifs = QRadioButton("GIFs")
        self.kind_stickers.setChecked(True)
        go = QPushButton("🔍 Search")
        go.clicked.connect(self._search)
        for w in (self.q, self.kind_stickers, self.kind_gifs, go):
            srow.addWidget(w)
        gv.addLayout(srow)
        self.found = self._grid()
        self.found.itemDoubleClicked.connect(self._giphy_pick)
        gv.addWidget(self.found, 1)
        self.gstatus = QLabel("Powered by GIPHY")
        self.gstatus.setObjectName("muted")
        gv.addWidget(self.gstatus)
        self.tabs.addTab(gp, "🔍 Search GIPHY")

        # --- bottom
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Show it for"))
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.5, 30.0)
        self.secs.setSingleStep(0.5)
        self.secs.setValue(3.0)
        self.secs.setSuffix(" s")
        bottom.addWidget(self.secs)
        hint = QLabel("It starts at the playhead. Place and resize it in the 📱 Final look tab.")
        hint.setObjectName("muted")
        bottom.addWidget(hint, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        add = QPushButton("＋ Add selected")
        add.setObjectName("primary")
        add.clicked.connect(self._add_selected)
        bottom.addWidget(cancel)
        bottom.addWidget(add)
        v.addLayout(bottom)

        self._results.connect(self._show_results)
        self._thumb.connect(self._set_thumb)
        self._downloaded.connect(self._done_download)
        self._fill_mine()
        if not any(gif_dir(self.s).iterdir()) and self.s.giphy_key:
            self.tabs.setCurrentIndex(1)

    @property
    def seconds(self) -> float:
        return self.secs.value()

    def _grid(self) -> QListWidget:
        g = QListWidget()
        g.setViewMode(QListWidget.IconMode)
        g.setIconSize(QSize(128, 96))
        g.setGridSize(QSize(144, 128))
        g.setResizeMode(QListWidget.Adjust)
        g.setMovement(QListWidget.Static)
        g.setSelectionMode(QAbstractItemView.SingleSelection)
        g.setWordWrap(True)
        return g

    # ---------- my GIFs
    def _fill_mine(self):
        self.mine.clear()
        f = self.filter.text().strip().lower()
        files = sorted((p for p in gif_dir(self.s).iterdir() if p.suffix.lower() in GIF_EXT),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            if f and f not in p.stem.lower():
                continue
            it = QListWidgetItem(p.stem[:22])
            pm = QPixmap(str(p))
            if not pm.isNull():
                it.setIcon(QIcon(pm.scaled(128, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            else:
                it.setText("🎞 " + p.stem[:20])
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(p.name)
            self.mine.addItem(it)
        if not files:
            it = QListWidgetItem("No GIFs yet —\nAdd from file\nor search GIPHY")
            it.setFlags(Qt.NoItemFlags)
            self.mine.addItem(it)

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "Add a GIF or sticker", "",
                                           "GIFs & stickers (*.gif *.webp *.png *.apng *.webm *.mp4 *.mov)")
        if not f:
            return
        dst = gif_dir(self.s) / Path(f).name
        if Path(f).resolve() != dst.resolve():
            shutil.copyfile(f, dst)
        self._pick(str(dst))

    def _pick(self, path):
        if path:
            self.chosen = path
            self.accept()

    def _add_selected(self):
        if self.tabs.currentIndex() == 0:
            it = self.mine.currentItem()
            if it and it.data(Qt.UserRole):
                self._pick(it.data(Qt.UserRole))
        else:
            it = self.found.currentItem()
            if it:
                self._giphy_pick(it)

    # ---------- GIPHY
    def _save_key(self):
        self.s.giphy_key = self.key.text().strip()
        save_settings(self.s)
        self.gstatus.setText("Key saved. Search away! · Powered by GIPHY")

    def _search(self):
        key = self.key.text().strip()
        if not key:
            self.gstatus.setText("Paste your free GIPHY key first (button above) — GIPHY needs one for searching.")
            return
        if key != self.s.giphy_key:
            self._save_key()
        q = self.q.text().strip()
        if not q:
            return
        kind = "stickers" if self.kind_stickers.isChecked() else "gifs"
        self.gstatus.setText(f"Searching GIPHY for “{q}”…")
        self.found.clear()
        url = (f"https://api.giphy.com/v1/{kind}/search?" +
               urllib.parse.urlencode({"api_key": key, "q": q, "limit": 30, "rating": "pg-13"}))

        def work():
            try:
                data = json.loads(_get(url).decode("utf-8")).get("data", [])
                self._results.emit(data)
            except Exception as e:
                self._results.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _show_results(self, data):
        if isinstance(data, str):
            hint = " (is the key right?)" if "401" in data or "403" in data else ""
            self.gstatus.setText(f"GIPHY search failed: {data}{hint}")
            return
        self.found.clear()
        jobs = []
        for i, d in enumerate(data):
            imgs = d.get("images", {})
            thumb = (imgs.get("fixed_width_small") or imgs.get("fixed_width_still") or {}).get("url", "")
            best = (imgs.get("downsized_medium") or imgs.get("fixed_height") or imgs.get("original") or {}).get("url")
            it = QListWidgetItem((d.get("title") or "GIF")[:22])
            it.setData(Qt.UserRole, {"id": d.get("id", str(i)), "url": best, "title": d.get("title") or ""})
            self.found.addItem(it)
            if thumb:
                jobs.append((i, thumb))
        self.gstatus.setText(f"{len(data)} results — double-click one to add it · Powered by GIPHY"
                             if data else "Nothing found — try other words · Powered by GIPHY")

        def thumbs():
            for i, u in jobs:
                try:
                    self._thumb.emit(i, _get(u, 10))
                except Exception:
                    pass
        threading.Thread(target=thumbs, daemon=True).start()

    def _set_thumb(self, i, data):
        it = self.found.item(i)
        if it is None:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            it.setIcon(QIcon(pm.scaled(128, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def _giphy_pick(self, it):
        info = it.data(Qt.UserRole) or {}
        if not info.get("url"):
            return
        safe = "".join(ch for ch in info["title"] if ch.isalnum() or ch in " -_")[:40].strip() or "giphy"
        dst = gif_dir(self.s) / f"{safe} ({info['id']}).gif"
        if dst.exists():
            self._pick(str(dst))
            return
        self.gstatus.setText("Downloading…")

        def work():
            try:
                dst.write_bytes(_get(info["url"], 30))
                self._downloaded.emit(str(dst), "")
            except Exception as e:
                self._downloaded.emit("", str(e))
        threading.Thread(target=work, daemon=True).start()

    def _done_download(self, path, err):
        if err:
            self.gstatus.setText(f"Download failed: {err}")
            return
        self._pick(path)
