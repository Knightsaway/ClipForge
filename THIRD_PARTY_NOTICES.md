# Third-party notices

ClipForge itself is licensed under the **MIT License** (see [LICENSE](LICENSE)).
Release builds also ship other components. Redistributors must keep this file and the
`licenses/` folder with the application.

## FFmpeg (GPL) — separate program

Release folders include a **BtbN win64-gpl** build of [FFmpeg](https://ffmpeg.org/) under
`ffmpeg/bin/` (`ffmpeg.exe`, `ffprobe.exe`).

- **License:** GPL (this build links GPL libraries such as x264). See `licenses/ffmpeg/` and
  [FFmpeg legal](https://ffmpeg.org/legal.html).
- **How ClipForge uses it:** only as an **external process** via command-line arguments
  (`subprocess`). ClipForge does **not** link against libav\* / FFmpeg libraries.
- **Source offer:** corresponding sources are available from the pinned BtbN release used to
  fetch the binaries (see `scripts/fetch_ffmpeg.py` and `licenses/ffmpeg/README.txt`).
  Current pin:
  https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2025-10-31-13-40

If you redistribute ClipForge with this FFmpeg build, you must preserve GPL notices and the
source offer for FFmpeg. You may replace the vendored binaries with another FFmpeg build that
provides the same CLI tools (including an LGPL build), as long as capture/encode features you
need remain available.

## Qt for Python (PySide6) — LGPL

UI is built with [Qt for Python / PySide6](https://doc.qt.io/qtforpython/).

- **License:** LGPL v3 (and GPL v3 as offered by The Qt Company for open-source use).
- Frozen builds bundle PySide6 / Qt libraries inside `_internal/`.
- Qt LGPL obligations (including providing object files / relinking information where
  applicable) are satisfied for this project by: (1) shipping ClipForge under MIT with full
  source on GitHub, and (2) using the unmodified PySide6 wheels from PyPI. See Qt’s
  [licensing](https://www.qt.io/licensing/) documentation.

## Other Python dependencies

Pinned in `requirements.lock` / installed into the frozen `_internal` tree, including:

| Component | Typical license | Notes |
|-----------|-----------------|-------|
| NumPy | BSD | Numerical helpers |
| soundcard | BSD-3-Clause | Loopback / mic capture |
| PyInstaller bootloader | GPL with exception / custom | See PyInstaller docs; used only to package |

Optional **not** shipped in the base Release (install separately if desired):

| Component | Notes |
|-----------|--------|
| faster-whisper + NVIDIA CUDA wheels | Large; captions add-on |
| piper-tts | AI caster voices → `%APPDATA%\ClipForge\voices` |
| Ollama | Separate installer; local LLM |

## Trademark / assets

GIPHY, YouTube, TikTok, NVIDIA, etc. are trademarks of their owners. API keys and OAuth
clients are supplied by the user; ClipForge does not embed third-party API secrets.
