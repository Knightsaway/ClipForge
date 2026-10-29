# Code signing policy

Windows release files (`ClipForge.exe` and `ClipForge-X.Y.Z-setup.exe`) will be signed once our
SignPath Foundation application is approved. Until then, Windows SmartScreen may say *"Windows protected
your PC"* — click **More info → Run anyway**. Each release lists SHA-256 checksums (`SHA256SUMS`) that
you can compare with `Get-FileHash`.

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

## What gets signed

Only files built from this repository by GitHub Actions (`.github/workflows/release-please.yml`) from
a tagged release. FFmpeg (`ffmpeg\bin`) and Qt/Python libraries are third-party files and are
shipped as published by their authors (see `THIRD_PARTY_NOTICES.md`).

## Team

| Role | Members |
|---|---|
| Committers and reviewers | [Knightsaway](https://github.com/Knightsaway) and the repository's collaborators |
| Approvers (approve every signing request) | [Knightsaway](https://github.com/Knightsaway) |

All changes reach `main` through pull requests. Everyone with write access uses two-factor
authentication on GitHub (and SignPath).

## Privacy

See [PRIVACY.md](PRIVACY.md): ClipForge has no telemetry and only goes online for the features listed there.
