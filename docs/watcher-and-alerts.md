# Watcher and alerts

`lpr watch` keeps an eye on the camera folder, processes new clips soon after they finish, and tells you
when a plate on your watchlist is seen.

## Read this first: what leaves your machine

Alerts go through [ntfy](https://ntfy.sh). By default that is the public ntfy.sh service. **An alert that
includes a plate number sends that plate number to that service.** The topic name is the only access control:
anyone who learns it can read the alerts, and a public service may log or cache messages.

Choices, from most private to least:

1. Turn alerts off: `ntfy.enabled = false`. The watcher still processes clips.
2. Run your own ntfy server and set `ntfy.server` to it. Nothing leaves your network.
3. Keep alerts on the public service, but make the messages carry only a nickname. The message text is built in
   `watch.check_matches`. The default text includes the nickname **and** the plate.
4. Use the public service as shipped.

No image is ever attached. The topic is random, stored mode 600, and never written to a repository.

## Setup

```bash
lpr watch --init                 # creates a random topic, prints it once
                                 # subscribe to that topic in the ntfy app on your phone
lpr watch --test-alert           # sends "LPR watcher test"; confirm it arrives
lpr watchlist add ABC1234 "Blue sedan"
lpr watchlist list
```

The Watch button on the [review page](review-ui.md) adds plates too. The watchlist is a plain file,
`<home>/watchlist.csv` (template: [`config/watchlist.example.csv`](../config/watchlist.example.csv)). Editing it
by hand is fine; it is re-read on every cycle.

## Matching

For every new plate, the watcher compares these reads with each watchlist entry: the first-pass read, its
variants, and the second-pass read.

| Level | Rule |
|---|---|
| **exact** | Same plate. |
| **possible** | One wrong, missing or extra character, for plates of 5 or more characters. Shorter plates are never matched fuzzily, because one character is too much of a short plate. |

On the author's data about one in seven first-pass reads has at least one wrong character, which is why the
possible level exists. Expect some false possibles; the message says which character differs.

The same watched plate does not alert again within `watch.cooldown_seconds` (default 10 minutes). If the
network is down, an alert is not recorded as sent and is retried on the next cycle.

## What the watcher does each cycle

1. Look at today's and yesterday's folders (so a clip that crosses midnight is found).
2. Keep clips whose **end time, taken from the file name,** is within `lookback_minutes`, that are not in
   `index.csv` yet, and whose **size is unchanged since the previous poll**. A clip still being written is skipped.
3. Run the pipeline on that batch with `watch.workers` workers, then the second pass, then check the watchlist.

Clips older than `lookback_minutes` are not touched. Process those with `lpr process`. Do not run `lpr process`
on a day while the watcher is also processing it, or both may process the same clip.

If the share is unreachable for 30 minutes the watcher sends one "share unreachable" alert (at most every 6 hours).

## Running it unattended (macOS)

```bash
scripts/install-watcher.sh install     # a LaunchAgent: starts at login, restarts if it stops
scripts/install-watcher.sh status
scripts/install-watcher.sh logs
scripts/install-watcher.sh uninstall
```

The agent runs under `caffeinate -i` so the machine does not idle-sleep while it runs. It needs the share
mounted and you logged in. If either is missing it waits, and catches up later because processing is resumable.
The log is `<home>/logs/watch.log`.

On first use under `launchd`, macOS may ask whether the Python executable may access a network volume. Approve it.

Set `LPR_LABEL` to name the agent something of your own, and `LPR_BIN` if `lpr` is not on the PATH.

## Trying it without waiting for a vehicle

```bash
lpr watch --replay 2026-01-01          # which already-processed plates WOULD alert (sends nothing)
lpr watch --replay 2026-01-01 --send   # really send them
lpr watch --once --dry-run             # one cycle, no alerts
```

## Limits

- Polling, not file events: file-change notifications do not work on network shares.
- The latency from a vehicle passing to an alert is the clip length plus the settle time plus processing:
  typically two to three minutes.
- No login on anything; alerts rely on the secrecy of the topic. See [privacy.md](privacy.md).
- It runs on one machine and does not coordinate with a second watcher. See [known-issues.md](known-issues.md).
