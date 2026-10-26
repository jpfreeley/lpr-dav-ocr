#!/usr/bin/env python3
"""Second opinion on the saved best frames. Fully local, no video decode.

For each <day>/*.png listed in index.csv, re-read the frame and record:
  plate2, conf2   the read that best matches the first-pass plate (else the most confident one)
  agree           1 if plate2 == plate1
  x0 y0 x1 y1     bounding box of that read in the saved frame (for the review page crop)
  aspect, shape   length/height of the detected text region. US plates are 2:1 and the plate reads
                  cluster at 1.8-2.4, while model names, state text and signs spread from 2 to 7.
                  shape = ok (1.5-3.0), wide (>3.0), narrow (<1.5). A hint for the review page,
                  not a filter: some non-plate text is plate-shaped.
The read is tried in up to three ways, stopping at the first that finds plate-shaped text:
  1. un-rotated, 960 px detector            (independent of the first pass)
  2. rotated by the camera tilt, 960 px     (independent: a different detector size)
  3. rotated, 640 px = the first pass's own settings. This gives the page an OUTLINE only; plate2
     stays empty so it can never count as an agreeing second opinion (and never auto-accept).
When the two reads disagree, a third read of a tight, enlarged crop around the outline breaks the tie:
  plate3, conf3   the crop read ('' if none; conf3 '0' means it was tried and found nothing)
  A crop read that equals plate1 or plate2 backs that read. It never changes plate2 or auto-accept.
Writes <day>/second_pass.csv. Incremental: images already listed are skipped.
  --retry-empty   re-run rows that have no read and no outline (use after upgrading this script)
  --fill-crop     add the crop read to existing rows where the two passes disagree
  --fill-elsewhere  for wide (aspect over 3.0) rows, look for another plate-shaped read in the frame (elsewhere, elsewhere_conf)
Run with:  lpr second-pass <data_root>/frames/<date>
"""
import csv
import fcntl, hashlib, math, os, re, sys, tempfile, time
from pathlib import Path

from . import pipeline as P
from .util import read_csv

HEADER = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs", "plate3", "conf3", "elsewhere", "elsewhere_conf"]
RX = re.compile(r"[A-Z0-9]{5,8}")


def rotation(shape, deg):
    """The affine matrix pipeline.prep uses (counter-clockwise by deg, canvas grown to fit) and its size."""
    import numpy as np
    h, w = shape[:2]
    a, b = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    cx, cy = w / 2, h / 2
    M = np.array([[a, b, (1 - a) * cx - b * cy], [-b, a, b * cx + (1 - a) * cy]], float)
    nw, nh = int(h * abs(b) + w * abs(a)), int(h * abs(a) + w * abs(b))
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return M, (nw, nh)


def map_back(pts, M):
    """Points in the rotated image -> points in the original image."""
    import numpy as np
    A, t = M[:, :2], M[:, 2]
    return (np.asarray(pts, float) - t) @ np.linalg.inv(A).T


def candidates(ocr, img, deg, cv2, np):
    """Plate-shaped reads in the frame: (text, conf, [x0,y0,x1,y1] in ORIGINAL coordinates, aspect)."""
    base = P.prep(img, 0)                                  # overlays blanked
    if deg:
        M, size = rotation(img.shape, deg)
        base = cv2.warpAffine(base, M, size)
    out = []
    for res in ocr.predict(base):
        for t, s, poly in zip(res["rec_texts"], res["rec_scores"], res["rec_polys"]):
            t = re.sub(r"[^A-Z0-9]", "", t.upper())
            if not RX.fullmatch(t):
                continue
            pts = np.array(poly, np.float32)
            (_, _), (rw, rh), _ = cv2.minAreaRect(pts)
            asp = max(rw, rh) / max(min(rw, rh), 1.0)
            if deg:
                pts = map_back(pts, M)
            x0, y0 = pts.min(axis=0)
            x1, y1 = pts.max(axis=0)
            out.append((t, float(s), [int(x0), int(y0), int(x1), int(y1)], asp))
    return out


