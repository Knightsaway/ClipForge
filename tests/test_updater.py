"""Pure unit tests for updater helpers (no GUI / network / FFmpeg)."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from clipforge import updater


def test_vtuple_and_newer():
    assert updater._vtuple("5.2.1") == (5, 2, 1)
    assert updater._vtuple("v5.2") == (5, 2, 0)
    assert updater.newer("5.3.0", "5.2.1")
    assert not updater.newer("5.2.1", "5.2.1")
    assert not updater.newer("5.1.9", "5.2.0")


def test_version_from_text_strips_release_please_comment():
    assert updater._version_from_text('__version__ = "5.2.1"  # x-release-please-version\n') == "5.2.1"
    assert updater._version_from_text("5.3.0\n") == "5.3.0"


def test_github_repo_release_vs_branch():
    assert updater._github_repo("github:Knightsaway/ClipForge") == ("Knightsaway/ClipForge", "")
    assert updater._github_repo("github:Knightsaway/ClipForge@main") == ("Knightsaway/ClipForge", "main")
    assert updater._github_repo("https://github.com/Knightsaway/ClipForge") == ("Knightsaway/ClipForge", "")
    assert updater._github_repo("https://example.com/x.json") == ("", "")


def test_require_https():
    updater.require_https("https://example.com/a.zip")
    with pytest.raises(ValueError):
        updater.require_https("http://example.com/a.zip")
    updater.require_https("http://example.com/a.zip", allow_insecure=True)


def test_unsafe_zip_names():
    assert updater._unsafe_zip_name("../evil.py")
    assert updater._unsafe_zip_name("/etc/passwd")
    assert updater._unsafe_zip_name("C:/Windows/evil.py")
    assert updater._unsafe_zip_name("foo/C:/evil.py")
    assert not updater._unsafe_zip_name("clipforge/__init__.py")


def test_sha256_verify(tmp_path: Path):
    p = tmp_path / "f.zip"
    p.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    updater.verify_sha256(p, digest)
    with pytest.raises(ValueError):
        updater.verify_sha256(p, "0" * 64)


def test_parse_sha256sums():
    text = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899  ClipForge-5.2.1.zip\n"
    assert updater._parse_sha256sums(text, "ClipForge-5.2.1.zip").startswith("aabb")
    assert updater._parse_sha256sums(text, "other.zip") == ""


def test_pick_release_asset_frozen_requires_win64():
    assets = [
        {"name": "source.zip", "browser_download_url": "https://example.com/source.zip"},
        {"name": "SHA256SUMS", "browser_download_url": "https://example.com/SHA256SUMS"},
    ]
    with pytest.raises(ValueError, match="win64"):
        updater.pick_release_asset(assets, frozen=True)
    win = {"name": "ClipForge-9.9.9-win64.zip", "browser_download_url": "https://example.com/w.zip"}
    assets.append(win)
    assert updater.pick_release_asset(assets, frozen=True) is win
    # source installs never take the exe bundle (it wouldn't update the Python code they run)
    assert updater.pick_release_asset(assets, frozen=False)["name"] == "source.zip"
    assert updater.pick_release_asset([win], frozen=False) is None


def test_pick_release_asset_source_fallback_when_not_frozen():
    assets = [{"name": "ClipForge-src.zip", "browser_download_url": "https://example.com/s.zip"}]
    assert updater.pick_release_asset(assets, frozen=False)["name"] == "ClipForge-src.zip"
    assert updater.pick_release_asset([], frozen=False) is None


def test_require_asset_digest_fail_closed():
    digest = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
    win = {"name": "ClipForge-5.2.1-win64.zip"}
    assets = [win, {"name": "SHA256SUMS"}]
    text = f"{digest}  ClipForge-5.2.1-win64.zip\n"
    assert updater.require_asset_digest(assets, win, text) == digest
    with pytest.raises(ValueError, match="Failed to fetch|refusing"):
        updater.require_asset_digest(assets, win, None)
    with pytest.raises(ValueError, match="Could not find"):
        updater.require_asset_digest(assets, win, "deadbeef  other.zip\n")
    with pytest.raises(ValueError, match="missing SHA256SUMS"):
        updater.require_asset_digest([win], win, None)
    # Non-win64 without SUMS: allowed (no digest)
    src = {"name": "ClipForge-src.zip"}
    assert updater.require_asset_digest([src], src, None) == ""


def test_install_zip_and_version(tmp_path: Path):
    src = tmp_path / "src"
    (src / "clipforge").mkdir(parents=True)
    (src / "clipforge" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (src / "requirements.txt").write_text("PySide6>=6.7\n", encoding="utf-8")
    zpath = tmp_path / "update.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, f"ClipForge-9.9.9/{f.relative_to(src)}")
    ver, prefix = updater.zip_version(zpath)
    assert ver == "9.9.9"
    assert prefix.endswith("ClipForge-9.9.9/")

    target = tmp_path / "install"
    target.mkdir()
    installed = updater.install_zip(zpath, target=target)
    assert installed == "9.9.9"
    assert (target / "clipforge" / "__init__.py").read_text(encoding="utf-8").startswith("__version__")


def test_install_rejects_zip_slip(tmp_path: Path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("pkg/clipforge/__init__.py", '__version__ = "1.0.0"\n')
        z.writestr("pkg/../outside.txt", "nope")
        # drive-letter style member
        info = zipfile.ZipInfo("pkg/C:/Windows/evil.txt")
        z.writestr(info, "nope")
    target = tmp_path / "install"
    target.mkdir()
    updater.install_zip(zpath, target=target)
    assert (target / "clipforge" / "__init__.py").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_frozen_zip_version_and_install(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    zpath = tmp_path / "win64.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("ClipForge-9.9.9-win64/VERSION", "9.9.9\n")
        z.writestr("ClipForge-9.9.9-win64/ClipForge.exe", b"MZ-fake")
        z.writestr("ClipForge-9.9.9-win64/ffmpeg/bin/ffmpeg.exe", b"fake")
        z.writestr("ClipForge-9.9.9-win64/THIRD_PARTY_NOTICES.md", "# notices\n")
    ver, prefix = updater.zip_version(zpath)
    assert ver == "9.9.9"
    assert prefix.endswith("ClipForge-9.9.9-win64/")
    target = tmp_path / "install"
    target.mkdir()
    assert updater.install_zip(zpath, target=target) == "9.9.9"
    assert (target / "ClipForge.exe").exists()
    assert (target / "ffmpeg" / "bin" / "ffmpeg.exe").exists()
    assert (target / "VERSION").read_text(encoding="utf-8").startswith("9.9.9")


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return path


def test_install_refuses_mismatched_payload(tmp_path, monkeypatch):
    exe_zip = _zip(tmp_path / "w.zip", {"ClipForge-9.9.9-win64/ClipForge.exe": "x",
                                        "ClipForge-9.9.9-win64/VERSION": "9.9.9"})
    src_zip = _zip(tmp_path / "s.zip", {"ClipForge-main/clipforge/__init__.py": '__version__ = "9.9.9"\n'})
    target = tmp_path / "app"
    target.mkdir()
    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    monkeypatch.setattr(updater, "sync_requirements", lambda *a, **k: None)
    with pytest.raises(ValueError, match="source"):
        updater.install_zip(exe_zip, target)
    assert updater.install_zip(src_zip, target) == "9.9.9"
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    with pytest.raises(ValueError, match="win64"):
        updater.install_zip(src_zip, target)
    assert updater.install_zip(exe_zip, target) == "9.9.9"
