# Changelog

## [0.5.3](https://github.com/Knightsaway/ClipForge/compare/v0.5.2...v0.5.3) (2026-10-30)


### Bug Fixes

* **config:** keep a damaged settings.json instead of overwriting it ([f597921](https://github.com/Knightsaway/ClipForge/commit/f597921a80e56d1c28a29a3fd9ecf44d9d77aa21))
* **editor:** effects just after a late slow-mo are no longer dropped ([b688ee2](https://github.com/Knightsaway/ClipForge/commit/b688ee25cccb850a987f0cd6438620cd5e830320))
* **library:** keep damaged clip sidecars and drafts instead of losing them ([847aad1](https://github.com/Knightsaway/ClipForge/commit/847aad1f1c38268dbbe838e6df51a3659c8780df))
* never lose settings, clip data or drafts to a damaged file; lost effects after late slow-mo ([796c7af](https://github.com/Knightsaway/ClipForge/commit/796c7af52815b40892912616dcd01c93ebb23407))
* **release:** keep pyproject.toml version in step with releases ([df7a881](https://github.com/Knightsaway/ClipForge/commit/df7a88120d5c4ae32407191f57f8434af695b1eb))
* **studio:** a failed re-recorded caster line or friend's-angle sync is reported ([41f5f3d](https://github.com/Knightsaway/ClipForge/commit/41f5f3d76a5f32a2cc78c207dd4f7422a13096f4))
* **studio:** clip list total matches the real length; clip list and playback move into ui/studio/ ([84ae9a2](https://github.com/Knightsaway/ClipForge/commit/84ae9a2310737e6adc1307b97b814b175067ccdb))
* **studio:** stuck caster re-word / friend's-angle sync; AI, auto edit and squad parts move into ui/studio/ ([9d58954](https://github.com/Knightsaway/ClipForge/commit/9d58954ac806bf66b5882659b604a92299aed9c2))
* **studio:** the clip list's total length matches the real video ([9b6098b](https://github.com/Knightsaway/ClipForge/commit/9b6098b6eb425a0b0aa6cae7645cdc84c48b3d5f))
* **studio:** volumes and sizes reopen at the value you saved ([d2f46c6](https://github.com/Knightsaway/ClipForge/commit/d2f46c69673cb06d2ce7da534b2859a2aa0bbc69))
* **studio:** volumes reopen as saved; Studio tests; undo/draft/style logic out of StudioPage ([d4fa75a](https://github.com/Knightsaway/ClipForge/commit/d4fa75a95d7a7e20cf823d023b5ee24c8763e6ea))
* time limits on the last FFmpeg and schtasks calls that had none ([a1b0581](https://github.com/Knightsaway/ClipForge/commit/a1b058189b01bec881bfee49f34e182b02cc48c9))

## [0.5.2](https://github.com/Knightsaway/ClipForge/compare/v0.5.1...v0.5.2) (2026-10-29)


### Performance Improvements

* much less memory — paged library, lighter in the tray and while gaming ([#17](https://github.com/Knightsaway/ClipForge/issues/17)) ([4cad2dd](https://github.com/Knightsaway/ClipForge/commit/4cad2dd082326f8ef45ae99c20960feb8c4f1870))

## [0.5.1](https://github.com/Knightsaway/ClipForge/compare/v0.5.0...v0.5.1) (2026-10-29)


### Bug Fixes

* **app:** options after --then belong to the restarted copy ([881a305](https://github.com/Knightsaway/ClipForge/commit/881a3051cf1dfb428c12a798a54a1b2446527323))
* **capture:** never leave FFmpeg running; webcam on/off switch; GPU priority works ([fe584dd](https://github.com/Knightsaway/ClipForge/commit/fe584dd2e7b14745bb771a655de671f47a2c6a50))
* **editor:** renders with the loop ending failed on FFmpeg 7.1 ([21772d6](https://github.com/Knightsaway/ClipForge/commit/21772d6404a5442754b57a695608d895851ac8c4))
* loop renders on FFmpeg 7.1, leftover FFmpeg processes, webcam switch, GPU priority, slow update restart ([cc4addf](https://github.com/Knightsaway/ClipForge/commit/cc4addf6d5807ac2a779416218d3201b9eeb9ec7))
* **updater:** the app reopens within seconds after an update ([33be4a8](https://github.com/Knightsaway/ClipForge/commit/33be4a8fca866f1bc2fa7679b140acf511b32f82))

## [0.5.0](https://github.com/Knightsaway/ClipForge/compare/v0.4.0...v0.5.0) (2026-10-29)


### Features

* another take, mic reactions, crossfades, session drafts, natural caster voice ([295ace3](https://github.com/Knightsaway/ClipForge/commit/295ace3eaf9d6a7bdab16a748b8e5adccc8f62a6))


### Bug Fixes

* **capture:** no more silent clip endings (heard as the caster cutting the sound) ([57c7d12](https://github.com/Knightsaway/ClipForge/commit/57c7d121c2fae2aa8abbcb4e8a684c31103121ec))

## [0.4.0](https://github.com/Knightsaway/ClipForge/compare/v0.3.0...v0.4.0) (2026-10-29)


### Features

* background auto edits, all-audio preview, real transitions, caster off ([#11](https://github.com/Knightsaway/ClipForge/issues/11)) ([e2e1064](https://github.com/Knightsaway/ClipForge/commit/e2e1064af89434122210779f193d9a53907996ab))

## [0.3.0](https://github.com/Knightsaway/ClipForge/compare/v0.2.2...v0.3.0) (2026-10-29)


### Features

* self-updating exe, installer, performance meter and bug reports ([#9](https://github.com/Knightsaway/ClipForge/issues/9)) ([52f7b4f](https://github.com/Knightsaway/ClipForge/commit/52f7b4fb1691782470b6962918e331a45d0593b1))

## [0.2.2](https://github.com/Knightsaway/ClipForge/compare/v0.2.1...v0.2.2) (2026-10-28)


### Performance Improvements

* **packaging:** slim the win64 bundle (424 → 115 MB) and self-test it ([#6](https://github.com/Knightsaway/ClipForge/issues/6)) ([83f737f](https://github.com/Knightsaway/ClipForge/commit/83f737f2442154e5822f03ded1b7780b0a29a723))

## [0.2.1](https://github.com/Knightsaway/ClipForge/compare/v0.2.0...v0.2.1) (2026-10-28)


### Bug Fixes

* **ci:** make the Windows release build survive non-ASCII output ([#4](https://github.com/Knightsaway/ClipForge/issues/4)) ([9d0b6ce](https://github.com/Knightsaway/ClipForge/commit/9d0b6ce809846acd5e37ae98817eace905a6e6dd))

## [0.2.0](https://github.com/Knightsaway/ClipForge/compare/v0.1.0...v0.2.0) (2026-10-28)


### Features

* CI, win64 releases, and security hardening ([179a06e](https://github.com/Knightsaway/ClipForge/commit/179a06edd84626ca07cf34be873c6f97124fc0da))
* harden security and ship win64 releases ([650f86e](https://github.com/Knightsaway/ClipForge/commit/650f86ec478935fc4598c85ddac24012c89983bd))


### Bug Fixes

* **security:** fail-closed digests; scrub settings ([9396657](https://github.com/Knightsaway/ClipForge/commit/93966577d9427c846bcf88ea11ae76d41096484c))
* **updater:** source installs take the source zip, not the win64 bundle ([749cc71](https://github.com/Knightsaway/ClipForge/commit/749cc714c43b3ad52a8c9b53f94a2debf02db7de))


### Documentation

* add CLAUDE git rules; align versions to 0.1.0 ([22c233a](https://github.com/Knightsaway/ClipForge/commit/22c233a1eda72db0f42de41b3a65fb1cb4c3b087))
* merge CLAUDE rules into AGENTS.md ([0b0a630](https://github.com/Knightsaway/ClipForge/commit/0b0a630a787b27b6d160b02e0c7a9f3deeeb535c))

## [0.1.0](https://github.com/Knightsaway/ClipForge/releases/tag/v0.1.0) (2026-10-28)

### Features

* initial public baseline (local replay buffer, editor, Auto Short)
