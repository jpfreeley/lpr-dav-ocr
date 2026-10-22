# Installation

## Requirements

| | |
|---|---|
| Python | 3.11 or 3.12. Use 3.12. PaddlePaddle publishes wheels for CPython 3.12 and older, so 3.13 and newer cannot install the OCR extras yet. |
| OS | macOS is the tested platform. The pipeline, review page and scoring are plain Python and should run on Linux. The watcher's installer (`scripts/install-watcher.sh`) uses macOS `launchd`. |
| Disk | About 1 GB for the virtual environment and about 150 MB of models, plus the frames you keep (roughly 1 MB per saved plate). |
| Memory | About 1 GB per worker. The default is 3 workers for batch runs and 1 for the watcher. Lower `pipeline.workers` on a small machine. |
| ffmpeg | Not required. The OCR extras bundle a static ffmpeg (`imageio-ffmpeg`). A system `ffmpeg` on the PATH is used if present. |
| Camera files | A folder per day (`YYYY-MM-DD`) of `.dav` clips named like `HH.MM.SS-HH.MM.SS[...].dav`, readable from the machine. A network share is fine. |

## Install

```bash
git clone <this repository>
cd lpr-dav-ocr
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[ocr]"
```

Check it:

```bash
lpr --version
python -m unittest          # about 70 tests, a few seconds, no OCR models needed
```

Download the OCR models once. This is the only step, besides optional alerts, that uses the network:

```bash
lpr setup
```

The models are cached under the `home` folder (default `~/.lpr-ocr/models`). After this, the program
sets offline flags so a missing model fails loudly instead of silently downloading.

## Configure

```bash
lpr config init       # writes ~/.config/lpr-dav-ocr/config.toml from the template
lpr config path       # shows where it is
lpr config show       # shows the effective values
```

At minimum set `paths.camera_dir` to the folder that contains the `YYYY-MM-DD` folders. See
[configuration.md](configuration.md) for every key. Do not commit the file you edit.

### Mounting a network share on macOS

In Finder choose Go, then Connect to Server, and enter `smb://<host>/<share>`. The share appears under
`/Volumes/<share>`. Use that path in `paths.camera_dir`.

Create the output folder yourself, next to (not inside) the camera folder, so the camera's own
recycling never touches it:

```bash
mkdir "/Volumes/<share>/db_<camera folder name>"     # the default data root is found by this name
```

Or set `paths.data_root` to any folder you prefer. If neither exists, results go to `~/lpr-data`.

## Try it with no camera

```bash
lpr demo results --out /tmp/lpr-demo      # fake processed results, instant
lpr review --root /tmp/lpr-demo           # http://localhost:8770/
```

To exercise the real pipeline on synthetic video as well:

```bash
lpr demo clips --out /tmp/lpr-demo-cam/CAM1
lpr process /tmp/lpr-demo-cam/CAM1/2026-01-01 --out /tmp/lpr-demo-frames/2026-01-01 --workers 1
lpr second-pass /tmp/lpr-demo-frames/2026-01-01
```

It reads the made-up plates, so this proves the OCR stack works on your machine.

## First real run

```bash
lpr process "$CAMERA_DIR/2026-01-01"
lpr second-pass "$(lpr where "$CAMERA_DIR/2026-01-01")"
lpr review
```

## Optional: the unattended watcher (macOS)

See [watcher-and-alerts.md](watcher-and-alerts.md). In short: create a topic with `lpr watch --init`,
subscribe to it in the ntfy app, add plates with `lpr watchlist add`, then
`scripts/install-watcher.sh install`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `pip` cannot find `paddlepaddle` | Your Python is newer than 3.12. Create the virtual environment with `python3.12`. |
| macOS says the Xcode license has not been accepted when running `git`, `python3` or `brew` | Run `sudo xcodebuild -license accept` once. Homebrew installs fail until you do. |
| `ModuleNotFoundError: lpr_dav_ocr` in a script | You used `python -I` (isolated mode) outside the virtual environment, or the environment is not active. Use `lpr ...`. |
| Python "quit unexpectedly" reports after a run | A known exit crash in PaddlePaddle's thread pool. The program already ends the process directly to avoid it. See [known-issues.md](known-issues.md#ki-3). |
| Reads appear but the review page is empty or stale | You are pointing at a different data root than `lpr process` wrote to. `lpr where <day-folder>` prints the folder `lpr process` uses. |
| The watcher cannot read the share when started by `launchd` | macOS may ask once whether Python can access a network volume. Approve it, or grant access in System Settings, Privacy and Security. |

## Uninstall

```bash
scripts/install-watcher.sh uninstall       # if you installed the watcher
pip uninstall lpr-dav-ocr
rm -rf ~/.lpr-ocr ~/.config/lpr-dav-ocr    # models, watchlist, topic, caches, config
```

Your frames and labels live in the data root. They are not removed. See [privacy.md](privacy.md) for
how to delete them.
