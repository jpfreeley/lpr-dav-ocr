# Configuration

One TOML file. Every key is optional. The template with comments is
[`config/config.example.toml`](../config/config.example.toml).

**Where it is read from:** `$LPR_CONFIG`, else `~/.config/lpr-dav-ocr/config.toml`. If neither exists the
defaults apply. `lpr config init` creates it, `lpr config show` prints the effective values.

**Precedence, lowest to highest:** built-in defaults, the config file, environment variables, command-line flags.

| Environment variable | Overrides |
|---|---|
| `LPR_CONFIG` | path of the config file |
| `LPR_HOME` | `paths.home` |
| `LPR_CAMERA_DIR` | `paths.camera_dir` |
| `LPR_DATA_ROOT` | `paths.data_root` |
| `LPR_ALLOW_NET` | (internal) lets `lpr setup` download models. Normal runs refuse network use. |
| `LPR_NO_FORCE_EXIT` | (testing) do not end the process with `os._exit` after a run |

Do not commit your edited config file. The repository's `.gitignore` excludes `config.toml`.

## `[paths]`

| Key | Default | Meaning |
|---|---|---|
| `home` | `~/.lpr-ocr` | Local per-user state: OCR models, the watchlist, the ntfy topic, alert log, locks, logs and the image cache. Keep it on a local disk. |
| `camera_dir` | empty | Folder that contains one `YYYY-MM-DD` folder per day of clips. |
| `data_root` | empty | Where frames, indexes, labels, crops and reports go. Empty means: a folder named `db_<camera folder name>` next to `camera_dir` if it exists, else `~/lpr-data`. |

The data root layout:

```
<data_root>/
  frames/<date>/        saved best frames, index.csv, second_pass.csv
  labels/labels.jsonl   your review verdicts (append only)
  crops/                leveled plate crops of confirmed plates (training data)
  reports/              accuracy.md, confusions.csv from `lpr score`
```

## `[camera]`

| Key | Default | Meaning |
|---|---|---|
| `frame_width`, `frame_height` | 1920, 1080 | Used by the review page to place the plate outline, and as a fallback when a clip's size cannot be probed. |
| `fps` | 30 | Used to turn a frame number into a time of day on the review page. |
| `tilt_degrees` | 20.0 | Rotate frames counter-clockwise by this much before OCR. 0 disables it. |
| `overlays` | timestamp and channel label boxes | Rectangles `[x0, y0, x1, y1]` as fractions of the frame that hold burned-in text. They are blanked before OCR and ignored by motion detection. Without this, the timestamp is read as if it were a plate. |

**If you move the overlay on the camera.** Add an `[[camera.overlay_changes]]` entry with `from` (the camera clock, the time in the
clip file names) and the new `overlays`. A clip, or a saved frame, that starts at or after `from` is masked with the new boxes and
anything earlier keeps the old ones, so a backfill of old days stays correct. Move it far from where plates cross (a corner) and
make the new box a little larger than the text, because some cameras show the text mid-move while you drag it. Leave the old
top-of-frame box out of the new list, otherwise the freed area is still blanked and a plate there would not be read.

### Measuring the tilt

On a fixed camera, plates share a tilt. Levelling them lets the detector run at a smaller, faster size
(640 px instead of 960 px, about 1.5 times faster with the same hit rate in testing).

1. Process a few clips with `tilt_degrees = 0` and `det_side = 960`.
2. Open the review page. The crop panel rotates the plate by `tilt_degrees`, so with 0 you see the raw tilt.
3. Set `tilt_degrees` to the angle that makes the plate text level, and reload the page. Repeat until it looks level.
4. Set `det_side = 640` and compare the hit rate on a handful of clips against the 960 run.

The tilt can differ by time of day and by where a vehicle passes. In daylight the tilt ranged from about -5 to 20
degrees on the author's camera. That is why the pipeline retries empty segments without rotation (see
[ADR 0003](adr/0003-fixed-camera-deskew-and-two-detector-sizes.md)).

If you find the overlay boxes by eye: open one saved frame, note the pixel box around the burned-in text,
and divide by the frame size.

## `[pipeline]`

