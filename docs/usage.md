# Usage

## A typical day

```bash
DAY="$CAMERA_DIR/2026-01-01"

lpr process "$DAY"                              # 1. find plates; resumable
lpr second-pass "$(lpr where "$DAY")"           # 2. independent second read of each saved frame
lpr review                                      # 3. look at what needs a human
lpr score                                       # 4. how accurate was it?
```

`lpr where <day-folder>` prints the folder the results are written to, so you never have to build that path by hand.

### Processing

- **Resumable.** Finished clips are recorded in `index.csv`. Run the same command again and it skips them.
  A day folder that is still being recorded picks up its new clips on the next run.
- **Several days:** `scripts/run-days.sh 2026-01-02 2026-01-01 ...` runs `process` then `second-pass` for each day.
- **Trial run:** `lpr process "$DAY" --limit 10`. Add `--out /tmp/trial` to keep it away from real results.
- **Redo:** `--redo` ignores `index.csv`. Remove old frames first if you do not want both, and clear the review
  page's image cache (`rm -rf <home>/imgcache`, see [KI-21](known-issues.md#ki-21)).
- **Speed.** A single-vehicle clip takes about 7 to 15 seconds per worker. Night hours are faster than day.
  A full day of 800 to 900 clips takes about 3 hours with 3 workers on an 8-core laptop. Most of the time goes
  to OCR on frames, not to decoding video.
- **Memory.** Each worker holds about 1 GB. If the machine is swapping, use `--workers 1`.
- **While it runs,** `scripts/follow-second-pass.sh "$DAY"` keeps the second pass current every two minutes, so the
  review page has crops for new plates without waiting for the whole day.

### The second pass

`lpr second-pass` re-reads each saved frame **without** the rotation and at a larger detector size, and records
where the plate is. It never decodes video, so it is quick (about 2 seconds per frame). A plate only enters the
review queue once it has a second pass. When the two passes disagree it also adds a third read of a tight crop (`plate3`) as a tie-breaker. `--retry-empty` re-runs rows with no read and no outline; `--fill-crop` adds the crop read to older rows. It is incremental and safe to run repeatedly. Only one copy runs per
folder at a time.

### Duplicate sightings

A vehicle can end up with two saved frames: two groups in one clip whose reads differ by a character or two (one frame
clipped or misread the plate), or the same plate seen again within seconds. `lpr dedupe` merges them: reads of the same
plate within 30 seconds of each other, chained, are one sighting, and inside one clip plate texts up to two characters
apart count as the same plate. It keeps one frame, in this order: a frame you verified; a read that fits a common plate
pattern, then the longer read (a clipped read can have the higher confidence); both passes agreeing; the sharper crop;
the confidence. Reads you marked not a plate, obstructed or can't tell are never merged.

```bash
lpr dedupe                       # report what would be merged (changes nothing)
lpr dedupe --apply               # merge: list the extra frames in labels/duplicates.jsonl and move them to <data_root>/trash/
lpr dedupe --purge               # delete the trash for good
lpr dedupe --day 2026-01-01 --window 45
```

The review page, history, reports and `lpr score` ignore merged frames. `index.csv` and `second_pass.csv` are never
rewritten, so it is safe to run while the watcher is working. A frame you verified is never dropped in favor of one you
did not.

### Vehicle make, body, year and color

The review and history pages can show what kind of vehicle a plate belongs to: for example "2019 Toyota SUV or wagon,
white (5 tickets)". The source is NYC's public parking-ticket data, which records the plate and what the ticket writer
typed for the make, body type, year and color. You build a local index once:

```bash
lpr vehicles import                      # fiscal years 2027, 2026, 2025; NY, NJ, PA, CT, MA and FL plates; resumable
lpr vehicles import --fy 2024 --states NY
lpr vehicles lookup ABC1234 --state NY
lpr vehicles stats
```

New tickets keep arriving, so refresh the index now and then:

```bash
lpr vehicles refresh                     # re-downloads the current fiscal year and swaps it in when it is complete
lpr vehicles refresh --fy 2026           # a specific year (for example right after a fiscal year ends)
```

