# AGENTS.md — working on ClipForge

Single source of truth for humans and coding agents (Claude, Cursor, etc.). Prefer this over older README install notes when they conflict. `CLAUDE.md` is a pointer here.

## Hard rules (non-negotiable)

These exist because force-pushed `main` already orphaned feature branches.

### Git — never rewrite shared history

1. **Never** `git push --force` / `--force-with-lease` to `main` (or the default branch).
2. **Never** amend, rebase, filter-repo, or change author/email on commits already on `origin/main`.
3. **Never** create a new root commit or re-import the tree onto `main` to “clean history.”
4. Before branch / rebase / PR work: `git fetch origin`. If local `main` ≠ `origin/main`, fast-forward: `git checkout main && git reset --hard origin/main`.
5. Rebase feature work onto **current** `origin/main` (`git rebase --onto origin/main <old-base> <branch>` when needed). If there is **no merge-base**, stop and tell the human — do not invent a new history.
6. Force-push is allowed **only** for the current feature branch after a requested rebase, and only with `--force-with-lease`. Never force-push someone else’s branch.
7. Do not push tags or cut GitHub Releases unless the human explicitly asked.
8. Do not skip hooks (`--no-verify`) unless the human explicitly asked.

### Commits & versioning

1. **Conventional Commits only** (`feat:`, `fix:`, `ci:`, …). release-please owns semver + `CHANGELOG.md`.
2. Do **not** hand-edit `__version__`, `pyproject.toml` version, or `.release-please-manifest.json` for routine releases.
3. Do not commit secrets, `.venv/`, `ffmpeg/`, `dist/`, or AppData files.

### Security / product defaults (do not regress)

1. Phone approval stays **opt-in**; LAN bind is a separate setting; default bind is localhost.
2. Never put phone tokens or secret links into ntfy Click URLs.
3. Secrets go through `secrets_store` (DPAPI); migrating must scrub plaintext `settings.json`.
4. Updater: HTTPS only; win64 releases **fail closed** without a usable SHA256; frozen installs never take source zipballs.
5. Keep zip-slip guards on update + backup restore.

### Working style

1. Prefer small, reviewable diffs. No drive-by refactors.
2. Add/extend tests for updater, backup path safety, secrets migrate, phone auth helpers when touching them.
3. When stuck on git history or release-please: explain and ask — do not “fix” by rewriting `main`.

## What ClipForge is

Windows-first Python/PySide6 desktop app: replay buffer, editor, Auto Short. **End users** get a
**PyInstaller onedir** zip (`ClipForge.exe` + vendored FFmpeg). Source + `.venv` / `setup.bat` is for
**developers** only.

## Repo map (distribution)

| Path | Role |
|------|------|
| `clipforge/` | Application package (`python -m clipforge`) |
| `ClipForge.spec` | PyInstaller onedir spec |
| `scripts/fetch_ffmpeg.py` | Pin + SHA256 download of BtbN win64-**gpl** FFmpeg |
| `scripts/build_windows.ps1` | Local/CI Windows release build |
| `THIRD_PARTY_NOTICES.md` | MIT + GPL FFmpeg + Qt LGPL obligations |
| `licenses/` | Bundled third-party license texts |
| `setup.bat` / `*.vbs` | Legacy **dev** bootstrap only — not the product |

Version lives in:

- `clipforge/__init__.py` → `__version__` (marked for release-please)
- `pyproject.toml` → `[project].version` (marked for release-please via `extra-files`)
- `.release-please-manifest.json` → last released version

## Conventional commits (required)

release-please drives version bumps and `CHANGELOG.md` from commit messages on `main`.

Format:

```text
<type>(optional-scope): <short summary>

[optional body]
```

### Types that matter for releases

