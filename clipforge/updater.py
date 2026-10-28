"""Updating ClipForge from inside the app.

* From a zip: release onedir (`ClipForge.exe` + `_internal` + vendored `ffmpeg`) or a source tree zip.
  User data in AppData / Videos is never touched. Source installs also sync `.venv` from requirements.
* From the internet: GitHub Releases via `github:owner/ClipForge`, or a HTTPS version.json.
  Use `github:owner/ClipForge@main` only for bleeding-edge branch updates.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import __version__
from .paths import app_root, is_frozen

APP_ROOT = app_root()
SKIP = {".venv", "__pycache__", ".git"}
_DRIVE = re.compile(r"^[A-Za-z]:")


def _vtuple(v: str) -> tuple:
    out = []
    for part in str(v).strip().lstrip("v").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def newer(a: str, b: str) -> bool:
    """True if version a is newer than b."""
    return _vtuple(a) > _vtuple(b)


def _unsafe_zip_name(name: str) -> bool:
    """Reject zip-slip and absolute / drive-letter members."""
    n = name.replace("\\", "/")
    if not n or n.startswith("/") or n.startswith("//"):
        return True
    if ".." in n.split("/"):
        return True
    if _DRIVE.match(n):
        return True
    for part in n.split("/"):
        if _DRIVE.match(part):
            return True
    return False


def _version_from_text(txt: str) -> str:
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("__version__"):
            rhs = line.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'")
            return rhs
        if line and not line.startswith("#") and re.fullmatch(r"v?\d+(\.\d+)*", line):
            return line.lstrip("v")
    return ""


def zip_version(zip_path: str | Path) -> tuple[str, str]:
    """(version inside the zip, the folder prefix to copy from). Raises if it isn't a ClipForge zip.

    Accepts source trees (`clipforge/__init__.py`) and frozen releases (`ClipForge.exe` + `VERSION`).
    """
    with zipfile.ZipFile(zip_path) as z:
        names = [n.replace("\\", "/") for n in z.namelist()]
        init = next((n for n in names if n.endswith("clipforge/__init__.py")), None)
        version_file = next((n for n in names if n.endswith("/VERSION") or n == "VERSION"), None)
        exe = next((n for n in names if n.endswith("ClipForge.exe")), None)

        if init:
            prefix = init[: -len("clipforge/__init__.py")]
            ver = _version_from_text(z.read(init).decode("utf-8", "replace")) or "?"
            return ver, prefix

        if exe or version_file:
            # Frozen onedir: prefix is the folder that contains ClipForge.exe (or VERSION).
            anchor = exe or version_file
            prefix = anchor[: anchor.rfind("/") + 1] if "/" in anchor else ""
            ver = "?"
            if version_file:
                ver = _version_from_text(z.read(version_file).decode("utf-8", "replace")) or "?"
            if ver == "?" and init:
                ver = _version_from_text(z.read(init).decode("utf-8", "replace")) or "?"
            return ver, prefix

    raise ValueError("That zip doesn't contain ClipForge.")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: str | Path, expected: str) -> None:
    """Raises ValueError if the file digest does not match expected (hex, optional sha256: prefix)."""
    exp = expected.strip().lower().removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", exp):
        raise ValueError("Invalid SHA256 digest in update metadata.")
    got = file_sha256(path)
    if not hmac_compare_hex(got, exp):
        raise ValueError("Update zip failed SHA256 check — refusing to install.")


def hmac_compare_hex(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


def require_https(url: str, *, allow_insecure: bool = False) -> None:
    u = url.strip()
    if allow_insecure:
        return
    if not u.lower().startswith("https://"):
        raise ValueError("Update URLs must use HTTPS.")


def install_zip(zip_path: str | Path, target: Path | None = None, expected_sha256: str = "") -> str:
    """Copies the new version over this one. Returns the installed version."""
    if expected_sha256:
        verify_sha256(zip_path, expected_sha256)
    ver, prefix = zip_version(zip_path)
    target = Path(target) if target is not None else app_root()
    tmp = Path(tempfile.mkdtemp(prefix="clipforge_update_"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename.replace("\\", "/")
                if not name.startswith(prefix) or info.is_dir():
                    continue
                rel = name[len(prefix):]
                if not rel or any(part in SKIP for part in rel.split("/")) or _unsafe_zip_name(rel):
                    continue
                dst = tmp / rel
                try:
                    dst.resolve().relative_to(tmp.resolve())
                except ValueError:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
        frozen_payload = (tmp / "ClipForge.exe").exists() or (tmp / "VERSION").exists()
        source_payload = (tmp / "clipforge" / "__init__.py").exists()
        if not frozen_payload and not source_payload:
            raise ValueError("The update looks incomplete.")
        for f in tmp.rglob("*"):
            if f.is_file():
                dst = target / f.relative_to(tmp)
                try:
                    dst.resolve().relative_to(target.resolve())
                except ValueError:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
        if source_payload and not is_frozen():
            sync_requirements(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ver


def sync_requirements(target: Path | None = None) -> None:
    """After a source overlay, keep .venv packages in sync with requirements.txt when present."""
    if is_frozen():
        return
    target = Path(target) if target is not None else app_root()
    req = target / "requirements.txt"
    py = target / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if not req.exists() or not py.exists():
        return
    try:
        subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(req)],
            capture_output=True, timeout=600, check=False,
        )
    except Exception as e:
        print("post-update pip sync:", e)


def _github_repo(url: str) -> tuple[str, str]:
    """Return (owner/repo, channel) where channel is '' (releases) or 'main' (branch)."""
    u = url.strip()
    channel = ""
    if "@" in u and not u.startswith("http"):
        # github:owner/repo@main
        base, _, ch = u.partition("@")
        u, channel = base, ch.strip() or "main"
    if u.lower().startswith("github:"):
        return u[7:].strip().strip("/"), channel
    if "github.com/" in u and not u.endswith(".json"):
        parts = u.split("github.com/", 1)[1].strip("/").split("/")
        if len(parts) >= 2:
            repo = f"{parts[0]}/{parts[1]}"
            if len(parts) >= 4 and parts[2] == "tree":
                channel = parts[3]
            return repo, channel
    return "", ""


def _parse_sha256sums(text: str, asset_name: str) -> str:
    """Parse SHA256SUMS / shasum lines for asset_name; return hex digest or ''."""
    want = asset_name.lower()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # "hex  filename" or "hex *filename"
        parts = line.replace("*", " ").split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            if parts[-1].replace("\\", "/").split("/")[-1].lower() == want:
                return parts[0].lower()
    return ""


def _zip_assets(assets: list) -> list:
    return [a for a in assets if a.get("name", "").lower().endswith(".zip")
            and "sha256" not in a.get("name", "").lower()]


def _sums_asset(assets: list):
    return next((a for a in assets if a.get("name", "").upper() in
                 ("SHA256SUMS", "SHA256SUMS.TXT") or a.get("name", "").lower().endswith(".sha256")), None)


def pick_release_asset(assets: list, *, frozen: bool | None = None) -> dict | None:
    """Choose a release zip. Frozen installs require a *win64* asset (never source zipball)."""
    if frozen is None:
        frozen = is_frozen()
    zips = _zip_assets(assets)
    win64 = next((a for a in zips if "win64" in a.get("name", "").lower()), None)
    if frozen:
        if win64 is None:
            raise ValueError("No ClipForge-*-win64.zip on this release — frozen installs cannot use source zips.")
        return win64
    return win64 if win64 is not None else (zips[0] if zips else None)


def digest_from_sums_text(text: str, asset_name: str, sums_name: str) -> str:
    """Parse a digest for asset_name; raise if the SUMS file cannot yield one."""
    sha = _parse_sha256sums(text, asset_name)
    if not sha and sums_name.lower().endswith(".sha256"):
        sha = text.strip().split()[0].lower() if text.strip() else ""
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha.lower()):
        raise ValueError(f"Could not find a SHA256 digest for {asset_name} in {sums_name}.")
    return sha.lower()


def require_asset_digest(assets: list, asset: dict, sums_text: str | None) -> str:
    """Win64 (and any release that publishes SUMS) must verify; fail closed on missing/bad digests.

    * If a SHA256SUMS / *.sha256 asset exists, its body must parse for this zip (sums_text required).
    * Win64 bundles always require a digest (our release pipeline always uploads SHA256SUMS).
    * Other zips with no SUMS asset may return '' (legacy / source-only releases).
    """
    sums = _sums_asset(assets)
    name = asset.get("name", "")
    is_win64 = "win64" in name.lower()
    if sums is None:
        if is_win64:
            raise ValueError("win64 release is missing SHA256SUMS — refusing update.")
        return ""
    if sums_text is None:
        raise ValueError(f"Failed to fetch {sums.get('name', 'SHA256SUMS')} — refusing update.")
    return digest_from_sums_text(sums_text, name, sums.get("name", "SHA256SUMS"))


def _from_github(repo: str) -> dict:
    """The latest GitHub release: tag = version; prefer *win64* zip + SHA256SUMS."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"User-Agent": "ClipForge/" + __version__,
                                          "Accept": "application/vnd.github+json"})
    rel = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    assets = rel.get("assets") or []
    asset = pick_release_asset(assets)
    sha = ""
    url = ""
    if asset:
        url = asset["browser_download_url"]
        sums = _sums_asset(assets)
        sums_text = None
        if sums is not None:
            try:
                sreq = urllib.request.Request(sums["browser_download_url"],
                                              headers={"User-Agent": "ClipForge/" + __version__})
                sums_text = urllib.request.urlopen(sreq, timeout=10).read().decode("utf-8", "replace")
            except Exception as e:
                raise ValueError(f"Failed to fetch release digests: {e}") from e
        sha = require_asset_digest(assets, asset, sums_text)
    elif not is_frozen():
        # Source checkout only: zipball fallback when the release has no zip assets yet.
        url = rel.get("zipball_url", "")
    return {"version": str(rel.get("tag_name", "")).lstrip("v"),
            "url": url,
            "sha256": sha,
            "notes": rel.get("body", "")}


