"""Renders ClipForge's logo (the same drawing as ui.widgets.app_icon) into assets/ClipForge.ico.

Run once after changing the logo: QT_QPA_PLATFORM=offscreen python scripts/make_icon.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402

from clipforge.ui import theme  # noqa: E402


def render(size: int) -> bytes:
    k = size / 64
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(k, k)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("white"))
    path = QPainterPath()
    path.moveTo(25, 18)
    path.lineTo(47, 32)
    path.lineTo(25, 46)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor(theme.RED))
    p.drawEllipse(QPointF(48, 16), 7, 7)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main() -> None:
    QGuiApplication(sys.argv[:1])
    theme.apply("legacy")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [render(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for s, png in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    out = ROOT / "assets" / "ClipForge.ico"
    out.write_bytes(head + entries + data)
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
