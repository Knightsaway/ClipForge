"""Settings boxes that keep ClipForge running without babysitting: storage, updates, backup, phone
notifications, per-game recording settings and your music folder."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import __version__, storage, updater
from ..config import Settings, app_data_dir, save_settings


class _Bridge(QObject):
    text = Signal(object, str)
    downloaded = Signal(str)


class MaintenanceBox(QWidget):
    def __init__(self, s: Settings, lib_fn, clips_fn, restart_fn, phone_url_fn=lambda: ""):
        super().__init__()
        self.s, self.lib_fn, self.clips_fn, self.restart_fn, self.phone_url_fn = s, lib_fn, clips_fn, restart_fn, phone_url_fn
        self.bridge = _Bridge()
        self.bridge.text.connect(lambda lbl, t: lbl.setText(t))
        self.bridge.downloaded.connect(self.install_zip)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        # ---- storage
        g = QGroupBox("Storage")
        f = QFormLayout(g)
        self.usage = QLabel("…")
        self.usage.setWordWrap(True)
        f.addRow(self.usage)
        self.clean_days = QSpinBox()
        self.clean_days.setRange(0, 365)
        self.clean_days.setSuffix(" days")
        self.clean_days.setSpecialValueText("never")
        self.clean_days.setValue(s.auto_clean_days)
        self.clean_days.setToolTip("Clips older than this are deleted for good — except favorites, clips in a draft\n"
                                   "and clips that went into a video you made.")
        f.addRow("Delete unused clips after", self.clean_days)
        self.warn_gb = QSpinBox()
        self.warn_gb.setRange(0, 1000)
        self.warn_gb.setSuffix(" GB free")
        self.warn_gb.setValue(s.disk_warn_gb)
        f.addRow("Warn me below", self.warn_gb)
        row = QHBoxLayout()
        prev = QPushButton("🧹 Clean up now…")
        prev.setToolTip("Shows exactly what would be deleted first")
        prev.clicked.connect(self.clean_now)
        open_f = QPushButton("📁 Clips folder")
        open_f.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib_fn().root))))
        row.addWidget(prev)
        trash = QPushButton("♻ Recently deleted")
        trash.setToolTip("Clips you deleted in the last 24 hours — get them back here")
        trash.clicked.connect(self.recently_deleted)
        row.addWidget(trash)
        row.addWidget(open_f)
        row.addStretch(1)
        f.addRow(row)
        v.addWidget(g)

        # ---- updates
        g = QGroupBox("Updates")
        f = QFormLayout(g)
        f.addRow(QLabel(f"You have ClipForge {__version__}."))
        z = QPushButton("⬆ Install update from a zip…")
        z.setToolTip("Pick the ClipForge zip you were sent — or just drop it on the ClipForge window.\n"
                     "Your clips, settings, drafts and installed packages stay as they are.")
        z.clicked.connect(self.install_zip_dialog)
        f.addRow(z)
        self.update_url = QLineEdit(s.update_url)
        self.update_url.setPlaceholderText("github:Knightsaway/ClipForge")
        f.addRow("Check online", self.update_url)
        crow = QHBoxLayout()
        chk = QPushButton("Check now")
        chk.clicked.connect(self.check_online)
        self.upd_lbl = QLabel("")
        self.upd_lbl.setObjectName("muted")
        self.upd_lbl.setWordWrap(True)
        crow.addWidget(chk)
        crow.addWidget(self.upd_lbl, 1)
        f.addRow(crow)
        v.addWidget(g)

        # ---- backup
        g = QGroupBox("Backup && restore")
        f = QFormLayout(g)
        info = QLabel("Settings, styles, kill icons, drafts and upload history in one file — for a new PC, or to "
                      "share your setup. (Clips aren't included; your YouTube login never is.)")
        info.setObjectName("muted")
        info.setWordWrap(True)
        f.addRow(info)
        self.bk_media = QCheckBox("Include my GIFs and sound effects")
        self.bk_media.setChecked(True)
        f.addRow(self.bk_media)
        brow = QHBoxLayout()
        b1 = QPushButton("💾 Back up…")
        b1.clicked.connect(self.backup)
        b2 = QPushButton("♻ Restore…")
        b2.clicked.connect(self.restore)
        brow.addWidget(b1)
        brow.addWidget(b2)
        brow.addStretch(1)
        f.addRow(brow)
        v.addWidget(g)

        # ---- phone notifications
        g = QGroupBox("Phone notifications")
        f = QFormLayout(g)
        how = QLabel("Get a ping on your phone when your session's Shorts are ready or a render finishes: install "
                     "the free <b>ntfy</b> app, tap <b>＋</b> and subscribe to the topic below.")
        how.setWordWrap(True)
        f.addRow(how)
        trow = QHBoxLayout()
        self.topic = QLineEdit(s.ntfy_topic)
        self.topic.setPlaceholderText("off")
        gen = QPushButton("New topic")
        gen.clicked.connect(self._new_topic)
        test = QPushButton("Send test")
        test.clicked.connect(self._test_push)
        trow.addWidget(self.topic, 1)
        trow.addWidget(gen)
        trow.addWidget(test)
        f.addRow("Topic", trow)
        wrow = QHBoxLayout()
        self.weekly = QCheckBox("Weekly summary on Sunday evening")
        self.weekly.setChecked(s.weekly_summary)
        self.weekly.setToolTip("Clips saved, videos made and posted, views and your best video of the week")
        now = QPushButton("Show it now")
        now.clicked.connect(lambda: self._show_summary())
        wrow.addWidget(self.weekly)
        wrow.addWidget(now)
        wrow.addStretch(1)
        f.addRow(wrow)
        v.addWidget(g)

        # ---- per-game recording
        g = QGroupBox("Per-game recording")
        f = QFormLayout(g)
        tip = QLabel("Different frame rate, quality or buffer for one game (e.g. 60 fps High for shooters, "
                     "30 fps for an MMO). Everything else uses the Capture settings.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        f.addRow(tip)
        self.pg_game = QComboBox()
        self.pg_game.setEditable(True)
        self.pg_game.currentTextChanged.connect(self._pg_load)
        f.addRow("Game", self.pg_game)
        self.pg_fps = QComboBox()
        self.pg_fps.addItems(["(normal)", "30", "60", "120", "144"])
        self.pg_q = QComboBox()
        self.pg_q.addItems(["(normal)"] + list(Settings.QUALITY_TABLE))
        self.pg_buf = QSpinBox()
        self.pg_buf.setRange(0, 1200)
        self.pg_buf.setSuffix(" s")
        self.pg_buf.setSpecialValueText("(normal)")
        f.addRow("Frame rate", self.pg_fps)
        f.addRow("Quality", self.pg_q)
        f.addRow("Replay buffer", self.pg_buf)
        prow = QHBoxLayout()
        sv = QPushButton("Save for this game")
        sv.clicked.connect(self._pg_save)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._pg_remove)
        self.pg_lbl = QLabel("")
        self.pg_lbl.setObjectName("muted")
        prow.addWidget(sv)
        prow.addWidget(rm)
        prow.addWidget(self.pg_lbl, 1)
        f.addRow(prow)
        v.addWidget(g)

        # ---- music
        g = QGroupBox("Music")
        f = QFormLayout(g)
        mrow = QHBoxLayout()
        self.music_dir = QLineEdit(s.music_dir)
        self.music_dir.setPlaceholderText("a folder with music you're allowed to use")
        pick = QPushButton("…")
        pick.setFixedWidth(30)
        pick.clicked.connect(self._pick_music_dir)
        mrow.addWidget(self.music_dir, 1)
        mrow.addWidget(pick)
        f.addRow("My music folder", mrow)
        lib_btn = QPushButton("Get free music (YouTube Audio Library) ↗")
        lib_btn.setToolTip("Free tracks you can use on YouTube without copyright claims. Download some into your "
                           "music folder;\nthe Studio's 🎲 button and styles can then pick from it.")
        lib_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.youtube.com/audiolibrary")))
        f.addRow(lib_btn)
        v.addWidget(g)

        # ---- wizard
        wz = QPushButton("🧭  Run the setup wizard again")
        wz.clicked.connect(lambda: getattr(self, "run_wizard", lambda: None)())
        v.addWidget(wz)
        self.refresh()

    # ---------- storage
    def refresh(self):
        self._pg_fill()

        if self.lib_fn() is None:
            return

        def work():
            try:
                u = storage.usage(self.lib_fn())
                old = storage.candidates(self.clips_fn(), self.s.auto_clean_days) if self.s.auto_clean_days else []
                txt = (f"Clips {storage.gb(u['clips'])} · Exports {storage.gb(u['exports'])} · "
                       f"{storage.gb(u['free'])} free on that drive")
                if old:
                    txt += f" · {len(old)} old clips can go"
                self.bridge.text.emit(self.usage, txt)
            except Exception as e:
                self.bridge.text.emit(self.usage, f"Couldn't measure: {e}")
        threading.Thread(target=work, daemon=True).start()

    def clean_now(self):
        days = self.clean_days.value() or 30
        clips = storage.candidates(self.clips_fn(), days)
        if not clips:
            QMessageBox.information(self, "Clean up", f"Nothing to clean: no unused clips older than {days} days.\n"
                                    "(Favorites, clips in drafts and clips used in videos are always kept.)")
            return
        size = sum((c.p.stat().st_size if c.p.exists() else 0) for c in clips)
        dlg = QDialog(self)
        dlg.setWindowTitle("Clean up")
        dlg.resize(560, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"These {len(clips)} clips (older than {days} days, not favorites, not in a draft, "
                             f"not used in a video) will be deleted for good — {storage.gb(size)}:"))
        lst = QListWidget()
        for c in clips:
            lst.addItem(f"{c.game} · {c.display_title}")
        lay.addWidget(lst, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        go = bb.addButton("Delete them", QDialogButtonBox.AcceptRole)
        go.setObjectName("primary")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        n, freed = storage.clean(self.lib_fn(), clips)
        QMessageBox.information(self, "Clean up", f"Deleted {n} clips ({storage.gb(freed)}). The space is freed "
                                "after 24 hours — until then you can still restore them under Recently deleted.")
        self.refresh()
        getattr(self, "on_cleaned", lambda: None)()

    def recently_deleted(self):
        lib = self.lib_fn()
        items = lib.recently_deleted() if lib else []
        dlg = QDialog(self)
        dlg.setWindowTitle("Recently deleted")
        dlg.resize(560, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Deleted clips are kept for 24 hours, then removed for good." if items else
                             "Nothing deleted in the last 24 hours."))
        lst = QListWidget()
        lst.setSelectionMode(QListWidget.ExtendedSelection)
        for info in items:
            left = max(0, 24 * 3600 - (time.time() - info.get("deleted", 0)))
            lst.addItem(f"{info.get('game', '')} · {info.get('title', '')}  —  {storage.gb(info.get('size', 0))}, "
                        f"gone in {int(left // 3600)} h {int(left % 3600 // 60)} min")
        lay.addWidget(lst, 1)
        row = QHBoxLayout()
        back = QPushButton("↩ Restore selected")
        back.setObjectName("primary")
        purge = QPushButton("Delete all now")
        close = QPushButton("Close")
        row.addWidget(back)
        row.addWidget(purge)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

        def do_restore():
            rows = sorted({lst.row(i) for i in lst.selectedItems()})
            n = sum(1 for r in rows if lib.restore_deleted(items[r]))
            QMessageBox.information(dlg, "Restore", f"Restored {n} clip{'s' if n != 1 else ''}.")
            getattr(self, "on_cleaned", lambda: None)()
            dlg.accept()

        def do_purge():
            if QMessageBox.question(dlg, "Delete for good", "Remove these clips for good right now?") == QMessageBox.Yes:
                lib.purge_deleted(all_now=True)
                self.refresh()
                dlg.accept()
        back.clicked.connect(do_restore)
        purge.clicked.connect(do_purge)
        close.clicked.connect(dlg.reject)
        dlg.exec()

    # ---------- updates
    def install_zip_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "ClipForge update", str(Path.home() / "Downloads"), "Zip (*.zip)")
        if f:
            self.install_zip(f)

    def install_zip(self, f: str):
        try:
            ver, _ = updater.zip_version(f)
        except Exception as e:
            QMessageBox.warning(self, "Update", str(e))
            return
        note = "" if updater.newer(ver, __version__) else "\n\n(That's not newer than what you have — install anyway?)"
        if QMessageBox.question(self, "Update", f"Install ClipForge {ver}? (you have {__version__})\nClipForge restarts "
                                f"afterwards; recordings, clips, settings and drafts are kept.{note}") != QMessageBox.Yes:
            return
        try:
            updater.install_zip(f)
        except Exception as e:
            QMessageBox.warning(self, "Update", f"The update failed: {e}")
            return
        QMessageBox.information(self, "Update", f"ClipForge {ver} is installed. Restarting now.")
        self.restart_fn()

    def check_online(self):
        url = self.update_url.text().strip()
        if not url:
            self.upd_lbl.setText("Add the address of a version.json first (or use the zip button).")
            return
        self.upd_lbl.setText("Checking…")

        def work():
            try:
                info = updater.check(url)
                if not info:
                    self.bridge.text.emit(self.upd_lbl, f"✔ You're up to date ({__version__}).")
                    return
                self.bridge.text.emit(self.upd_lbl, f"Downloading {info['version']}…")
                path = updater.download(info["url"])
                self._pending_zip = str(path)
                self.bridge.text.emit(self.upd_lbl, f"⬆ {info['version']} downloaded")
                self.bridge.downloaded.emit(str(path))
            except Exception as e:
                self.bridge.text.emit(self.upd_lbl, f"Couldn't check: {e}")
        threading.Thread(target=work, daemon=True).start()

    # ---------- backup
    def backup(self):
        name = f"ClipForge backup {time.strftime('%Y-%m-%d')}.zip"
        f, _ = QFileDialog.getSaveFileName(self, "Back up", str(Path.home() / "Documents" / name), "Zip (*.zip)")
        if not f:
            return
        from ..backup import make_backup
        save_settings(self.s)
        try:
            make_backup(f, self.bk_media.isChecked())
            QMessageBox.information(self, "Backup", f"Saved:\n{f}")
        except Exception as e:
            QMessageBox.warning(self, "Backup", f"Couldn't back up: {e}")

    def restore(self):
        f, _ = QFileDialog.getOpenFileName(self, "Restore a backup", str(Path.home() / "Documents"), "Zip (*.zip)")
        if not f:
            return
        if QMessageBox.question(self, "Restore", "Put this backup's settings, styles, kill icons and drafts back? "
                                "ClipForge restarts afterwards.") != QMessageBox.Yes:
            return
        from ..backup import restore_backup
        try:
            n = restore_backup(f)
        except Exception as e:
            QMessageBox.warning(self, "Restore", str(e))
            return
        QMessageBox.information(self, "Restore", f"Restored {n} files. Restarting now.")
        self.restart_fn(no_save=True)

    # ---------- phone push
    def _new_topic(self):
        from ..notify import new_topic
        self.topic.setText(new_topic())

    def _test_push(self):
        from ..notify import send
        t = self.topic.text().strip()
        if not t:
            self._new_topic()
            t = self.topic.text().strip()
        self.s.ntfy_topic = t
        save_settings(self.s)
        ok = send(t, "ClipForge", "It works! You'll get a ping when your Shorts are ready. 🎬",
                  self.phone_url_fn())
        QMessageBox.information(self, "Phone notifications",
                                f"Sent to topic “{t}”. Subscribe to exactly that topic in the ntfy app." if ok else
                                "Couldn't reach ntfy.sh — check your internet connection.")

    def _show_summary(self):
        msg = getattr(self, "send_summary", lambda: None)()
        if msg:
            QMessageBox.information(self, "Your week", msg)

    # ---------- per game
    def _pg_fill(self):
        games = sorted({c.game for c in self.clips_fn()} | set(self.s.game_overrides))
        cur = self.pg_game.currentText()
        self.pg_game.blockSignals(True)
        self.pg_game.clear()
        self.pg_game.addItems(games)
        if cur:
            self.pg_game.setCurrentText(cur)
        self.pg_game.blockSignals(False)
        self._pg_load(self.pg_game.currentText())

    def _pg_load(self, game: str):
        ov = self.s.game_overrides.get(game, {})
        self.pg_fps.setCurrentText(str(ov["fps"]) if ov.get("fps") else "(normal)")
        self.pg_q.setCurrentText(ov.get("quality") or "(normal)")
        self.pg_buf.setValue(int(ov.get("buffer_seconds") or 0))
        self.pg_lbl.setText("custom settings" if ov else "")

    def _pg_save(self):
        game = self.pg_game.currentText().strip()
        if not game:
            return
        ov = {}
        if self.pg_fps.currentText() != "(normal)":
            ov["fps"] = int(self.pg_fps.currentText())
        if self.pg_q.currentText() != "(normal)":
            ov["quality"] = self.pg_q.currentText()
        if self.pg_buf.value():
            ov["buffer_seconds"] = self.pg_buf.value()
        if ov:
            self.s.game_overrides[game] = ov
        else:
            self.s.game_overrides.pop(game, None)
        save_settings(self.s)
        self.pg_lbl.setText("✔ saved — used next time you play it")

    def _pg_remove(self):
        self.s.game_overrides.pop(self.pg_game.currentText().strip(), None)
        save_settings(self.s)
        self._pg_load(self.pg_game.currentText())

    def _pick_music_dir(self):
        d = QFileDialog.getExistingDirectory(self, "My music folder", self.music_dir.text() or str(Path.home()))
        if d:
            self.music_dir.setText(d)

    def apply(self):
        s = self.s
        s.auto_clean_days = self.clean_days.value()
        s.disk_warn_gb = self.warn_gb.value()
        s.update_url = self.update_url.text().strip()
        s.ntfy_topic = self.topic.text().strip()
        s.music_dir = self.music_dir.text().strip()
        s.weekly_summary = self.weekly.isChecked()