def _from_github_branch(repo: str, branch: str = "main") -> dict:
    """The newest code on a branch (bleeding edge): version from clipforge/__init__.py. No digest."""
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/{branch}/clipforge/__init__.py",
                                 headers={"User-Agent": "ClipForge/" + __version__})
    txt = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
    ver = _version_from_text(txt)
    return {"version": ver, "url": f"https://github.com/{repo}/archive/refs/heads/{branch}.zip",
            "sha256": "", "notes": f"branch {branch}"}


def check(url: str, *, allow_insecure: bool = False) -> dict | None:
    """Reads a version.json — or a GitHub repo ('github:you/ClipForge') — and returns it
    if it's newer than this version. Releases are preferred; use @main for branch updates."""
    if not url:
        return None
    repo, channel = _github_repo(url)
    if repo:
        if channel:
            try:
                d = _from_github_branch(repo, channel)
            except Exception:
                return None
            return d if d.get("version") and d.get("url") and newer(d["version"], __version__) else None
        try:
            d = _from_github(repo)
        except ValueError:
            raise  # digest / frozen-asset policy — surface to the UI
        except Exception:
            d = None
        if d and d.get("version") and d.get("url") and newer(d["version"], __version__):
            return d
        return None
    require_https(url, allow_insecure=allow_insecure)
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge/" + __version__})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
    if data.get("version") and data.get("url") and newer(data["version"], __version__):
        require_https(data["url"], allow_insecure=allow_insecure)
        # version.json: if a sha256 field is present it must be usable (fail closed).
        if "sha256" in data and data.get("sha256"):
            exp = str(data["sha256"]).strip().lower().removeprefix("sha256:")
            if not re.fullmatch(r"[0-9a-f]{64}", exp):
                raise ValueError("Invalid sha256 in version.json — refusing update.")
        elif "sha256" in data and not data.get("sha256"):
            raise ValueError("version.json lists sha256 but it is empty — refusing update.")
        return data
    return None


def download(url: str, progress=None, expected_sha256: str = "", *, allow_insecure: bool = False) -> Path:
    require_https(url, allow_insecure=allow_insecure)
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
    if expected_sha256:
        verify_sha256(out, expected_sha256)
    return out
