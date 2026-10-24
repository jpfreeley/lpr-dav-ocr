# ADR 0014: Merge duplicate sightings of one plate

- Status: Accepted
- Date: 2026-10-23

## Context

A vehicle that stops, or one that spans two clips, is saved and read more than once. On the author's data, 402 groups
of duplicates (447 extra frames) were found, all inside one clip, and 245 of them had been reviewed twice. Counting them
twice also inflated every report and made the watcher alert more than once. Accuracy on labeled plates rose from 53.6% to
61.1% once the duplicates were excluded, so they hurt the measurement as well.

## Decision

Treat reads of the same plate within 30 seconds of the next one (chained) as one sighting. Inside one clip, plate texts up
to two edits apart also count as the same plate, because one frame often misreads or clips it. Keep one frame, chosen in
this order: a frame the user verified, then a read that fits a common plate pattern or is longer, then one where both passes
agree, then the sharper crop (variance of the Laplacian), then the confidence. Confidence comes last because a clipped read
(`1123456` for `T123456C`) can carry the higher confidence.

The other frames are listed in `labels/duplicates.jsonl` and moved to `<data_root>/trash/`. `index.csv` and
`second_pass.csv` are never rewritten, so a merge is safe while the watcher works. The watcher merges after each batch, and
trash older than 7 days after the merge is deleted by the nightly cleanup (the user's choice).

## Consequences

- Reviews, reports and scoring count a vehicle once, and the user never labels the same car twice.
- Human work is never thrown away: a group is left alone if the user verified a frame that would be dropped.
- A wrong merge (two different cars within 30 seconds with similar plates) is possible but rare, and the frames stay in the
  trash for 7 days.

## Alternatives considered

- **Delete duplicates immediately.** Not reversible. The trash and the 7-day delay cost little disk.
- **Pick the highest confidence.** Measured to choose clipped reads.
- **Merge across clips by fuzzy match.** Too many false merges between different cars. Only exact text merges across clips.
