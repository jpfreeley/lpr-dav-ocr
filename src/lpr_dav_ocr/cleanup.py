"""Give disk space back, safely: `lpr cleanup` reports what it could free, `lpr cleanup --apply` does it.

  lpr cleanup [--apply] [--only frames,cache,trash,vacuum,logs] [--cache-mb 1024] [--cache-days 14]
              [--trash-days N] [--max-seconds 900]

Every step is either lossless or only removes something that can be made again:

  frames  Saved frames are grayscale (an infrared camera) but stored as three identical color channels. Rewriting them as
          single-channel PNGs keeps every pixel and the file name, and cuts a frame to about 37% of its size. Each new file is
          decoded and compared with the original before it replaces it. Files written in the last 10 minutes are skipped.
  cache   <home>/imgcache holds copies of frames the review page showed, so it can serve them fast. It only ever grows. The
          oldest files go until it is under --cache-mb and nothing is older than --cache-days. The page refetches from the
          share when it needs one again.
  trash   Frames that `lpr dedupe` merged away wait in <data_root>/trash. With --trash-days N (N > 0), files older than N days
          are deleted for good, counted from the day they were merged. Off unless you ask: this is the one step that removes data.
  vacuum  SQLite does not shrink a file after rows are replaced. When more than 15% of the vehicle index is free space,
          rebuild it (not while an import or refresh is running).
  logs    Logs larger than 10 MB keep only their last megabyte.

It stops after --max-seconds so a scheduled run never hogs the machine, and it continues where it stopped next time.
Schedule it with cron, for example weekly at 03:30:

  30 3 * * 0  ~/.lpr-ocr/venv/bin/python -I ~/.claude/skills/lpr-dav-ocr/scripts/cleanup.py --apply --only frames,cache,vacuum,logs

or let the watcher do it (watch.cleanup_days). Never run two at once; a second copy exits at once.
"""
import argparse
import fcntl
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STEPS = ("frames", "cache", "trash", "vacuum", "logs")
SAFE_STEPS = ("frames", "cache", "vacuum", "logs")      # what a scheduled run does unless you ask for more
DONE_MARK = ".gray_done"                                    # a day folder with this file has no color-channel frames left
FRESH_SECONDS = 600                                      # a frame younger than this may still be in use
DEFAULT_ROOT = Path("~/lpr_plates")


def resolve(root=None, home=None):
    """(data_root, home). The repo reads the configuration; the standalone script falls back to fixed places."""
    try:
        from . import config as _config
        cfg = _config.load()
        return Path(root or _config.data_root(cfg)), Path(home or _config.home(cfg))
    except Exception:
        return Path(root or DEFAULT_ROOT), Path(home or os.environ.get("LPR_HOME") or Path.home() / ".lpr-ocr")


def stamp_path(home):
    return Path(home) / "cleanup_last.txt"


def mark_done(home):
    stamp_path(home).write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")


