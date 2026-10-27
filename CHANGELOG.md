# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Most valuable queue: "seen on N days" now counts only other days and only sightings that count in the plate history (not ones you marked obstructed, not a plate or can't tell), so it no longer contradicts "not seen again" on the review page.
- Review page: the gold standard button is always visible beside History and Watch. On a frame you have not saved yet, clicking it marks the frame as the gold standard when you save it with Correct or Save fix (it used to appear only after you had verified the frame).
- Review page: in the **Most valuable first** queue, **Last reviewed** jumps to the label you saved most recently, not the furthest plate in time order of one day. The other queues keep the old meaning.
- Nightly cleanup also writes the storage snapshot and refreshes `reports/accuracy.md` and `confusions.csv`. In the standalone script the snapshot step failed silently under `python -I`, because that mode leaves the script folder off the import path.
- Reports: the stale-record count counts only marks whose record is unchanged. A mark whose record changed after a refresh is shown separately as no longer applying.
- History page: searching a plate that has a NYC ticket record failed with "Search failed" because the stale-mark button used a helper that exists only on the reports page.

### Added

- Gold standard: stars in the review page's montage of earlier sightings (verified frames only), the gold frame shown first in the montage, and a "gold standard in this plate's history" line with the crop on the review page.
- Gold standard frames: mark a verified frame as the clear exemplar of its plate (a star in the plate history, a button on the review page). The history shows it, the review page offers it as a visual comparison for that plate and for near reads, the duplicate merge never drops it, and `lpr score` reports the first pass on gold frames. Saved in `labels/gold.jsonl`.
- Review page: auto-accept from confidence 0.97 when both passes agree, the shape is plate-like and the plate has a vehicle in the local ticket data (`review.record_accept_conf`). A **matches** button beside the vehicle line records that you saw the car matches the record; the plate then vouches for its other sightings for a year (`review.confirmed_days`). `lpr score` and the self-check measure the new rule.
- `review.not_plate_texts` (default `NEWYORK`): exact words that are printed on plates but are never the plate. A read of one is never auto-approved and is auto-rejected unless the second pass found plate-shaped text elsewhere in the frame; then that text becomes the best guess.
- Review page: when no read fits three letters then four digits but a fix of a digit in a letter position would (0 as D, 6 as G, 2 as Z), a one-click offer shows the fixed read. Never automatic.
- Review page: **Most valuable first** queue, the top 40 unreviewed plates across all days ranked by how much one label changes the results (watchlist matches, plates where one label approves many more sightings, near-matches of verified plates, repeat plates with disagreeing reads). `review.value_queue` sets the size.
- Second pass records `elsewhere` and `elsewhere_conf`: a plate-shaped read other than the chosen one, looked for when the chosen read is wide (`--fill-elsewhere` fills older days). Auto-reject no longer hides a frame that has one, and the review page offers it as a button.
- `lpr selfcheck` and `scripts/install-selfcheck.sh`: an hourly check that sends an ntfy alert only when something needs attention (watcher, jobs, disk, or an automatic rule falling below its floor on your labels). ADR 0019.
- `[[camera.overlay_changes]]`: say when you moved the burned-in timestamp on the camera. Clips and saved frames that start at or after that camera-clock time are masked with the new boxes, earlier ones keep the old boxes, so backfills stay correct and the freed area is read again.
- `lpr demo results --days N`: several synthetic days that share plates, so the Reports page has regulars to show. README and docs now carry screenshots of the Reports and Operations pages made from it.
- Operations page (`/ops`, `lpr ops`): needs-attention list, pipeline and watcher, scheduled jobs, storage with a nightly snapshot, accuracy by confidence, confusion-matrix heatmap, review and labels, alerts and machine health. See docs/operations.md.
- Reports: a Vehicles section (fleet mix, vehicle kind by hour, regulars by vehicle kind, record coverage by plate type) and a Vehicle column in the regulars table, from the local ticket index. The report build reuses one stale-mark map and remembers a missing file, so it takes about a second.
- Second pass ladder (un-rotated 960 px, rotated 960 px, rotated 640 px outline-only) and `--retry-empty`.
- Crop read: when the two passes disagree, a third read of a tight, enlarged crop around the outline breaks the tie.
  Stored as `plate3` and `conf3`; it never changes `plate2` or auto-accept. `--fill-crop` adds it to existing rows.
  On the author's labels it picked the right read 53% of the time against 48% for "take the higher confidence".
