# Known issues and limitations

Each has an identifier so other documents can link to it. "Workaround" is what to do today. Nothing here is
hidden: if you hit something that is not listed, please open an issue.

## Reading accuracy

<a id="ki-1"></a>
### KI-1: Look-alike characters, and a clipped first or last character

About 1 in 7 first-pass reads has at least one wrong character on the author's data. The biggest confusion is M read
as H, followed by D/0, T/1, 8/0 and 7/1. Partly visible plates give a read with a missing first or last character.
A high confidence does not rule these out: one deskewed read of a plate scored 0.99 to 1.00 and was wrong.
**Workaround:** the second pass, agreement between passes, and the review page's "2nd pass says" button.
Fixing it properly needs the confusion-aware rerank or recogniser fine-tuning ([ADR 0012](adr/0012-confusion-rerank-deferred.md)).

<a id="ki-2"></a>
### KI-2: Non-plate text gets read

Model names, dealer plate frames, state names and signs are read as if they were plates, because they are text of a
plate-like length. Roughly 14% of reviewed items on the author's day were "not a plate". The plate-shape hint
catches about 63% of them (36 of 57) with no false flags on real plates in that sample, but plate-shaped
non-plates exist. Plate-frame text next to a real plate can be read **instead of** the plate by the first pass; the
second pass often finds the real one.
**Workaround:** the *Not a plate* verdict and the shape badge. The pipeline does not yet drop shape-flagged reads itself.

<a id="ki-8"></a>
### KI-8: Daylight tilt varies; night settings are tuned for night

At night the plates on the author's camera tilt a steady 20 degrees. In daylight the tilt ranged from about -5 to
20 degrees. A single `tilt_degrees` therefore costs some real plates in daylight (2 of about 16 in a small test).
The un-rotated retry recovers most. Per-frame tilt estimation is not implemented.

<a id="ki-9"></a>
### KI-9: Busy daylight clips

Foliage, people and traffic create many motion bursts per clip. Only the strongest `max_segments` are read, so a
real vehicle can be missed in a very busy clip. Such clips are also the slowest to process.

<a id="ki-10"></a>
### KI-10: Plates under the burned-in timestamp are missed

Overlay boxes are blanked before OCR. A plate that sits under the timestamp box is invisible to the pipeline.

<a id="ki-11"></a>
### KI-11: The auto-accept rule rests on one day of labels

Its 99.6% precision comes from a single camera and day (about 480 labels). The rule also lets some non-plates
through (13 of 236 matches in that sample). Audit it with the spot-check view. `review.auto_accept_conf` can be raised.

<a id="ki-12"></a>
### KI-12: Accuracy figures measure precision, not recall

Nothing here measures plates that were never reported. See [accuracy.md](accuracy.md).

## Platform and operations

<a id="ki-3"></a>
### KI-3: PaddlePaddle crashes at interpreter exit

PaddlePaddle's native thread pool segfaults during normal Python shutdown, and macOS then records a "Python quit
unexpectedly" crash report, even though all results were written. The pipeline and second pass end with `os._exit`
and reset the SIGTERM handler in workers to avoid it. If you write your own script that loads the models, do the
same. If crash reports return, look for `libpaddle` in the stack.

<a id="ki-4"></a>
### KI-4: Python 3.12 or older only

PaddlePaddle has no wheels for newer CPython versions at the time of writing, so the OCR extras cannot install there.
The non-OCR parts (config, scoring, review server, watchlist, tests) run on 3.11 or newer.

<a id="ki-5"></a>
### KI-5: Network shares can be slow, and their metadata is stale

With several heavy jobs running at once, small reads from a network share took 10 to 20 seconds on the author's
setup, and file modification times were not updated while another process was appending. The review server works
around both ([ADR 0009](adr/0009-data-layout-and-share-caching.md)), but the root cause is load: do not run more than
one heavy `lpr process` next to an interactive review session. Memory is the usual bottleneck (about 1 GB per
worker). `scripts/pause.sh` helps.

<a id="ki-6"></a>
### KI-6: macOS-centric

The tested platform is macOS. The watcher installer uses `launchd` and `caffeinate`. Paths in the docs use `/Volumes`.
The Python code is portable and the test suite does not need macOS, but nothing else has been exercised.

<a id="ki-7"></a>
### KI-7: One camera model, one file naming scheme

Written against a Dahua camera's `.dav` files, named `HH.MM.SS-HH.MM.SS[...].dav` inside a folder per day. The clip's
time range comes from the file name. Other naming schemes need `clip_stamp` and `watch.clip_end` changed. Clips
are decoded by ffmpeg, so other containers work if the names fit. Do not seek by time with ffmpeg on these files:
timestamps start at a large offset, and a seek lands on the wrong second. The pipeline selects frames by index.

<a id="ki-13"></a>
### KI-13: Frame time is approximate

The time shown for a plate is the clip's start plus the frame number divided by `camera.fps`. It is exact only for a
constant frame rate.

<a id="ki-14"></a>
### KI-14: The watcher and a batch run can collide

If `lpr watch` and `lpr process` process the same day at the same time, a clip near the lookback boundary can be
processed twice (duplicate rows, one image overwritten). Run the batch first, then start the watcher. There is no
cross-process lock on `process`.

<a id="ki-15"></a>
### KI-15: Alerts leave the machine

See [privacy.md](privacy.md). The default alert text includes the plate.

<a id="ki-16"></a>
### KI-16: Labels are keyed by file name

Renaming a saved frame orphans its label. The file name also encodes the first-pass read and confidence, so redoing a
day with different settings can produce new names for the same plate, and old labels will not follow.

<a id="ki-17"></a>
### KI-17: A killed run can leave a partial last line

Index files are flushed per clip, but a process killed mid-write can leave a truncated last line. The review server
ignores an unparseable last line of the labels file and keeps its previous read. Delete the truncated line from a CSV
by hand if a command complains.

<a id="ki-18"></a>
### KI-18: No authentication on the review page

It binds the loopback interface only and relies on that. See [privacy.md](privacy.md#who-can-see-the-review-page).

<a id="ki-19"></a>
### KI-19: Some plates have no outline on the review page

When the second pass finds no plate-shaped read in a frame (the first pass found one only with rotation), the page
says so and shows no crop. Judge it from the full frame.

<a id="ki-20"></a>
### KI-20: The review page's alignment logic is not unit tested

The was/new box labelling is JavaScript in `page.html`. The Python test suite does not run it. It was checked by hand
against insertions, substitutions and deletions.

<a id="ki-21"></a>
### KI-21: The local image cache is keyed by file name

The review page keeps a local copy of each frame it serves in `<home>/imgcache` (see
[ADR 0009](adr/0009-data-layout-and-share-caching.md)). If you reprocess a day with `--redo` and a frame is written again under
the same name, the page keeps showing the old copy. **Workaround:** `rm -rf <home>/imgcache` after reprocessing.
