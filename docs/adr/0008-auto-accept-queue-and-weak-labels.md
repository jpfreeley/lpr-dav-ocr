# ADR 0008: Let confident, agreeing, plate-shaped reads skip the queue

- Status: Accepted
- Date: 2026-10-22

## Context

Reviewing every plate does not scale: one day is several hundred. Two-thirds of first-pass reads are at 0.99 or higher
and almost all of those are right.

## Decision

A plate with confidence at or above `review.auto_accept_conf` (0.99), whose second pass agrees, and whose shape is `ok`,
is **auto-accepted**: it leaves the default queue. It remains reachable in an "Auto-accepted: spot check" view. It gets
no human label, so it is a weak label for any later training and must be kept apart from reviewed ones.

## Consequences

On one labelled day the rule covered about 45% to 49% of plates and was right on 222 of 223 real plates (99.6%). It
also admitted 13 non-plates or unreadable ones among 236 matches, so it saves effort, it does not filter non-plates.
That is one day on one camera: thin evidence ([KI-11](../known-issues.md#ki-11)). `lpr score` reports the rule's
precision from whatever labels exist, including spot checks, and the threshold is configurable. Setting it above 1.0
turns the feature off.

## Alternatives considered

- **No auto-accept.** Safest, slowest.
- **Auto-accept on confidence alone.** Admits look-alike errors that the second pass would have caught.
