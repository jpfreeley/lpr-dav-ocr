# ADR 0009: Keep results beside the camera folder; text files; cache for a slow share

- Status: Accepted
- Date: 2026-10-22

## Context

The clips live on a network share written by the camera, which recycles old recordings. Results need a home with the
same long life as the clips, but the camera should never see or prune them. In practice the share was slow under load:
with several heavy jobs running, reads of tiny files took 10 to 20 seconds, which hung the review page. File
modification times on the share were also stale while another process appended, so a cache keyed on the mtime served old data
and the page stopped showing new plates.

## Decision

- Default data root: a folder named `db_<camera folder name>` **next to** the camera folder (not inside it) if it
  exists, else a local folder. `paths.data_root` overrides it.
- Plain text files only (CSV and JSON lines). No SQLite on the share.
- The review server uses **stale-while-revalidate**: it answers from the last good read and refreshes in a
  background thread when the read is more than 2 seconds old. Only the first read of a file blocks. A half-written last
  line keeps the previous read. Newly saved labels are added to the cache at once.
- Frames served are copied to a local cache (`<home>/imgcache`).
- Per-user state (models, watchlist, topic, locks) stays in a local `home` folder, never on the share.

## Consequences

- The camera never touches results. Results survive the recordings being recycled.
- The review page stays responsive when the share is slow. Data can be a couple of seconds stale.
- The real fix for slowness is not overloading the share and the machine ([KI-5](../known-issues.md#ki-5)).
- A cache entry that exists is never invalidated by a process that edits the files by hand within 2 seconds. Acceptable.

## Alternatives considered

- **Write results inside the camera folder.** Risks pruning by the camera.
- **Keep everything on local disk only.** Fine for a single user, loses the clips' shared durability.
- **mtime-keyed cache.** Failed, as above.
