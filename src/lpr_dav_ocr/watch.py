"""Unattended watcher: process new camera clips as they appear, then alert on watchlist plates.

  lpr watch [CAMERA_DIR]                   run forever (CAMERA_DIR defaults to paths.camera_dir)
  lpr watch --init                         create the ntfy topic (once) and print it
  lpr watch --test-alert                   send one test notification
  lpr watch --once                         one cycle, then exit
  lpr watch --replay YYYY-MM-DD [--send]   re-check already processed plates against the watchlist

Every `--poll` seconds it looks at today's and yesterday's folders for clips whose end time (from the
file name) is within `--lookback` minutes, that are not in index.csv yet, and whose size has stopped
growing. Those go through process_day.py and second_pass.py, then each new plate is checked against
<home>/watchlist.csv. exact = same plate; possible = one wrong character. Alerts carry the
nickname and the plate and go to ntfy.sh (the only network call). Everything else stays local.
"""
import argparse, re, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path

from . import config as _config
from . import watchlist as W
from .util import read_csv

CFG = _config.load()
WCFG = CFG["watch"]

NAME = re.compile(r"(\d\d)\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d)")
COOLDOWN = WCFG["cooldown_seconds"]   # seconds between alerts for the same watched plate
RANK = {"possible": 1, "exact": 2}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def clip_end(day, name):
    m = NAME.match(name)
    if not m:
        return None
    h1, m1, s1, h2, m2, s2 = map(int, m.groups())
    start = datetime(day.year, day.month, day.day, h1, m1, s1)
    end = datetime(day.year, day.month, day.day, h2, m2, s2)
    return end + timedelta(days=1) if end < start else end


def db_frames(cam, day):
    cfg = dict(CFG)
    cfg["paths"] = dict(CFG["paths"], camera_dir=str(cam))
    return _config.frames_dir(cfg, day.isoformat())


def done_clips(frames):
    p = frames / "index.csv"
    if not p.exists():
        return set()
    return {r["clip"] for r in read_csv(p)}


def run(*cmd):
    return subprocess.run([sys.executable, "-I", *map(str, cmd)], capture_output=True, text=True)


def check_matches(frames, clips, send, label=""):
    """Look at the plates read from `clips` (names) and alert on watchlist hits."""
    entries = W.load()
    if not entries:
        return 0
    idx = [r for r in read_csv(frames / "index.csv") if r["clip"] in clips and r["plate"]]
    sp = {}
    if (frames / "second_pass.csv").exists():
        sp = {r["image"]: r for r in read_csv(frames / "second_pass.csv")}
    hits = {}
    for r in idx:
        asp = sp.get(r["image"], {}).get("aspect")
        if REJECT_ASPECT and asp and float(asp) > REJECT_ASPECT:
            continue                       # auto-rejected: not plate-shaped, never alert on it
        cands = [r["plate"]] + [v for v in (r.get("variants") or "").split("|") if v]
        if sp.get(r["image"], {}).get("plate2"):
            cands.append(sp[r["image"]]["plate2"])
        if sp.get(r["image"], {}).get("plate3"):
            cands.append(sp[r["image"]]["plate3"])
        for e, level, read in W.match(cands, entries):
            key = e["plate"]
            if key not in hits or RANK[level] > RANK[hits[key][1]]:
                hits[key] = (e, level, read, r)
    sent = 0
    recent = W.recent_alerts(COOLDOWN)
    for key, (e, level, read, r) in hits.items():
        if any(a.get("watch") == key and a.get("level") == level for a in recent) or any(a.get("watch") == key and a.get("clip") == r["clip"] and a.get("level") == level for a in W.recent_alerts(86400)):
            continue
        t = re.search(r"_(\d\d)\.(\d\d)\.(\d\d)-", r["image"])
        when = f"{t[1]}:{t[2]}" if t else "?"
        if level == "exact":
            title, pri = f"Watchlist: {e['nickname']}", 4
            msg = f"{e['nickname']} ({e['plate']}) seen at {when} CamTime (camera clock).\nConfidence {float(r['conf']):.2f}. Clip {r['clip'].split('[')[0]}."
        else:
            title, pri = f"Possible match: {e['nickname']}", 3
            msg = f"Possible {e['nickname']} ({e['plate']}) at {when} CamTime. Read as {read} (one character off).\nConfidence {float(r['conf']):.2f}. Clip {r['clip'].split('[')[0]}."
        if send:
            try:
                W.notify(title, msg, pri, "rotating_light" if level == "exact" else "mag")
                W.log_alert(watch=key, level=level, clip=r["clip"], read=read)
                log(f"ALERT sent: {title} ({read})")
                sent += 1
            except Exception as ex:
                log(f"ALERT failed, will retry next cycle: {ex}")
        else:
            log(f"{label}would alert: {title} | {msg.splitlines()[0]}")
            sent += 1
    return sent


