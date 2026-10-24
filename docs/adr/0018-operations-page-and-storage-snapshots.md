# ADR 0018: An Operations page fed by cheap reads and a daily storage snapshot

- Status: Accepted
- Date: 2026-10-24

## Context

The pipeline runs unattended, and problems showed up late: a backfill beside the watcher left it 12 minutes behind, a share
error stalled a cycle, and disk use grew without a number to watch. Measuring the frame folder over a network share takes
about 3 minutes, far too slow for a page that should open at once.

## Decision

Add `/ops` and `/api/ops`. The page reads only cheap sources: the watcher log, stamp and lock files, the process list, a
directory listing of today's clips, `accuracy.md` and `confusions.csv`. The server builds the data in a background thread
and answers from the last build (stale while revalidate, 25 seconds). Disk sizes come from one line a day in
`storage_history.jsonl`, written by `lpr ops snapshot` at the end of the nightly cleanup. A short "needs attention" list
comes first. Status always shows an icon and a word, never color alone. The page never sends an alert.

## Consequences

- The page opens in about a second and a slow share does not block it.
- Growth and days-until-full need two nightly snapshots.
- A job that never ran shows as "Never run", not "Late".

## Alternatives considered

- **Measure disk use live.** Too slow over the share.
- **An external monitoring stack.** Heavy for one machine and would hold local paths and plates.
