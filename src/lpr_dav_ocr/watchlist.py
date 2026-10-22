"""Watchlist, plate matching and ntfy helpers shared by watch.py and the review server. Stdlib only.

State lives on this machine only, in the `home` folder (default ~/.lpr-ocr; never on the camera share, never in a repository):
  watchlist.csv   plate,nickname,added
  ntfy_topic      random topic name, mode 600. Treat it like a password: anyone who knows it can read the alerts.
  alerts.jsonl    what was sent, for de-duplication
"""
import csv, json, os, re, secrets, string, time, urllib.request
from datetime import datetime
from pathlib import Path

from . import config as _config

CFG = _config.load()
HOME = _config.home(CFG)
WATCHLIST = HOME / "watchlist.csv"
TOPIC_FILE = Path(CFG["ntfy"]["topic_file"])
ALERTS = HOME / "alerts.jsonl"
NTFY = CFG["ntfy"]["server"].rstrip("/") + "/"
MIN_LEN_FUZZY = 5      # one wrong character is too loose for very short plates


def norm(p):
    return re.sub(r"[^A-Z0-9]", "", str(p).upper())


def load():
    if not WATCHLIST.exists():
        return []
    with WATCHLIST.open(newline="") as f:
        return [dict(plate=norm(r["plate"]), nickname=r.get("nickname", ""), added=r.get("added", "")) for r in csv.DictReader(f) if norm(r.get("plate", ""))]


def _write(rows):
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = WATCHLIST.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["plate", "nickname", "added"])
        for r in rows:
            w.writerow([r["plate"], r["nickname"], r["added"]])
    os.replace(tmp, WATCHLIST)


def add(plate, nickname):
    plate = norm(plate)
    if not re.fullmatch(r"[A-Z0-9]{2,10}", plate):
        raise ValueError("bad plate")
    nickname = re.sub(r"[^\w .'\-]", "", str(nickname))[:40].strip() or plate
    rows = [r for r in load() if r["plate"] != plate]
    rows.append(dict(plate=plate, nickname=nickname, added=datetime.now().astimezone().isoformat(timespec="seconds")))
    _write(rows)
    return rows[-1]


def remove(plate):
    plate = norm(plate)
    _write([r for r in load() if r["plate"] != plate])


def within_one(a, b):
    """Edit distance <= 1 (one substitution, insertion or deletion)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = sorted((a, b), key=len)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def match(candidates, entries=None):
    """candidates: plate strings read from one sighting (first pass, variants, second pass).
    Returns [(entry, level, read)] with level 'exact' or 'possible' (one wrong character)."""
    out = []
    cands = [norm(c) for c in candidates if c]
    for e in (entries if entries is not None else load()):
        best = None
        for c in cands:
            if c == e["plate"]:
                best = ("exact", c)
                break
            if len(c) >= MIN_LEN_FUZZY and len(e["plate"]) >= MIN_LEN_FUZZY and within_one(c, e["plate"]):
                best = best or ("possible", c)
        if best:
            out.append((e, best[0], best[1]))
    return out


def topic(create=False):
    if TOPIC_FILE.exists():
        return TOPIC_FILE.read_text().strip()
    if not create:
        return None
    HOME.mkdir(parents=True, exist_ok=True)
    t = "lpr-" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(24))
    TOPIC_FILE.write_text(t + "\n")
    os.chmod(TOPIC_FILE, 0o600)
    return t


def notify(title, message, priority=3, tags="car"):
    """POST to ntfy.sh. Raises on failure so the caller can retry later."""
    if not CFG["ntfy"]["enabled"]:
        raise RuntimeError("ntfy is disabled in the configuration")
    t = topic()
    if not t:
        raise RuntimeError("no ntfy topic; run `lpr watch --init`")
    req = urllib.request.Request(NTFY + t, data=message.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("ascii", "replace").decode(), "Priority": str(priority), "Tags": tags})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def recent_alerts(seconds):
    if not ALERTS.exists():
        return []
    cut = time.time() - seconds
    out = []
    for l in ALERTS.read_text().splitlines():
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        if r.get("epoch", 0) >= cut:
            out.append(r)
    return out


def log_alert(**kw):
    HOME.mkdir(parents=True, exist_ok=True)
    with ALERTS.open("a") as f:
        f.write(json.dumps(dict(epoch=time.time(), ts=datetime.now().astimezone().isoformat(timespec="seconds"), **kw)) + "\n")


def main(argv=None):
    """`lpr watchlist add PLATE NICKNAME | remove PLATE | list`."""
    import argparse
    ap = argparse.ArgumentParser(prog="lpr watchlist", description=main.__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("plate")
    p.add_argument("nickname", nargs="?", default="")
    p = sub.add_parser("remove")
    p.add_argument("plate")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.cmd == "add":
        e = add(a.plate, a.nickname)
        print(f"watching {e['plate']} as {e['nickname']!r}")
    elif a.cmd == "remove":
        remove(a.plate)
        print("removed", norm(a.plate))
    else:
        for e in load():
            print(f"{e['plate']}\t{e['nickname']}\t{e['added']}")
