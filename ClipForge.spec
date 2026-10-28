# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec: ClipForge.exe + _internal + vendored ffmpeg/ next to the exe.

Kept deliberately small: only the five Qt modules ClipForge uses are bundled (PyInstaller's
PySide6 hooks pull in exactly their DLLs and plugins), never the whole of Qt.

Build (Windows):
  python scripts/fetch_ffmpeg.py
  pyinstaller --noconfirm ClipForge.spec
Check the result: dist\ClipForge\ClipForge.exe --selftest report.txt
"""
from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()

# Every ClipForge module, including the ones only imported inside functions.
hiddenimports = collect_submodules("clipforge") + [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "numpy",
    "soundcard",
]

# Qt modules ClipForge never uses. Excluding them keeps their DLLs, QML files and plugins out.
QT_UNUSED = [
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender", "QtAxContainer",
    "QtBluetooth", "QtCanvasPainter", "QtCharts", "QtConcurrent", "QtDataVisualization", "QtDBus", "QtDesigner",
    "QtGraphs", "QtGraphsWidgets", "QtHelp", "QtHttpServer", "QtLocation", "QtNetworkAuth", "QtNfc",
    "QtOpenGL", "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets", "QtPositioning", "QtPrintSupport", "QtQml",
    "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickTest", "QtQuickWidgets", "QtRemoteObjects", "QtScxml",
    "QtSensors", "QtSerialBus", "QtSerialPort", "QtSpatialAudio", "QtSql", "QtStateMachine", "QtSvg",
    "QtSvgWidgets", "QtTest", "QtTextToSpeech", "QtUiTools", "QtWebChannel", "QtWebEngineCore",
    "QtWebEngineQuick", "QtWebEngineWidgets", "QtWebSockets", "QtWebView", "QtXml",
]
excludes = ["PySide6." + m for m in QT_UNUSED] + [
    # optional extras that are installed separately, and Python batteries ClipForge never loads
    "faster_whisper", "torch", "torchaudio", "piper", "piper_tts",
    "tkinter", "unittest", "pydoc_data", "test", "lib2to3", "IPython", "matplotlib", "scipy", "PIL",
]

datas = []
for rel in ("LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"):
    p = ROOT / rel
    if p.exists():
        datas.append((str(p), "."))
licenses = ROOT / "licenses"
if licenses.is_dir():
    datas.append((str(licenses), "licenses"))

a = Analysis(
    [str(ROOT / "clipforge" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)


def _keep(entry) -> bool:
    name = entry[0].replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    if base == "opengl32sw.dll":            # 20 MB software-OpenGL fallback; gaming PCs have GPU drivers
        return False
    if "/translations/" in name:            # Qt's own UI translations; ClipForge is English-only
        return False
    if "/platforminputcontexts/" in name or base.startswith("qt6virtualkeyboard"):   # on-screen keyboard
        return False
    if base.startswith(("qt6quick", "qt6qml", "qt6pdf", "qt6webengine", "qt63d", "qt6designer")):
        return False
    return True


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClipForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed; use run_debug / python -m for console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ClipForge",
)