| Key | Default | Meaning |
|---|---|---|
| `det_side` | 640 | Text detector input size in px. Use 960 when `tilt_degrees` is 0. |
| `min_conf` | 0.80 | Reads below this confidence are ignored. |
| `plate_regex` | `[A-Z0-9]{5,8}` | What a plate read looks like. Do **not** require both letters and digits: letters-only (vanity) and numbers-only plates exist. Adjust the length range for your region. |
| `max_segments` | 6 | Strongest motion bursts kept per clip. Daylight clips can have dozens of bursts (foliage, people). |
| `max_frames` | 16 | Full-resolution frames read per burst. |
| `stride` | 2 | Read every Nth moving frame. |
| `workers` | 3 | Parallel clips. About 1 GB of RAM each. |
| `stop_conf`, `stop_hits`, `min_frames` | 0.98, 2, 4 | Stop reading a burst once reads reach `stop_conf` at least `stop_hits` times and at least `min_frames` frames were read. The minimum stops a first confident but clipped read from ending the search. |
| `fallback_segments`, `fallback_frames` | 2, 6 | Where rotation found nothing in one of the top bursts, retry this many frames un-rotated at 960 px. 0 frames disables it. |
| `min_motion`, `motion_k` | 3.0, 6.0 | A frame counts as moving when its change from the previous frame exceeds `max(min_motion, median + motion_k * MAD)`. Raise them on a noisy camera. |
| `pad` | 2 | Frames added around each moving frame. |

Every one of these is also a `lpr process` flag (`lpr process -h`).

## `[review]`

| Key | Default | Meaning |
|---|---|---|
| `port` | 8770 | The review page listens on `127.0.0.1` at this port. Pick a free one. |
| `reject_aspect` | 3.5 | A read whose text region has a length/height ratio above this is auto-rejected: it leaves the review queue, is not counted in plate history and never triggers a watchlist alert. US plates are about 2:1 and plate reads cluster at 1.8 to 2.4. On the author's labels every one of 120 reads above 3.5 was not a usable plate. Check `lpr score` on your own data. 0 switches it off. |
| `value_queue` | 40 | How many plates the **Most valuable first** queue lists (see [the review page](review-ui.md#review-by-value)). |
| `not_plate_texts` | `["NEWYORK"]` | Exact texts printed on plates that are never the plate, such as the state banner. A read of one is never auto-approved and is auto-rejected unless a plate-shaped read exists elsewhere in the frame. See [the review page](review-ui.md#known-non-plate-words-the-state-banner). |
| `record_accept_conf` | 0.97 | A plate that has a vehicle in the ticket data is auto-accepted from this confidence when both passes agree and the shape is plate-like. 0 turns it off. See [the ticket record as evidence](review-ui.md#the-ticket-record-as-evidence). |
| `confirmed_days` | 365 | How many days either side of a sighting a plate whose vehicle you confirmed (the **matches** button) vouches for other sightings of exactly that text. The normal window is `recent_days`. |
| `recent_days` | 30 | A read identical to a plate you verified (Correct or Save fix) within this many days of the sighting is auto-approved. Plates one character away from such a plate get a one-click correction instead. Set 0 to switch it off. |
| `auto_accept_conf` | 0.99 | A plate at or above this confidence, with both passes agreeing and a plate-like shape, skips the review queue. **Validate this on your own labels first**: `lpr score` prints the rule's precision. Set it above 1.0 to switch auto-accept off. |

## `[watch]`

| Key | Default | Meaning |
|---|---|---|
| `poll_seconds` | 60 | How often to look for new clips. |
| `lookback_minutes` | 90 | Only clips that ended this recently are picked up. Older unprocessed clips are left to `lpr process`. |
| `settle_seconds` | 45 | A clip must have ended this long ago and stopped growing. |
| `cooldown_seconds` | 600 | Minimum time between alerts for the same watched plate. |
| `dedupe` | true | After each batch the watcher merges duplicate sightings of one plate ([`lpr dedupe`](usage.md#duplicate-sightings)): the frame you verified, or else the complete, sharper one, is kept and the others move to `<data_root>/trash`. Nothing is deleted until you run `lpr dedupe --purge`. |
| `vehicles_refresh_days` | 7 | If the local vehicle index exists ([`lpr vehicles import`](usage.md#vehicle-make-body-year-and-color)), the watcher refreshes the current fiscal year in the background this often, at low priority. 0 switches it off. |
| `cleanup_days` | 1 | Once a day, between 02:00 and 06:00, the watcher frees disk space in the background ([`lpr cleanup`](usage.md#freeing-disk-space)): saved frames are rewritten as gray PNGs (lossless), the image cache and logs are trimmed, the vehicle index is compacted. 0 switches it off. |
| `cache_mb` | 1024 | Size limit for the review page's image cache. |
| `trash_keep_days` | 0 | Delete frames that `lpr dedupe` merged away after this many days. 0 keeps them until you run `lpr dedupe --purge`. |
| `dedupe_window` | 30 | Seconds. Reads of one plate this close together are one sighting. |
| `workers` | 1 | Workers for the watcher's batches. Keep it low. |

## `[ntfy]`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Set `false` to make alerts fail closed (nothing leaves the machine). |
| `server` | `https://ntfy.sh/` | Use your own server URL to keep alerts off a public service. |
| `topic_file` | `{home}/ntfy_topic` | File with the topic name. Created by `lpr watch --init`. Treat the topic like a password. |
