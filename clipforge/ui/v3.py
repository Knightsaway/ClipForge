"""Screens for v3: Insights (what works + trends + title tests), thumbnail picker, title A/B dialog,
and the settings boxes for AI, weekly best-of and squad."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import ai, analytics, youtube
from ..config import Settings
from . import theme


def _h(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("h2")
    return l


def _muted(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


# ====================================================================== insights
class InsightsPage(QWidget):
    trendsReady = Signal(object)

    def __init__(self, s: Settings, queue: youtube.UploadQueue, uploader: youtube.Uploader):
        super().__init__()
        self.s, self.q, self.up = s, queue, uploader
        self.trendsReady.connect(self._trends_done)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 10, 18, 14)
        head = QHBoxLayout()
        t = QLabel("INSIGHTS")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        upd = QPushButton("📈  Update views")
        upd.clicked.connect(self._update_views)
        head.addWidget(upd)
        outer.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.cols = QHBoxLayout(inner)
        self.left = QVBoxLayout()
        self.right = QVBoxLayout()
        self.cols.addLayout(self.left, 3)
        self.cols.addLayout(self.right, 2)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # trends box (right column, built once)
        tb = QFrame()
        tb.setObjectName("panel")
        tv = QVBoxLayout(tb)
        tv.addWidget(_h("WHAT'S TRENDING (YOUTUBE SHORTS, LAST 7 DAYS)"))
        row = QHBoxLayout()
        self.game = QComboBox()
        self.game.setEditable(True)
        self.trend_btn = QPushButton("Check")
        self.trend_btn.clicked.connect(self._check_trends)
        row.addWidget(self.game, 1)
        row.addWidget(self.trend_btn)
        tv.addLayout(row)
        tv.addWidget(_muted("Each check costs ~101 of your 10,000 free daily YouTube API units — "
                            "fine for a few games a day."))
        self.trend_box = QVBoxLayout()
        tv.addLayout(self.trend_box)
        self.right.addWidget(tb)
        self.compare_box = QVBoxLayout()
        cb = QFrame()
        cb.setObjectName("panel")
        cb.setLayout(self.compare_box)
        self.right.addWidget(cb)
        self.right.addStretch(1)

    def set_games(self, games: list[str]):
        cur = self.game.currentText()
        self.game.clear()
        self.game.addItems(games)
        if cur:
            self.game.setCurrentText(cur)

    def _update_views(self):
        threading.Thread(target=lambda: (self.up.refresh_stats(),), daemon=True).start()

    def _clear(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear(it.layout())

    def refresh(self):
        self._clear(self.left)
        items = self.q.items
        rec = QFrame()
        rec.setObjectName("panel")
        rv = QVBoxLayout(rec)
        rv.addWidget(_h("WHAT TO DO NEXT"))
        for tip in analytics.recommendations(items):
            l = QLabel("• " + tip)
            l.setWordWrap(True)
            l.setStyleSheet("font-size:14px; padding:3px 0;")
            rv.addWidget(l)
        self.left.addWidget(rec)

        ins = analytics.insights(items)
        if ins:
            grid_box = QFrame()
            grid_box.setObjectName("panel")
            g = QGridLayout(grid_box)
            g.addWidget(_h("WHAT PERFORMS (1.0× = your typical video)"), 0, 0, 1, 2)
            r, c = 1, 0
            for key in ("Game", "Hook", "Length", "Posting hour", "Weekday", "Layout", "Kill streak in it",
                        "Written by AI", "Format"):
                rows = ins.get(key, [])
                if not rows:
                    continue
                box = QVBoxLayout()
                box.addWidget(QLabel(f"<b>{key}</b>"))
                for v, score, n in rows[:5]:
                    col = theme.GREEN if score >= 1.15 else (theme.RED if score <= 0.85 else theme.TEXT)
                    bar = "█" * max(1, min(20, int(score * 6)))
                    box.addWidget(QLabel(f"<span style='color:{col}'>{bar}</span> {score:.1f}×  "
                                         f"<span style='color:{theme.MUTED}'>{v} ({n})</span>"))
                w = QWidget()
                w.setLayout(box)
                g.addWidget(w, r, c)
                c += 1
                if c == 2:
                    c, r = 0, r + 1
            self.left.addWidget(grid_box)

        tests = [i for i in items if i.title_b]
        if tests:
            tb = QFrame()
            tb.setObjectName("panel")
            tv = QVBoxLayout(tb)
            tv.addWidget(_h("TITLE A/B TESTS"))
            for it in tests[-12:][::-1]:
                state = {"": "waiting for 24 h with title A", "b": "running title B",
                         "done": f"winner: {it.ab_winner}"}.get(it.ab_state, it.ab_state)
                extra = (f" — A {it.ab_views_a:,} views day 1, B {it.ab_views_b:,} views day 2"
                         if it.ab_state == "done" else "")
                l = QLabel(f"<b>A:</b> {it.title}<br><b>B:</b> {it.title_b}<br>"
                           f"<span style='color:{theme.MUTED}'>{state}{extra}</span>")
                l.setWordWrap(True)
                tv.addWidget(l)
            self.left.addWidget(tb)
        self.left.addStretch(1)
        self._show_compare()

    # ---------- trends
    def _check_trends(self):
        game = self.game.currentText().strip()
        if not game:
            return
        if not youtube.connected():
            QMessageBox.information(self, "Trends", "Connect YouTube first (Settings → Uploads).")
            return
        self.trend_btn.setEnabled(False)
        self.trend_btn.setText("…")

        def work():
            try:
                self.trendsReady.emit(youtube.trending(game))
            except Exception as e:
                self.trendsReady.emit({"error": str(e)})
        threading.Thread(target=work, daemon=True).start()

    def _trends_done(self, data):
        self.trend_btn.setEnabled(True)
        self.trend_btn.setText("Check")
        self._clear(self.trend_box)
        if "error" in data:
            self.trend_box.addWidget(_muted(data["error"]))
            return
        self.trend_box.addWidget(QLabel(f"<b>{data['game']}</b>: top Shorts average "
                                        f"<b>{data['avg_per_hour']:,.0f}</b> views/hour"))
        if data["words"]:
            self.trend_box.addWidget(QLabel("Hot words in titles: " +
                                            ", ".join(f"<b>{w}</b>" for w, _ in data["words"][:10])))
        for v in data["top"][:8]:
            b = QPushButton(f"{v['per_hour']:,.0f}/h · {v['title'][:70]}")
            b.setStyleSheet("text-align:left;")
            b.setToolTip(f"{v['channel']} — {v['views']:,} views. Click to watch.")
            b.clicked.connect(lambda _=False, vid=v["id"]: QDesktopServices.openUrl(QUrl(f"https://youtu.be/{vid}")))
            self.trend_box.addWidget(b)
        self._show_compare()

    def _show_compare(self):
        self._clear(self.compare_box)
        cache = youtube.load_trends()
        if not cache:
            return
        self.compare_box.addWidget(_h("WHICH GAME IS HOT RIGHT NOW"))
        rows = sorted(cache.values(), key=lambda d: d.get("avg_per_hour", 0), reverse=True)
        top = rows[0].get("avg_per_hour", 1) or 1
        for d in rows:
            age_h = (datetime.now().timestamp() - d.get("fetched", 0)) / 3600
            bar = "█" * max(1, int(20 * d.get("avg_per_hour", 0) / top))
            self.compare_box.addWidget(QLabel(f"<span style='color:{theme.ACCENT}'>{bar}</span> <b>{d['game']}</b> "
                                              f"{d.get('avg_per_hour', 0):,.0f}/h "
                                              f"<span style='color:{theme.MUTED}'>({age_h:.0f} h ago)</span>"))
        self.compare_box.addWidget(_muted("Make more Shorts of the hottest game you actually play."))


# ====================================================================== thumbnails
class ThumbDialog(QDialog):
    """Pick one of 3 auto thumbnails (or regenerate with different text)."""

    def __init__(self, video: Path, title: str, parent=None, hint_times=None):
        super().__init__(parent)
        self.video, self.hint_times = video, hint_times
        self.choice: Path | None = None
        self.setWindowTitle("Thumbnail")
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Text"))
        from ..thumbnail import thumb_text
        self.text = QLineEdit(thumb_text(title))
        regen = QPushButton("Regenerate")
        regen.clicked.connect(self._make)
        row.addWidget(self.text, 1)
        row.addWidget(regen)
        v.addLayout(row)
        self.grid = QHBoxLayout()
        v.addLayout(self.grid)
        v.addWidget(_muted("Click the one you like. It's set automatically when the video uploads "
                           "(YouTube needs your channel phone-verified for custom thumbnails)."))
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(1250, 360)
        self._make()

    def _make(self):
        from ..thumbnail import make_thumbnails
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        from ..library import thumb_work_dir
        out_dir = thumb_work_dir(self.video)
        try:
            files = make_thumbnails(str(self.video), self.text.text(), out_dir, self.hint_times)
        except Exception as e:
            QMessageBox.warning(self, "Thumbnail", str(e))
            return
        for f in files:
            b = QPushButton()
            pm = QPixmap(str(f)).scaled(400, 225, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            b.setIcon(QIcon(pm))
            b.setIconSize(pm.size())
            b.setMinimumSize(410, 235)
            b.clicked.connect(lambda _=False, f=f: self._pick(f))
            self.grid.addWidget(b)

    def _pick(self, f: Path):
        import shutil
        dst = Path(str(self.video) + ".thumb.jpg")
        shutil.copyfile(f, dst)
        self.choice = dst
        self.accept()


class ABDialog(QDialog):
    """Schedule a long video with a title A/B test."""

    def __init__(self, title: str, game: str, s: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Title A/B test")
        f = QFormLayout(self)
        f.addRow(_muted("Title A runs for the first 24 h, then ClipForge switches to title B for 24 h and keeps "
                        "whichever did better (allowing for the normal day-2 drop-off)."))
        self.a = QLineEdit(title)
        self.b = QLineEdit()
        self.b.setPlaceholderText("a different angle on the same video")
        f.addRow("Title A", self.a)
        row = QHBoxLayout()
        row.addWidget(self.b, 1)
        gen = QPushButton("🤖 Suggest")
        gen.setEnabled(s.ai_enabled)
        gen.clicked.connect(lambda: self._suggest(game, s))
        row.addWidget(gen)
        f.addRow("Title B", row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.resize(620, 200)

    def _suggest(self, game, s):
        try:
            alts = ai.alt_titles(self.a.text(), game, 1, s.ai_model)
            if alts:
                self.b.setText(alts[0])
        except Exception as e:
            QMessageBox.information(self, "AI", str(e))


# ====================================================================== settings boxes
class V3SettingsBox(QWidget):
    def __init__(self, s: Settings):
        super().__init__()
        self.s = s
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("AI writer (local, free)")
        f = QFormLayout(g)
        self.ai_on = QCheckBox("Write hooks, titles & hashtags with local AI")
        self.ai_on.setChecked(s.ai_enabled)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(ai.models() or [s.ai_model])
        self.model.setCurrentText(s.ai_model)
        self.ai_status = QLabel()
        test = QPushButton("Test")
        test.clicked.connect(self._test_ai)
        row = QHBoxLayout()
        row.addWidget(self.ai_status, 1)
        row.addWidget(test)
        f.addRow(self.ai_on)
        f.addRow("Model", self.model)
        f.addRow("Status", row)
        f.addRow(_muted("Uses Ollama (ollama.com). The model is unloaded right after each request, so it "
                        "never takes VRAM while you game."))
        v.addWidget(g)
        self._ai_state()

        g = QGroupBox("Learning")
        f = QFormLayout(g)
        self.learn = QCheckBox("Lean towards hooks / lengths / times that win on my channel")
        self.learn.setChecked(s.learn)
        self.ab = QCheckBox("Offer title A/B tests for long videos")
        self.ab.setChecked(s.ab_titles)
        f.addRow(self.learn)
        f.addRow(self.ab)
        v.addWidget(g)

        g = QGroupBox("Weekly best-of")
        f = QFormLayout(g)
        self.bo = QCheckBox("Make a 'Top plays of the week' countdown video every week")
        self.bo.setChecked(s.bestof_enabled)
        self.bo_day = QComboBox()
        self.bo_day.addItems(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        self.bo_day.setCurrentIndex(s.bestof_day)
        self.bo_hour = QSpinBox()
        self.bo_hour.setRange(0, 23)
        self.bo_hour.setSuffix(":00")
        self.bo_hour.setValue(s.bestof_hour)
        self.bo_n = QSpinBox()
        self.bo_n.setRange(3, 20)
        self.bo_n.setPrefix("Top ")
        self.bo_n.setValue(s.bestof_count)
        self.bo_post = QCheckBox("Schedule it on YouTube automatically")
        self.bo_post.setChecked(s.bestof_autopost)
        f.addRow(self.bo)
        f.addRow("Day", self.bo_day)
        f.addRow("Time", self.bo_hour)
        f.addRow("Length", self.bo_n)
        f.addRow(self.bo_post)
        v.addWidget(g)

        g = QGroupBox("Squad — Friends' POV")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.sq_dir = QLineEdit(s.squad_dir)
        self.sq_dir.setPlaceholderText("a folder you all share via Google Drive / OneDrive / Dropbox")
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._pick_sq)
        row.addWidget(self.sq_dir)
        row.addWidget(b)
        self.sq_name = QLineEdit(s.squad_name)
        self.sq_name.setPlaceholderText("your name in the squad")
        f.addRow("Shared folder", row)
        f.addRow("Your name", self.sq_name)
        f.addRow(_muted("Everyone in the squad runs ClipForge with the same shared folder. When anyone "
                        "presses Marker, everyone's angle of that moment is saved and shared."))
        v.addWidget(g)

    def _pick_sq(self):
        d = QFileDialog.getExistingDirectory(self, "Shared squad folder", self.sq_dir.text())
        if d:
            self.sq_dir.setText(d)

    def _ai_state(self):
        ms = ai.models()
        self.ai_status.setText(f"Ollama running ✔ ({len(ms)} model{'s' if len(ms) != 1 else ''})" if ms
                               else "Ollama not running — templates are used instead")

    def _test_ai(self):
        self._ai_state()
        try:
            r = ai.write_post("Call of Duty", [{"game": "Call of Duty", "kills": 3, "streak": "Triple kill"}],
                              True, self.model.currentText())
            QMessageBox.information(self, "AI writer", "Hooks:\n" + "\n".join(r["hooks"]) +
                                    f"\n\nTitle: {r['title']}\nTags: {' '.join(r['tags'])}")
        except Exception as e:
            QMessageBox.information(self, "AI writer", f"Not working yet: {e}")

    def apply(self):
        s = self.s
        s.ai_enabled = self.ai_on.isChecked()
        s.ai_model = self.model.currentText().strip() or s.ai_model
        s.learn = self.learn.isChecked()
        s.ab_titles = self.ab.isChecked()
        s.bestof_enabled = self.bo.isChecked()
        s.bestof_day = self.bo_day.currentIndex()
        s.bestof_hour = self.bo_hour.value()
        s.bestof_count = self.bo_n.value()
        s.bestof_autopost = self.bo_post.isChecked()
        s.squad_dir = self.sq_dir.text().strip()
        s.squad_name = self.sq_name.text().strip()


class V4SettingsBox(QWidget):
    """Automation: phone approvals, loops, TikTok versions, reaction zoom, caster, recaps, monthly, series."""

    def __init__(self, s: Settings, phone_url=lambda: ""):
        super().__init__()
        self.s, self.phone_url = s, phone_url
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("📱 Approve from your phone")
        f = QFormLayout(g)
        self.phone = QCheckBox("Phone approval page on my home Wi-Fi")
        self.phone.setChecked(s.phone_enabled)
        f.addRow(self.phone)
        self.link = QLineEdit(phone_url())
        self.link.setReadOnly(True)
        row = QHBoxLayout()
        copy = QPushButton("Copy link")
        copy.clicked.connect(self._copy)
        new = QPushButton("New link")
        new.setToolTip("Makes the old link stop working (if you shared it by accident)")
        new.clicked.connect(self._new)
        row.addWidget(copy)
        row.addWidget(new)
        f.addRow("Link", self.link)
        f.addRow("", row)
        f.addRow(_muted("Open the link on your phone (same Wi-Fi) and add it to your home screen. "
                        "Windows asks once to allow ClipForge on private networks — allow it."))
        v.addWidget(g)

        g = QGroupBox("Automatic content")
        f = QFormLayout(g)
        self.today_n = QSpinBox()
        self.today_n.setRange(1, 6)
        self.today_n.setSuffix(" Shorts")
        self.today_n.setValue(s.today_shorts)
        self.recap = QCheckBox("60 s recap after each gaming session")
        self.recap.setChecked(s.recap_enabled)
        self.recap_min = QSpinBox()
        self.recap_min.setRange(5, 240)
        self.recap_min.setSuffix(" min")
        self.recap_min.setPrefix("if I played at least ")
        self.recap_min.setValue(s.recap_min_minutes)
        self.monthly = QCheckBox("Best of the month (8+ min, chapters, mid-roll ready) on the 1st")
        self.monthly.setChecked(s.monthly_enabled)
        self.parts = QSpinBox()
        self.parts.setRange(2, 7)
        self.parts.setSuffix(" parts")
        self.parts.setValue(s.series_parts)
        f.addRow("Make today's content", self.today_n)
        f.addRow(self.recap)
        f.addRow("", self.recap_min)
        f.addRow(self.monthly)
        f.addRow("Series length", self.parts)
        f.addRow(_muted("Everything made automatically waits for your OK before it's posted."))
        v.addWidget(g)

        g = QGroupBox("Shorts style")
        f = QFormLayout(g)
        self.loop = QCheckBox("Seamless loops (end flows into the start)")
        self.loop.setChecked(s.loop_shorts)
        self.tik = QCheckBox("Make a TikTok version of every Short")
        self.tik.setChecked(s.tiktok_versions)
        self.rz = QCheckBox("Facecam zooms in when I laugh / yell")
        self.rz.setChecked(s.reaction_zoom)
        self.caster = QCheckBox("AI caster voice on Auto Shorts")
        self.caster.setChecked(s.caster_auto)
        self.voice = QComboBox()
        self.voice.setEditable(True)
        self.voice.addItems(["en_US-ryan-high", "en_US-lessac-high", "en_GB-alan-medium", "en_US-joe-medium",
                             "no_NO-talesyntese-medium"])
        self.voice.setCurrentText(s.caster_voice)
        self.voice.setToolTip("Piper voice name (downloaded on first use). Norwegian works too!")
        for w in (self.loop, self.tik, self.rz, self.caster):
            f.addRow(w)
        f.addRow("Caster voice", self.voice)
        v.addWidget(g)

    def _copy(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.link.text())

    def _new(self):
        from ..phone import new_token
        self.s.phone_token = new_token()
        from ..config import save_settings
        save_settings(self.s)
        w = self.window()
        if hasattr(w, "restart_phone"):
            w.restart_phone()
        self.link.setText(self.phone_url())

    def apply(self):
        s = self.s
        s.phone_enabled = self.phone.isChecked()
        s.today_shorts = self.today_n.value()
        s.recap_enabled = self.recap.isChecked()
        s.recap_min_minutes = self.recap_min.value()
        s.monthly_enabled = self.monthly.isChecked()
        s.series_parts = self.parts.value()
        s.loop_shorts = self.loop.isChecked()
        s.tiktok_versions = self.tik.isChecked()
        s.reaction_zoom = self.rz.isChecked()
        s.caster_auto = self.caster.isChecked()
        s.caster_voice = self.voice.currentText().strip() or s.caster_voice
