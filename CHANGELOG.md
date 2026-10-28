# Changelog

## [5.2.1](https://github.com/Knightsaway/ClipForge/compare/v5.2.0...v5.2.1) (2026-10-28)

### Features

* Windows Release is a PyInstaller onedir (`ClipForge.exe`) with vendored FFmpeg; see `THIRD_PARTY_NOTICES.md`
* release-please automation for conventional-commit-driven version bumps and changelog

### Bug Fixes

* phone approval opt-in; LAN bind separate; ntfy no longer receives the phone secret link
* updates verify HTTPS and optional SHA256; zip/backup path traversal hardened
* secrets (GIPHY key, phone token, ntfy topic) stored outside plaintext `settings.json`

### Miscellaneous

* CI on PR/main; tagged/manual release builds upload `ClipForge-*-win64.zip` + `SHA256SUMS`
* post-update `pip install -r requirements.txt` keeps `.venv` in sync (source installs)
