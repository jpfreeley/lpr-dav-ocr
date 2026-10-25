"""Merge duplicate sightings: the same plate read again within a few seconds is one sighting.

  lpr dedupe [--root DIR] [--day YYYY-MM-DD] [--window 30] [--apply] [--purge]

A vehicle that stops, or one that spans two clips, is read twice. Reads of the same plate whose times are within
--window seconds of the next one (a chain: 25 s then 25 s is still one sighting) are grouped. Inside one clip, plate
texts up to two characters apart count as the same plate too (one frame misread it). One frame is kept:

  1. a frame you verified by hand (Correct or Save fix) always wins, so your work is never thrown away,
  2. then a read that fits a common plate pattern (three letters and four digits, or the taxi pattern), then the longer
     read (a clipped read such as 1123456 for T123456C can carry the higher confidence, so confidence is not trusted first),
  3. then one where both passes agree on the plate,
  4. then the sharper plate crop (variance of the Laplacian inside the second pass's outline), then the confidence.

Without --apply this only reports what it would do. With --apply the other frames are listed in
<data_root>/labels/duplicates.jsonl (every view and score then ignores them) and their PNG files are moved to
<data_root>/trash/<day>/, so nothing is lost until you run --purge, which deletes the trash. index.csv and
second_pass.csv are never rewritten, so this is safe to run while the watcher is working.

Only plates are merged: reads you marked not a plate, obstructed or can't tell, and auto-rejected reads are left alone. The time of a read is the clip's start time plus the frame offset,
on the camera's own clock.
"""
import argparse
import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

FPS = 30
TLC_RX = re.compile(r"[TY]\d{6}[A-Z]")
STD_RX = re.compile(r"[A-Z]{3}\d{4}")


def seconds(image, frame, fps=FPS):
    """Camera-clock seconds since midnight for one saved frame, or None."""
    m = re.search(r"_(\d\d)\.(\d\d)\.(\d\d)-", image)
    if not m:
        return None
    off = int(frame) // fps if str(frame).isdigit() else 0
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + off


def _rows(path):
    if not Path(path).exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load_labels(root):
    out = {}
    p = Path(root) / "labels" / "labels.jsonl"
    if p.exists():
        with open(p) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    out[r["image"]] = r                      # latest line per image wins
    return out


def load_registry(root):
    """image -> record, for frames already merged away."""
    out = {}
    p = Path(root) / "labels" / "duplicates.jsonl"
    if p.exists():
        with open(p) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    out[r["image"]] = r
    return out


def candidates(day_dir, labels, skip, reject_aspect=3.5, fps=FPS):
    """The reads of one day that may be merged: dicts with plate, t, conf, agree, human, box, image."""
    sp = {r["image"]: r for r in _rows(Path(day_dir) / "second_pass.csv")}
    out, seen = [], set()
    for r in _rows(Path(day_dir) / "index.csv"):
        img = r.get("image")
        if not img or not r.get("plate") or img in seen or img in skip:
            continue
        seen.add(img)
        s = sp.get(img, {})
        lb = labels.get(img)
        verdict = lb["verdict"] if lb else None
        if verdict not in (None, "ok", "fix"):
            continue                                          # not a plate / obstructed / can't tell
        asp = float(s["aspect"]) if s.get("aspect") else None
        if verdict is None and reject_aspect and asp is not None and asp > reject_aspect:
            continue                                          # auto-rejected: not plate-shaped
        t = seconds(img, r.get("frame"), fps)
        if t is None:
            continue
        plate = (lb.get("truth") or "").upper() if verdict == "fix" else r["plate"]
        out.append(dict(image=img, clip=r.get("clip", ""), plate=plate, t=t, conf=float(r["conf"]), verdict=verdict,
                        agree=bool(s.get("plate2")) and s.get("plate2") == r["plate"],
                        box=[int(s[k]) for k in ("x0", "y0", "x1", "y1")] if s.get("x0") not in (None, "") else None))
    return out


