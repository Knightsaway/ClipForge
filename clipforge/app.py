"""Entry point: single instance, crash log, then the main window."""
from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime

from .config import IS_WINDOWS, app_data_dir, load_settings

LOG = app_data_dir() / "clipforge.log"


def _log_exception(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}")
    print(text, file=sys.stderr)  # UI errors are logged, never crash the recorder


def main():
    sys.excepthook = _log_exception
    try:
        faulthandler.enable(open(app_data_dir() / "crash.log", "a"))
    except Exception:
        pass
    if IS_WINDOWS:
        import ctypes
        if "--after" in sys.argv:   # restarted (e.g. as admin): wait for the old copy to close first
            try:
                pid = int(sys.argv[sys.argv.index("--after") + 1])
                h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
                if h:
                    ctypes.windll.kernel32.WaitForSingleObject(h, 15000)
                    ctypes.windll.kernel32.CloseHandle(h)
            except (ValueError, IndexError, OSError):
                pass
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClipForge.App")
        # one copy only: two recorders would fight over the GPU encoder and hotkeys
        ctypes.windll.kernel32.CreateMutexW(None, False, "ClipForgeSingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, "ClipForge is already running (check the tray icon).",
                                             "ClipForge", 0x40)
            return

    from PySide6.QtWidgets import QApplication
    from .ui import theme
    from .config import SETTINGS_FILE, save_settings
    first_run = not SETTINGS_FILE.exists()
    s = load_settings()
    if not first_run and not s.wizard_done:
        s.wizard_done = True          # set up before the wizard existed: don't ask again
        save_settings(s)
    theme.apply(getattr(s, "skin", "legacy"))     # the skin must be set before any window is built
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ClipForge")
    app.setQuitOnLastWindowClosed(False)  # closing the window keeps recording in the tray
    app.setStyleSheet(theme.QSS)
    win = MainWindow(s, start_minimized="--minimized" in sys.argv)
    if not ("--minimized" in sys.argv or s.start_minimized):
        win.show()
    if not s.wizard_done:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(900, lambda: (win.show(), win._run_wizard()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
