"""`ClipForge.exe --memreport <file>`: how much memory ClipForge uses in real situations (CI keeps it honest).

Runs the real window with whatever library there is: started, a clip playing, the Studio with clips,
then in the tray (where it spends most of its life), then back. Writes one line per stage and quits.
"""
from __future__ import annotations

import os
from pathlib import Path


def schedule(win, report: Path) -> None:
    from PySide6.QtCore import QTimer

    from . import perf
    lines: list[str] = []

    def note(stage: str):
        pid = os.getpid()
        ws, priv = perf.proc_memory(pid, "ws") or 0.0, perf.proc_memory(pid) or 0.0
        lines.append(f"{stage}: in RAM {ws:.0f} MB (private {priv:.0f} MB)")
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    clips = lambda: list(win.library.clips)   # noqa: E731

    def s1():
        note(f"started, library of {len(clips())} clips")
        if clips():
            win._open_clip(clips()[0])
        QTimer.singleShot(6000, s2)

    def s2():
        note("a clip open and playing")
        win.studio.clear()
        win._to_studio(clips()[:6])
        QTimer.singleShot(6000, s3)

    def s3():
        note("Studio with 6 clips")
        win.close()                       # to the tray, like closing the window
        QTimer.singleShot(25000, s4)

    def s4():
        note("in the tray")
        win._show()
        QTimer.singleShot(6000, s5)

    def s5():
        note("window opened again")
        lines.append("DONE")
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os._exit(0)

    QTimer.singleShot(20000, s1)          # let the library load and make its thumbnails
