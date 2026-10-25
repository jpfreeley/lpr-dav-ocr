# ADR 0007: A local review page, and labels as an append-only text file

- Status: Accepted
- Date: 2026-10-22

## Context

Accuracy cannot be known, tuned or improved without ground truth, and the cheapest source is the owner correcting the
reads. A correction should cost one keystroke, not retyping a plate. Verdict categories matter for training: a non-plate,
an unreadable plate and an unsure reviewer are different things.

## Decision

A local review page (`lpr review`) with:

- one input box per character, always visible, so fixing a read is changing one box. Editing any box means the read
  was wrong, so there is no separate "wrong" button;
- verdicts: Correct, Save fix, Not a plate, Obstructed, Can't tell, kept separate because richer labels are more
  useful later;
- a state chip (unknown by default), stored but never inferred;
- keyboard-first operation and automatic advance.

Labels are appended to `labels/labels.jsonl`, one JSON object per line, flushed and fsynced. The latest line per image
wins. A leveled crop of each confirmed plate is saved beside the labels as training data.

## Consequences

- Simple, diffable, easy to back up, and no database to corrupt. It is safe on a network share because there is one
  writer and writes only append.
- Relabelling is just another line. The history stays.
- Labels are keyed by image file name ([KI-16](../known-issues.md#ki-16)).
- Label counts drive three plans: scoring now; confusion-aware reranking from roughly 300 to 1,000 labels
  ([ADR 0012](0012-confusion-rerank-deferred.md)); fine-tuning the recogniser from about 1,000 labels, on this
  machine, with a held-out 20%. Fine-tuning on CPU is slow and not yet attempted.

## Alternatives considered

- **A SQLite database.** Unreliable over network shares and harder to inspect.
- **Label in a spreadsheet.** Too slow per plate, no image alongside.
- **A hosted labelling tool.** Would send frames off the machine ([ADR 0001](0001-local-first-processing.md)).
