"""Diagnostics: everything needed to troubleshoot, one click to copy."""
from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .. import __version__, ff
from ..config import app_data_dir


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log yet)"


class DiagnosticsPage(QWidget):
    def __init__(self, engine, settings):
        super().__init__()
        self.engine, self.s = engine, settings
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 10, 18, 14)
        head = QHBoxLayout()
        t = QLabel("DIAGNOSTICS")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        self.copied = QLabel("")
        self.copied.setObjectName("muted")
        head.addWidget(self.copied)
        copy = QPushButton("📋  Copy for Claude")
        copy.setObjectName("primary")
        copy.setToolTip("Copies the report below — paste it into the chat")
        copy.clicked.connect(self._copy)
        ref = QPushButton("⟳  Refresh")
        ref.clicked.connect(self.refresh)
        folder = QPushButton("📁  Open log folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir()))))
        admin = QPushButton("🛡  Restart as administrator")
        admin.setToolTip("Gives capture top GPU priority — fixes stutter in games that max out the GPU")
        admin.clicked.connect(lambda: self.window()._restart_admin())
        for w in (copy, ref, folder, admin):
            head.addWidget(w)
        v.addLayout(head)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.text.setFont(f)
        v.addWidget(self.text, 1)

    def report(self) -> str:
        st = self.engine.state
        try:
            fver = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-version"], timeout=10).stdout.splitlines()[0]
        except Exception as e:
            fver = f"FFmpeg not found: {e}"
        live = "\n".join(list(self.engine._stderr_tail)[-25:]) or "(nothing)"
        lines = [
            f"ClipForge {__version__} — report from {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Windows: {platform.platform()} | Python {sys.version.split()[0]}",
            f"FFmpeg: {fver}",
            f"Capture: {st.pipeline or '(not started)'} | {self.s.resolution} {self.s.fps}fps {self.s.quality} "
            f"{self.s.codec}/{self.s.encoder} | monitor {self.s.monitor}",
            f"Audio: desktop={self.s.desktop_audio} mic={self.s.mic_audio} webcam={self.s.webcam or 'none'}",
            "Sound sources: " + (" | ".join(
                f"{sp.kind}: '{sp.sender.device_name or '?'}' level {sp.sender.level:.3f} "
                f"heard sound={sp.sender.heard}" for sp in self.engine._sides if sp.sender) or "none running")
            + f" | mic setting: '{self.s.mic_device or 'Windows default'}'",
            "Webcam / sound problems: " + (" | ".join(f"{k}: {v}" for k, v in self.engine.audio_warnings.items()) or "none"),
            f"State: buffering={st.buffering} recording={st.recording} standby={st.paused_no_game} "
            f"game={st.game!r} error={st.error!r}",
            f"Options dropped for this FFmpeg: {sorted(self.engine._strip) or 'none'}",
            f"Smoothness: {self.engine.real_fps or '-'} real fps (target {self.s.fps}) | GPU priority: "
            f"{self.engine.gpu_priority} | admin: {__import__('clipforge.capture', fromlist=['x']).is_admin()} | "
            f"follow game: {self.s.follow_game} | method: {self.s.capture_method} | per game: {self.s.method_by_game or '-'}",
            "Helper recorders: " + (", ".join(
                f"{sp.kind} (run {sp.runs}, {'running' if sp.proc and sp.proc.poll() is None else 'stopped'})"
                for sp in self.engine._sides) or "none"),
            "",
            "── Live FFmpeg output (game video) ──",
            live,
            "",
            "── Webcam / audio recorders (last lines) ──",
            "\n".join(f"[{sp.kind}] {l}" for sp in self.engine._sides for l in list(sp.tail)[-4:]) or "(nothing)",
            "",
            "── capture.log (last 60 lines) ──",
            _tail(app_data_dir() / "capture.log", 60),
            "",
            "── clipforge.log (app errors, last 40 lines) ──",
            _tail(app_data_dir() / "clipforge.log", 40),
        ]
        return "\n".join(lines)

    def refresh(self):
        self.text.setPlainText(self.report())
        self.text.verticalScrollBar().setValue(0)

    def _copy(self):
        txt = self.report()
        self.text.setPlainText(txt)
        QGuiApplication.clipboard().setText(txt)
        self.copied.setText("Copied ✔ — paste it into the chat")