def due(days, home, now=None, hour_from=2, hour_to=6):
    """True when a scheduled run should start: it is the night window and the last run is more than `days` days old."""
    if days <= 0:
        return False
    t = time.localtime(now or time.time())
    if not (hour_from <= t.tm_hour < hour_to):
        return False
    p = stamp_path(home)
    try:
        last = time.mktime(time.strptime(p.read_text().strip(), "%Y-%m-%dT%H:%M:%S"))
    except (OSError, ValueError):
        return True
    return (now or time.time()) - last > days * 86400 - 3600


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def png_color_type(path):
    """The PNG color type byte (0 gray, 2 RGB, ...) read from the 26-byte header, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
        return head[25] if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 26 else None
    except OSError:
        return None


def day_dirs(root):
    base = root / "frames" if (root / "frames").is_dir() else root
    return sorted(p for p in base.iterdir() if re.fullmatch(r"\d{4}-\d\d-\d\d", p.name)) if base.is_dir() else []


def rgb_frames(root, threads=8, now=None):
    """Frames still stored with three color channels (old enough to be safe to rewrite)."""
    now = now or time.time()
    recent = {d.name for d in day_dirs(root)[-2:]}              # the newest days are always looked at
    files = [p for d in day_dirs(root) if d.name in recent or not (d / DONE_MARK).exists() for p in d.glob("*.png")]
    with ThreadPoolExecutor(max_workers=threads) as ex:
        types = list(ex.map(png_color_type, files))
    out = []
    for p, t in zip(files, types):
        if t == 2:
            try:
                if now - p.stat().st_mtime > FRESH_SECONDS:
                    out.append(p)
            except OSError:
                pass
    return out


def to_gray(path):
    """Rewrite one frame as a single-channel PNG if it is pure gray. Returns bytes saved (0 when skipped)."""
    import cv2
    import numpy as np
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None or im.ndim != 3 or im.shape[2] != 3:
        return 0
    if not (np.array_equal(im[:, :, 0], im[:, :, 1]) and np.array_equal(im[:, :, 1], im[:, :, 2])):
        return 0                                           # a real color frame: leave it alone
    ok, buf = cv2.imencode(".png", im[:, :, 0], [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        return 0
    back = cv2.imdecode(buf, cv2.IMREAD_COLOR)             # what every reader of the file will see
    if back is None or not np.array_equal(back, im):
        return 0                                           # not identical: keep the original
    before = path.stat().st_size
    if len(buf) >= before:
        return 0
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(buf.tobytes())
    os.replace(tmp, path)
    return before - len(buf)


def step_frames(root, apply, deadline, log):
    files = rgb_frames(root)
    est = int(sum(p.stat().st_size for p in files) * 0.63) if files else 0
    if not apply:
        log(f"frames: {len(files)} frame(s) are stored as three identical color channels; rewriting them as gray PNGs would free about {human(est)} (lossless)")
        return est
    saved = done = 0
    for p in files:
        if time.time() > deadline:
            log(f"frames: stopped at the time limit after {done} of {len(files)}; the rest continue next run")
            break
        try:
            saved += to_gray(p)
        except OSError as e:
            log(f"frames: skipped {p.name}: {e}")
        done += 1
    else:                                                    # finished the whole list: older days are now fully converted
        for d in day_dirs(root)[:-2]:
            if not (d / DONE_MARK).exists():
                try:
                    (d / DONE_MARK).write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                except OSError:
                    pass
    log(f"frames: rewrote {done} frame(s), freed {human(saved)}")
    return saved


def step_cache(home, apply, cache_mb, cache_days, log, now=None):
    d = Path(home) / "imgcache"
    if not d.is_dir():
        return 0
    now = now or time.time()
    files = []
    for p in d.iterdir():
        try:
            st = p.stat()
            if p.is_file():
                files.append((st.st_mtime, st.st_size, p))
        except OSError:
            pass
    files.sort()                                           # oldest first
    total = sum(s for _, s, _ in files)
    drop, freed = [], 0
    for m, s, p in files:
        too_old = cache_days > 0 and now - m > cache_days * 86400
        too_big = cache_mb > 0 and total - freed > cache_mb * 1024 * 1024
        if too_old or too_big:
            drop.append(p)
            freed += s
    if apply:
        for p in drop:
            try:
                p.unlink()
            except OSError:
                pass
    log(f"cache: {human(total)} in {len(files)} file(s); {'removed' if apply else 'would remove'} {len(drop)} ({human(freed)}), "
        f"keeping at most {cache_mb} MB and {cache_days} days")
    return freed


def merged_at(root):
    """image name -> when `lpr dedupe` merged it away (epoch seconds), from labels/duplicates.jsonl."""
    out = {}
    p = Path(root) / "labels" / "duplicates.jsonl"
    if p.is_file():
        import json
        from datetime import datetime
        with open(p) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    try:
                        out[r["image"]] = datetime.fromisoformat(r["ts"]).timestamp()
                    except (KeyError, ValueError):
                        pass
    return out


def step_trash(root, apply, trash_days, log, now=None):
    """Delete merged-away frames that have been in the trash longer than trash_days. The age counts from the day they were
    merged (moving a file keeps its old modification time), falling back to the file's own time."""
    t = Path(root) / "trash"
    if trash_days <= 0 or not t.is_dir():
        log("trash: left alone" + (" (use --trash-days N to delete merged frames older than N days)" if t.is_dir() else ""))
        return 0
    now = now or time.time()
    when = merged_at(root)
    files = [p for p in t.rglob("*") if p.is_file()]
    old = [p for p in files if now - when.get(p.name, p.stat().st_mtime) > trash_days * 86400]
    size = sum(p.stat().st_size for p in old)
    if apply:
        for p in old:
            p.unlink()
        for d in sorted((x for x in t.rglob("*") if x.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
    log(f"trash: {'deleted' if apply else 'would delete'} {len(old)} of {len(files)} file(s) merged more than {trash_days} days ago ({human(size)})")
    return size


def step_vacuum(home, apply, log):
    p = Path(home) / "vehicles.sqlite"
    if not p.is_file():
        return 0
    lock = open(Path(home) / "vehicles.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("vacuum: a vehicle import or refresh is running; skipped")
        return 0
    try:
        db = sqlite3.connect(str(p), timeout=60)
        free, total, size = db.execute("PRAGMA freelist_count").fetchone()[0], db.execute("PRAGMA page_count").fetchone()[0], db.execute("PRAGMA page_size").fetchone()[0]
        reclaim = free * size
        if total == 0 or free / total < 0.15:
            log(f"vacuum: vehicle index is compact ({human(reclaim)} free inside)")
            db.close()
            return 0
        if apply:
            db.execute("VACUUM")
        db.close()
        log(f"vacuum: vehicle index {'shrunk by' if apply else 'would shrink by'} about {human(reclaim)}")
        return reclaim
    finally:
        lock.close()


def step_logs(home, apply, log, max_mb=10, keep=1024 * 1024):
    freed = 0
    for p in (Path(home) / "logs").glob("*.log") if (Path(home) / "logs").is_dir() else []:
        try:
            size = p.stat().st_size
            if size <= max_mb * 1024 * 1024:
                continue
            if apply:
                with open(p, "rb") as f:
                    f.seek(size - keep)
                    tail = f.read()
                tail = tail[tail.find(b"\n") + 1:]
                with open(p, "r+b") as f:                  # truncate in place so a process that has the log open keeps working
                    f.write(tail)
                    f.truncate(len(tail))
            freed += size - keep
            log(f"logs: {p.name} {'trimmed' if apply else 'would be trimmed'} from {human(size)} to the last {human(keep)}")
        except OSError:
            pass
    return freed


def run(root=None, home=None, apply=False, only=SAFE_STEPS, cache_mb=1024, cache_days=14, trash_days=0, max_seconds=900, log=print):
    root, home = resolve(root, home)
    deadline = time.time() + max_seconds
    freed = {}
    if "frames" in only:
        freed["frames"] = step_frames(root, apply, deadline, log)
    if "cache" in only:
        freed["cache"] = step_cache(home, apply, cache_mb, cache_days, log)
    if "trash" in only:
        freed["trash"] = step_trash(root, apply, trash_days, log)
    if "vacuum" in only:
        freed["vacuum"] = step_vacuum(home, apply, log)
    if "logs" in only:
        freed["logs"] = step_logs(home, apply, log)
    return freed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lpr cleanup", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="data root (default: the configured one)")
    ap.add_argument("--apply", action="store_true", help="do it (the default only reports)")
    ap.add_argument("--only", default=",".join(SAFE_STEPS), help="steps to run, from: " + ", ".join(STEPS) + " (default: all but trash)")
    ap.add_argument("--cache-mb", type=int, default=1024)
    ap.add_argument("--cache-days", type=int, default=14)
    ap.add_argument("--trash-days", type=int, default=0, help="delete merged-away frames older than this many days (0: never)")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--home", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    only = tuple(s.strip() for s in a.only.split(",") if s.strip())
    bad = [s for s in only if s not in STEPS]
    if bad:
        sys.exit(f"unknown step(s): {', '.join(bad)}")
    root, home = resolve(a.root, a.home)
    home.mkdir(parents=True, exist_ok=True)
    lock = open(home / "cleanup.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another cleanup is already running")
    freed = run(root, home, a.apply, only, a.cache_mb, a.cache_days, a.trash_days, a.max_seconds)
    if a.apply:
        mark_done(home)
        try:                                    # one line a day for the Operations page (size of each part of the data root)
            from . import ops as _ops
            _ops.storage_snapshot(home, root)
        except Exception as ex:
            print(f"storage snapshot skipped: {ex}")
    total = sum(freed.values())
    print(f"{'freed' if a.apply else 'could free'} about {human(total)}" + ("" if a.apply else ". Add --apply to do it."))


if __name__ == "__main__":
    main()
