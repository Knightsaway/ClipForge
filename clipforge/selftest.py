"""`ClipForge.exe --selftest <report file>`: proves a (slimmed) build still has everything it needs.

Checks every UI module imports, Qt can make a window, play video and load GIFs, and the bundled
FFmpeg runs with the screen grabber and GPU encoders ClipForge records with. Writes a report and
exits 0 when all is well, 1 otherwise. Used by the Windows build check in CI.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback
from pathlib import Path


def run(report: Path) -> int:
    lines, ok = [], True
    report.write_text("STARTED\n", encoding="utf-8")

    def flush(status="RUNNING"):
        report.write_text("\n".join([status] + lines) + "\n", encoding="utf-8")

    def check(name, fn):
        nonlocal ok
        lines.append(f"...   {name}")             # if it hangs, the report shows where
        flush()
        try:
            lines[-1] = f"ok    {name}: {fn() or ''}"
        except Exception:
            ok = False
            lines[-1] = f"FAIL  {name}:\n{traceback.format_exc()}"
        flush()

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])

    def modules():
        import clipforge
        names = [m.name for m in pkgutil.walk_packages(clipforge.__path__, "clipforge.")
                 if m.name not in ("clipforge.__main__", "clipforge.selftest")]
        for n in names:
            importlib.import_module(n)
        return f"{len(names)} modules"

    def window():
        from PySide6.QtWidgets import QLabel
        w = QLabel("ClipForge")
        w.show()
        app.processEvents()
        w.close()
        return app.platformName()

    def media():
        """Makes a 2-second test clip with the bundled FFmpeg and plays it, like the Studio preview does."""
        import tempfile
        import time
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from . import ff
        clip = Path(tempfile.mkdtemp(prefix="clipforge_selftest_")) / "test.mp4"
        r = ff.run([ff.ffmpeg_bin(), "-y", "-hide_banner", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(clip)], timeout=60)
        if r.returncode != 0 or not clip.exists():
            raise RuntimeError("couldn't make the test clip: " + r.stderr[-300:])
        pl, out, vw = QMediaPlayer(), QAudioOutput(), QVideoWidget()
        pl.setAudioOutput(out)
        pl.setVideoOutput(vw)
        out.setVolume(0.0)
        vw.resize(320, 180)
        vw.show()
        pl.setSource(QUrl.fromLocalFile(str(clip)))
        pl.play()
        end = time.time() + 15
        while time.time() < end and not (pl.duration() > 0 and pl.position() > 300):
            app.processEvents()
            if pl.error() != QMediaPlayer.Error.NoError:
                raise RuntimeError("player error: " + pl.errorString())
            time.sleep(0.02)
        pl.stop()
        vw.close()
        if pl.duration() <= 0:
            raise RuntimeError(f"video never loaded (status {pl.mediaStatus()})")
        return f"played test clip ({pl.duration()} ms)"

    def images():
        from PySide6.QtGui import QImageReader
        fmts = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        missing = {"png", "jpg", "gif"} - fmts
        if missing:
            raise RuntimeError(f"missing image formats: {missing}")
        return ", ".join(sorted(fmts))

    def ffmpeg():
        from . import ff
        out = []
        for name in ("ffmpeg", "ffprobe"):
            r = ff.run([ff.ffmpeg_bin(name), "-hide_banner", "-version"], timeout=30)
            if r.returncode != 0:
                raise RuntimeError(f"{name} exited {r.returncode}")
            out.append(name)
        filters = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-filters"], timeout=30).stdout
        encoders = ff.run([ff.ffmpeg_bin(), "-hide_banner", "-encoders"], timeout=30).stdout
        text = (filters if isinstance(filters, str) else filters.decode("utf-8", "replace")) + \
               (encoders if isinstance(encoders, str) else encoders.decode("utf-8", "replace"))
        need = ["ddagrab", "h264_nvenc", "hevc_nvenc", "h264_amf", "h264_qsv", "libx264", "aac"]
        missing = [n for n in need if n not in text]
        if missing:
            raise RuntimeError(f"ffmpeg lacks {missing}")
        return " + ".join(out) + " with " + ", ".join(need)

    def voice():
        """Only when the natural-voice add-on is installed: it must load inside the exe and speak."""
        import tempfile

        from . import voices
        out = Path(tempfile.mkdtemp(prefix="clipforge_voice_")) / "line.wav"
        if not voices.speak("What a play!", out):
            raise RuntimeError("the natural voice add-on is installed but didn't speak")
        from . import ff
        return f"Kokoro spoke {ff.duration(out):.1f} s"

    def render():
        """A tiny Short through the real renderer with the bundled FFmpeg: loop ending, a crossfade, a flash,
        text and a hook — the filters most likely to differ between FFmpeg versions."""
        import tempfile

        from . import editor, ff
        from .editor import Piece, RenderOptions
        work = Path(tempfile.mkdtemp(prefix="clipforge_render_"))
        srcs = []
        for k, colour in enumerate(("red", "blue")):
            src = work / f"src{k}.mp4"
            r = ff.run([ff.ffmpeg_bin(), "-y", "-hide_banner", "-f", "lavfi", "-i", f"color=c={colour}:size=640x360:rate=60",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(src)], timeout=60)
            if r.returncode != 0:
                raise RuntimeError("couldn't make a test clip: " + r.stderr[-300:])
            srcs.append(str(src))
        pieces = [Piece(srcs[0], 0, 3), Piece(srcs[1], 2, 4, effects=[{"type": "transition", "name": "crossfade", "t": 2}]),
                  Piece(srcs[0], 3, 5, effects=[{"type": "transition", "name": "flash", "t": 3}])]
        o = RenderOptions(hook_text="SELF TEST")
        o.extra["loop"] = True
        o.extra["texts"] = [{"t": 1, "dur": 1, "text": "OK", "cx": 0.5, "cy": 0.5}]
        out = editor.render(pieces, "short", o, work / "short.mp4")
        d = ff.duration(out)
        if not d or abs(d - 7.05) > 0.3:
            raise RuntimeError(f"rendered video is {d} s long, expected ~7.05")
        return f"9:16 Short with loop + crossfade rendered ({d:.2f} s)"

    checks = [("modules", modules), ("window", window), ("images", images), ("ffmpeg", ffmpeg), ("media", media),
              ("render", render)]
    from . import voices as _v
    if _v.installed():
        checks.append(("voice", voice))
    for name, fn in checks:
        check(name, fn)
    flush("PASS" if ok else "FAIL")
    return 0 if ok else 1
