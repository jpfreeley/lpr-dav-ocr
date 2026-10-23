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
