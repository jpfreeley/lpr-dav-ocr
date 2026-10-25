# ADR 0010: A polling watcher, a LaunchAgent, and ntfy alerts with one-character fuzzy matching

- Status: Accepted
- Date: 2026-10-22

## Context

The user wants new clips processed unattended, and a notification when a particular plate appears. Clips arrive on a
network share, where file-change notifications do not work. Camera files are written while being recorded. First-pass
reads are wrong about 1 time in 7, so exact matching alone would miss sightings.

## Decision

- **Polling watcher** (`lpr watch`): every `poll_seconds` look at today's and yesterday's folders for clips that ended
  within `lookback_minutes` (end time parsed from the file name), are not yet in `index.csv`, and whose size has been
  unchanged since the previous poll. Process that batch with one worker, run the second pass, then check the watchlist.
- **macOS LaunchAgent** with `KeepAlive`, running under `caffeinate -i`. Installed by `scripts/install-watcher.sh`
  from a template, with no personal values in the repository.
- **Watchlist matching:** *exact*, or *possible* (one wrong, missing or extra character) for plates of 5 or more
  characters. It checks the first-pass read, its variants and the second-pass read.
- **ntfy** for delivery: a random topic stored mode 600 outside any repository; a cool-down per plate; failed sends are
  retried. The server is configurable so a private instance can be used.
- **Alert text** includes the nickname **and** the plate (the maintainer's choice), time, confidence and the clip's
  name. No image is attached.
- A "share unreachable" alert after 30 minutes, at most every 6 hours.

## Consequences

- Latency is about two to three minutes after a vehicle passes.
- Alerts are the one place plate text leaves the machine, so they are opt-in and documented
  ([privacy](../privacy.md#what-leaves-the-machine)). The topic is a shared secret, not authentication.
- Possible matches produce some false alerts. The message names the differing read.
- The watcher and a manual batch run on the same day can collide ([KI-14](../known-issues.md#ki-14)).
- It runs on one machine, while logged in, awake and with the share mounted. Running it on the NAS that hosts the share
  was considered and deferred.

## Alternatives considered

- **File-system events.** Not available on network shares.
- **Alert with nickname only.** Safer. Available by editing the message text, and the privacy page says how.
- **Email, SMS or a chat webhook.** More setup and more places a plate would go.
