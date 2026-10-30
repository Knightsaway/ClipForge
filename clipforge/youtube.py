"""YouTube upload + scheduling (YouTube Data API v3), with no extra libraries.

One-time setup (5 minutes, free): create a Google Cloud project, enable "YouTube Data API v3",
create an OAuth client of type "Desktop app", download its JSON and pick it in ClipForge
Settings → Uploads. Then "Connect YouTube" once in the browser.

Scheduling: videos are uploaded straight away as private with a publish time (publishAt);
YouTube itself makes them public at that moment, so your PC doesn't need to be on.
The login token is stored encrypted with Windows DPAPI (only your Windows account can read it).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import IS_WINDOWS, app_data_dir

# full "youtube" scope: upload + edit titles/thumbnails (A/B tests) + read stats and trends
SCOPES = "https://www.googleapis.com/auth/youtube"
TOKEN_FILE = app_data_dir() / "youtube_token.bin"
QUEUE_FILE = app_data_dir() / "upload_queue.json"
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB


# ------------------------------------------------------------------ secure storage
def _dpapi(data: bytes, encrypt: bool) -> bytes:
    if not IS_WINDOWS:
        raise OSError("YouTube tokens require Windows DPAPI; refusing plaintext storage.")
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _save_token(tok: dict):
    TOKEN_FILE.write_bytes(_dpapi(json.dumps(tok).encode(), True))


def _load_token() -> dict | None:
    if not IS_WINDOWS:
        return None
    try:
        return json.loads(_dpapi(TOKEN_FILE.read_bytes(), False))
    except Exception:
        return None


def disconnect():
    TOKEN_FILE.unlink(missing_ok=True)


# ------------------------------------------------------------------ http
def _req(url, data=None, headers=None, method=None, timeout=60):
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _client(secret_path: str) -> dict:
    data = json.loads(Path(secret_path).read_text(encoding="utf-8"))
    c = data.get("installed") or data.get("web")
    if not c:
        raise ValueError("That JSON isn't an OAuth client file (create a 'Desktop app' OAuth client).")
    return c


# ------------------------------------------------------------------ auth
def connect(secret_path: str, timeout=300) -> str:
    """Opens the browser for Google sign-in (loopback + PKCE). Returns the channel name."""
    c = _client(secret_path)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in result
            self.wfile.write(("<html><body style='font-family:sans-serif;background:#161b21;color:#ddd;"
                              "text-align:center;padding-top:80px'><h2>" +
                              ("ClipForge is connected to YouTube ✔" if ok else "Sign-in failed") +
                              "</h2><p>You can close this tab.</p></body></html>").encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = c.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent"})
    webbrowser.open(url)
    srv.timeout = 1
    end = time.time() + timeout
    while "code" not in result and "error" not in result and time.time() < end:
        srv.handle_request()
    srv.server_close()
    if "code" not in result:
        raise RuntimeError(result.get("error", "Timed out waiting for Google sign-in"))
    st, _, body = _req(c.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "code": result["code"], "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    tok = json.loads(body or b"{}")
    if st != 200 or "access_token" not in tok:
        raise RuntimeError(f"Token exchange failed: {tok}")
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    tok["secret_path"] = secret_path
    tok["scope_v"] = 3
    _save_token(tok)
    return channel_name() or "your channel"


def needs_reconnect() -> bool:
    """v1/v2 logins only had upload permission; title tests & thumbnails need the newer one."""
    tok = _load_token()
    return bool(tok) and tok.get("scope_v", 1) < 3


def _access_token() -> str:
    tok = _load_token()
    if not tok:
        raise RuntimeError("Not connected to YouTube (Settings → Uploads → Connect YouTube)")
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    c = _client(tok["secret_path"])
    st, _, body = _req(c.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    new = json.loads(body or b"{}")
    if st != 200:
        raise RuntimeError(f"YouTube login expired — reconnect in Settings ({new.get('error', st)})")
    tok["access_token"] = new["access_token"]
    tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
    _save_token(tok)
    return tok["access_token"]


def connected() -> bool:
    return _load_token() is not None


def channel_name() -> str:
    st, _, body = _req("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                       headers={"Authorization": f"Bearer {_access_token()}"})
    try:
        return json.loads(body)["items"][0]["snippet"]["title"]
    except Exception:
        return ""


# ------------------------------------------------------------------ upload
def upload(path: str, title: str, description: str, tags: list[str], privacy="public",
           publish_at: datetime | None = None, progress=None, category="20") -> str:
    """Resumable upload; returns the video id. category 20 = Gaming."""
    size = os.path.getsize(path)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"  # required for scheduled publishing
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    meta = {"snippet": {"title": title[:100], "description": description[:4900],
                        "tags": [t.lstrip("#") for t in tags][:30], "categoryId": category},
            "status": status}
    st, hdr, body = _req(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
    if st != 200:
        raise RuntimeError(f"YouTube refused the upload ({st}): {body[:400]!r}")
    loc = {k.lower(): v for k, v in hdr.items()}.get("location")
    sent = 0
    retries = 0
    with open(path, "rb") as f:
        while sent < size:
            f.seek(sent)
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            st, hdr, body = _req(loc, data=chunk, method="PUT", timeout=300, headers={
                "Authorization": f"Bearer {_access_token()}", "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {sent}-{end}/{size}"})
            if st in (200, 201):
                if progress:
                    progress(1.0)
                return json.loads(body)["id"]
            if st == 308:
                rng = {k.lower(): v for k, v in hdr.items()}.get("range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                retries = 0
                if progress:
                    progress(sent / size)
                continue
            if st in (500, 502, 503, 504) and retries < 6:  # transient: ask where we are and resume
                retries += 1
                time.sleep(2 ** retries)
                st2, hdr2, _ = _req(loc, data=b"", method="PUT", headers={
                    "Authorization": f"Bearer {_access_token()}", "Content-Range": f"bytes */{size}"})
                rng = {k.lower(): v for k, v in hdr2.items()}.get("range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                continue
            raise RuntimeError(f"Upload failed ({st}): {body[:400]!r}")
    raise RuntimeError("Upload ended unexpectedly")


def _api(method: str, path: str, body=None, raw: bytes | None = None, ctype="application/json"):
    url = "https://www.googleapis.com" + path
    headers = {"Authorization": f"Bearer {_access_token()}"}
    data = None
    if raw is not None:
        data, headers["Content-Type"] = raw, ctype
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    st, _, resp = _req(url, data=data, headers=headers, method=method)
    if st not in (200, 201, 204):
        raise RuntimeError(f"YouTube API {st}: {resp[:300]!r}")
    return json.loads(resp) if resp else {}


def update_title(video_id: str, title: str):
    cur = _api("GET", f"/youtube/v3/videos?part=snippet&id={video_id}")["items"][0]["snippet"]
    snip = {k: cur[k] for k in ("title", "description", "categoryId", "tags", "defaultLanguage") if k in cur}
    snip["title"] = title[:100]
    _api("PUT", "/youtube/v3/videos?part=snippet", {"id": video_id, "snippet": snip})


def set_thumbnail(video_id: str, jpg: str):
    """Needs a phone-verified channel (YouTube's rule for custom thumbnails)."""
    data = Path(jpg).read_bytes()
    _api("POST", f"/upload/youtube/v3/thumbnails/set?videoId={video_id}", raw=data, ctype="image/jpeg")


TRENDS_FILE = app_data_dir() / "trends.json"


def trending(game: str, days=7, max_results=25) -> dict:
    """What's blowing up for a game on Shorts this week (costs ~101 of your 10,000 daily API units)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.quote(f"{game} #shorts")
    res = _api("GET", f"/youtube/v3/search?part=snippet&type=video&videoDuration=short&order=viewCount"
                      f"&publishedAfter={since}&maxResults={max_results}&q={q}")
    ids = [it["id"]["videoId"] for it in res.get("items", []) if it.get("id", {}).get("videoId")]
    stats = {}
    if ids:
        v = _api("GET", f"/youtube/v3/videos?part=statistics,snippet&id={','.join(ids)}")
        for it in v.get("items", []):
            pub = datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z", "+00:00"))
            hours = max(1.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600)
            views = int(it.get("statistics", {}).get("viewCount", 0))
            stats[it["id"]] = {"title": it["snippet"]["title"], "views": views, "per_hour": views / hours,
                               "channel": it["snippet"].get("channelTitle", ""),
                               "tags": it["snippet"].get("tags", [])}
    vids = sorted(stats.items(), key=lambda kv: kv[1]["per_hour"], reverse=True)
    words: dict[str, int] = {}
    stop = set("the a an and or of to in on i my me you is it this that for with at be are was so".split()
               + [w.lower() for w in game.split()] + ["shorts", "#shorts"])
    for _, d in vids:
        for w in re_words(d["title"]):
            if w not in stop and len(w) > 2:
                words[w] = words.get(w, 0) + 1
    out = {"game": game, "fetched": time.time(),
           "total_views": sum(d["views"] for _, d in vids),
           "avg_per_hour": (sum(d["per_hour"] for _, d in vids) / len(vids)) if vids else 0,
           "top": [{"id": k, **d} for k, d in vids[:10]],
           "words": sorted(words.items(), key=lambda kv: kv[1], reverse=True)[:15]}
    cache = load_trends()
    cache[game] = out
    TRENDS_FILE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return out


def load_trends() -> dict:
    try:
        return json.loads(TRENDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def re_words(text: str) -> list[str]:
    import re
    return [w.lower() for w in re.findall(r"#?[A-Za-zÀ-ÿ0-9']+", text)]


def video_stats(ids: list[str]) -> dict[str, dict]:
    """views / likes / comments for your uploaded videos (used to learn which hooks work)."""
    out = {}
    for i in range(0, len(ids), 50):
        q = ",".join(ids[i:i + 50])
        st, _, body = _req(f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={q}",
                           headers={"Authorization": f"Bearer {_access_token()}"})
        if st == 200:
            for it in json.loads(body).get("items", []):
                out[it["id"]] = {k: int(v) for k, v in it.get("statistics", {}).items() if v.isdigit()}
    return out


# ------------------------------------------------------------------ queue + schedule
@dataclass
class QueueItem:
    path: str
    title: str
    description: str = ""
    tags: list = field(default_factory=list)
    privacy: str = "public"
    publish_at: str = ""          # ISO local time, "" = publish when uploaded
    status: str = "queued"        # queued / uploading / scheduled / published / failed
    video_id: str = ""
    error: str = ""
    hook: str = ""
    added: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)   # [(timestamp, views)] for learning
    meta: dict = field(default_factory=dict)      # what's in the video (from the export's .meta.json)
    thumb: str = ""                               # custom thumbnail to set after upload (long videos)
    # title A/B test: A runs 24 h, then B runs 24 h, the winner stays
    title_b: str = ""
    ab_state: str = ""                            # "" / a / b / done
    ab_switch_at: float = 0.0
    ab_views_a: int = 0
    ab_views_b: int = 0
    ab_winner: str = ""


class UploadQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self.items: list[QueueItem] = []
        self.load()

    def load(self):
        try:
            data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            known = set(QueueItem.__dataclass_fields__)
            self.items = [QueueItem(**{k: v for k, v in d.items() if k in known}) for d in data]
        except Exception:
            self.items = []
        for it in self.items:  # an upload interrupted by closing the app goes back in line
            if it.status == "uploading":
                it.status = "queued"

    def save(self):
        with self._lock:
            tmp = QUEUE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(i) for i in self.items], indent=1), encoding="utf-8")
            os.replace(tmp, QUEUE_FILE)

    def next_slot(self, post_times: list[str], after: datetime | None = None) -> datetime:
        """First daily slot (e.g. 17:30) that's in the future and not taken yet."""
        now = after or datetime.now()
        taken = {it.publish_at[:16] for it in self.items if it.publish_at and it.status != "failed"}
        times = sorted(post_times or ["17:30"])
        for day in range(0, 365):
            d = (now + timedelta(days=day)).date()
            for t in times:
                hh, mm = (int(x) for x in t.split(":"))
                slot = datetime(d.year, d.month, d.day, hh, mm)
                if slot > now + timedelta(minutes=20) and slot.isoformat()[:16] not in taken:
                    return slot
        return now + timedelta(days=1)

    def add(self, path: str, title: str, description: str, tags: list[str], privacy: str,
            schedule: bool, post_times: list[str], hook: str = "", title_b: str = "", thumb: str = "",
            publish_at: datetime | None = None) -> QueueItem:
        from .analytics import read_meta
        it = QueueItem(path=path, title=title, description=description, tags=tags, privacy=privacy, hook=hook,
                       meta=read_meta(path), title_b=title_b, thumb=thumb)
        if publish_at is not None:   # fixed time (e.g. series parts on consecutive days)
            it.publish_at = publish_at.replace(tzinfo=None).isoformat(timespec="minutes")
        elif schedule:
            it.publish_at = self.next_slot(post_times).isoformat(timespec="minutes")
        self.items.append(it)
        self.save()
        return it

    def remove(self, item_id: str):
        self.items = [i for i in self.items if i.id != item_id]
        self.save()

    def pending(self) -> list[QueueItem]:
        return [i for i in self.items if i.status == "queued"]


class Uploader(threading.Thread):
    """Background worker: uploads queued videos one at a time, retries later on failure."""

    def __init__(self, queue: UploadQueue, on_change=None):
        super().__init__(name="uploader", daemon=True)
        self.q = queue
        self.on_change = on_change or (lambda *a: None)
        self.wake = threading.Event()
        self.progress: dict[str, float] = {}

    def run(self):
        last_maint = 0.0
        while True:
            self.wake.wait(60)
            self.wake.clear()
            if not connected():
                continue
            if time.time() - last_maint > 3 * 3600:  # stats + title tests every 3 hours
                last_maint = time.time()
                try:
                    self.maintenance()
                except Exception as e:
                    print("youtube maintenance:", e)
            for it in self.q.pending():
                if not Path(it.path).exists():
                    it.status, it.error = "failed", "file is gone"
                    self.q.save()
                    continue
                it.status, it.error = "uploading", ""
                self.q.save()
                self.on_change(it)
                try:
                    when = datetime.fromisoformat(it.publish_at) if it.publish_at else None
                    if when and when.tzinfo is None:
                        when = when.astimezone()  # local time -> aware
                    it.video_id = upload(it.path, it.title, it.description, it.tags, it.privacy, when,
                                         progress=lambda f, i=it: (self.progress.__setitem__(i.id, f),
                                                                   self.on_change(i)))
                    it.status = "scheduled" if when and when > datetime.now().astimezone() else "published"
                    if it.thumb and Path(it.thumb).exists():
                        try:
                            set_thumbnail(it.video_id, it.thumb)
                        except Exception as e:  # unverified channels can't set thumbnails; not fatal
                            it.error = f"thumbnail not set: {str(e)[:120]}"
                except Exception as e:
                    it.status, it.error = "failed", str(e)[:300]
                self.q.save()
                self.on_change(it)

    def refresh_stats(self):
        ids = [i.video_id for i in self.q.items if i.video_id]
        if not ids or not connected():
            return
        stats = video_stats(ids)
        now = time.time()
        for it in self.q.items:
            if it.video_id in stats:
                it.stats = stats[it.video_id]
                it.history.append((round(now), it.stats.get("viewCount", 0)))
                it.history = it.history[-200:]
                if it.status == "scheduled" and it.publish_at and datetime.fromisoformat(it.publish_at) < datetime.now():
                    it.status = "published"
        self.q.save()
        self.on_change(None)

    def maintenance(self):
        self.refresh_stats()
        changed = False
        ratio = day2_ratio(self.q.items)
        now = time.time()
        for it in self.q.items:
            if not (it.title_b and it.video_id and it.status == "published") or it.ab_state == "done":
                continue
            views = it.stats.get("viewCount", 0)
            live_at = (datetime.fromisoformat(it.publish_at).timestamp() if it.publish_at else it.added)
            if it.ab_state == "" and now - live_at >= 24 * 3600:
                it.ab_views_a = views                    # A had its first 24 h
                update_title(it.video_id, it.title_b)
                it.ab_state, it.ab_switch_at = "b", now
                changed = True
            elif it.ab_state == "b" and now - it.ab_switch_at >= 24 * 3600:
                it.ab_views_b = views - it.ab_views_a
                # a video's second day normally gets fewer views than its first — correct for that
                expected_b = it.ab_views_a * ratio
                if it.ab_views_b > expected_b * 1.1:
                    it.ab_winner = "B"
                    it.title = it.title_b
                else:
                    it.ab_winner = "A"
                    update_title(it.video_id, it.title)
                it.ab_state = "done"
                changed = True
        if changed:
            self.q.save()
            self.on_change(None)


def day2_ratio(items) -> float:
    """Typical (views on day 2) / (views on day 1) for this channel, from the view history."""
    ratios = []
    for it in items:
        if it.title_b or len(it.history) < 3:
            continue
        live = datetime.fromisoformat(it.publish_at).timestamp() if it.publish_at else it.added

        def views_at(t):  # only called within this iteration
            best = None
            for ts, v in it.history:  # noqa: B023
                if abs(ts - t) < 4 * 3600 and (best is None or abs(ts - t) < abs(best[0] - t)):
                    best = (ts, v)
            return best[1] if best else None
        d1, d2 = views_at(live + 86400), views_at(live + 2 * 86400)
        if d1 and d2 is not None and d1 > 20:
            ratios.append((d2 - d1) / d1)
    if len(ratios) >= 3:
        import statistics
        return max(0.05, statistics.median(ratios))
    return 0.6


def parse_post_text(txt: str) -> tuple[str, str, list[str]]:
    """Reads the TITLE / DESCRIPTION / HASHTAGS file ClipForge writes next to each export."""
    title, desc, tags, cur = "", [], [], None
    for line in txt.splitlines():
        u = line.strip().upper()
        if u in ("TITLE:", "DESCRIPTION:", "HASHTAGS:"):
            cur = u
            continue
        if cur == "TITLE:" and line.strip() and not title:
            title = line.strip()
        elif cur == "DESCRIPTION:":
            desc.append(line)
        elif cur == "HASHTAGS:":
            tags += [t for t in line.split() if t.startswith("#")]
    description = "\n".join(desc).strip()
    if tags:
        description += "\n\n" + " ".join(tags)
    return title, description, tags
