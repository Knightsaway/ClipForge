"""Diagnostics: everything needed to troubleshoot, one click to copy."""
from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path

import threading

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .. import __version__, bugreport, ff, perf
from ..config import app_data_dir


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log yet)"


class BugDialog(QDialog):
    """Shows exactly what goes into the GitHub issue before the browser opens."""

    def __init__(self, parent, repo: str, pc: str, logs: str):
        super().__init__(parent)
        self.setWindowTitle("Report a bug")
        self.resize(640, 560)
        self.repo, self.pc, self.logs = repo, bugreport.scrub(pc), bugreport.scrub(logs)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<b>What happened?</b> What did you do, what did you expect, what happened instead?"))
        self.what = QPlainTextEdit()
        self.what.setPlaceholderText("I pressed the clip hotkey in Valorant and no clip was saved…")
        self.what.setFixedHeight(110)
        v.addWidget(self.what)
        info = QLabel("This opens GitHub in your browser with the details below filled in — you can still edit "
                      "everything there before you submit (you need a free GitHub account). Your Windows user "
                      "name has been removed from the paths.")
        info.setWordWrap(True)
        info.setObjectName("muted")
        v.addWidget(info)
        prev = QPlainTextEdit(f"ClipForge {__version__}\nPC: {self.pc}\n\n{self.logs}")
        prev.setReadOnly(True)
        f = QFont("Consolas")
        f.setPointSize(8)
        prev.setFont(f)
        v.addWidget(prev, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        copy = QPushButton("📋  Copy instead")
        copy.setToolTip("No GitHub account? Copy this and send it however you like")
        copy.clicked.connect(self._copy)
        go = QPushButton("🐞  Open on GitHub")
        go.setObjectName("primary")
        go.clicked.connect(self._open)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for b in (cancel, copy, go):
            row.addWidget(b)
        v.addLayout(row)

    def _open(self):
        what = self.what.toPlainText().strip()
        title = "Bug: " + (what.splitlines()[0][:70] if what else "")
        QDesktopServices.openUrl(QUrl(bugreport.issue_url(self.repo, what, self.pc, self.logs, title)))
        self.accept()

    def _copy(self):
        QGuiApplication.clipboard().setText(
            f"{self.what.toPlainText().strip()}\n\nClipForge {__version__}\nPC: {self.pc}\n\n{self.logs}")
        self.accept()


class DiagnosticsPage(QWidget):
    measured = Signal(object)
    measureProgress = Signal(float)

    def __init__(self, engine, settings):
        super().__init__()
        self.engine, self.s = engine, settings
        self.monitor = perf.Monitor(engine.pids, lambda: engine.state.buffering)
        self.last_measure: perf.Summary | None = None
        self.measured.connect(self._measured)
        self.measureProgress.connect(lambda f: self.measure_btn.setText(f"⏱  Measuring… {int(f * 100)}%"))
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
        bug = QPushButton("🐞  Report a bug")
        bug.setToolTip("Opens a GitHub issue with your ClipForge version, PC and log filled in")
        bug.clicked.connect(self._report_bug)
        for w in (copy, ref, folder, admin, bug):
            head.addWidget(w)
        v.addLayout(head)
        prow = QHBoxLayout()
        self.perf_lbl = QLabel("")
        self.perf_lbl.setWordWrap(True)
        self.perf_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        prow.addWidget(self.perf_lbl, 1)
        self.measure_btn = QPushButton("⏱  Measure 60 s")
        self.measure_btn.setToolTip("Play normally for a minute: measures ClipForge's CPU, memory, disk and\n"
                                    "NVIDIA encoder use while it records — numbers you can quote")
        self.measure_btn.clicked.connect(self._measure)
        prow.addWidget(self.measure_btn)
        v.addLayout(prow)
        self._perf_timer = QTimer(self)
        self._perf_timer.timeout.connect(self._update_perf)
        self._perf_timer.start(10000)
        QTimer.singleShot(0, self._update_perf)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.text.setFont(f)
        v.addWidget(self.text, 1)

    def perf_text(self) -> str:
        parts = []
        if perf.startup_seconds is not None:
            parts.append(f"started in {perf.startup_seconds:.1f} s")
        live = self.monitor.summary()
        if live:
            parts.append(f"while recording (last {live.seconds / 60:.0f} min): {live.text()}")
        elif self.engine.state.buffering:
            parts.append("while recording: measuring…")
        else:
            parts.append("not recording right now")
        if self.last_measure:
            parts.append(f"60 s test: {self.last_measure.text()}")
        mem = perf.memory_breakdown(self.engine.labelled_pids())
        return "Performance: " + " | ".join(parts) + (f"\nMemory now: {mem}" if mem else "")

    def _update_perf(self):
        if self.isVisible() or not self.perf_lbl.text():
            self.perf_lbl.setText(self.perf_text())

    def _measure(self):
        if not self.engine.state.buffering:
            QMessageBox.information(self, "Measure", "Start recording first (and ideally play a game) — the "
                                    "point is to see what ClipForge costs while it records.")
            return
        self.measure_btn.setEnabled(False)

        def work():
            try:
                res = perf.measure(self.engine.pids, 60, self.measureProgress.emit)
            except Exception as e:
                res = e
            self.measured.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def _measured(self, res):
        self.measure_btn.setEnabled(True)
        self.measure_btn.setText("⏱  Measure 60 s")
        if not isinstance(res, perf.Summary):
            QMessageBox.warning(self, "Measure", f"Couldn't measure: {res}")
            return
        self.last_measure = res
        self.perf_lbl.setText(self.perf_text())
        msg = (f"While recording, ClipForge and its {max(0, res.processes - 1)} FFmpeg recorder(s) used:\n\n"
               f"CPU: {res.cpu_pct:.1f}% of your PC on average (peak {res.cpu_peak:.1f}%)\n"
               f"Memory: {res.ram_mb:.0f} MB (peak {res.ram_peak:.0f} MB)\n"
               f"Disk: {res.write_mbs:.1f} MB/s written\n")
        if res.enc_pct is not None:
            msg += f"NVIDIA encoder: {res.enc_pct:.0f}% busy (the whole GPU incl. the game: {res.gpu_pct:.0f}%)\n"
        msg += "\nCopied to the clipboard."
        QGuiApplication.clipboard().setText(f"ClipForge {__version__} while recording ({self.s.resolution} "
                                            f"{self.s.fps}fps {self.s.encoder}): {res.text()}")
        QMessageBox.information(self, "Measure", msg)

    def _report_bug(self):
        st = self.engine.state
        extra = f"capture {st.pipeline or 'not started'} | {self.s.resolution} {self.s.fps}fps {self.s.encoder}"
        g, _ = perf._nvidia()
        if g is not None:
            extra += " | NVIDIA GPU"
        logs = ""
        for name, n in (("crash.log", 15), ("clipforge.log", 30), ("capture.log", 20)):
            t = bugreport.tail(app_data_dir() / name, n)
            if t.strip():
                logs += f"--- {name} ---\n{t}\n"
        BugDialog(self, bugreport.repo_from(self.s.update_url), bugreport.pc_summary(extra),
                  logs or "(no log lines)").exec()

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
            self.perf_text(),
            f"FFmpeg: {fver}",
            f"Capture: {st.pipeline or '(not started)'} | {self.s.resolution} {self.s.fps}fps {self.s.quality} "
            f"{self.s.codec}/{self.s.encoder} | monitor {self.s.monitor}",
            f"Audio: desktop={self.s.desktop_audio} mic={self.s.mic_audio} webcam={(self.s.webcam or 'none') if self.s.use_webcam else 'off (switched off)'}",
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
