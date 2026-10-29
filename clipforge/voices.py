"""Natural caster voice (Kokoro): an optional add-on, downloaded only if you ask for it.

ClipForge itself stays small. "Get the natural voice" fetches Kokoro's Python packages (pinned wheels
from PyPI, ~25 MB) and its voice model (~120 MB from the kokoro-onnx project) into
%APPDATA%\\ClipForge\\addons\\kokoro. Every file is checked against a SHA-256 written here.
It runs on your PC, offline, after that.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from .config import app_data_dir

ADDON = app_data_dir() / "addons" / "kokoro"
SITE = ADDON / "site"
MODEL = ADDON / "kokoro-v1.0.int8.onnx"
VOICES_BIN = ADDON / "voices-v1.0.bin"
DEFAULT = "am_puck"
# English voices that suit a hyped caster first, then the rest of the male voices
VOICES = ["am_puck", "am_fenrir", "am_michael", "am_eric", "am_liam", "am_adam", "am_onyx", "am_echo",
          "bm_george", "bm_lewis", "bm_daniel", "bm_fable"]

_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = [
    (_MODEL_URL + "kokoro-v1.0.int8.onnx", "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    (_MODEL_URL + "voices-v1.0.bin", "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
]
# kokoro-onnx 0.6.1 and everything it needs, for 64-bit Windows + Python 3.12 (numpy is already in ClipForge)
WHEELS = [
    ("https://files.pythonhosted.org/packages/64/b4/17d4b0b2a2dc85a6df63d1157e028ed19f90d4cd97c36717afef2bc2f395/attrs-26.1.0-py3-none-any.whl",
     "c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309"),  # 65 KB
    ("https://files.pythonhosted.org/packages/88/39/799be3f2f0f38cc727ee3b4f1445fe6d5e4133064ec2e4115069418a5bb6/cloudpickle-3.1.2-py3-none-any.whl",
     "9acb47f6afd73f60dc1df93bb801b472f05ff42fa6c84167d25cb206be1fbf4a"),  # 21 KB
    ("https://files.pythonhosted.org/packages/da/90/022c79d6e5e6f843268c10b84d4a021ee3afba0621d3c176d3ff2024bfc8/dlinfo-2.0.0-py3-none-any.whl",
     "b32cc18e3ea67c0ca9ca409e5b41eed863bd1363dbc9dd3de90fedf11b61e7bc"),  # 3 KB
    ("https://files.pythonhosted.org/packages/9d/ed/a3d872fbad4f3a3f3db0e8c31768ab14e77cd77306de16b8b20b1e1df7ea/espeakng_loader-0.2.4-py3-none-win_amd64.whl",
     "41f1e08ac9deda2efd1ea9de0b81dab9f5ae3c4b24284f76533d0a7b1dd7abd7"),  # 9216 KB
    ("https://files.pythonhosted.org/packages/e8/2d/d2a548598be01649e2d46231d151a6c56d10b964d94043a335ae56ea2d92/flatbuffers-25.12.19-py2.py3-none-any.whl",
     "7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4"),  # 26 KB
    ("https://files.pythonhosted.org/packages/18/53/84099323c2ec4be98d935f63c033ac4151ee83836ca1050ede3b3aadf155/joblib-1.6.0-py3-none-any.whl",
     "3dbbf9f6e4b592a2357b854608e980fe6390d131d7a82f011a377ef2ebef7aba"),  # 298 KB
    ("https://files.pythonhosted.org/packages/60/e1/a27e5a70a525a5ee1fd5357596f07b724d02ff317f134e86cb6e3d9db968/kokoro_onnx-0.6.1-py3-none-any.whl",
     "50c8de4950d601df41428ee5462a48c8a78bef441bf671f2492e070ef44d8a32"),  # 25 KB
    ("https://files.pythonhosted.org/packages/45/b2/793b4051d8dc6acb379ead9b72050f0ffa283ca49a6904add2e218b83e2e/onnxruntime-1.31.1-cp312-cp312-win_amd64.whl",
     "ae8a9a351efda685cc59a66226cdefaa37a126c1d66872ca23a2de9425326130"),  # 14539 KB
    ("https://files.pythonhosted.org/packages/63/34/ba1c580383c9eada3711951fef0795c80b829a078d72188184bcab9dd527/packaging-26.3-py3-none-any.whl",
     "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"),  # 126 KB
    ("https://files.pythonhosted.org/packages/37/41/09bafd0ccf4f4ddd1530c9f9d10e75a24056e10e120b9daa97f691fcabc3/phonemizer-3.4.0-py3-none-any.whl",
     "5ff1215d0efa3606dd1b92449f6d71b5c1741efcc84c6d40c17bfaf64f6ded5f"),  # 105 KB
    ("https://files.pythonhosted.org/packages/8a/55/b77bda4e5e5f5971fb51b07663694690e9afdb9402136c16a522bd621cad/protobuf-7.36.2-cp310-abi3-win_amd64.whl",
     "a300819d441e078a5608c0d3c709796bb548136058fda017ae51d425b44fd353"),  # 445 KB
    ("https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl",
     "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),  # 44 KB
]
SIZE_MB = 145

_lock = threading.Lock()
_engine = None


def supported() -> bool:
    return sys.platform == "win32" and sys.version_info[:2] == (3, 12)


def installed() -> bool:
    return (SITE / "kokoro_onnx").is_dir() and MODEL.exists() and VOICES_BIN.exists()


def _download(url: str, sha: str, dst: Path, progress=None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge"})
    h = hashlib.sha256()
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / total)
    if h.hexdigest() != sha:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{dst.name} failed its checksum — not installed.")
    tmp.replace(dst)


def _unpack_wheel(whl: Path, site: Path) -> None:
    root = site.resolve()
    with zipfile.ZipFile(whl) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.startswith("/") or ".." in name.split("/"):
                continue
            dst = (site / name).resolve()
            if root not in dst.parents:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)


def install(progress=None) -> None:
    """Downloads and unpacks the add-on. progress(fraction, message). Raises on failure."""
    if not supported():
        raise RuntimeError("The natural voice add-on is for the 64-bit Windows version of ClipForge.")
    prog = progress or (lambda f, m: None)
    ADDON.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="kokoro_", dir=ADDON))
    new_site = ADDON / "site_new"
    shutil.rmtree(new_site, ignore_errors=True)
    try:
        jobs = [(u, s, work / u.rsplit("/", 1)[1]) for u, s in WHEELS] + \
               [(u, s, ADDON / u.rsplit("/", 1)[1]) for u, s in MODEL_FILES]
        for k, (url, sha, dst) in enumerate(jobs):
            if dst.parent == ADDON and dst.exists() and _sha(dst) == sha:
                continue                                     # model already here from an earlier try
            label = dst.name.split("-")[0]
            _download(url, sha, dst, lambda f, k=k, label=label:
                      prog((k + f) / len(jobs) * 0.95, f"⬇ Natural voice: {label}… {int(f * 100)}%"))
        prog(0.96, "Unpacking…")
        for u, _ in WHEELS:
            _unpack_wheel(work / u.rsplit("/", 1)[1], new_site)
        shutil.rmtree(SITE, ignore_errors=True)
        new_site.rename(SITE)
        prog(1.0, "✔ Natural voice installed")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(new_site, ignore_errors=True)


def remove() -> None:
    global _engine
    _engine = None
    shutil.rmtree(ADDON, ignore_errors=True)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load():
    global _engine
    with _lock:
        if _engine is None:
            site = str(SITE)
            if site not in sys.path:
                sys.path.append(site)          # after ClipForge's own packages, so ours always win
            from kokoro_onnx import Kokoro
            _engine = Kokoro(str(MODEL), str(VOICES_BIN))
        return _engine


def speak(text: str, out: Path, voice: str = DEFAULT, speed: float = 1.12) -> bool:
    """Writes `text` spoken by Kokoro to a 16-bit WAV. False if the add-on isn't there or fails."""
    if not installed():
        return False
    try:
        import wave

        import numpy as np
        samples, rate = _load().create(text, voice=voice if voice in VOICES else DEFAULT, speed=speed,
                                       lang="en-us")
        x = np.clip(np.asarray(samples, dtype=np.float32), -1, 1)
        if len(x) < rate * 0.2 or float(np.abs(x).max()) < 0.01:
            return False                        # came out empty: let another engine try
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(rate))
            w.writeframes((x * 32767).astype(np.int16).tobytes())
        return True
    except Exception as e:
        print("kokoro:", e)
        return False