def _edit(a, b):
    """Levenshtein distance."""
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[:], i
        for j in range(1, len(b) + 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
    return d[len(b)]


def same_vehicle(a, b, window=30, fuzz=2):
    """Two reads that are one sighting: within `window` seconds of each other, and either the same plate text or, inside
    one clip, plate texts at most `fuzz` characters apart (one frame misread or clipped the plate, the other did not)."""
    if abs(a["t"] - b["t"]) > window:
        return False
    if a["plate"] == b["plate"]:
        return True
    return a["clip"] == b["clip"] and min(len(a["plate"]), len(b["plate"])) >= 5 and _edit(a["plate"], b["plate"]) <= fuzz


def group(cands, window=30, fuzz=2):
    """Sightings seen more than once: chains of reads where each is `same_vehicle` as one before it. Groups of 2+ only."""
    rows = sorted(cands, key=lambda c: c["t"])
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            if rows[j]["t"] - a["t"] > window:
                break
            if same_vehicle(a, rows[j], window, fuzz):
                parent[find(i)] = find(j)
    groups = {}
    for i, c in enumerate(rows):
        groups.setdefault(find(i), []).append(c)
    return [g for g in groups.values() if len(g) > 1]


def sharpness(path, box):
    """Variance of the Laplacian inside the plate outline (the whole frame when there is none). 0 when unreadable."""
    import cv2
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    if box:
        x0, y0, x1, y1 = box
        crop = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        img = crop if crop.size else img
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def choose(g, day_dir, sharp=sharpness):
    """(keeper, [others], reason) for one group of same-plate reads."""
    human = [c for c in g if c["verdict"]]
    for c in g:
        c["sharp"] = sharp(Path(day_dir) / c["image"], c["box"]) if len(g) > 1 else 0.0
    # A clipped read ("1123456" for "T123456C") can have the HIGHER confidence, so confidence comes last.
    key = lambda c: (c["verdict"] is not None, bool(TLC_RX.fullmatch(c["plate"]) or STD_RX.fullmatch(c["plate"])),
                     len(c["plate"]), c["agree"], c["sharp"], c["conf"])
    best = max(g, key=key)
    if len(human) > 1:
        reason = "you verified several; kept the best frame"
    elif human:
        reason = "kept the frame you verified"
    elif len({c["plate"] for c in g}) > 1 and best["plate"] == max((c["plate"] for c in g), key=len):
        reason = "the complete plate (the other read is clipped)"
    elif best["agree"] and not all(c["agree"] for c in g):
        reason = "both passes agree on it"
    else:
        reason = "sharpest plate"
    return best, [c for c in g if c is not best], reason


def plan(root, days=None, window=30, sharp=sharpness):
    """Everything --apply would do: [(day, keeper, [others], reason)]."""
    root = Path(root)
    base = root / "frames" if (root / "frames").is_dir() else root
    labels, skip, out = load_labels(root), set(load_registry(root)), []
    for d in sorted(p for p in base.iterdir() if re.fullmatch(r"\d{4}-\d\d-\d\d", p.name) and (p / "index.csv").exists()):
        if days and d.name not in days:
            continue
        for g in group(candidates(d, labels, skip), window):
            out.append((d.name, *choose(g, d, sharp)))
    return out


def apply(root, decisions):
    """Record the merges and move the extra frames to <root>/trash/<day>/. Returns (moved, missing, skipped groups)."""
    root = Path(root)
    base = root / "frames" if (root / "frames").is_dir() else root
    reg = root / "labels" / "duplicates.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    moved = missing = skipped = 0
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    labels = load_labels(root)                  # labels saved while the plan was being made must not be lost
    done = set(load_registry(root))
    with open(reg, "a") as fh:
        for day, keeper, others, reason in decisions:
            verified = lambda c: (labels.get(c["image"]) or {}).get("verdict") in ("ok", "fix")
            if any(verified(c) for c in others) and not verified(keeper):
                skipped += 1                    # you verified a frame we were about to drop: leave the group alone
                continue
            for c in others:
                if c["image"] in done:
                    continue
                fh.write(json.dumps(dict(ts=now, image=c["image"], kept=keeper["image"], plate=c["plate"], day=day,
                                         gap_s=abs(c["t"] - keeper["t"]), reason=reason)) + "\n")
                src = base / day / c["image"]
                if src.is_file():
                    dst = root / "trash" / day
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst / c["image"]))
                    moved += 1
                else:
                    missing += 1
        fh.flush()
    return moved, missing, skipped


def purge(root):
    """Delete the trash folder for good. Returns (files, bytes)."""
    t = Path(root) / "trash"
    files = [p for p in t.rglob("*") if p.is_file()] if t.is_dir() else []
    size = sum(p.stat().st_size for p in files)
    if t.is_dir():
        shutil.rmtree(t)
    return len(files), size


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lpr dedupe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="folder holding frames/ labels/ (default: the configured data root)")
    ap.add_argument("--day", action="append", help="YYYY-MM-DD; repeat for several days (default: every day)")
    ap.add_argument("--window", type=int, default=30, help="seconds between reads of one sighting (default 30)")
    ap.add_argument("--apply", action="store_true", help="merge: record the duplicates and move their frames to the trash")
    ap.add_argument("--purge", action="store_true", help="delete the trash folder permanently")
    a = ap.parse_args(argv)
    if a.root:
        root = Path(a.root).expanduser()
    else:
        from . import config as _config
        root = _config.data_root(_config.load())
    if a.purge:
        n, size = purge(root)
        print(f"deleted {n} frames ({size / 1e6:.0f} MB) from {root / 'trash'}")
        return
    decisions = plan(root, a.day, a.window)
    extra = [c for _, _, others, _ in decisions for c in others]
    print(f"{len(decisions)} duplicate groups; {len(extra)} frames would be merged into {len(decisions)} "
          f"(window {a.window} s)")
    reasons = {}
    for _, _, _, why in decisions:
        reasons[why] = reasons.get(why, 0) + 1
    for why, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  kept by: {why}: {n}")
    if not a.apply:
        print("dry run: nothing changed. Add --apply to merge.")
        return
    moved, missing, skipped = apply(root, decisions)
    print(f"merged: {moved} frames moved to {root / 'trash'} and recorded in labels/duplicates.jsonl"
          + (f", {missing} already missing" if missing else "") + (f", {skipped} groups left alone (you verified a frame meanwhile)" if skipped else "")
          + ". Run --purge to delete them for good.")


if __name__ == "__main__":
    main()