A refresh keeps answering lookups from the old data until the new year is complete, so an interrupted refresh loses
nothing. The watcher also starts one in the background about every 7 days when the index exists (`watch.vehicles_refresh_days`).
A new fiscal year (every July) needs no change: its dataset is found by name in the city's public catalog.

The first import takes a while (the city's data is large and the download is polite, three requests at a time) and creates
`<home>/vehicles.sqlite`. The download asks the city for aggregate rows by two-character plate prefix: it contains none of
your plates and sends none. After that every lookup is a local query.

When a plate has a record and no state chip is picked, the review page picks the record's state for you ("State set to NY
(from NYC ticket data)"). A chip you chose is never changed, a record marked stale is ignored, and a plate with no record
leaves the chip alone.

Know the limits. Only vehicles ticketed in New York City in the imported years appear, so many plates have no entry. The
values are what a ticket writer typed: typos and old registrations happen, and the answer is the most common record with
the number of tickets behind it. The city records no model and no trim.

Seen a vehicle that does not match its record (the plate may have moved to another car)? Press **doesn't match** on the
car chip, or **Mark verified stale** on the history page. Nothing else changes: the record stays, marked "verified stale",
and the mark is kept in `<data_root>/labels/vehicle_flags.jsonl` with the record you saw. If a refresh later changes the
record, the page says it was marked stale before the record changed. **undo** removes the mark.

### Freeing disk space

```bash
lpr cleanup                              # report what could be freed (changes nothing)
lpr cleanup --apply                      # do the safe steps
lpr cleanup --apply --only trash --trash-days 30    # also delete merged-away frames older than 30 days
```

What it does, and what it never does:

| Step | What | Loss? |
|---|---|---|
| frames | Saved frames are gray (an infrared camera) but stored as three identical color channels. They are rewritten as single-channel PNGs: same pixels, same file name, about 37% of the size. Each new file is decoded and compared with the original first. New frames are saved this way from the start. | none |
| cache | The review page's image cache (`<home>/imgcache`) is trimmed to `--cache-mb` (default 1024) and 14 days, oldest first. The page refetches from the share when it needs a frame again. | none |
| vacuum | The vehicle index is compacted when more than 15% of it is free space. | none |
| logs | Logs over 10 MB keep their last megabyte. | old log lines |
| trash | Merged-away frames older than `--trash-days`. Only when you ask. | **yes** |

On the author's data, about 5.4 GB of frames and 4.3 GB of cache could be freed from a 8.9 GB frame folder and a 5.3 GB cache,
and new frames save about 1.3 GB a day.

The watcher runs the safe steps once a day between 02:00 and 06:00 at low priority (`watch.cleanup_days`). Or use cron:

```
30 3 * * *  /path/to/venv/bin/lpr cleanup --apply --max-seconds 3000
```

A run stops after `--max-seconds` and continues next time; two runs never overlap. It does not touch the camera's own clips.
An option this does not take, because it loses pixels: replacing old full frames with JPEGs would save another 60% but
makes re-reading and reviewing them worse.

### Pausing and stopping

```bash
scripts/pause.sh pause     # SIGSTOP every lpr job (memory stays held)
scripts/pause.sh resume
scripts/pause.sh stop      # free the memory; progress is kept, run the same command again later
scripts/pause.sh status
```

## Reviewing

`lpr review` serves the page on `http://localhost:<port>/` (default 8770). It opens on the first plate that
needs review in the newest day that has any. See [review-ui.md](review-ui.md).

Options: `--root DIR` (a data root), `--day YYYY-MM-DD`, `--port N`.

## Scoring

`lpr score` reads your labels and the pass results and writes `reports/accuracy.md` and `reports/confusions.csv`
under the data root. See [accuracy.md](accuracy.md) for how to read it and what it cannot tell you.

## Watching for plates

See [watcher-and-alerts.md](watcher-and-alerts.md).

## Disk use

Each saved plate is one full-resolution PNG, about 1 MB. A busy day can be 500 to 1000 of them.
Crops of confirmed plates are about 50 KB each. Nothing is deleted automatically. See
[privacy.md](privacy.md) for retention advice.

## Command reference

`lpr <command> -h` prints every option. The pipeline flags mirror the `[pipeline]` keys in
[configuration.md](configuration.md).
