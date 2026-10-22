# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

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
