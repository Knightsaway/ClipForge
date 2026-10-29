# Privacy

ClipForge runs on your PC. Your recordings, clips, edits and settings stay on your PC
(`Videos\ClipForge` and `%APPDATA%\ClipForge`). There are no accounts, no analytics and no telemetry.

ClipForge only goes online for these things:

| What | When | Sent to | What is sent |
|---|---|---|---|
| Update check | When ClipForge starts (turn off: Settings → Upkeep → "Check for updates when ClipForge starts") and when you press *Check now* | GitHub (`api.github.com`, `github.com`, `raw.githubusercontent.com`) | A normal download request with your IP address and a `ClipForge/<version>` user agent |
| GIF search | Only if you paste your own GIPHY key and search, or turn on auto GIFs | GIPHY (`api.giphy.com`) | Your search words and your key |
| Phone notifications | Only if you set an ntfy topic | ntfy.sh | The notification text (e.g. "3 Shorts ready") |
| YouTube posting | Only if you connect your YouTube channel | Google (`googleapis.com`) | The videos, titles and descriptions you choose to post, and your login token |
| Report a bug | Only when you press it | Your browser opens GitHub | Nothing is sent automatically. You see and can edit everything first (Windows user names are removed from paths) |

Things that stay on your PC even though they use a network address:

- **Local AI** (titles and hooks) talks to Ollama on your own PC (`127.0.0.1`).
- **Phone approvals** is off by default. When it's on, it's a page served from your PC to your own
  phone on your home Wi-Fi.

Secrets you enter (the GIPHY key, ntfy topic, phone link and YouTube login) are encrypted with
Windows (DPAPI) so only your Windows account can read them. Backups leave them out.

Questions: [open an issue](https://github.com/Knightsaway/ClipForge/issues).
