"""Synthetic demo data, so the pipeline and the review page can be tried without a camera and without
any real license plate.

  lpr demo results --out DIR   fake "already processed" results (frames, index, second pass) for
                               the review page. Fast, needs no OCR models.
  lpr demo clips --out DIR     a few short synthetic clips (video inside a .dav file name) for
                               `lpr process`. Needs the OCR extras installed.

All plate text is made up ("DEMO001" and so on) and every image is drawn from scratch.
"""
import argparse
import csv
import random
from pathlib import Path

import numpy as np

W, H = 1920, 1080
FONT_PLATE = 4         # cv2.FONT_HERSHEY_DUPLEX


def _cv2():
    import cv2
    return cv2


def _background(seed=0):
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(16, 4, (H, W, 1)), 0, 255).astype(np.uint8).repeat(3, axis=2)
    return img


def _overlays(img, stamp):
    cv2 = _cv2()
    cv2.rectangle(img, (1290, 30), (1850, 105), (0, 0, 0), -1)
    cv2.putText(img, stamp, (1305, 85), cv2.FONT_HERSHEY_SIMPLEX, 1.25, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(img, "IPC", (48, 1030), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3, cv2.LINE_AA)


def draw_plate(img, text, cx, cy, tilt=20.0, size=(250, 125)):
    """Draw a white 2:1 plate with black text, rotated clockwise by `tilt` degrees. Returns the
    axis-aligned bounding box (x0, y0, x1, y1) of the plate."""
    cv2 = _cv2()
    pw, ph = size
    patch = np.zeros((ph, pw, 3), np.uint8)
    mask = np.zeros((ph, pw), np.uint8)
    cv2.rectangle(patch, (0, 0), (pw - 1, ph - 1), (210, 210, 210), -1)
    cv2.rectangle(patch, (4, 4), (pw - 5, ph - 5), (150, 150, 150), 2)
    (bw, _), _ = cv2.getTextSize(text, FONT_PLATE, 1.0, 3)
    scale = min(2.2, 0.82 * pw / bw)                 # fit the text inside the plate
    (tw, th), _ = cv2.getTextSize(text, FONT_PLATE, scale, 3)
    cv2.putText(patch, text, ((pw - tw) // 2, (ph + th) // 2), FONT_PLATE, scale, (20, 20, 20), 3, cv2.LINE_AA)
    mask[:] = 255
    M = cv2.getRotationMatrix2D((pw / 2, ph / 2), -tilt, 1.0)       # negative = clockwise on screen
    cs, sn = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(ph * sn + pw * cs), int(ph * cs + pw * sn)
    M[0, 2] += nw / 2 - pw / 2
    M[1, 2] += nh / 2 - ph / 2
    rp = cv2.warpAffine(patch, M, (nw, nh))
    rm = cv2.warpAffine(mask, M, (nw, nh))
    x0, y0 = int(cx - nw / 2), int(cy - nh / 2)
    xs, ys = max(0, x0), max(0, y0)
    xe, ye = min(W, x0 + nw), min(H, y0 + nh)
    if xe > xs and ye > ys:
        sub = rp[ys - y0:ye - y0, xs - x0:xe - x0]
        m = rm[ys - y0:ye - y0, xs - x0:xe - x0] > 0
        img[ys:ye, xs:xe][m] = sub[m]
    return max(0, x0), max(0, y0), min(W, x0 + nw), min(H, y0 + nh)


def _mutate(text, rng):
    """A plausible OCR slip: swap one character for a look-alike."""
    look = {"M": "H", "H": "M", "0": "D", "D": "0", "8": "B", "B": "8", "1": "7", "7": "1", "N": "M"}
    idx = [i for i, c in enumerate(text) if c in look] or [len(text) - 1]
    i = rng.choice(idx)
    return text[:i] + look.get(text[i], "X") + text[i + 1:]


def make_results(root, day="2026-01-01", n=40, seed=7):
    """Write fake processed results under root/frames/<day>/. Returns the frames folder."""
    cv2 = _cv2()
    rng = random.Random(seed)
    frames = Path(root) / "frames" / day
    frames.mkdir(parents=True, exist_ok=True)
    idx_rows, sp_rows = [], []
    minute = 5
    for k in range(n):
        minute += rng.randint(3, 21)
        hh, mm, ss = (minute // 60) % 24, minute % 60, rng.randint(0, 40)
        clip = f"{hh:02d}.{mm:02d}.{ss:02d}-{hh:02d}.{mm:02d}.{ss + 18:02d}[M][0@0][0].dav"
        true = f"DEMO{k + 1:03d}"
        kind = rng.choices(["good", "slip", "lowconf", "sign", "nosecond"], [58, 18, 8, 8, 8])[0]
        p1, c1, p2, c2 = true, round(rng.uniform(0.992, 1.0), 4), true, round(rng.uniform(0.97, 1.0), 4)
        if kind == "slip":
            p1, c1 = _mutate(true, rng), round(rng.uniform(0.9, 0.99), 4)
        elif kind == "lowconf":
            c1 = round(rng.uniform(0.82, 0.93), 4)
        elif kind == "sign":
            true = p1 = p2 = "DEMOSIGN"
            c1, c2 = 0.98, 0.97
        img = _background(k)
        _overlays(img, f"{day} {hh:02d}:{mm:02d}:{ss + 3:02d}")
        if kind == "sign":
            cv2.rectangle(img, (500, 300), (1240, 460), (190, 190, 190), -1)
            cv2.putText(img, "DEMO SIGN", (560, 410), cv2.FONT_HERSHEY_DUPLEX, 3.0, (20, 20, 20), 6, cv2.LINE_AA)
            box = (500, 300, 1240, 460)
        else:
            cx, cy = rng.randint(250, 1100), rng.randint(180, 800)
            cv2.rectangle(img, (cx - 380, cy - 140), (cx + 380, cy + 500), (28, 28, 30), -1)    # a dark "vehicle"
            box = draw_plate(img, true, cx, cy)
        image = f"{day}_{clip.split('[')[0]}-{p1}_{c1:.2f}.png"
        cv2.imwrite(str(frames / image), img)
        idx_rows.append([clip, p1, f"{c1:.4f}", rng.randint(40, 200), "", image, 560, 1, rng.randint(3, 12), round(rng.uniform(8, 40), 1)])
        if kind != "nosecond" and k < n - 3:      # the last few stay "waiting for second pass"
            aspect = 4.5 if kind == "sign" else round(rng.uniform(1.9, 2.4), 2)
            shape = "wide" if aspect > 3 else "ok"
            sp_rows.append([image, p1, f"{c1:.4f}", p2, f"{c2:.4f}", int(p1 == p2), *box, f"{aspect:.2f}", shape, 2.5])
        elif kind == "nosecond" and k < n - 3:
            sp_rows.append([image, p1, f"{c1:.4f}", "", "", 0, "", "", "", "", "", "", 2.5])
    idx_rows.append([f"{hh:02d}.59.50-{hh:02d}.59.59[M][0@0][0].dav", "", "", "", "", "", 560, 0, 0, 3.1])    # a clip with no plate
    with (frames / "index.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"])
        w.writerows(idx_rows)
    with (frames / "second_pass.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"])
        w.writerows(sp_rows)
    return frames


def make_clips(camera_dir, day="2026-01-01", n=2, seed=3):
    """Write short synthetic clips: still background, then a dark vehicle with a plate crossing the
    frame. The files are MPEG-4 video named like a camera's .dav files (ffmpeg detects the format by
    content). Returns the day folder."""
    cv2 = _cv2()
    rng = random.Random(seed)
    folder = Path(camera_dir) / day
    folder.mkdir(parents=True, exist_ok=True)
    for k in range(n):
        text = f"DEMO{k + 11:03d}"
        bg = _background(seed + k)
        tmp = folder / f"clip{k}.mp4"
        vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), 30, (W, H))
        y = rng.randint(300, 600)
        for f in range(150):
            img = bg.copy()
            _overlays(img, f"{day} 03:1{k}:{f // 30:02d}")
            if 50 <= f < 110:
                cx = 300 + (f - 50) * 12
                cv2.rectangle(img, (cx - 380, y - 140), (cx + 380, y + 500), (28, 28, 30), -1)
                draw_plate(img, text, cx, y)
            vw.write(img)
        vw.release()
        tmp.rename(folder / f"03.1{k}.00-03.1{k}.05[M][0@0][0].dav")
    return folder


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lpr demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["results", "clips"])
    ap.add_argument("--out", required=True, help="folder to create")
    ap.add_argument("--day", default="2026-01-01")
    a = ap.parse_args(argv)
    if a.what == "results":
        p = make_results(a.out, a.day)
        print(f"fake results in {p}\nopen them with:  lpr review --root {a.out} --day {a.day}")
    else:
        p = make_clips(a.out, a.day)
        print(f"synthetic clips in {p}\ntry:  lpr process {p} --out {Path(a.out).parent / 'demo-frames' / a.day}")