| Type | Semver | Examples |
|------|--------|----------|
| `feat` | **minor** | `feat(capture): add AV1 NVENC preset` |
| `fix` | **patch** | `fix(updater): verify SHA256 before install` |
| `perf` | patch | `perf(editor): faster concat demux` |
| `revert` | patch | `revert: feat(capture): add AV1 NVENC preset` |
| `feat!` / `fix!` / footer `BREAKING CHANGE:` | **major** | `feat!: drop Python 3.10` |
| `docs`, `ci`, `chore`, `test`, `refactor`, `style`, `build` | no bump (unless configured) | `ci: harden release-please upload` |

Scopes (optional, useful): `capture`, `editor`, `updater`, `ui`, `phone`, `security`, `packaging`, `ci`.

### Good / bad examples

```text
feat(phone): default approval server to localhost-only
fix(backup): reject drive-letter zip members on restore
ci: upload win64 assets from release-please job
chore: ignore .ruff_cache
docs: document frozen ffmpeg path in AGENTS.md

# Avoid — release-please will ignore or mis-classify:
Updated stuff
WIP
fix stuff
Feat: new thing          # capital Type without conventional form is fragile; use feat:
```

Keep the subject ≤ ~72 chars, imperative mood (“add”, not “added”).

## Day-to-day development (Windows)

```bat
git clone https://github.com/Knightsaway/ClipForge.git
cd ClipForge
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.lock
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python scripts\fetch_ffmpeg.py
.venv\Scripts\python -m clipforge
```

Checks (also run in CI):

```bat
.venv\Scripts\pytest -q
.venv\Scripts\ruff check clipforge tests scripts
```

Local standalone build (same as release):

```bat
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
rem → dist\ClipForge\ClipForge.exe
```

## CI / release pipelines

```text
PR / push to main  →  ci.yml          (lint + tests)
push to main       →  release-please.yml
                      ├─ opens/updates Release PR (chore: release X.Y.Z)
                      └─ when merged & release cut:
                           build win64 → upload ClipForge-*-win64.zip + SHA256SUMS
manual tag v*      →  release.yml     (fallback / emergency rebuild)
```

### Normal release flow

1. Land work on `main` with **conventional commits** (via PRs).
2. release-please opens or refreshes a **Release Please** PR that bumps versions + CHANGELOG.
3. Review and **merge** that PR.
4. release-please creates tag `vX.Y.Z` + GitHub Release notes.
5. Same workflow builds PyInstaller onedir, vendors FFmpeg, uploads:
   - `ClipForge-X.Y.Z-win64.zip`
   - `SHA256SUMS`

In-app updater prefers the `*win64*.zip` asset and checks SHA256 when present.

### Do not

- Manually edit `__version__` / CHANGELOG for routine releases (release-please owns them).
- Strip `THIRD_PARTY_NOTICES.md` or `licenses/` from redistributable zips.
- Commit `.venv/`, `ffmpeg/`, `dist/`, secrets, or AppData files.
- Put phone tokens or ntfy topics into notification Click URLs.

## Security / packaging rules of thumb

- Phone approval is **opt-in**; LAN bind is a separate setting; HTTP only on trusted networks.
- Updates: HTTPS only; win64 **fail closed** without a usable SHA256SUMS digest; frozen installs never take source zipballs.
- FFmpeg is a **separate GPL binary** invoked via subprocess — see `THIRD_PARTY_NOTICES.md`.
- Secrets (`giphy_key`, `phone_token`, `ntfy_topic`) go through `secrets_store` (DPAPI on Windows), not plaintext backups; migrate must scrub `settings.json`.

## Agent checklist before finishing a change

1. Conventional commit message(s) match the change type (`feat` / `fix` / …).
2. Tests/lint still pass for touched pure logic; add tests for updater/path/security helpers when relevant.
3. No secrets in the tree; no drive-by refactors unrelated to the task.
4. If the change affects distribution, update `THIRD_PARTY_NOTICES.md` / README only when needed — not for every feature.
5. Do not push tags or cut releases unless the user asked; merging to `main` with proper commits is enough for release-please.
6. Confirm you did not rewrite `origin/main` history.
