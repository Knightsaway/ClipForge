# ClipForge

Your own GG Moments / ShadowPlay replacement: a light replay buffer that records while you
play, plus a quick editor and an **Auto Short** button that turns clips into a ready-to-post
YouTube Short / TikTok.

## Install (once)

1. Download **`ClipForge-*-win64.zip`** from
   [Releases](https://github.com/Knightsaway/ClipForge/releases) (not a source checkout).
2. Unzip somewhere permanent, e.g. `C:\ClipForge`.
3. Run **`ClipForge.exe`**. FFmpeg is already vendored under `ffmpeg\bin\` (GPL — see
   `THIRD_PARTY_NOTICES.md` inside the zip).
4. **Quit SteelSeries GG Moments** (and turn off Instant Replay in the NVIDIA app) — they grab the
   same hotkeys and the same GPU encoder.

Optional captions / AI caster / Ollama are separate add-ons (Settings or a source `setup.bat` for
devs). Logs live in `%APPDATA%\ClipForge\`.

## Updating

**In-app (recommended):** Settings → Upkeep → **Check now**. ClipForge downloads the newest **GitHub Release**
zip, checks its SHA256 when published, overlays the new files, syncs Python packages, and restarts.
Your clips, settings, drafts and `.venv` stay put.

**Manual zip:** Quit ClipForge (tray icon → Quit), then unzip the new **win64** release **over the same
folder**. Clips and settings live in Videos and AppData, so they're never lost.

## While playing

| Key | What it does |
|---|---|
| **PageUp** | Save the last 60 s (same key you use in GG) |
| **Home** | **Marker**: "that was good". Saves a tight 16 s highlight a few seconds later — these are perfect Short material |
| **Ctrl+End** | Start / stop a full recording |
| **Ctrl+PageUp** | Save the whole buffer (3 min) |

All keys can be changed in Settings. A small notification pops up top-right when a clip is saved
(visible in borderless/windowed games).

## Making videos

- **⚡ Auto Short** (Clips page): with nothing selected it uses your highlights, markers and
  favorites from the last 3 days. It finds the loudest/most hype moments, puts the best one first
  as the hook, cuts out dead air, adds hook text, and renders a 1080×1920 60 fps video straight away.
  Ctrl+click clips first to choose them yourself.
- **Clip view**: click any clip. Drag the purple handles (or press I / O) to trim. Yellow flags
  are your markers; grey ticks are moments found automatically. Export buttons on the right.
- **Studio**: Ctrl+click clips → **Create Montage**. The clips arrive already edited (effects, GIFs, hook text)
  and nothing renders until you press **Render video**. **⚡ Auto Short** works the same way: it builds the
  Short in the Studio for you to check (turn that off in Settings → Edits & effects to render straight away).
- **Timeline** (under the preview, *Advanced* on): every clip, the hook text, effects, GIFs and the caster's
  lines on their own tracks. Drag clip edges to trim, drag anything to move it in time, drag the end of a
  GIF or the text to change how long it shows, drag a clip sideways to move it, right-click to split / duplicate / remove, double-click a
  caster line to change his words. Ctrl+scroll zooms; **▶ Play whole edit** plays the clips back to back.
- **📱 Final look tab**: the finished frame. Drag the hook text, your webcam and GIFs anywhere on it; press
  play to watch the edit with the text, animated GIFs and the caster's voice at their moments.
- **Undo / redo**: Ctrl+Z / Ctrl+Y (or the ↶ ↷ buttons) for anything you change in the Studio.
- **Ready to post**: every export comes with a title, description and hashtags. Copy them, click
  YouTube ↑ / TikTok ↑, drag the file in.

Exports are saved in `Videos\ClipForge\Exports`, one folder per video named after the game and title (e.g. `2026-10-28 AION2 - This boss had NO chance\`), holding the YouTube version, the TikTok version and a .txt with the title, description and hashtags for each. Long montages render one piece at a time, so
memory use stays flat however long the edit is.

## After a session: barely touch it

- **📥 Session inbox**: when you've played a while and stop, ClipForge builds the session's best Shorts
  (effects, GIFs, hook, caster if on, your style) and leaves them in **🗂 Drafts → Inbox**. Press
  **✔ Render & schedule** (renders + TikTok version, schedules on YouTube), **✏ Open** to polish it in the Studio,
  or **✖ Skip**. **Render everything in the inbox** does them all.
- **Drafts**: every Studio edit saves itself as you work. Close the app, come back next week, open it from Drafts.
- **Render queue**: **＋ Queue** in the Studio (or ▶ Queue in Drafts) and keep editing; queued videos render one
  after another in the background and never start while you're in a game.
- **🎨 My style**: set up a look once (layout, webcam spot, text size & place, captions, caster, music, GIFs),
  press **Save…** next to *My style* in the Studio. Make it the default (⋯ menu) or use it for one game; every
  automatic edit then uses it.
- **Captions, word by word**: four styles — Pop (3 words, spoken one yellow), One word at a time, Karaoke, Boxed.
  The caster's lines are captioned too.
- **✂ Cut boring parts**: stretches where nothing happens show as red stripes on the timeline; one click cuts
  them (Create Montage does it for you). Ctrl+Z brings them back.
- **Full-screen, follows the action**: a 9:16 layout whose frame glides to where things are moving.
- **🎵 Sync to beat**: with background music, cuts and zooms land on the beat.
- **🖼 Thumbnail from the timeline**: right-click a clip on the timeline → *Use this moment as the thumbnail*.
- **No repeats**: automatic edits skip moments you've already used in another video.
- **Skins**: Settings → Look. *Legacy* is the classic look; four more are inspired by Half-Life 2, Control
  Resonant, Star Wars: The Last Jedi and Elden Ring.

## Editing tools (5.1)

- **T+ Text**: your own text anywhere (white, yellow, green, or on a red / white box), popping in, as long as you
  want. Drag it on the video in 📱 Final look; it sits on the timeline's Text track.
- **Transitions**: pick how each clip comes in (Flash, Zoom punch, Whip pan, Glitch, Fade) under *Effects on the
  selected piece*. Automatic edits mix them in for you.
- **▦ Blur box**: hide chat, usernames, Discord pop-ups. Drag and resize it on the video, set how long it stays.
- **🔇 Mute / bleep** a moment, or **🤬 Bleep swearing** to find and bleep swear words in what you say
  (needs the captions add-on).
- **Keyboard**: S split · Delete remove · J/K/L back / pause / play · Space play · ←/→ nudge the selected item
  (Shift = 1 s) · Ctrl+D duplicate clip · T text · B blur · M mute · Ctrl+Z / Ctrl+Y undo / redo.

## Hands-off upkeep (5.1)

- **Storage** (Settings): see how much space clips take; unused clips older than N days are deleted for you
  (never favorites, clips in a draft, or clips used in a video). A warning pops up when the drive gets full.
- **Updates**: drop a new ClipForge zip on the window (or Settings → Updates). Optionally point it at a
  version.json online and it tells you when there's a new version.
- **Setup wizard** on first start (keys, mic test, webcam test, look, autopilot) — run it again from Settings.
- **Backup & restore**: settings, styles, kill icons, drafts and history in one zip.
- **Phone pings** via the free ntfy app when your session's Shorts or a render are ready.
- **Tags**: right-click a clip → 🏷 Tags. Search `#fail` and press ⚡ Auto Short or Create Montage to build from
  just those clips.
- **Per-game recording**: own frame rate / quality / buffer for one game.
- **Music**: set a folder of music you're allowed to use; 🎲 in the Studio picks a track. The Studio warns when
  the game sound itself contains music YouTube might claim.

## 5.2

- **Settings in tabs**: Recording · Editing · Automation · Posting · Upkeep · Look.
- **Recently deleted**: deleted clips stay 24 hours in Settings → Upkeep → ♻ Recently deleted (restore them
  there), then they're gone for good. Still no Recycle Bin.
- **Quick trim while playing**: for 8 seconds after a clip is saved, Ctrl+→ cuts 5 s off its start (Ctrl+← puts
  it back) — without leaving the game.
- **Weekly summary**: Sunday evening, your week in numbers (clips, videos, posts, views, best video) as a
  pop-up and a phone ping. Settings → Posting → Phone notifications → *Show it now*.
- **Text templates**: select a text in 📱 Final look → *Save look…*. The last look you saved or picked is used
  for every new text.
- **Smooth slow-mo**: slow-mo now eases in and out. Right-click a slow-mo on the timeline to snap instead.
- **Updates from GitHub**: put `github:yourname/ClipForge` in Settings → Upkeep → Check online; ClipForge finds
  the newest **Release**, downloads it, verifies SHA256 when available, and installs it. Append `@main` only if
  you want bleeding-edge branch builds (no digest).

### Updates come from GitHub Releases
ClipForge checks https://github.com/Knightsaway/ClipForge when it starts (Settings → Upkeep → Check online,
`github:Knightsaway/ClipForge` by default) and offers the latest **GitHub Release** with a higher version
(prefers the `*-win64.zip` asset).
To publish: merge conventional commits to `main`; **release-please** opens a Release PR, and merging it
cuts tag `vX.Y.Z` and uploads `ClipForge-X.Y.Z-win64.zip` + `SHA256SUMS`. See [AGENTS.md](AGENTS.md).

