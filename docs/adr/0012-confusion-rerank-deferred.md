# ADR 0012: Defer confusion-aware reranking until there are more labels

- Status: Accepted (revisit at about 1,000 labels)
- Date: 2026-10-22

## Context

Errors are concentrated in a few look-alike pairs (M read as H is 12 of 81 character errors). A natural step is to
use per-pass character confusion tables to choose between the two reads, or to score candidate readings.

## Decision

Evaluate it, and adopt it only if it clearly helps. A leave-one-out test on 342 labelled plates with two reads, choosing on
single-character disagreements with a Bayes pick from the per-pass tables built from all other labels, gave **90.4%**
against **89.5%** for "take the higher-confidence read" and 92.7% as the either-right ceiling. The gain is 3 plates,
within noise. It is **not** adopted.

## Consequences

- No extra moving part in the pipeline for an unproven gain.
- Revisit at about 1,000 labels, with the labels from more days and times of day. Known-plate priors (a read one
  character from a confirmed plate is probably that plate) become more useful as the label set grows.
- The confusion table and the evaluation recipe are in `lpr score` and [accuracy](../accuracy.md).
