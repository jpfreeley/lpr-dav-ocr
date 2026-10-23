# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Second pass ladder (un-rotated 960 px, rotated 960 px, rotated 640 px outline-only) and `--retry-empty`.
- Crop read: when the two passes disagree, a third read of a tight, enlarged crop around the outline breaks the tie.
  Stored as `plate3` and `conf3`; it never changes `plate2` or auto-accept. `--fill-crop` adds it to existing rows.
  On the author's labels it picked the right read 53% of the time against 48% for "take the higher confidence".
- The review page shows the crop read and lets you click it to copy it into the boxes, and the watcher also matches it against the watchlist.

- Plate history: `lpr seen <plate>` and a History page (`/history`) list every sighting of a plate across all days, exact
  match, from plates you confirmed or corrected or that passed auto-accept (an option includes unreviewed reads). The
  review page shows "seen N other times within 7 / 30 / 90 days / all time"; the number opens a montage of the earlier
  sightings. Search suggestions show how often each plate was seen.

- Re-check view: earlier "not a plate" labels where the second pass found different plate-shaped text, so a real plate hiding behind a model badge or sign can be recovered.
- Editing the boxes re-runs the checks live (seen count and montage, same-last-4 hints, verified-plate offer, watch status) for the text you typed.
- Times are labeled **CamTime** (the camera clock from the clip file name) on the review page, montage, history page and alerts.
- History page: shows whether a plate is on the watchlist, lets you watch or stop watching it (summary and the suggestion
  chips), and lists your watched plates when no search is active.
- Auto-reject: reads with an aspect ratio above `review.reject_aspect` (3.5) leave the queue, plate history and watchlist
  alerts, with an "Auto-rejected: spot check" view. 120 of 120 such reads were not usable plates on the author's labels.
- "Same last four" hints: for an AAANNNN read, known plates with the same four digits and one or two different leading
  letters are offered as one-click buttons (right 107 of 113 times on the author's labels, when one existed).
- Recently verified plates: a read equal to a plate you verified within `review.recent_days` (30) is auto-approved; a
  read one character away gets a one-click correction. `lpr score` reports the precision of both.
- "Best guess" on the review page when the passes disagree: prefers a read that fits AAANNNN or the TLC pattern, then a
  crop read that backs a pass, then the more confident pass (81 of 144 right against 76 and 69 for the alternatives).
- NYC TLC pattern hint on the review page: offers `T123456C` when a read looks like `11234560`. A hint, not a filter.

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