### Phone approvals & ntfy (trust model)
- **Phone page** is **off by default**. Enable it in Settings; leave “Reachable on my LAN” off unless a phone on
  your Wi-Fi needs it (HTTP on your LAN — not for hostile networks). The link contains a secret token: don’t
  share it in chat or ntfy.
- **ntfy** topics are capability secrets — anyone who knows the topic can read your pings. Keep the topic private.

## v2 features

### 💀 Kill detection (any game with a kill icon or medal)
1. Open a clip, pause on a frame where the kill icon / hitmarker skull / medal shows.
2. Press **💀 Teach kill icon**, drag a tight box around just the icon, name it (kill, headshot, double kill…).
   Teach one per medal type; give medals a higher "excitement".
3. ClipForge scans your clips for it (red ticks on the timeline, 💀 on the card) and — while you play —
   watches the buffer. A streak of 2+ kills is saved automatically as a **"Triple kill"** highlight.
If it misses kills, lower *Strictness* when teaching; if it finds kills that aren't there, raise it.

### 🎥 Facecam
Settings → Audio & webcam → pick your webcam and press **🎥 Test** (it shows a picture, or tells you why the
camera won't open — usually another app like Camera Hub, Discord or OBS holding it). It's recorded as its own
track, so clips show it in the corner of the player.
In the Studio open the **📱 Final look** tab next to the preview: you see the finished frame, and you can
drag the webcam box anywhere, drag a corner to resize it, or use the arrow buttons for the 8 standard spots.
Pick the shape (wide, 4:3, square, tall), a white border, *webcam on top + gameplay below* for Shorts, or no
webcam. Your choice is remembered and used by Auto Short and every automatic video too.

### 🎞 GIFs & stickers
In the Studio, put the playhead where it should pop up and press **＋ GIF / sticker**. Pick one of your own
(any .gif, .webp, .png, or a .webm/.mp4 sticker — they're kept in your GIF folder) or search **GIPHY** right
in the app (stickers have see-through backgrounds). GIPHY needs a free API key once: the dialog has a
*Get a free key* button (sign in → Create an App → API → copy the key → paste → Save key).
The 📱 **Final look** tab opens with the GIF selected: drag it anywhere (it can peek in from the edge), drag a
corner to resize, use the arrow buttons for quick spots, set how long it shows, or *Start at playhead* to
move it in time. A pink bar on the timeline shows when each GIF is on screen. GIFs loop while they show and
keep going through slow-mo.

### 📝 Script & voiceover mode
**Script** page: write one line per beat, record your voiceover in the app (or import one), press
**Build video from script**. ClipForge finds where each line is spoken, picks matching gameplay for each
line (kill streaks for `[kill]` lines, a named clip for `[clip: name]`, `[same]` keeps the shot),
lines the big moment up with the end of the sentence, ducks the game sound under your voice,
cleans up your mic and puts the lines on screen. It opens in the Studio so you can tweak, then renders.

### 📅 YouTube upload & daily schedule
Settings → Uploads: pick a Google OAuth client file (free, 5 min — the link there walks you through it),
press **Connect YouTube**, set your daily posting times (e.g. `12:00, 17:30`).
On **Ready to post**: **📅 Schedule** uploads a video now and YouTube makes it public at your next free
slot — your PC doesn't need to be on then. **Schedule all unposted Shorts** turns your backlog into one
post per slot. **📈 Update views** pulls views/likes/comments so you can see which hooks work.
Your login is stored encrypted for your Windows account only.
TikTok doesn't allow personal apps to post, so **TikTok ↑** copies the caption, opens TikTok's uploader
and shows the file to drag in.

### ✨ Speed ramps, zoom punches, sound effects
In the Studio, select a piece and add effects at the playhead: **Zoom** (punch-in), **Slow-mo**
(1 s at 0.4×, pitched down), **Sound** (8 built-in originals — boom, bass drop, air horn, riser, glitch,
whoosh, ding, pop — plus anything you drop in your own SFX folder). **✨ Auto FX** does it for every piece:
zoom on each kill/marker, boom on multi-kills, slow-mo on the opening moment. Auto Short does this
automatically (turn off in Settings → Edits & effects).

## v3 features

### 🤖 Local AI writer
Writes 3 hook options, a title, description and hashtags for every video — on your own GPU via
Ollama (setup.bat offers to install it; free, offline). The model is unloaded right after each request so it
never eats VRAM while you play. In the Studio press **🤖** (press again to cycle hooks). Auto Short and the
weekly best-of use it automatically. Without Ollama, templates are used.

### 📈 Insights — learn what works
Every export records what's in it (game, hook, length, layout, kills, AI or not). Once videos have a day
of stats, **Insights** shows what beats your usual video (1.0× = normal) and tells you what to do next:
best posting hour, best length, strongest game, winning hooks. With *Learning* on, Auto Short and the AI
lean towards what wins — but still try new things 30% of the time.
**What's trending** checks the fastest-growing Shorts for a game this week, the words in their titles,
and which of your games is hottest right now.

### 🅰🅱 Title A/B tests (long videos)
On *Ready to post*, **🅰🅱** schedules a video with two titles (🤖 suggests the second). Title A runs
24 h, then B runs 24 h, and the winner stays — corrected for the normal day-2 drop-off, which ClipForge
learns from your own videos. Results are on the Insights page. (If you connected YouTube with an older
version, disconnect and connect again once — tests and thumbnails need one extra permission.)

### 🖼 Auto thumbnails (long videos)
**🖼** makes 3 thumbnails from the sharpest, most exciting frames with big text in 3 styles. Pick one; it's
set automatically after upload (YouTube requires a phone-verified channel for custom thumbnails).

### 🏆 Weekly best-of
Every week (Sunday 12:00 by default) ClipForge makes a "Top 10 plays of the week" countdown from your
best moments, with rank numbers, the kill-streak names, a slow-mo #1, a thumbnail, and schedules it on
YouTube. It never renders while you're in a game, and catches up if the PC was off.

### 👥 Friends' POV (squad)
Everyone in your squad runs ClipForge and points **Settings → Squad** at one folder you share through
Google Drive / OneDrive / Dropbox, each with their own name. When anyone presses Marker, everyone's
ClipForge saves *their* angle of that moment and shares it. In the Studio, **👥 Friend's angle** finds the
other angles, lines them up to the hundredth of a second using the voice chat that's in both recordings,
and makes a **split-screen** or adds an **instant replay from their view**.

## v4 features

### 🌅 Make today's content (one button)
On the Clips page, **Make today's content** turns the last 24 h of clips into 3 Shorts (+ TikTok versions)
and a 16:9 "Top 10 plays today" video with chapters and a thumbnail. Nothing is posted until you approve it.

### 📱 Approve from your phone
**Settings → Approve from your phone** shows a private link. Open it on your phone (same Wi-Fi), add it to your
home screen, and you can watch each video and tap ✔ (schedules it on YouTube) or ✖ (skips it). TikTok versions
get a **⬇ Save video** button and a caption to copy, so you can post from the TikTok app. Only someone with the
link can open it, and it only works on your home network. Windows asks once to allow it on private networks.

### 🌙 Session recap
When you've played for 20+ minutes and stop for 10 minutes, ClipForge makes a 60-second "Tonight in <game>"
recap in the background and puts it in the approval list.

### 📺 Series (Part 1, 2, 3…)
Right-click a long clip (or Ctrl+click several) → **Make a series**. ClipForge picks the best moments in
story order, splits them into parts that end with "PART 2 TOMORROW", and schedules them on consecutive days.

### 🏆 Best of the month (8+ min)
In the first week of each month: an 8+ minute countdown of last month's best moments with YouTube chapters in
the description — long enough for mid-roll ads, which pay far more than Shorts. Waits for your approval.

### 🔁 Seamless loops
Shorts end by dissolving into their first frame, so a replay feels like one continuous video. The hook text
stays on the whole time so the seam is invisible.

### 🎯 TikTok versions
Every Short also gets a TikTok version with a different hook (which also tells you which hook works where),
text placed clear of TikTok's buttons and tabs, and TikTok-style hashtags.

### 😂 Reaction zoom
With a facecam layout, the webcam punches in on your face when you laugh or yell (detected on your mic track).

### 🎙 AI caster voice
**🎙 Add AI caster voice** in the Studio: a hyped esports caster reacts right after each big moment
("The lobby is in shambles!"). The game stays at full volume and only dips a little while he talks (set how much with
*Game while the caster talks* under the button; 100 % = no dip). Lines come from the local AI; the voice is Piper (free, offline — setup.bat
installs it, Norwegian voices exist too) or the built-in Windows voice. Turn it on for every Auto Short in
Settings → Shorts style.

## Export formats

| Preset | Output |
|---|---|
| Short / TikTok / Reels | 1080×1920, 60 fps, H.264 High, AAC 48 kHz, -14 LUFS, ≤ 70 MB (TikTok Android limit) |
| YouTube 1080p60 | 1920×1080, 60 fps, 20 Mbps |
| YouTube 1440p60 | 2560×1440 — upload in 1440p even from a 1080p game; YouTube gives it a much better codec |
| Discord | 720p, under 10 MB |

Vertical layouts: **gameplay + blurred background** (default), **full-screen center crop**
(best for shooters, crosshair stays centered), **square zoom**, or **whole frame with bars**.

## Why it's lighter and steadier than GG

- Capture is FFmpeg using Windows' own capture APIs straight into NVENC on your RTX card —
  frames never touch the CPU.
- Game video, webcam, game sound and mic each record in their own process, so a slow sound card
  or webcam can never drag the game video down. Every file carries real clock time and saving lines
  them up to the millisecond.
- The buffer is 2-second files on disk, not RAM. Saving a clip is a file join with no re-encode,
  so it's instant and can't stutter the game.
- Recording runs in its own process with a watchdog: if it ever crashes it restarts in 2 s, and
  if your PC crashes mid-recording the footage is recovered next launch.
- On startup it tests capture methods in order (GPU zero-copy → GPU + download → GDI) and uses
  the first that works on your machine.
- When no game is focused for 20 s it goes to standby and frees the GPU encoder completely.
- Game and mic are recorded on separate tracks, so you can turn your mic up or down when editing.
- Clips get the real game name from the window title, not "Win64".

## Settings worth knowing

- **Codec**: h264 plays everywhere. hevc or av1 is the same quality at about half the size on an
  RTX 50 card.
- **Also show clips from**: add your old GG Moments / ShadowPlay folder to see and edit those clips here.
- **🎤 Test** (next to Mic device): talk for 4 s to check ClipForge hears your mic. In exports your voice is
  cleaned up and levelled, and the game dips a little while you talk.
- **Audio sync offset**: if sound is slightly early or late versus the picture, nudge it.
- **Auto-clip hype moments**: listens for shouting/laughing on the mic and big audio spikes, and
  drops a marker automatically.

## For developers

ClipForge is Windows-first (capture APIs, DPAPI). End users should use the **win64 Release** zip;
`setup.bat` / `.vbs` remain for source checkouts only.

```bat
git clone https://github.com/Knightsaway/ClipForge.git
cd ClipForge
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.lock
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python scripts\fetch_ffmpeg.py
.venv\Scripts\python -m clipforge
```

Local standalone build (same as CI):

```bat
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
rem → dist\ClipForge\ClipForge.exe
```

Tests and lint (also run in GitHub Actions on every PR):

```bat
.venv\Scripts\pytest -q
.venv\Scripts\ruff check clipforge tests
```

Release: use **conventional commits** on `main`; merge the release-please PR (see [AGENTS.md](AGENTS.md)).
That builds `ClipForge-X.Y.Z-win64.zip` (PyInstaller onedir + vendored GPL FFmpeg) and `SHA256SUMS`.
Keep `THIRD_PARTY_NOTICES.md` / `licenses/` with redistributions.

### Licensing (release builds)

| Piece | License | Notes |
|-------|---------|--------|
| ClipForge code | MIT | [LICENSE](LICENSE) |
| Vendored FFmpeg (BtbN win64-**gpl**) | GPL | Separate `ffmpeg.exe` process; source offer in notices |
| PySide6 / Qt | LGPL | Bundled under `_internal/` |

Do not strip `THIRD_PARTY_NOTICES.md` or `licenses/ffmpeg/` from redistributed zips.
