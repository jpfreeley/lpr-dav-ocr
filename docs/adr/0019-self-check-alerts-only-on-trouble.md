# ADR 0019: A self-check that speaks only when something needs attention

- Status: Accepted
- Date: 2026-10-26

## Context

The pipeline runs unattended, and some automatic rules decide what the reviewer never sees. Two kinds of failure were
silent. A job could stop (the nightly snapshot failed for a day with no message, and the accuracy report was three days
old), and a rule could drift (on the author's labels, auto-reject was right on 120 of 120 reads at first and 174 of 185
a few days later, which hides real plates from review). The Operations page shows both, but only when someone opens it.

## Decision

Add `lpr selfcheck`, run hourly by its own LaunchAgent. It does two checks.

- **Health**, every run: the same list as the Operations page's "needs attention" strip (watcher quiet, errors, clips waiting,
  low disk, a late job, an overloaded machine).
- **Rules**, about weekly: parse the precision of auto-accept, auto-approve and auto-reject from `reports/accuracy.md` and
  compare each with a floor (95%, 95%, 97%). Auto-reject has the higher floor because its mistake is invisible. Fewer than
  50 labeled examples means no verdict.

It sends an ntfy alert only when a check fails, repeats a problem at most once a day (health) or once a week (rules), and
forgets a problem that clears. The alert text names no plate. It changes no setting.

## Consequences

- Silence means healthy. Because it runs apart from the watcher, it can report a dead watcher.
- The floors are judgments, not measurements; they are constants in `selfcheck.py`.
- The rule precision comes from labeled reads, which skew toward the hard cases, so the numbers read lower than the
  real rate. A falling trend is the signal, not the absolute value.
- It tells you and stops there. Retuning a threshold stays a human decision.

## Alternatives considered

- **Tune thresholds automatically.** A rule that adjusts itself from a labeled sample that is biased toward hard reads
  could drift without anyone noticing, and the rules protect against hiding real plates.
- **Run the check inside the watcher.** Cannot report that the watcher stopped.
- **A daily "all clear" message.** Trains you to ignore it.
