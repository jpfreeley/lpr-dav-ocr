# Operations page

`http://localhost:8770/ops` shows whether the pipeline is healthy. It is read-only apart from one button (recompute accuracy).
The page rebuilds its numbers in the background every 30 seconds and answers from the last build, so it opens fast.

| Panel | What it shows | Where the numbers come from |
|---|---|---|
| Needs attention | a short list, worst first: watcher quiet over 10 minutes, watcher errors in 24 h, more than 15 clips waiting, under 20 GB free, a job late, machine overloaded | computed from the panels below |
| Pipeline and watcher | clips on the share today, read so far, waiting, errors | the camera share and the watcher log |
| Clips processed by hour | new clips per hour over 24 h | the watcher log |
| Scheduled jobs | watcher, vehicle index refresh, nightly cleanup, duplicate merge; status, last run, off switch | stamp files and lock files in the local state folder, `duplicates.jsonl` |
| Storage | size of each part of the data root, free space, growth per day, days until full | `storage_history.jsonl` (one line a day) and the disk |
| Confidence and accuracy | exact-match rate by confidence band, agreement, night versus day | `reports/accuracy.md` from `lpr score` |
| Confusion matrix | 36 by 36 heatmap of which characters the reader swaps, plus the top pairs | `reports/confusions.csv` |
| Review and labels | per-day split of every read, label counts, stale vehicle marks, re-check queue | the review store |
| Alerts | alerts sent per day | `alerts.jsonl` (the page never sends one) |
| Machine | load, swap, process counts | the operating system |

A job shows **Late** when it has not run in twice its usual interval, **Off** when its switch file exists
(`no_vehicle_refresh`, `no_cleanup`, `no_dedupe` in the state folder), and **Never run** when no stamp exists yet.

## Storage snapshots

Measuring the frame folder over a network share takes minutes, so the page never does it. The nightly cleanup runs
`lpr ops snapshot` after it finishes, which appends one line to `storage_history.jsonl`. Run it by hand to get the first
point. Growth and days until full need two snapshots.

## For developers

`ops.py` holds pure functions over paths (`watcher`, `jobs`, `storage`, `accuracy`, `confusion`, `review`, `alerts`,
`attention`, `build`) and is tested in `tests/test_ops.py`. The server route `/api/ops` caches the build
(`ops_cached`), and `POST /api/ops/score` starts `lpr score` detached.
