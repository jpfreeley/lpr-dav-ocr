"""Find license plates in a folder of Dahua .dav clips. Fully local.

Per clip:
  1. Pass 1: decode at 480px gray, score frame-to-frame motion (cheap).
  2. Pick the moving frames (capped), re-decode only those at full resolution.
  3. PaddleOCR (CPU) on each, keep plate-shaped reads, group variants of one plate.
  4. Save the best full frame per plate as
       <date>_<start>-<end>-<PLATE>_<conf>.png
Run with `lpr process`. Resumable via <out>/index.csv.
"""
import argparse, csv, os, re, signal, subprocess, sys, time
from pathlib import Path

from . import config as _config
from .util import read_csv

CFG = _config.load()
PCFG = CFG["pipeline"]
HOME = _config.home(CFG)
os.environ["PADDLE_PDX_CACHE_HOME"] = str(HOME / "models")
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
if not os.environ.get("LPR_ALLOW_NET"):
    # models are cached by setup.sh, so refuse any network use at runtime
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[k] = "1"

import numpy as np

# Overlay regions as fractions (x0, y0, x1, y1) of the frame. Reads centered here are dropped.
# Configured under [camera] overlays.
OVERLAYS = [tuple(o) for o in CFG["camera"]["overlays"]]


def ffmpeg_exe():
    import shutil
    return shutil.which("ffmpeg") or __import__("imageio_ffmpeg").get_ffmpeg_exe()


def probe(ff, clip):
    err = subprocess.run([ff, "-hide_banner", "-i", clip], capture_output=True, text=True).stderr
    line = next((l for l in err.splitlines() if "Video:" in l), "")
    m = re.search(r"(\d{3,5})x(\d{3,5})", line)
    return (int(m.group(1)), int(m.group(2))) if m else (CFG["camera"]["frame_width"], CFG["camera"]["frame_height"])


def motion_scores(ff, clip, W, H):
    sw = 480
    sh = int(round(sw * H / W / 2) * 2)
    p = subprocess.Popen([ff, "-v", "error", "-i", clip, "-vf", f"scale={sw}:{sh},format=gray",
                          "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    keep = np.ones((sh, sw), bool)
    for x0, y0, x1, y1 in OVERLAYS:
        keep[int(y0 * sh):int(y1 * sh) + 1, int(x0 * sw):int(x1 * sw) + 1] = False
    prev, out = None, []
    while True:
        buf = p.stdout.read(sw * sh)
        if len(buf) < sw * sh:
            break
        f = np.frombuffer(buf, np.uint8).reshape(sh, sw).astype(np.int16)
        out.append(0.0 if prev is None else float(np.abs(f - prev)[keep].mean()))
        prev = f
    p.wait()
    return np.array(out)


def pick_frames(d, min_motion, k, pad, cap, stride=1, gap=15, max_segments=6):
    """Return ([(frame, segment)], n_segments). A segment is a burst of motion, roughly one vehicle
    pass. Segment 0 is the strongest (most motion mass); daylight clips can have dozens of bursts
    (leaves, people), so only the top max_segments are kept.
    Clips are mostly pre/post-record buffer; plates show up within ~1s of motion onset."""
    if len(d) == 0:
        return [], 0
    base = np.median(d)
    mad = np.median(np.abs(d - base)) * 1.4826 + 1e-6
    thr = max(min_motion, base + k * mad)
    hit = np.where(d > thr)[0]
    segs, cur = [], []
    for h in hit:
        if cur and h - cur[-1] > gap:
            segs.append(cur); cur = []
        cur.append(h)
    if cur:
        segs.append(cur)
    segs.sort(key=lambda sg: -float(np.clip(d[sg] - thr, 0, None).sum()))
    segs = segs[:max_segments]
    out = []
    for sid, sg in enumerate(segs):
        idx = list(range(max(0, sg[0] - pad), min(len(d), sg[-1] + pad + 1)))[::stride]
        if len(idx) > cap:
            idx = [idx[j] for j in np.linspace(0, len(idx) - 1, cap).astype(int)]
        out += [(i, sid) for i in idx]
    return out, len(segs)


def full_frames(ff, clip, W, H, idx):
    idx = sorted(idx)
    expr = "+".join(f"eq(n,{i})" for i in idx)
    p = subprocess.Popen([ff, "-v", "error", "-i", clip, "-vf", f"select='{expr}'",
                          "-fps_mode", "passthrough", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = W * H * 3
    for i in idx:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        yield i, np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
    p.stdout.close()
    p.wait()


def make_ocr(threads=None, side=640):
    from paddleocr import PaddleOCR
    # det side capped (native 1080p is 4x slower). 640 needs --deskew; un-rotated plates need ~960
    kw = dict(cpu_threads=threads) if threads else {}
    return PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                     use_textline_orientation=False, text_det_limit_type="max",
                     text_det_limit_side_len=side, lang="en", **kw)


def prep(frame, deskew):
    """Blank the burned-in overlays, then rotate so the (fixed-camera) plates read level."""
    import cv2
    h, w = frame.shape[:2]
    img = frame.copy()
    for x0, y0, x1, y1 in OVERLAYS:
        img[int(y0 * h):int(y1 * h) + 1, int(x0 * w):int(x1 * w) + 1] = 0
    if not deskew:
        return img
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deskew, 1.0)
    c, s = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * s + w * c), int(h * c + w * s)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, M, (nw, nh))


