"""“Report a bug”: opens a GitHub issue with the details a fix needs already filled in.

The person sees everything before it's sent (it opens in their browser, editable), and paths
are scrubbed so their Windows user name doesn't end up in a public issue.
"""
from __future__ import annotations

import os
import platform
import re
import urllib.parse
from pathlib import Path

from . import __version__

MAX_URL = 7500           # GitHub (and browsers) cut very long URLs; keep well under ~8 KB
DEFAULT_REPO = "Knightsaway/ClipForge"


def repo_from(update_url: str) -> str:
    u = (update_url or "").strip()
    if u.lower().startswith("github:"):
        return u[7:].split("@", 1)[0].strip("/") or DEFAULT_REPO
    if "github.com/" in u:
        parts = u.split("github.com/", 1)[1].strip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return DEFAULT_REPO


def scrub(text: str) -> str:
    """Hide the Windows user name: C:\\Users\\Dani\\… → %USERPROFILE%\\…, and any other C:\\Users\\<name>."""
    home = os.path.expanduser("~")
    if home and len(home) > 3:
        for variant in {home, home.replace("\\", "/")}:
            text = re.sub(re.escape(variant), "%USERPROFILE%", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"']+", r"\1<user>", text)
    text = re.sub(r"(?i)(/home/)[^/\s\"']+", r"\1<user>", text)
    return text


def tail(path: Path, lines: int = 30) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def pc_summary(extra: str = "") -> str:
    bits = [platform.platform(), f"{os.cpu_count()} threads"]
    if extra:
        bits.append(extra)
    return " | ".join(bits)


def issue_url(repo: str, what: str, pc: str, logs: str, title: str = "") -> str:
    """URL for the bug-report issue form, trimming the log until the URL fits."""
    logs = scrub(logs).strip()
    pc = scrub(pc)
    while True:
        q = urllib.parse.urlencode({
            "template": "bug_report.yml",
            "title": title or "Bug: ",
            "labels": "bug",
            "version": __version__,
            "pc": pc,
            "what": what,
            "logs": logs,
        })
        url = f"https://github.com/{repo}/issues/new?{q}"
        if len(url) <= MAX_URL or not logs:
            return url
        cut = logs.splitlines()
        logs = "\n".join(["(older lines cut to fit)"] + cut[len(cut) // 3 + 1:]) if len(cut) > 3 else ""
