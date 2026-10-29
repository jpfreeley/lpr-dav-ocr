# ADR 0020: Review queues that spend human time where it changes the most

- Status: Accepted
- Date: 2026-10-29

## Context

Reviewing is the only manual step, and the backlog is always longer than the time available. A day order (oldest first)
treats every plate alike, but one label does not change the same amount everywhere: a label on a plate seen many times
approves its other sightings, a watched plate matters more than an unknown one, and a wrong "verified" plate poisons the
checks that trust verified plates (auto-approve, the one-character hint, the ticket-record rule). Measured on the author's
labels, about 130 pairs of verified or auto-accepted plates were exactly one character apart, which is more than chance
alone explains, so some of those plates were verified wrongly.

## Decision

Keep the per-day queue, and add three views that rank across all days and drop a plate once it is reviewed.

- **Review by value.** Unreviewed plates ranked by what a label would change: a watchlist match first, then plates where one
  label approves many other sightings of the same text, a read one character from a verified plate, and repeat plates whose
  two reads disagree. Plates with no such reason are left out, and the page says so when none is left.
- **Gold standard frames.** The user marks one very clear frame per plate (`labels/gold.jsonl`). It is a reference for the
  montage and the history, and it is a human statement that the read is certain. It changes no rule and no threshold.
- **Pair check.** For each pair of verified, fixed or auto-accepted plates one character apart (a substitution, a missing or
  an extra character), offer the weaker plate for review. Weaker means: no ticket record against one with a record, then
  unverified against verified, then fewer sightings. Saving a label from this view writes `pair_checked`, so the plate does
  not return. A pair is a question, not an error, because two cars can have similar plates.

All three are computed from the label files on demand and kept for 20 seconds, then dropped when a label is saved. Nothing new
is stored except `pair_checked` on a label and the gold file, so a queue repopulates by itself as reads arrive.

## Consequences

- The most useful labels come first, so each review minute moves the history and the automatic rules further.
- The queues are views over existing data. Deleting the cache or restarting the server loses nothing.
- Ranking is a judgment (the order of the four reasons is a constant in `server.py`), not a measurement.
- The pair check finds mistakes only where a near-identical plate exists. A wrong read with no neighbor is invisible to it.
- Each request that builds a queue reads every day. That cost stayed small only after the plate-state list and the day list
  were cached (page requests dropped from 2 to 3 seconds to about 20 ms), so those caches are part of this design.

## Alternatives considered

- **Review strictly by time.** Simple, but a long backlog is read in an order that ignores value.
- **Let gold frames tune the rules automatically.** A few frames chosen by hand are a biased sample, and the rules protect
  against hiding real plates (see [0019](0019-self-check-alerts-only-on-trouble.md)).
- **Treat every one-character pair as an error and merge it.** Real neighboring plates exist, and a merge is hard to undo.