def read_plates(ocr, frame, rx, deskew):
    """Yield (text, conf) for plate-shaped reads."""
    for res in ocr.predict(prep(frame, deskew)):
        for t, s in zip(res["rec_texts"], res["rec_scores"]):
            t = re.sub(r"[^A-Z0-9]", "", t.upper())
            if rx.fullmatch(t):
                yield t, float(s)


def similar(a, b):
    """Same plate, different read: one contains the other, or edit distance <= 1."""
    if a in b or b in a:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    s, l = sorted((a, b), key=len)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def clip_stamp(clip):
    p = Path(clip)
    m = re.match(r"(\d\d\.\d\d\.\d\d)-(\d\d\.\d\d\.\d\d)", p.name)
    date = p.parent.name if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.parent.name) else "unknown-date"
    return f"{date}_{m.group(1)}-{m.group(2)}" if m else f"{date}_{p.stem}"


def add_read(groups, t, s, i, fr):
    g = next((g for g in groups if similar(g["text"], t)), None)
    key = s + 0.01 * len(t)          # prefer longer read when confidences are close
    if g is None:
        groups.append(dict(text=t, conf=s, key=key, i=i, frame=fr, variants={t}))
    else:
        g["variants"].add(t)
        if key > g["key"]:
            g.update(text=t, conf=s, key=key, i=i, frame=fr)


def default_out(clip, date):
    """<data_root>/frames/<date>. See config.data_root for how the data root is chosen."""
    return _config.frames_dir(CFG, date, clip)


def process_clip(ff, W_, clip, a, rx):
    W, H = probe(ff, clip)
    d = motion_scores(ff, clip, W, H)
    picks, nseg = pick_frames(d, a.min_motion, a.k, a.pad, a.max_frames, a.stride, max_segments=a.max_segments)
    seg_of = dict(picks)
    seg_hits, seg_seen, seg_found, used = {}, {}, set(), 0
    groups = []   # each: dict(text, conf, frame_i, frame, variants)
    for i, fr in full_frames(ff, clip, W, H, [i for i, _ in picks]):
        sid = seg_of[i]
        if seg_hits.get(sid, 0) >= a.stop_hits and seg_seen.get(sid, 0) >= a.min_frames:
            continue                     # this vehicle already has a confident, repeated plate read
        seg_seen[sid] = seg_seen.get(sid, 0) + 1
        used += 1
        for t, s in read_plates(W_["ocr"], fr, rx, a.deskew):
            if s < a.min_conf:
                continue
            seg_found.add(sid)
            if s >= a.stop_conf:
                seg_hits[sid] = seg_hits.get(sid, 0) + 1
            add_read(groups, t, s, i, fr)

    # Daylight plates tilt anywhere from about -5 to 20 degrees. Where rotation found nothing in a
    # strong segment, retry a few frames un-rotated at 960 px (this recovered plates in the daylight test).
    empty = [sid for sid in range(min(nseg, a.fallback_segments)) if sid not in seg_found]
    if empty and a.fallback_frames:
        pick2 = []
        for sid in empty:
            fs = [i for i, sg in picks if sg == sid][::2]
            if len(fs) > a.fallback_frames:
                fs = [fs[j] for j in np.linspace(0, len(fs) - 1, a.fallback_frames).astype(int)]
            pick2 += fs
        if W_.get("ocr_full") is None:
            W_["ocr_full"] = make_ocr(W_.get("threads"), 960)
        for i, fr in full_frames(ff, clip, W, H, pick2):
            used += 1
            for t, s in read_plates(W_["ocr_full"], fr, rx, 0):
                if s >= a.min_conf:
                    add_read(groups, t, s, i, fr)
    return len(d), used, nseg, groups


_W = {}


def _init(a, threads):
    import cv2
    _W.update(threads=threads, ocr=make_ocr(threads, a.det_side), ocr_full=None, rx=re.compile(a.plate_regex), ff=ffmpeg_exe(), a=a, cv2=cv2)
    # Paddle installs its own SIGTERM handler; Pool.terminate() then trips it and macOS logs a crash
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _work(job):
    clip, out = job
    t0 = time.time()
    try:
        total, used, nseg, groups = process_clip(_W["ff"], _W, clip, _W["a"], _W["rx"])
    except Exception as e:   # corrupt clip: report, do not mark done
        return clip, None, str(e)
    rows = []
    for g in sorted(groups, key=lambda g: -g["conf"]):
        name = f"{clip_stamp(clip)}-{g['text']}_{g['conf']:.2f}.png"
        _W["cv2"].imwrite(str(Path(out) / name), g["frame"])
        rows.append((g["text"], g["conf"], g["i"], "|".join(sorted(g["variants"])), name))
    return clip, (total, used, nseg, round(time.time() - t0, 1), rows), None


