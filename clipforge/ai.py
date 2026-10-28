"""Local AI writer: titles, hooks, descriptions, hashtags and meme captions.

Runs on your own GPU through Ollama (free, offline, nothing leaves your PC). setup.bat can
install it. The model is unloaded straight after each request (keep_alive=0) so it never
holds VRAM while you're gaming. If Ollama isn't running, ClipForge falls back to templates.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"

STYLE = ("You write for a gaming YouTube Shorts / TikTok channel. Style: funny, punchy, a bit chaotic, "
         "like a real gamer talking to friends. No cringe corporate tone, no emojis in hooks, "
         "never use the words 'epic', 'insane gameplay' or 'must watch'. Keep hooks under 7 words.")


def _post(path: str, body: dict, timeout=120) -> dict:
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def models() -> list[str]:
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=2) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def available(model: str = "") -> bool:
    ms = models()
    return bool(ms) and (not model or any(m.startswith(model.split(":")[0]) for m in ms))


def _pick_model(model: str) -> str:
    ms = models()
    if not ms:
        raise RuntimeError("Ollama isn't running (install it from ollama.com or via setup.bat)")
    if model in ms:
        return model
    base = model.split(":")[0]
    return next((m for m in ms if m.startswith(base)), ms[0])


def ask_json(prompt: str, model: str = DEFAULT_MODEL, temperature=0.9) -> dict:
    m = _pick_model(model)
    res = _post("/api/generate", {"model": m, "prompt": prompt, "system": STYLE, "stream": False,
                                  "format": "json", "keep_alive": 0,
                                  "options": {"temperature": temperature, "num_predict": 600}})
    text = res.get("response", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m2 = re.search(r"\{.*\}", text, re.S)
        return json.loads(m2.group(0)) if m2 else {}


def describe_moments(clips_context: list[dict]) -> str:
    """Short factual summary the model can riff on."""
    parts = []
    for c in clips_context[:8]:
        bits = [c.get("game", "")]
        if c.get("kills"):
            bits.append(f"{c['kills']} kills" + (f" ({c['streak']})" if c.get("streak") else ""))
        if c.get("title"):
            bits.append(f"clip titled '{c['title']}'")
        if c.get("speech"):
            bits.append(f"players said: \"{c['speech'][:160]}\"")
        parts.append(", ".join(b for b in bits if b))
    return "\n".join(f"- {p}" for p in parts)


def write_post(game: str, context: list[dict], vertical: bool, model: str = DEFAULT_MODEL,
               winning_hooks: list[str] | None = None) -> dict:
    """{"hooks": [3], "title": str, "description": str, "tags": [..]}"""
    learned = ""
    if winning_hooks:
        learned = "Hooks that performed best on this channel so far (match their vibe, don't copy):\n" + \
                  "\n".join(f"- {h}" for h in winning_hooks[:5]) + "\n"
    prompt = (f"Game: {game}\nFormat: {'vertical Short/TikTok' if vertical else 'YouTube video'}\n"
              f"What happens in the video:\n{describe_moments(context) or '- gameplay highlights'}\n{learned}\n"
              "Return JSON with keys: hooks (3 different on-screen hook texts for the first 3 seconds), "
              "title (YouTube title, max 70 chars, curiosity-driven), description (2 short lines), "
              "tags (8 hashtags without spaces, include the game).")
    d = ask_json(prompt, model)
    hooks = [h.strip().strip('"') for h in d.get("hooks", []) if isinstance(h, str) and h.strip()][:3]
    tags = [("#" + t.lstrip("#").replace(" ", "")) for t in d.get("tags", []) if isinstance(t, str)][:10]
    return {"hooks": hooks, "title": str(d.get("title", "")).strip()[:95],
            "description": str(d.get("description", "")).strip(), "tags": tags}


def alt_titles(title: str, game: str, n=2, model: str = DEFAULT_MODEL) -> list[str]:
    d = ask_json(f"Game: {game}\nCurrent YouTube title: {title}\nWrite {n} alternative titles that test a "
                 "different angle (curiosity, challenge, humor). Return JSON: {\"titles\": [...]}", model)
    return [t.strip() for t in d.get("titles", []) if isinstance(t, str) and t.strip()][:n]


def meme_captions(context: list[dict], n: int, model: str = DEFAULT_MODEL) -> list[str]:
    """One funny on-screen caption per moment (for montages)."""
    d = ask_json("Moments in order:\n" + describe_moments(context) +
                 f"\nWrite exactly {n} short funny on-screen captions (max 6 words each), one per moment, "
                 "in order. Return JSON: {\"captions\": [...]}", model)
    caps = [c.strip() for c in d.get("captions", []) if isinstance(c, str)]
    return (caps + [""] * n)[:n]


def clip_context(clips) -> list[dict]:
    """Facts about clips for the prompt (kills, streak names, titles)."""
    from .vision import clusters, label_for
    out = []
    for c in clips:
        cl = clusters(c.events) if c.events else []
        best = max((x["count"] for x in cl), default=0)
        out.append({"game": c.game, "kills": len(c.events), "streak": label_for(best) if best > 1 else "",
                    "title": c.title})
    return out
