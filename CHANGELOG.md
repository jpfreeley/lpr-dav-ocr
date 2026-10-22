# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Second pass ladder (un-rotated 960 px, rotated 960 px, rotated 640 px outline-only) and `--retry-empty`.
- Crop read: when the two passes disagree, a third read of a tight, enlarged crop around the outline breaks the tie.
  Stored as `plate3` and `conf3`; it never changes `plate2` or auto-accept. `--fill-crop` adds it to existing rows.
  On the author's labels it picked the right read 53% of the time against 48% for "take the higher confidence".
- The review page shows the crop read, and the watcher also matches it against the watchlist.

### Fixed

- The "+ box" and "- box" buttons act on the box you last clicked in (a button click used to lose the selection).

## [0.1.0] - 2026-10-22

First packaged release. Developed against one camera and one site; see [docs/known-issues.md](docs/known-issues.md).

### Added

- `lpr process`: motion-segmented frame selection, PaddleOCR reading with an un-rotated fallback, best-frame export,
  resumable index.
- `lpr second-pass`: independent re-read of saved frames, plate outline, plate-shape (aspect) measurement.
- `lpr review`: local review page with per-character correction boxes, five verdicts, state chips, a leveled crop with
  per-letter overlay, day selector, queue views, auto-accept, and a Watch button. Loopback only.
- `lpr score`: accuracy against labels, confidence calibration, character confusion table, auto-accept precision.
- `lpr watch` and `lpr watchlist`: unattended processing of new clips, exact and one-character-off matching, ntfy alerts.
- `lpr demo`: synthetic results and clips, so everything can be tried without a camera or real plates.
- TOML configuration with a documented template; `lpr config`, `lpr setup`, `lpr where`.
- Helper scripts: batch runs, second-pass follower, pause/resume/stop, macOS LaunchAgent installer.
- Guard script that blocks real data from the repository, plus tests, CI and architecture decision records.
