"""Updating ClipForge from inside the app.

* From a zip: the ClipForge zip you were sent (or downloaded) — pick it in Settings → Updates or drop it on the
  window. Files are copied over this installation; the .venv (Python + packages), your clips, settings and
  drafts are never touched. Then ClipForge restarts.
* From the internet (optional): put the address of a version.json in Settings → Updates, e.g. a GitHub release:
  {"version": "0.2.0", "url": "https://…/ClipForge.zip", "notes": "What's new"}.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import __version__

APP_ROOT = Path(__file__).resolve().parent.parent       # the folder with setup.bat and clipforge/
SKIP = {".venv", "__pycache__", ".git"}


def _vtuple(v: str) -> tuple:
    out = []
    for part in str(v).strip().lstrip("v").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def newer(a: str, b: str) -> bool:
    """True if version a is newer than b."""
    return _vtuple(a) > _vtuple(b)


def zip_version(zip_path: str | Path) -> tuple[str, str]:
    """(version inside the zip, the folder prefix to copy from). Raises if it isn't a ClipForge zip."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        init = next((n for n in names if n.replace("\\", "/").endswith("clipforge/__init__.py")), None)
        if not init:
            raise ValueError("That zip doesn't contain ClipForge.")
        prefix = init.replace("\\", "/")[: -len("clipforge/__init__.py")]
        txt = z.read(init).decode("utf-8", "replace")
    ver = "?"
    for line in txt.splitlines():
        if line.startswith("__version__"):
            ver = line.split("=", 1)[1].strip().strip("\"'")
    return ver, prefix


def install_zip(zip_path: str | Path, target: Path = APP_ROOT) -> str:
    """Copies the new version over this one. Returns the installed version."""
    ver, prefix = zip_version(zip_path)
    tmp = Path(tempfile.mkdtemp(prefix="clipforge_update_"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename.replace("\\", "/")
                if not name.startswith(prefix) or info.is_dir():
                    continue
                rel = name[len(prefix):]
                if not rel or any(part in SKIP for part in rel.split("/")) or ".." in rel.split("/"):
                    continue
                dst = tmp / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
        if not (tmp / "clipforge" / "__init__.py").exists():
            raise ValueError("The update looks incomplete.")
        for f in tmp.rglob("*"):
            if f.is_file():
                dst = target / f.relative_to(tmp)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ver


def _github_repo(url: str) -> str:
    """'github:you/ClipForge' or 'https://github.com/you/ClipForge' → 'you/ClipForge' ('' if not GitHub)."""
    u = url.strip()
    if u.lower().startswith("github:"):
        return u[7:].strip().strip("/")
    if "github.com/" in u and not u.endswith(".json"):
        parts = u.split("github.com/", 1)[1].strip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return ""


def _from_github(repo: str) -> dict:
    """The latest GitHub release: its tag is the version, its .zip asset (or the source zip) is the download."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": "ClipForge/" + __version__,
                                          "Accept": "application/vnd.github+json"})
    rel = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".zip")), None)
    return {"version": str(rel.get("tag_name", "")).lstrip("v"),
            "url": asset["browser_download_url"] if asset else rel.get("zipball_url", ""),
            "notes": rel.get("body", "")}


def _from_github_branch(repo: str, branch: str = "main") -> dict:
    """The newest code on the repo's main branch (no release needed): version from clipforge/__init__.py."""
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/{branch}/clipforge/__init__.py",
                                 headers={"User-Agent": "ClipForge/" + __version__})
    txt = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
    ver = next((ln.split("=", 1)[1].strip().strip("\"'") for ln in txt.splitlines() if ln.startswith("__version__")), "")
    return {"version": ver, "url": f"https://github.com/{repo}/archive/refs/heads/{branch}.zip", "notes": ""}


def check(url: str) -> dict | None:
    """Reads a version.json — or the latest release of a GitHub repo ('github:you/ClipForge') — and returns it
    if it's newer than this version."""
    if not url:
        return None
    repo = _github_repo(url)
    if repo:
        found = []
        for fn in (_from_github, _from_github_branch):      # a release if there is one, else the main branch
            try:
                d = fn(repo)
                if d.get("version") and d.get("url"):
                    found.append(d)
            except Exception:
                continue
        best = max(found, key=lambda d: _vtuple(d["version"]), default=None)
        return best if best and newer(best["version"], __version__) else None
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge/" + __version__})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    if data.get("version") and data.get("url") and newer(data["version"], __version__):
        return data
    return None


def download(url: str, progress=None) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge/" + __version__})
    out = Path(tempfile.gettempdir()) / "ClipForge-update.zip"
    with urllib.request.urlopen(req, timeout=30) as r, open(out, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / total)
    return out
