"""Phone approval page: a private web page on your home Wi-Fi.

Open the link (shown in Settings) on your phone, bookmark it, and from the couch you can watch the
day's videos, tap ✔ to schedule them or ✖ to skip, and save TikTok versions straight to your phone
to post from the TikTok app. Only devices with the secret link can use it, and it isn't reachable
from outside your home network. Windows asks once whether to allow it on private networks — say yes.
"""
from __future__ import annotations

import hmac
import http.server
import json
import mimetypes
import os
import secrets
import socket
import threading
import urllib.parse
from pathlib import Path

from .producer import Approvals


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def new_token() -> str:
    return secrets.token_urlsafe(18)


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><title>ClipForge</title>
<style>
:root{--bg:#161b21;--card:#232a33;--text:#d5dce4;--muted:#8a95a3;--acc:#5b5ff0;--green:#3ddc97;--red:#ff5c6c}
body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:14px 16px;font-weight:700;font-size:18px;
 border-bottom:1px solid #2e3640;z-index:2}
h2{font-size:13px;letter-spacing:1px;color:var(--muted);margin:22px 16px 8px}
.card{background:var(--card);margin:10px 16px;border-radius:12px;overflow:hidden}
video{width:100%;max-height:70vh;background:#000;display:block}
.meta{padding:10px 14px}.t{font-weight:600;font-size:15px}.s{color:var(--muted);font-size:12px;margin-top:3px}
.row{display:flex;gap:10px;padding:0 14px 14px}
button,a.btn{flex:1;border:0;border-radius:10px;padding:14px;font-size:16px;font-weight:700;color:#fff;
 text-align:center;text-decoration:none}
.ok{background:#1f8f62}.no{background:#3a2229;color:var(--red)}.dl{background:var(--acc)}
textarea{width:calc(100% - 28px);margin:0 14px 12px;background:#161b21;color:var(--text);border:1px solid #2e3640;
 border-radius:8px;padding:8px;font-size:13px}
.empty{color:var(--muted);text-align:center;padding:40px 16px}
#toast{position:fixed;left:16px;right:16px;bottom:20px;background:#000c;color:#fff;padding:14px;border-radius:10px;
 display:none;text-align:center;z-index:3}
</style></head><body><header>◆ ClipForge</header>
<h2>WAITING FOR YOU</h2><div id="pending"></div>
<h2>TIKTOK — READY TO POST</h2><div id="tiktok"></div>
<div id="toast"></div>
<script>
const K=new URLSearchParams(location.search).get('k');
const q=p=>p+(p.includes('?')?'&':'?')+'k='+encodeURIComponent(K);
function toast(t){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';
 setTimeout(()=>e.style.display='none',2600)}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function load(){
 const r=await fetch(q('/api/items'));if(!r.ok){document.body.innerHTML='<p class=empty>Link expired — open the new link from ClipForge Settings.</p>';return}
 const d=await r.json();
 const P=document.getElementById('pending');P.innerHTML='';
 if(!d.pending.length)P.innerHTML='<div class=empty>Nothing to approve 🎉</div>';
 for(const it of d.pending){const c=document.createElement('div');c.className='card';
  c.innerHTML=`<video controls playsinline preload="metadata" src="${q('/video/'+it.id)}"></video>
  <div class=meta><div class=t>${esc(it.title)}</div><div class=s>${esc(it.platform.toUpperCase())} · ${esc(it.kind)}${it.publish_at?' · '+esc(it.publish_at.replace('T',' ')):''}</div></div>
  <div class=row><button class=no>✖ Skip</button><button class=ok>✔ ${it.platform=='youtube'?'Schedule':'Approve'}</button></div>`;
  c.querySelector('.ok').onclick=()=>decide(it.id,1,c);c.querySelector('.no').onclick=()=>decide(it.id,0,c);P.appendChild(c)}
 const T=document.getElementById('tiktok');T.innerHTML='';
 if(!d.tiktok.length)T.innerHTML='<div class=empty>Approved TikTok videos show up here.</div>';
 for(const it of d.tiktok){const c=document.createElement('div');c.className='card';
  c.innerHTML=`<div class=meta><div class=t>${esc(it.title)}</div></div><textarea rows=3 readonly>${esc(it.caption)}</textarea>
  <div class=row><a class="btn dl" href="${q('/dl/'+it.id)}">⬇ Save video</a><button class=ok>✔ Posted</button></div>`;
  c.querySelector('textarea').onclick=e=>{e.target.select();try{document.execCommand('copy');toast('Caption copied')}catch(_){}}
  c.querySelector('.ok').onclick=async()=>{await fetch(q('/api/posted?id='+it.id),{method:'POST'});c.remove()};T.appendChild(c)}
}
async function decide(id,ok,card){const r=await fetch(q('/api/decide?id='+id+'&ok='+ok),{method:'POST'});
 const d=await r.json();toast(d.message);card.remove();setTimeout(load,800)}
load();setInterval(load,60000);
</script></body></html>"""


class PhoneServer:
    def __init__(self, token: str, decide, port: int = 8765):
        """decide(item_id, approve: bool) -> message (runs the same code as the PC buttons)."""
        self.token, self.decide, self.port = token, decide, port
        self.httpd: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}/?k={self.token}"

    def start(self):
        if self.httpd:
            return
        server = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _auth(self, qs) -> bool:
                k = qs.get("k", [""])[0]
                if not hmac.compare_digest(k.encode(), server.token.encode()):
                    self.send_response(403)
                    self.end_headers()
                    return False
                return True

            def _json(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(u.query)
                if not self._auth(qs):
                    return
                if u.path == "/":
                    data = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif u.path == "/api/items":
                    ap = Approvals()
                    pend = [dict(id=i.id, title=i.title, platform=i.platform, kind=i.kind, publish_at=i.publish_at)
                            for i in ap.pending()]
                    tik = []
                    for i in ap.items:
                        if i.platform == "tiktok" and i.status == "approved" and Path(i.path).exists():
                            caption = (i.title + "\n" + " ".join(i.tags)).strip()
                            tik.append(dict(id=i.id, title=i.title, caption=caption))
                    self._json({"pending": pend, "tiktok": tik})
                elif u.path.startswith("/video/") or u.path.startswith("/dl/"):
                    it = Approvals().get(u.path.rsplit("/", 1)[-1])
                    if not it or not Path(it.path).exists():
                        self.send_response(404)
                        self.end_headers()
                        return
                    self._file(Path(it.path), attachment=u.path.startswith("/dl/"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(u.query)
                if not self._auth(qs):
                    return
                item_id = qs.get("id", [""])[0]
                if u.path == "/api/decide":
                    try:
                        msg = server.decide(item_id, qs.get("ok", ["0"])[0] == "1")
                    except Exception as e:
                        msg = f"error: {e}"
                    self._json({"message": msg})
                elif u.path == "/api/posted":
                    ap = Approvals()
                    it = next((i for i in ap.items if i.id == item_id), None)
                    if it:
                        it.status = "posted"
                        ap.save()
                    self._json({"ok": True})
                else:
                    self.send_response(404)
                    self.end_headers()

            def _file(self, path: Path, attachment=False):
                """Range requests are required for video playback on iPhone Safari."""
                size = path.stat().st_size
                ctype = mimetypes.guess_type(str(path))[0] or "video/mp4"
                rng = self.headers.get("Range")
                start, end = 0, size - 1
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].split(",")[0].partition("-")
                    if a:
                        start = int(a)
                        end = int(b) if b else size - 1
                    elif b:
                        start = max(0, size - int(b))
                    end = min(end, size - 1)
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if attachment:
                    name = urllib.parse.quote(path.name)
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{name}")
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    try:
                        while left > 0:
                            chunk = f.read(min(256 * 1024, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        self.httpd = http.server.ThreadingHTTPServer(("0.0.0.0", self.port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="phone", daemon=True).start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
