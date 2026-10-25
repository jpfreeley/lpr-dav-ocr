# Privacy

A license plate is personal data: it can be linked to a person, and a log of when it was seen says something
about their movements. This project reads plates, keeps a record of them, and can send them in alerts. Treat what
it produces accordingly. This page is practical guidance, not legal advice. Rules about recording public spaces,
retaining plate data and sharing it differ by country and region. Find out which apply to you.

## What the software stores, and where

| Data | Where | Sensitivity | Notes |
|---|---|---|---|
| Recordings (`.dav`) | the camera share | high | Not created by this project. It only reads them. |
| Saved frames with the plate in the file name | `<data_root>/frames/` | high | One full frame per plate, plus the plate text in the name and in `index.csv`. |
| `index.csv`, `second_pass.csv` | `<data_root>/frames/` | high | A timestamped list of plates seen. |
| Vehicle stale marks | `<data_root>/labels/vehicle_flags.jsonl` | high | Plates whose ticket record you marked stale. |
| Plate states | `<data_root>/labels/plate_states.jsonl` | high | The state you set on each plate. |
| Merged duplicates | `<data_root>/labels/duplicates.jsonl`, `<data_root>/trash/` | high | Which frames were merged away, and the frames themselves until you purge them. |
| Your labels and crops | `<data_root>/labels/`, `<data_root>/crops/` | high | Confirmed plate text and plate images. |
| Reports | `<data_root>/reports/` | low | Aggregate counts and character confusions. No plates. |
| Watchlist | `<home>/watchlist.csv` | high | Plates you chose to watch, with your nicknames. |
| Notification topic | `<home>/ntfy_topic` | secret | Whoever knows it can read your alerts. Mode 600. |
| Alert log | `<home>/alerts.jsonl` | high | Which watched plates were seen when. |
| Vehicle index | `<home>/vehicles.sqlite` | medium | Public NYC ticket data (plate, state, make, body, year, color). Public, but it links plates to vehicles. Safe to delete. |
| Image cache | `<home>/imgcache/` | high | Copies of frames the review page served. Safe to delete. |
| OCR models | `<home>/models/` | none | Public models. |

Nothing is written outside `<home>`, `<data_root>` and the config file.

## What leaves the machine

- **OCR models, once,** from the model host, during `lpr setup`. No plate data is involved.
- **Alerts, if you enable them.** A message is sent to the ntfy server you configured. By default the message
  contains your nickname **and the plate**, plus time, confidence and the clip's name. No image is attached.
  The public ntfy.sh service may log or cache messages and the topic name is the only access control. See
  [watcher-and-alerts.md](watcher-and-alerts.md#read-this-first-what-leaves-your-machine) for how to keep alerts
  inside your network (your own ntfy server) or switch them off (`ntfy.enabled = false`).
- **`lpr vehicles import`, once.** It downloads public parking-ticket records from NYC Open Data (data.cityofnewyork.us),
  grouped by plate prefix. The requests contain no plate of yours. The result is stored locally in `<home>/vehicles.sqlite`.
- **The "How's My Driving NY" link on the history page.** Clicking it opens a public website in a new tab and copies the
  plate to your clipboard. The program sends nothing: the plate only leaves your computer if you paste it into that site.
- Nothing else. The review page is loopback-only and uses no external scripts, fonts or analytics.
  At run time the program also sets offline flags so a model library cannot download anything silently.

## Keeping data out of this repository

The `.gitignore` excludes recordings, images (except documentation images), `index.csv`, `second_pass.csv`,
`labels.jsonl`, the watchlist, the topic, the config file and cache folders.

`scripts/check-private-data.sh` checks tracked files for those, for images outside `docs/images/`, for a string shaped like
a generated topic, and for result file names that carry a real-looking plate. It runs in CI. Run it yourself
before pushing, and consider making it a pre-commit hook:

```bash
ln -s ../../scripts/check-private-data.sh .git/hooks/pre-commit
```

**Rules for contributors**

- Tests, documentation, screenshots and demo data must be synthetic. `lpr demo` generates all of it. Plate
  strings in this repository are made up (`DEMO001` and similar).
- Never paste real plates, camera identifiers, host names, user names or file paths from your own setup into
  code, tests, issues or pull requests. Use placeholders such as `<camera-id>` and `/Volumes/camera-share`.
- Screenshots of the review page must come from demo data.
- Commit with a no-reply email address, not your real one (`git config user.email <id>+<name>@users.noreply.github.com`,
  or the address GitHub shows under Settings, Emails). Author and committer addresses are part of every commit.
- Git history is permanent. If something private is committed, rewriting history is required, not a later
  commit that deletes it. Check before you push.

## Retention and deletion

Nothing is deleted automatically. Decide how long you need results, then delete deliberately:

```bash
rm -rf "<data_root>/frames/2026-01-01"       # one day's frames and indexes
rm -rf "<data_root>/crops" "<data_root>/labels"
rm -rf ~/.lpr-ocr/imgcache
```

Deleting frames while keeping labels leaves labels that point at nothing, which is fine for scoring but not for review.
Back up labels if you want to keep them, since they are the only thing that took human effort to make.

## Sharing

Do not share frames, indexes, labels, crops or the watchlist without thinking about the people in them. Aggregate
reports from `lpr score` contain no plates and are safe to share. If you share accuracy numbers, say how they were
measured ([accuracy.md](accuracy.md)).

## Who can see the review page

Anyone who can open `http://localhost:<port>` on your machine: that is anyone using your account, or anyone you let
reach the port. There is no login. Do not expose it to a network. If you need remote access, use an SSH tunnel to
the loopback address.
