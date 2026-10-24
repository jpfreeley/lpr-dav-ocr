# ADR 0015: Auto-approve recently verified plates, auto-reject wide reads

- Status: Accepted
- Date: 2026-10-23

## Context

[ADR 0008](0008-auto-accept-queue-and-weak-labels.md) lets confident, agreeing, plate-shaped reads skip the review queue.
Two more kinds of read cost the user time and are almost always decided the same way: a read that equals a plate they
verified recently, and a read of a model badge or sign (text much wider than a plate).

## Decision

- **Auto-approve** a read that exactly equals a plate the user verified within 30 days. Only human-verified plates vouch,
  never auto-approved ones. This was right on 165 of 168 checked reads. A read that is one character off a verified plate is
  an offer on the review page, never automatic.
- **Auto-reject** a read whose text region has an aspect ratio over 3.5. This was right on 120 of 120 reads. The user chose
  the threshold, and `review.reject_aspect` changes it (0 turns it off).
- A human label always wins over both rules. Auto decisions write weak labels only.

## Consequences

- The queue holds mostly hard cases, so each review minute is worth more.
- Three wrong auto-approvals in 168 are possible, and they are visible with the "auto" filter.
- Auto-approved plates never vouch for others, so one error cannot spread.

## Alternatives considered

- **Auto-approve one-character-off reads.** Rejected: the cases that look alike are exactly the ones that matter.
- **Filter by letters or digits.** Rejected in [ADR 0006](0006-plate-shape-hint-not-a-character-filter.md).
