"""Phone notifications through ntfy (free, no account): install the ntfy app on your phone, subscribe to the
topic shown in Settings, and ClipForge pings you when the session inbox or a render is ready.
The topic is a long random name — only someone who knows it can read the messages."""
from __future__ import annotations

import secrets
import threading
import urllib.request

SERVER = "https://ntfy.sh/"


def new_topic() -> str:
    return "clipforge-" + secrets.token_hex(6)


def send(topic: str, title: str, message: str, click: str = "", tags: str = "clapper") -> bool:
    if not topic:
        return False
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Tags": tags}
    if click:
        headers["Click"] = click
    try:
        req = urllib.request.Request(SERVER + topic, data=message.encode("utf-8"), headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=8).read()
        return True
    except Exception as e:
        print("ntfy:", e)
        return False


def send_async(topic: str, title: str, message: str, click: str = "", tags: str = "clapper"):
    threading.Thread(target=send, args=(topic, title, message, click, tags), daemon=True).start()