def cycle(cam, a, state):
    now = datetime.now()
    for day in (now.date(), now.date() - timedelta(days=1)):
        d = cam / day.isoformat()
        if not d.is_dir():
            continue
        frames = db_frames(cam, day)
        done = done_clips(frames)
        batch = []
        for c in sorted(d.glob("*.dav")):
            end = clip_end(day, c.name)
            if c.name in done or not end or end < now - timedelta(minutes=a.lookback) or end > now - timedelta(seconds=a.settle):
                continue
            size = c.stat().st_size
            if state["sizes"].get(c) == size:       # same size on two polls: the camera is done with it
                batch.append(c)
            state["sizes"][c] = size
        if not batch:
            continue
        log(f"{day}: {len(batch)} new clip(s): " + ", ".join(c.name[:17] for c in batch[:6]) + (" ..." if len(batch) > 6 else ""))
        W.HOME.mkdir(parents=True, exist_ok=True)
        lst = W.HOME / "watch_batch.txt"
        lst.write_text("\n".join(str(c) for c in batch) + "\n")
        r = run("-m", "lpr_dav_ocr.pipeline", "--files-from", lst, "--workers", str(WCFG["workers"]))
        if r.returncode:
            log("process_day failed:", (r.stderr or r.stdout)[-300:])
            continue
        run("-m", "lpr_dav_ocr.second_pass", frames)
        names = {c.name for c in batch}
        check_matches(frames, names, send=not a.dry_run)
        state["last_ok"] = time.time()


REJECT_ASPECT = CFG["review"]["reject_aspect"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cam", nargs="?", help="camera folder (default: paths.camera_dir from the config)")
    ap.add_argument("--poll", type=int, default=WCFG["poll_seconds"])
    ap.add_argument("--lookback", type=int, default=WCFG["lookback_minutes"], help="minutes; only clips that ended this recently are picked up")
    ap.add_argument("--settle", type=int, default=WCFG["settle_seconds"], help="seconds a clip must have ended before it is read")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="process but do not send alerts")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--test-alert", action="store_true")
    ap.add_argument("--replay", metavar="YYYY-MM-DD", help="re-check processed plates of that day against the watchlist")
    ap.add_argument("--send", action="store_true", help="with --replay, really send")
    a = ap.parse_args(argv)

    if a.init:
        print("ntfy topic:", W.topic(create=True), "\nSubscribe to it in the ntfy app. Keep it private.")
        return
    if a.test_alert:
        W.notify("LPR watcher test", "If you can read this, alerts work.", 3, "white_check_mark")
        print("sent")
        return
    cam_arg = a.cam or CFG["paths"]["camera_dir"]
    if not cam_arg:
        ap.error("camera folder required (argument or paths.camera_dir in the config)")
    cam = Path(cam_arg)
    if a.replay:
        frames = db_frames(cam, datetime.strptime(a.replay, "%Y-%m-%d").date())
        clips = done_clips(frames)
        n = check_matches(frames, clips, send=a.send, label="replay: ")
        print(f"{n} alert(s) {'sent' if a.send else 'that would be sent'}")
        return

    state = {"sizes": {}, "last_ok": time.time(), "down_since": None, "down_alert": 0}
    log(f"watching {cam}  poll {a.poll}s  lookback {a.lookback} min  watchlist {len(W.load())} plate(s)")
    while True:
        try:
            if cam.is_dir():
                if state["down_since"]:
                    log("share is back")
                state["down_since"] = None
                cycle(cam, a, state)
            else:
                state["down_since"] = state["down_since"] or time.time()
                if time.time() - state["down_since"] > 1800 and time.time() - state["down_alert"] > 21600 and not a.dry_run:
                    try:
                        W.notify("LPR watcher: share unreachable", f"Cannot see {cam} since {datetime.fromtimestamp(state['down_since']):%H:%M}. New clips are not being read.", 3, "warning")
                        state["down_alert"] = time.time()
                    except Exception:
                        pass
        except Exception as ex:      # never let one bad cycle kill an unattended job
            log("cycle error:", repr(ex))
        if a.once:
            return
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
