"""Data for the Operations page: is the pipeline healthy, what ran, what it costs in disk, how good the reads are.

Everything here is read-only and cheap except `storage_snapshot`, which walks the frame folder (minutes over SMB) and is
meant to run once a day from the nightly cleanup:

  lpr ops snapshot [--root DIR]     append today's sizes to <home>/storage_history.jsonl
  lpr ops                                 print the page data as JSON

Paths: `home` is the local state folder (~/.lpr-ocr), `root` is the data root (frames/ labels/ reports/ trash/).
"""
import csv
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

JOBS = [("vehicles", "Vehicle index refresh", 7), ("cleanup", "Nightly cleanup", 1), ("dedupe", "Duplicate merge", None)]


def _lines(path, keep=4000):
    try:
        with open(path, errors="replace") as fh:
            return fh.read().splitlines()[-keep:]
    except OSError:
        return []


def _jsonl(path):
    out = []
    for line in _lines(path, 100000):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def log_events(lines, now=None):
    """[(epoch, text)] from the watcher log. Lines carry only HH:MM:SS, so walk backwards and step a day each time the
    clock wraps."""
    now = now or datetime.now()
    day, prev, out = now.date(), None, []
    for ln in reversed(lines):
        m = re.match(r"(\d\d):(\d\d):(\d\d) (.*)", ln)
        if not m:
            continue
        secs = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and secs > prev:
            day -= timedelta(days=1)
        prev = secs
        t = datetime(day.year, day.month, day.day) + timedelta(seconds=secs)
        out.append((t.timestamp(), m[4]))
    return list(reversed(out))


def watcher(home, now=None):
    now = now or time.time()
    ev = log_events(_lines(Path(home) / "logs" / "watch.log"), datetime.fromtimestamp(now))
    day = now - 86400
    errs = [(t, x) for t, x in ev if t >= day and re.search(r"error|failed|skipped", x, re.I)]
    clips = [(t, int(m[1])) for t, x in ev if (m := re.search(r": (\d+) new clip", x))]
    per_hour = [0] * 24
    for t, n in clips:
        if t >= day:
            per_hour[datetime.fromtimestamp(t).hour] += n
    merges = sum(int(m[1]) for t, x in ev if t >= day and (m := re.search(r"merged (\d+) duplicate", x)))
    alerts = [t for t, x in ev if x.startswith("ALERT sent")]
    last = ev[-1][0] if ev else None
    return dict(last_line=last, idle_s=max(0, round(now - last)) if last else None, errors_24h=len(errs),
                recent_errors=[dict(t=t, text=x[:160]) for t, x in errs[-6:]], clips_24h=sum(n for t, n in clips if t >= day),
                clips_per_hour=per_hour, merged_24h=merges, alerts_24h=sum(1 for t in alerts if t >= day),
                workers=_int(Path(home) / "watch_workers", 2))


def _int(p, default):
    try:
        return int(Path(p).read_text().strip())
    except (OSError, ValueError):
        return default


