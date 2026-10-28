# CLAUDE.md — hard rules for agents on ClipForge

Read [AGENTS.md](AGENTS.md) for product/release context. **This file overrides** when they conflict.

## Git: never rewrite shared history

These rules exist because force-pushed `main` already orphaned feature branches twice.

1. **Never** `git push --force` / `--force-with-lease` to `main` (or the default branch).
2. **Never** amend, rebase, filter-repo, or change author/email on commits that are already on `origin/main`.
3. **Never** create a new root commit or re-import the tree onto `main` to “clean history.”
4. Before branch / rebase / PR work: `git fetch origin`. If local `main` ≠ `origin/main`, fast-forward local `main` first (`git checkout main && git reset --hard origin/main`).
5. Feature branches: rebase onto **current** `origin/main` with  
   `git rebase --onto origin/main <old-base> <branch>`  
   when histories diverged. If there is **no merge-base**, stop and tell the human — do not invent a new history.
6. Force-push is allowed **only** for the current feature branch after a requested rebase, and only with `--force-with-lease`. Never force-push someone else’s branch.
7. Do not push tags or cut GitHub Releases unless the human explicitly asked.

## Commits & versioning

1. **Conventional Commits only** (`feat:`, `fix:`, `ci:`, …). release-please owns semver + `CHANGELOG.md`.
2. Do **not** hand-edit `__version__`, `pyproject.toml` version, or `.release-please-manifest.json` for routine releases.
3. Do not commit secrets, `.venv/`, `ffmpeg/`, `dist/`, or AppData files.
4. Do not skip hooks (`--no-verify`) unless the human explicitly asked.

## Security / product defaults (do not regress)

1. Phone approval stays **opt-in**; LAN bind is a separate setting; default bind is localhost.
2. Never put phone tokens or secret links into ntfy Click URLs.
3. Secrets go through `secrets_store` (DPAPI); migrating must scrub plaintext `settings.json`.
4. Updater: HTTPS only; win64 releases **fail closed** without a usable SHA256; frozen installs never take source zipballs.
5. Keep zip-slip guards on update + backup restore.

## Working style

1. Prefer small, reviewable diffs. No drive-by refactors.
2. Add/extend tests for updater, backup path safety, secrets migrate, phone auth helpers when touching them.
3. When stuck on git history or release-please: explain the situation and ask — do not “fix” by rewriting `main`.