def _finish():
    """Paddle's native thread pool segfaults during normal interpreter teardown (and the OS then logs a
    crash report), so the CLI ends the process directly. Tests set LPR_NO_FORCE_EXIT to skip this."""
    sys.stdout.flush()
    if os.environ.get("LPR_NO_FORCE_EXIT"):
        return
    os._exit(0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", help="folder of .dav clips (one day), or a single .dav")
    ap.add_argument("--out", help="output folder (default <data_root>/frames/<date>)")
    ap.add_argument("--plate-regex", default=PCFG["plate_regex"])
    ap.add_argument("--min-conf", type=float, default=PCFG["min_conf"])
    ap.add_argument("--max-frames", type=int, default=PCFG["max_frames"], help="full-res frames OCR'd per motion segment")
    ap.add_argument("--max-segments", type=int, default=PCFG["max_segments"], help="keep this many strongest motion segments per clip")
    ap.add_argument("--fallback-segments", type=int, default=PCFG["fallback_segments"], help="retry this many empty top segments un-rotated")
    ap.add_argument("--fallback-frames", type=int, default=PCFG["fallback_frames"], help="frames per retried segment (0 disables the retry)")
    ap.add_argument("--stop-conf", type=float, default=PCFG["stop_conf"], help="stop OCR on a segment once a plate reaches this")
    ap.add_argument("--deskew", type=float, default=CFG["camera"]["tilt_degrees"], help="rotate frames by this many degrees (CCW) before OCR; 0=off. A fixed camera gives plates a shared tilt")
    ap.add_argument("--det-side", type=int, default=PCFG["det_side"], help="detector max side in px (use 960 with --deskew 0)")
    ap.add_argument("--stop-hits", type=int, default=PCFG["stop_hits"], help="confident reads needed to stop a segment")
    ap.add_argument("--min-frames", type=int, default=PCFG["min_frames"], help="OCR at least this many frames per segment before stopping")
    ap.add_argument("--stride", type=int, default=PCFG["stride"], help="OCR every Nth moving frame")
    ap.add_argument("--workers", type=int, default=PCFG["workers"], help="parallel clips")
    ap.add_argument("--min-motion", type=float, default=PCFG["min_motion"], help="floor for motion threshold (gray levels)")
    ap.add_argument("-k", type=float, default=PCFG["motion_k"], help="motion threshold = median + k*MAD")
    ap.add_argument("--pad", type=int, default=PCFG["pad"], help="frames added around each moving frame")
    ap.add_argument("--files-from", metavar="LIST", help="text file with one .dav path per line (used by the watcher)")
    ap.add_argument("--limit", type=int, help="only the first N clips (for trial runs)")
    ap.add_argument("--redo", action="store_true", help="ignore index.csv, reprocess everything")
    ap.add_argument("--warmup", action="store_true", help="download/cache models and exit")
    a = ap.parse_args(argv)

    if a.warmup:
        ocr = make_ocr()
        list(ocr.predict(np.zeros((320, 320, 3), np.uint8)))
        _finish()
        return
    if not a.src and not a.files_from:
        ap.error("src or --files-from required")

    if a.files_from:
        clips = [Path(l.strip()) for l in open(a.files_from) if l.strip()]
    else:
        src = Path(a.src)
        clips = [src] if src.is_file() else sorted(src.glob("*.dav"))
    stamp0 = clip_stamp(clips[0]) if clips else "x"
    out = Path(a.out) if a.out else default_out(clips[0], stamp0.split("_")[0]) if clips else Path(a.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    index = out / "index.csv"
    done = set()
    if index.exists() and not a.redo:
        done = {r["clip"] for r in read_csv(index)}
    new = not index.exists()
    f = index.open("a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"])

    import multiprocessing as mp
    todo = [c for c in clips if c.name not in done][:a.limit]
    print(f"{len(clips)} clips, {len(todo)} to do, out={out}", flush=True)
    t_all = time.time()
    threads = max(1, (os.cpu_count() or 4) // a.workers)
    ctx = mp.get_context("spawn")
    with ctx.Pool(a.workers, _init, (a, threads)) as pool:
        for n, (clip, r, err) in enumerate(pool.imap_unordered(_work, [(str(c), str(out)) for c in todo]), 1):
            name = Path(clip).name
            if err:
                print(f"[{n}/{len(todo)}] {name}: ERROR {err}", flush=True)
                continue
            total, used, nseg, secs, rows = r
            if not rows:
                w.writerow([name, "", "", "", "", "", total, nseg, used, secs])
            for text, conf, i, var, img in rows:
                w.writerow([name, text, f"{conf:.4f}", i, var, img, total, nseg, used, secs])
            f.flush()
            res = ", ".join(f"{t} {c:.2f}" for t, c, *_ in rows) or "-"
            print(f"[{n}/{len(todo)}] {name}: {res} ({nseg} seg, {used} OCR frames, {secs}s)", flush=True)
    print(f"done in {round(time.time() - t_all)}s", flush=True)
    f.close()
    _finish()


if __name__ == "__main__":
    main()
