# ADR 0011: A plate is reviewable only after its second pass

- Status: Accepted
- Date: 2026-10-22

## Context

The review page uses the second pass for the plate's outline, the leveled crop, the alternative read and the shape
hint. A plate shown before its second pass looked incomplete and made the reviewer judge without the second opinion.

## Decision

A plate is held out of the "Needs review" and "All unreviewed" views until a second-pass row exists for it. This is true
even if that pass found no read. The header counts the held plates ("waiting for 2nd pass") and the page re-checks every
30 seconds, so they join the queue by themselves. The "Everything" view still lists them.

## Consequences

- Reviewers never see a half-processed plate in the default views.
- Review lags processing by the second pass's backlog. `scripts/follow-second-pass.sh` keeps that short while a day runs.
- A second pass that finds no plate to outline still counts as done. The page then says so instead of "pending"
  ([KI-19](../known-issues.md#ki-19)).
