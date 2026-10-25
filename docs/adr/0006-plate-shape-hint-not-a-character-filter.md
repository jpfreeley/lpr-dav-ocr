# ADR 0006: Use the plate's 2:1 shape as a hint; never filter by letters or digits

- Status: Accepted
- Date: 2026-10-22

## Context

The first pass reads any plate-length text: vehicle model names, dealer plate-frame text, state names and signs
(about 14% of reviewed items were "not a plate"). An obvious filter is "a plate has letters and digits". That is
wrong: vanity plates are letters only, and numbers-only plates exist. It was proposed and then rejected on exactly
that ground.

US plates are 12 by 6 inches, a 2:1 aspect. The detected text region of real plates clusters near that, while
model names and signs do not.

## Decision

Measure the aspect ratio of the second pass's detected text region and record `shape` = `ok` (1.5 to 3.0), `wide`
or `narrow`. Use it to badge plates on the review page and as one condition of auto-accept
([ADR 0008](0008-auto-accept-queue-and-weak-labels.md)). Do **not** filter reads by character class, and do
not hard-drop shape-flagged reads yet.

## Consequences

Measured on 399 labelled items: real plates had an aspect of 1.94, 2.19, 2.41 (10th, 50th, 90th percentile);
non-plates had a median of 3.35 and a 90th percentile of 5.46. A shape other than `ok` flagged 36 of 57
non-plates and 0 of 342 real plates. That is strong enough to be a hard filter, but some non-plate text is
plate-shaped (a few signs and plate-frame texts fell inside the band), so it is a hint for now. Dropping shape-flagged
reads in the pipeline is a possible next step ([KI-2](../known-issues.md#ki-2)).

The aspect band is for US plates. Other regions need their own band.

## Alternatives considered

- **Letters-and-digits rule.** Rejected: excludes valid plates.
- **A word list to reject model names.** Brittle and region-specific.
- **Require the box to lie on moving pixels.** Plausible, not done.