- The review page shows the crop read and lets you click it to copy it into the boxes, and the watcher also matches it against the watchlist.

- Plate history: `lpr seen <plate>` and a History page (`/history`) list every sighting of a plate across all days, exact
  match, from plates you confirmed or corrected or that passed auto-accept (an option includes unreviewed reads). The
  review page shows "seen N other times within 7 / 30 / 90 days / all time"; the number opens a montage of the earlier
  sightings. Search suggestions show how often each plate was seen.

- "Verified stale" mark for a vehicle record that does not match what you see (car chip button, history page), kept per plate with the record you saw.
- Review page layout: the lookup lines (seen count, vehicle, live check) take a full line each, the right column scrolls when it is taller than the window, and the Correct / Save fix / verdict buttons stay pinned at the bottom of it.
- `lpr cleanup` frees disk space safely (lossless gray frames, image cache, merged-away frames on request, vehicle index, logs); the watcher runs the safe steps nightly (`watch.cleanup_days`). New frames are saved single-channel.
- The state chip is picked from the NYC ticket record when the plate has one and no chip is chosen.
- `lpr vehicles refresh` re-downloads the current fiscal year and swaps it in; the watcher starts one about weekly (`watch.vehicles_refresh_days`).
- `lpr vehicles`: a local index of make, body type, year and color by plate built from NYC's public parking-ticket data; shown on the review page and the history page. No plate of yours is sent anywhere.
- History page: the plate links to How's My Driving NY (copies the plate, opens the site without a referrer).
- Pattern badges for NJ (A12BCD), NY commercial (12345AB) and NY moped (123AB4) plates, with one-click state buttons; the TLC hint also selects NY. The Reports plate-mix chart counts both.
- The watcher runs the duplicate merge after each batch (`watch.dedupe`, `watch.dedupe_window`).
- `lpr dedupe`: merges duplicate sightings (same plate within 30 s, or a misread of it in the same clip), keeping the frame you verified, then the complete, sharper one. Extra frames go to a trash folder; `--purge` deletes them.
- Reports: a Refresh button and an optional 60-second auto-refresh that pauses in a hidden tab.
- Reports page (`/reports`): traffic heatmap and hourly profile, new versus returning plates, regulars and their usual
  arrival times, repeat-visit gaps, day-to-day overlap, plate type and state mix, watched-plate timeline, review effort
  per day and first-pass accuracy by hour. Local, with day filters, a table view for every chart and light and dark themes.
- Confidences on the review and history pages show three decimals (a read of 0.9864 used to display as 0.99, next to the 0.99 auto-accept line).
- Re-check view: earlier "not a plate" labels where the second pass found different plate-shaped text, so a real plate hiding behind a model badge or sign can be recovered.
- Editing the boxes re-runs the checks live (seen count and montage, same-last-4 hints, verified-plate offer, watch status) for the text you typed.
- Times are labeled **CamTime** (the camera clock from the clip file name) on the review page, montage, history page and alerts.
- History page: shows whether a plate is on the watchlist, lets you watch or stop watching it (summary and the suggestion
  chips), and lists your watched plates when no search is active.
- Auto-reject: reads with an aspect ratio above `review.reject_aspect` (3.5) leave the queue, plate history and watchlist
  alerts, with an "Auto-rejected: spot check" view. 120 of 120 such reads were not usable plates on the author's labels.
- A state set on one sighting of a plate applies to all its other sightings (one `plate_states.jsonl` line per plate; no labels written for auto-accepted ones); the save toast says how many.
- Picking a "same last 4" or "one character from a verified plate" hint also sets the state you gave that plate before.
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
