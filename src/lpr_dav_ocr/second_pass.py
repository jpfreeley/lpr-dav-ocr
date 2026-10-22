"""Second opinion on the saved best frames. Fully local, no video decode.

For each <day>/*.png listed in index.csv, re-read the frame WITHOUT rotation at 960 px (the
first pass rotates 20 degrees and uses 640 px), and record:
  plate2, conf2   the read that best matches the first-pass plate (else the most confident one)
  agree           1 if plate2 == plate1
  x0 y0 x1 y1     bounding box of that read in the saved frame (for the review page crop)
  aspect, shape   length/height of the detected text region. US plates are 2:1 and the plate reads
                  cluster at 1.8-2.4, while model names, state text and signs spread from 2 to 7.
                  shape = ok (1.5-3.0), wide (>3.0), narrow (<1.5). A hint for the review page,
                  not a filter: some non-plate text is plate-shaped.
Writes <day>/second_pass.csv. Incremental: images already listed are skipped.
Run with:  lpr second-pass <data_root>/frames/<date>
"""
import csv, fcntl, hashlib, re, sys, time
from pathlib import Path

from . import pipeline as P
from .util import read_csv   # sets the offline/model-cache env on import


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.exit(__doc__)
    day = Path(argv[0]).expanduser()
    out = day / "second_pass.csv"
    # one second_pass per folder at a time (lock lives on the Mac, SMB locking is unreliable)
    lockdir = P.HOME / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lock = open(lockdir / (hashlib.md5(str(day.resolve()).encode()).hexdigest() + ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another second_pass is already running on this folder")
    rows = [r for r in read_csv(day / "index.csv") if r["plate"]]
    done = set()
    if out.exists():
        done = {r["image"] for r in read_csv(out)}
    todo = [r for r in rows if r["image"] not in done]
    print(f"{len(rows)} images, {len(todo)} to do", flush=True)

    import cv2
    import numpy as np
    ocr = P.make_ocr(None, 960)
    rx = re.compile(r"[A-Z0-9]{5,8}")
    new = not out.exists()
    f = out.open("a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"])
    t_all = time.time()
    for n, r in enumerate(todo, 1):
        t0 = time.time()
        img = cv2.imread(str(day / r["image"]))
        cands = []
        for res in ocr.predict(P.prep(img, 0)):          # overlays blanked, no rotation
            for t, s, b, poly in zip(res["rec_texts"], res["rec_scores"], res["rec_boxes"], res["rec_polys"]):
                t = re.sub(r"[^A-Z0-9]", "", t.upper())
                if rx.fullmatch(t):
                    (_, _), (rw, rh), _ = cv2.minAreaRect(np.array(poly, np.float32))
                    asp = max(rw, rh) / max(min(rw, rh), 1.0)
                    cands.append((t, float(s), [int(v) for v in b], asp))
        p1 = r["plate"]
        near = [c for c in cands if P.similar(p1, c[0])]
        best = max(near or cands, key=lambda c: c[1], default=None)
        if best:
            shape = "ok" if 1.5 <= best[3] <= 3.0 else "wide" if best[3] > 3.0 else "narrow"
            w.writerow([r["image"], p1, r["conf"], best[0], f"{best[1]:.4f}", int(best[0] == p1), *best[2], f"{best[3]:.2f}", shape, round(time.time() - t0, 1)])
        else:
            w.writerow([r["image"], p1, r["conf"], "", "", 0, "", "", "", "", "", "", round(time.time() - t0, 1)])
        f.flush()
        if n % 10 == 0:
            print(f"  {n}/{len(todo)}", flush=True)
    f.close()
    print(f"done in {round(time.time() - t_all)}s", flush=True)
    P._finish()


if __name__ == "__main__":
    main()
