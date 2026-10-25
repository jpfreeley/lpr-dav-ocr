# ADR 0017: Store frames as single-channel PNG and clean up nightly

- Status: Accepted
- Date: 2026-10-24

## Context

The camera is infrared, so every frame is gray, but frames were saved with three identical channels. The frame folder
and the review page's image cache grew by about 1.3 GB a day.

## Decision

Save new frames as single-channel PNG. Rewrite old ones losslessly, and replace a file only after decoding both and
comparing every pixel, using an atomic rename. The result is 37% of the old size. Run a cleanup once a night (02:00 to
06:00, low priority, resumable, one run at a time) that also trims the image cache to 1 GB and 14 days, deletes merged-away
frames 7 days after the merge, compacts the vehicle index when over 15% is free space, and trims large logs.

## Consequences

- About 60% less disk for frames with no change in what the review or second pass sees.
- The nightly run needs the Mac awake in that window. A run that is cut off continues the next night.
- The cleanup then records a storage snapshot for the [Operations page](../operations.md).

## Alternatives considered

- **JPEG or WebP for old frames.** Would save about 60% more but makes re-reading and review worse. Rejected.
- **Contrast enhancement (CLAHE) before reading.** Measured to hurt, so not part of this.
- **Delete old frames.** Loses the evidence behind labels.