def _locked(p):
    """True when another process holds this lock file."""
    try:
        with open(p, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except OSError:
        return False


def _stamp(p):
    try:
        return time.mktime(time.strptime(Path(p).read_text().strip()[:19], "%Y-%m-%dT%H:%M:%S"))
    except (OSError, ValueError):
        return None


def procs():
    """Counts of the pipeline's processes, from ps."""
    try:
        out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    def n(pat):
        return sum(1 for ln in out.splitlines() if re.search(pat, ln) and "ops.py" not in ln)
    return dict(process_day=n(r"process_day\.py"), second_pass=n(r"second_pass\.py"), cleanup=n(r"cleanup\.py"),
                vehicles=n(r"vehicles\.py (refresh|import)"), watcher=n(r"watch\.py"), review=n(r"review/server\.py"))


def jobs(home, root, pr, now=None):
    now = now or time.time()
    home, root = Path(home), Path(root)
    dup = _jsonl(root / "labels" / "duplicates.jsonl")
    last_dup = None
    for r in reversed(dup):
        try:
            last_dup = datetime.fromisoformat(r["ts"]).timestamp()
            break
        except (KeyError, ValueError):
            continue
    rows = [
        dict(key="watcher", name="Watcher (every 60 s)", last=None, every_s=120, running=pr["watcher"] > 0, note=f"{pr['process_day']} reading process(es), {pr['second_pass']} second-pass"),
        dict(key="vehicles", name="Vehicle index refresh (weekly)", last=_stamp(home / "vehicles_refreshed.txt"), every_s=7 * 86400,
             running=_locked(home / "vehicles.lock") or pr["vehicles"] > 0, off=(home / "no_vehicle_refresh").exists()),
        dict(key="cleanup", name="Cleanup (nightly, 02:00 to 06:00)", last=_stamp(home / "cleanup_last.txt"), every_s=86400,
             running=_locked(home / "cleanup.lock") or pr["cleanup"] > 0, off=(home / "no_cleanup").exists()),
        dict(key="dedupe", name="Duplicate merge (after each batch)", last=last_dup, every_s=None, running=False, off=(home / "no_dedupe").exists()),
        dict(key="backfill", name="Backfill (manual)", last=None, every_s=None, running=False, note="runs by hand; shows in Processing when active"),
    ]
    for r in rows:
        r["age_s"] = round(now - r["last"]) if r.get("last") else None
        r["late"] = bool(r.get("every_s") and r["last"] and now - r["last"] > 2 * r["every_s"] and not r.get("off") and not r["running"])
    return rows


def storage(home, root):
    home, root = Path(home), Path(root)
    hist = _jsonl(home / "storage_history.jsonl")
    out = dict(history=hist[-60:])
    for name, p in (("data", root), ("home", home)):
        try:
            u = shutil.disk_usage(p)
            out[name + "_disk"] = dict(total=u.total, free=u.free)
        except OSError:
            pass
    out["local"] = {"vehicle index": _size(home / "vehicles.sqlite"), "image cache": _tree(home / "imgcache")}
    if len(hist) >= 2:
        a, b = hist[max(0, len(hist) - 8)], hist[-1]
        days = max(1, (b["epoch"] - a["epoch"]) / 86400)
        out["growth_per_day"] = round((b["total"] - a["total"]) / days)
        free = out.get("data_disk", {}).get("free")
        out["days_until_full"] = round(free / out["growth_per_day"]) if free and out["growth_per_day"] > 0 else None
    return out


def _size(p):
    try:
        return Path(p).stat().st_size
    except OSError:
        return 0


def _tree(p):
    tot = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            try:
                tot += os.stat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return tot


def storage_snapshot(home, root):
    """Measure the data root and append one line to storage_history.jsonl. Slow over SMB; run once a day."""
    root = Path(root)
    parts = {n: _tree(root / n) for n in ("frames", "trash", "crops", "labels", "reports") if (root / n).is_dir()}
    parts["vehicle index"] = _size(Path(home) / "vehicles.sqlite")
    parts["image cache"] = _tree(Path(home) / "imgcache")
    rec = dict(epoch=time.time(), ts=datetime.now().astimezone().isoformat(timespec="seconds"), parts=parts,
               total=sum(v for k, v in parts.items() if k not in ("image cache",)))
    with open(Path(home) / "storage_history.jsonl", "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def accuracy(root):
    """Calibration and agreement lines parsed from reports/accuracy.md (written by score.py)."""
    p = Path(root) / "reports" / "accuracy.md"
    try:
        text = p.read_text()
        mtime = p.stat().st_mtime
    except OSError:
        return dict(available=False)
    cal = [dict(band=f"{m[1]} to {m[2]}", right=int(m[3]), total=int(m[4])) for m in re.finditer(r"- conf ([\d.]+) to ([\d.]+): (\d+)/(\d+)", text)]
    def pair(pat):
        m = re.search(pat, text)
        return dict(right=int(m[1]), total=int(m[2])) if m else None
    return dict(available=True, updated=mtime, calibration=cal, first_pass=pair(r"First pass .*?all known: (\d+)/(\d+)"),
                agree=pair(r"AGREE \(\d+\): right (\d+)/(\d+)"),
                night=pair(r"night 00-06: (\d+)/(\d+)"), day=pair(r"day 07\+: (\d+)/(\d+)"),
                header=(text.splitlines()[2] if len(text.splitlines()) > 2 else ""))


def confusion(root):
    rows = []
    try:
        with open(Path(root) / "reports" / "confusions.csv", newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(kind=r["kind"], ocr=r["ocr_char"], true=r["true_char"], n=int(r["count"])))
    except (OSError, KeyError, ValueError):
        return dict(available=False)
    subs = [r for r in rows if r["kind"] == "sub" and len(r["ocr"]) == 1 and len(r["true"]) == 1]
    return dict(available=True, subs=subs, other=[r for r in rows if r["kind"] != "sub"][:20])


def review(store):
    ds = store.days()
    reg = Path(store.root) / "labels"
    flags = _jsonl(reg / "vehicle_flags.jsonl")
    return dict(days=[dict(day=d["day"], total=d["total"], left=d["left"], auto=d["auto"], rejected=d["rejected"], recheck=d["recheck"],
                           waiting=d["waiting"]) for d in ds[-14:]],
                labels=len(_jsonl(reg / "labels.jsonl")), merged=len(_jsonl(reg / "duplicates.jsonl")),
                states=len(_jsonl(reg / "plate_states.jsonl")), gold=store.gold_check(), stale=len({r["plate"] for r in flags if r.get("stale")}),
                left=sum(d["left"] for d in ds), waiting=sum(d["waiting"] for d in ds), recheck=sum(d["recheck"] for d in ds), pairs=len(store.pair_queue()))


def alerts(home, now=None):
    now = now or time.time()
    rows = _jsonl(Path(home) / "alerts.jsonl")
    return dict(total=len(rows), last=rows[-1]["epoch"] if rows else None, last_7d=sum(1 for r in rows if now - r.get("epoch", 0) < 7 * 86400),
                by_day=_by_day([r["epoch"] for r in rows if "epoch" in r and now - r["epoch"] < 14 * 86400]))


def _by_day(epochs):
    out = {}
    for e in epochs:
        d = datetime.fromtimestamp(e).strftime("%m-%d")
        out[d] = out.get(d, 0) + 1
    return [dict(day=k, n=v) for k, v in sorted(out.items())]


def machine():
    out = dict(load=list(os.getloadavg()), cpus=os.cpu_count())
    try:
        sw = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"used = ([\d.]+)M", sw)
        out["swap_used_mb"] = float(m[1]) if m else None
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def lag(cam_dir, frames_root, now=None):
    """Minutes between the newest clip on the share and the newest clip in the day's index (both read from file names)."""
    now = now or datetime.now()
    day = now.date().isoformat()
    try:
        clips = sorted(p.name for p in (Path(cam_dir) / day).glob("*.dav"))
    except OSError:
        return dict(available=False)
    done = set()
    try:
        with open(Path(frames_root) / day / "index.csv", newline="") as fh:
            done = {r["clip"] for r in csv.DictReader(fh)}
    except OSError:
        pass
    pending = [c for c in clips if c not in done]
    return dict(available=True, on_share=len(clips), processed=len(clips) - len(pending), waiting=len(pending),
                newest_clip=clips[-1][:17] if clips else None, oldest_waiting=pending[0][:17] if pending else None)


def attention(d):
    """The short list at the top of the page: [{key, level, text}], worst first. `key` names the kind of problem and
    stays the same while its numbers change, so a scheduler can tell a new problem from a continuing one."""
    out = []
    w = d["watcher"]
    if w["idle_s"] is None or w["idle_s"] > 600:
        out.append(dict(key="watcher-quiet", level="critical", text="The watcher log has been quiet for " + (f"{w['idle_s'] // 60} minutes." if w["idle_s"] else "a long time.") + " It may not be running."))
    if w["errors_24h"]:
        out.append(dict(key="watcher-errors", level="warning", text=f"{w['errors_24h']} watcher error(s) in the last 24 hours. Last: {w['recent_errors'][-1]['text']}"))
    lg = d.get("lag", {})
    if lg.get("available") and lg["waiting"] > 15:
        out.append(dict(key="clips-waiting", level="warning", text=f"{lg['waiting']} clips on the share are not processed yet (oldest {lg['oldest_waiting']} CamTime)."))
    free = d["storage"].get("data_disk", {}).get("free")
    if free is not None and free < 20e9:
        out.append(dict(key="disk-low", level="critical" if free < 5e9 else "warning", text=f"Only {free / 1e9:.0f} GB free on the data disk."))
    for j in d["jobs"]:
        if j["late"]:
            out.append(dict(key="job-late-" + j["key"], level="warning", text=f"{j['name']} has not run in {j['age_s'] // 3600} hours."))
    if d["machine"]["load"][0] > 2 * (d["machine"]["cpus"] or 4):
        out.append(dict(key="overloaded", level="warning", text=f"The machine is overloaded (load {d['machine']['load'][0]:.0f})."))
    order = {"critical": 0, "warning": 1}
    return sorted(out, key=lambda x: order[x["level"]])


def build(store, home, cam_dir=None):
    root = Path(store.root)
    pr = procs()
    d = dict(generated=time.time(), watcher=watcher(home), procs=pr, jobs=jobs(home, root, pr), storage=storage(home, root),
             accuracy=accuracy(root), confusion=confusion(root), review=review(store), alerts=alerts(home), machine=machine())
    if cam_dir:
        d["lag"] = lag(cam_dir, Path(store.base))
    d["attention"] = attention(d)
    return d


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from . import config as _config
    cfg = _config.load()
    home = _config.home(cfg)
    root = Path(argv[argv.index("--root") + 1]).expanduser() if "--root" in argv else _config.data_root(cfg)
    if argv and argv[0] == "snapshot":
        print(json.dumps(storage_snapshot(home, root)))
        return
    print(json.dumps(dict(watcher=watcher(home), jobs=jobs(home, root, procs()), storage=storage(home, root), alerts=alerts(home)), indent=1, default=str))


if __name__ == "__main__":
    main()