def crop_read(ocr, img, box, cv2):
    """Third read: a tight crop around the plate outline, enlarged to 200 px tall. (plate, conf) or None."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    pw, ph = int((x1 - x0) * 0.15), int((y1 - y0) * 0.5)
    crop = img[max(0, y0 - ph):min(h, y1 + ph), max(0, x0 - pw):min(w, x1 + pw)]
    if crop.size == 0:
        return None
    s = 200 / crop.shape[0]
    crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    best = None
    for res in ocr.predict(crop):
        for t, sc in zip(res["rec_texts"], res["rec_scores"]):
            t = re.sub(r"[^A-Z0-9]", "", t.upper())
            if RX.fullmatch(t) and (best is None or sc > best[1]):
                best = (t, float(sc))
    return best


def plate_shaped(cands, exclude=()):
    """The most confident candidate whose outline is plate-shaped (aspect 1.5 to 3.0) and whose text is not in `exclude`.
    (text, conf) or None."""
    ok = [x for x in cands if 1.5 <= x[3] <= 3.0 and x[0] not in exclude]
    best = max(ok, key=lambda x: x[1], default=None)
    return (best[0], best[1]) if best else None


def find_elsewhere(ocr, get_fast, img, chosen, tilt, cv2, np):
    """Another plate-shaped read in a frame whose chosen read is not plate-shaped (a badge or a decal was read instead
    of the plate). Looks un-rotated and rotated by the tilt. (text, conf) or None."""
    seen = []
    for deg in (0, tilt):
        if deg == tilt and not tilt:
            continue
        seen += candidates(ocr, img, deg, cv2, np)
    return plate_shaped(seen, {chosen})


def shape_of(asp):
    return "ok" if 1.5 <= asp <= 3.0 else "wide" if asp > 3.0 else "narrow"


def second_read(ocr, get_fast, img, p1, tilt, cv2, np):
    """Returns (plate2, conf2, box, aspect, elsewhere) or None. plate2 is '' for an outline-only result. `elsewhere` is
    (text, conf) of another plate-shaped read, looked for only when the chosen read is wide, or None."""
    for deg in (0, tilt):
        if deg == tilt and not tilt:
            continue
        c = candidates(ocr, img, deg, cv2, np)
        near = [x for x in c if P.similar(p1, x[0])]
        best = max(near or c, key=lambda x: x[1], default=None)
        if best:
            alt = None
            if best[3] > 3.0:
                alt = plate_shaped(c, {best[0]}) or find_elsewhere(ocr, get_fast, img, best[0], tilt, cv2, np)
            return best[0], best[1], best[2], best[3], alt
    c = candidates(get_fast(), img, tilt, cv2, np)            # first pass's own settings: outline only
    near = [x for x in c if P.similar(p1, x[0])]
    best = max(near or c, key=lambda x: x[1], default=None)
    if best:
        return "", None, best[2], best[3], (plate_shaped(c, {best[0]}) if best[3] > 3.0 else None)
    return None


def row_for(r, res, secs, third=None):
    """third: None = not tried, (plate, conf) = crop read, () = tried, nothing found."""
    p1 = r["plate"]
    t3 = ["", ""] if third is None else [third[0], f"{third[1]:.4f}"] if third else ["", "0"]
    if res is None:
        return [r["image"], p1, r["conf"], "", "", 0, "", "", "", "", "", "", secs, *t3, "", ""]
    p2, c2, box, asp = res[:4]
    alt = res[4] if len(res) > 4 else None
    el = ["", ""] if asp <= 3.0 else [alt[0], f"{alt[1]:.4f}"] if alt else ["", "0"]      # "0" means looked, nothing found
    return [r["image"], p1, r["conf"], p2, f"{c2:.4f}" if c2 is not None else "", int(p2 == p1 and p2 != ""), *box, f"{asp:.2f}", shape_of(asp), secs, *t3, *el]


def needs_crop(p1, res):
    return bool(res and res[0] and res[0] != p1)


def rewrite(day, out, existing, new_rows):
    """Replace second_pass.csv in one atomic step so the review page never sees a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=str(day), suffix=".tmp")
    with os.fdopen(fd, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for r in existing:
            w.writerow(new_rows.get(r["image"]) or [r.get(k, "") for k in HEADER])
    os.replace(tmp, out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = [a for a in argv if not a.startswith("--")]
    retry = "--retry-empty" in argv
    fill = "--fill-crop" in argv
    fill_else = "--fill-elsewhere" in argv
    if not args:
        sys.exit(__doc__)
    day = Path(args[0]).expanduser()
    out = day / "second_pass.csv"
    # one second_pass per folder at a time (lock lives on the Mac, SMB locking is unreliable)
    lockdir = P.HOME / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lock = open(lockdir / (hashlib.md5(str(day.resolve()).encode()).hexdigest() + ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another second_pass is already running on this folder")

    index = [r for r in read_csv(day / "index.csv") if r["plate"]]
    existing = read_csv(out) if out.exists() else []
    have = {r["image"]: r for r in existing}
    if existing and "elsewhere_conf" not in existing[0]:      # file from before the crop read or the elsewhere read: add the columns
        rewrite(day, out, existing, {})
        existing = read_csv(out)
        have = {r["image"]: r for r in existing}
    if fill_else:
        pend = {i for i, r in have.items() if r["aspect"] and float(r["aspect"]) > 3.0 and not r.get("elsewhere_conf")}
        todo = [r for r in index if r["image"] in pend]
    elif fill:
        pend = {i for i, r in have.items() if r["plate2"] and r["plate2"] != r["plate1"] and r["x0"] and not r.get("conf3")}
        todo = [r for r in index if r["image"] in pend]
    elif retry:
        empty = {i for i, r in have.items() if not r["plate2"] and not r["x0"]}
        todo = [r for r in index if r["image"] in empty]
    else:
        todo = [r for r in index if r["image"] not in have]
    print(f"{len(index)} images, {len(todo)} to do" + (" (retrying empty ones)" if retry else ""), flush=True)

    import cv2
    import numpy as np
    tilt = P.CFG["camera"]["tilt_degrees"]
    ocr = P.make_ocr(None, 960)
    fast = {}

    def get_fast():
        if "o" not in fast:
            fast["o"] = P.make_ocr(None, 640)
        return fast["o"]

    new_rows = {}
    f = None
    if not retry and not fill and not fill_else:
        f = out.open("a", newline="")
        w = csv.writer(f)
        if not existing and not out.stat().st_size:
            w.writerow(HEADER)
    t_all = time.time()
    for n, r in enumerate(todo, 1):
        t0 = time.time()
        img = cv2.imread(str(day / r["image"]))
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d\d\.\d\d\.\d\d)-", r["image"])
        if m:
            P.use_layout(m.group(1), m.group(2))                # the overlay boxes that were burned into this frame
        if fill_else:
            old = have[r["image"]]
            alt = find_elsewhere(ocr, get_fast, img, old["plate2"] or old["plate1"], tilt, cv2, np) if img is not None else None
            row = [old.get(k, "") for k in HEADER]
            row[-2:] = [alt[0], f"{alt[1]:.4f}"] if alt else ["", "0"]
            new_rows[r["image"]] = row
            if n % 10 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
            continue
        if fill:
            old = have[r["image"]]
            res = (old["plate2"], float(old["conf2"]), [int(old[k]) for k in ("x0", "y0", "x1", "y1")], float(old["aspect"] or 0)) if img is not None else None
        else:
            res = second_read(ocr, get_fast, img, r["plate"], tilt, cv2, np) if img is not None else None
        third = None
        if img is not None and needs_crop(r["plate"], res):
            third = crop_read(get_fast(), img, res[2], cv2) or ()
        row = row_for(r, res, round(time.time() - t0, 1), third)
        if fill:
            old = have[r["image"]]
            row[12] = old.get("secs", row[12])
        if retry or fill:
            new_rows[r["image"]] = row
        else:
            w.writerow(row)
            f.flush()
        if n % 10 == 0:
            print(f"  {n}/{len(todo)}", flush=True)
    if f:
        f.close()
    if (retry or fill or fill_else) and new_rows:
        rewrite(day, out, existing, new_rows)
        if fill_else:
            found = sum(1 for r in new_rows.values() if r[-2])
            print(f"elsewhere read for {len(new_rows)} wide frames: {found} have a plate-shaped read", flush=True)
        if fill:
            print(f"crop read added to {len(new_rows)} rows", flush=True)
        else:
            fixed = sum(1 for r in new_rows.values() if r[3] or r[6] != "")
            print(f"retried {len(new_rows)}: {fixed} now have a read or an outline", flush=True)
    print(f"done in {round(time.time() - t_all)}s", flush=True)
    P._finish()


if __name__ == "__main__":
    main()
