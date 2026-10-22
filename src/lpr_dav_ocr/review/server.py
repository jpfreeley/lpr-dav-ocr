"""Local plate review server. Binds 127.0.0.1 only; nothing leaves this Mac.

  lpr review [--root DIR] [--day YYYY-MM-DD] [--port N]

Reads  <root>/frames/<day>/index.csv  (+ second_pass.csv when present) and the saved best frames.
Writes <root>/labels/labels.jsonl (append only, latest line per image wins) and, for correct/fixed
plates, a leveled crop in <root>/crops/ (training data).
Default root: the data root from the configuration (paths.data_root, see docs/configuration.md).
Pages: /  (first unreviewed)   /i/<image-file>   ?f=need|auto|unrev|flag|all filters the queue   ?day=YYYY-MM-DD picks a day.
All days under <root>/frames/ are available from the day selector; labels are shared.
"""
import argparse, csv, json, os, re, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
from .. import config as _config
from .. import watchlist as WL

CFG = _config.load()
TILT, FPS = CFG["camera"]["tilt_degrees"], CFG["camera"]["fps"]
FRAME_W, FRAME_H = CFG["camera"]["frame_width"], CFG["camera"]["frame_height"]
VERDICTS = {"ok", "fix", "notplate", "obstructed", "skip"}
# Auto-accept: skip the review queue when the read is very likely right (confidence at or above this,
# both passes agree, plate-shaped). Validate on your own labels before trusting it (docs/accuracy.md),
# and audit it with the "auto" filter.
AUTO_CONF = CFG["review"]["auto_accept_conf"]



# NYC TLC (taxi / for-hire) plates are a letter T or Y, six digits, then a letter, e.g. T123456C. The first-pass reader
# most often gets this wrong by reading the T as 1 and the final C as 0. That is a hint offered with one-click "use",
# never a filter and never applied automatically (see docs/review-ui.md).
TLC_RX = re.compile(r"[TY]\d{6}[A-Z]")
_TLC_FIRST, _TLC_LAST = {"1": "T", "I": "T", "L": "T"}, {"0": "C", "O": "C", "D": "C", "Q": "C", "G": "C"}
_TLC_MID = {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2"}


def tlc_fix(p):
    """The TLC-pattern plate this read most likely is, or None (wrong length or it cannot fit the pattern)."""
    if not p or len(p) != 8:
        return None
    c = _TLC_FIRST.get(p[0], p[0]) + "".join(_TLC_MID.get(x, x) for x in p[1:7]) + _TLC_LAST.get(p[7], p[7])
    return c if TLC_RX.fullmatch(c) else None


class Store:
    def __init__(self, root, default_day=None):
        self.root = root
        self.base = root / "frames" if (root / "frames").is_dir() else root
        self.default_day = default_day
        (root / "labels").mkdir(parents=True, exist_ok=True)
        (root / "crops").mkdir(parents=True, exist_ok=True)
        self.labels_path = root / "labels" / "labels.jsonl"
        self.write_lock = threading.Lock()
        self._cache = {}
        self._busy = set()
        self.imgcache = _config.home(CFG) / "imgcache"
        self.imgcache.mkdir(parents=True, exist_ok=True)

    def frames(self, day):
        return self.base / day

    def _load(self, path, kind):
        with path.open(newline="") as f:
            return list(csv.DictReader(f)) if kind == "csv" else [json.loads(l) for l in f if l.strip()]

    def _read(self, path, kind):
        """Stale-while-revalidate. Reads over SMB can take many seconds when the share is busy, and
        file mtimes there are unreliable, so: answer from the last good read at once and refresh it
        in the background when it is older than 2 s. Only the very first read of a file blocks."""
        now = time.monotonic()
        c = self._cache.get(path)
        if c is None:
            try:
                rows = self._load(path, kind)
            except FileNotFoundError:
                return []
            except json.JSONDecodeError:
                return []
            self._cache[path] = (now, rows)
            return rows
        if now - c[0] >= 2 and path not in self._busy:
            self._busy.add(path)
            threading.Thread(target=self._refresh, args=(path, kind), daemon=True).start()
        return c[1]

    def _refresh(self, path, kind):
        try:
            self._cache[path] = (time.monotonic(), self._load(path, kind))
        except Exception:                 # half-written line or share hiccup: keep the last good read
            pass
        finally:
            self._busy.discard(path)

    def labels(self):
        return {r["image"]: r for r in self._read(self.labels_path, "jsonl")}

    def days(self):
        """Every day folder with an index.csv, oldest first, with review counts."""
        names = sorted(p.name for p in self.base.iterdir() if re.fullmatch(r"\d{4}-\d\d-\d\d", p.name) and (p / "index.csv").exists()) if self.base.is_dir() else []
        out = []
        for d in names:
            its = self.items(d)
            out.append(dict(day=d, total=len(its), left=sum(1 for i in its if i["ready"] and not i["label"] and not i["auto"]),
                            auto=sum(1 for i in its if i["auto"] and not i["label"]),
                            waiting=sum(1 for i in its if not i["ready"] and not i["label"])))
        return out

    def pick_day(self, want=None):
        ds = self.days()
        names = [d["day"] for d in ds]
        if want in names:
            return want
        if self.default_day in names:
            return self.default_day
        # newest day that still has unreviewed plates, else the newest day
        return next((d["day"] for d in reversed(ds) if d["left"]), names[-1] if names else None)

    @staticmethod
    def day_of(image):
        m = re.match(r"(\d{4}-\d\d-\d\d)_", image or "")
        return m[1] if m else None

    def items(self, day):
        fr = self.frames(day)
        sp = {r["image"]: r for r in self._read(fr / "second_pass.csv", "csv")}
        labels, seen, out = self.labels(), set(), []
        for r in sorted(self._read(fr / "index.csv", "csv"), key=lambda r: r.get("image", "")):
            img = r.get("image")
            if not r.get("plate") or not img or img in seen:
                continue
            seen.add(img)
            s = sp.get(img, {})
            conf = float(r["conf"])
            box = [int(s[k]) for k in ("x0", "y0", "x1", "y1")] if s.get("x0") not in (None, "") else None
            alt = s.get("plate2") or None
            shape = s.get("shape") or None
            auto = conf >= AUTO_CONF and bool(alt) and alt == r["plate"] and shape == "ok"
            out.append(dict(
                image=img, day=day, auto=auto, plate=r["plate"], conf=conf, clip=r["clip"].split("[")[0], time=self._time(img, r.get("frame")),
                alt=({"plate": alt, "conf": float(s["conf2"])} if alt else None), crop=s.get("plate3") or None, box=box,
                tlc=self._tlc(r["plate"], alt, s.get("plate3")),
                shape=shape, aspect=s.get("aspect") or None, second_pass=img in sp, ready=img in sp,
                variants=[v for v in (r.get("variants") or "").split("|") if v and v != r["plate"]],
                label=labels.get(img),
                flagged=conf < 0.9 or bool(alt and alt != r["plate"]) or bool(shape and shape != "ok"),
            ))
        return out

    @staticmethod
    def _tlc(p1, p2, p3):
        """Hint for the review page: {plate, shaped} when a read fits the TLC pattern. shaped = the first-pass read already does."""
        if p1 and TLC_RX.fullmatch(p1):
            return dict(plate=p1, shaped=True)
        for p in (p1, p2, p3):
            f = tlc_fix(p)
            if f:
                return dict(plate=f, shaped=False)
        return None

    @staticmethod
    def _time(img, frame):
        m = re.search(r"_(\d\d)\.(\d\d)\.(\d\d)-", img)
        if not m:
            return ""
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + (int(frame) // FPS if frame and frame.isdigit() else 0)
        return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"

    # --- plate history: every sighting of one plate, across all days ---------------------------------------
    BASES = ("verified", "fixed", "auto", "unverified")

    @staticmethod
    def basis(i):
        """(how sure, the plate text) for one saved plate. None when the verdict says there is no plate to speak of."""
        lb = i["label"]
        if lb:
            if lb["verdict"] == "ok":
                return "verified", i["plate"]
            if lb["verdict"] == "fix":
                return "fixed", (lb.get("truth") or "").upper()
            return None, None                    # not a plate / obstructed / can't tell
        return ("auto", i["plate"]) if i["auto"] else ("unverified", i["plate"])

    def _day_names(self):
        if not self.base.is_dir():
            return []
        return sorted(p.name for p in self.base.iterdir() if re.fullmatch(r"\d{4}-\d\d-\d\d", p.name) and (p / "index.csv").exists())

    def reads(self, unverified=False):
        """(plate, basis, item) for every plate that is human verified or auto-accepted (plus unreviewed ones on request)."""
        c = getattr(self, "_reads_cache", None)
        if c is None or time.time() - c[0] > 20:          # a few seconds of staleness is fine; labels clear it
            allr = []
            for d in self._day_names():
                for i in self.items(d):
                    bs, t = self.basis(i)
                    if bs and t:
                        allr.append((t, bs, i))
            c = self._reads_cache = (time.time(), allr)
        return [r for r in c[1] if unverified or r[1] != "unverified"]

    def sightings(self, plate, unverified=False):
        """Exact-match history of one plate, newest first."""
        plate = re.sub(r"[^A-Z0-9]", "", (plate or "").upper())
        rows = [dict(image=i["image"], day=i["day"], time=i["time"], clip=i["clip"], conf=i["conf"], basis=b, box=i["box"],
                     state=(i["label"] or {}).get("state"))
                for t, b, i in self.reads(unverified) if t == plate]
        rows.sort(key=lambda r: (r["day"], r["time"]), reverse=True)
        hours = [0] * 24
        for r in rows:
            hours[int(r["time"][:2]) if r["time"] else 0] += 1
        states = {}
        for r in rows:
            if r["state"] and r["state"] != "UNKN":
                states[r["state"]] = states.get(r["state"], 0) + 1
        return dict(plate=plate, count=len(rows), days=len({r["day"] for r in rows}),
                    first=(rows[-1]["day"] + " " + rows[-1]["time"]) if rows else None,
                    last=(rows[0]["day"] + " " + rows[0]["time"]) if rows else None,
                    by_hour=hours, states=states, sightings=rows)

    def plate_search(self, q, unverified=False, limit=20):
        """Plates containing q, with sighting counts, most seen first (for the search box)."""
        q = re.sub(r"[^A-Z0-9]", "", (q or "").upper())
        found = {}
        for t, b, i in self.reads(unverified):
            if q in t:
                e = found.setdefault(t, dict(plate=t, count=0, last=""))
                e["count"] += 1
                e["last"] = max(e["last"], i["day"] + " " + i["time"])
        return sorted(found.values(), key=lambda e: (-e["count"], e["plate"]))[:limit]

    def view(self, day, f):
        its = self.items(day)
        if f == "need":          # default queue: second pass done, no verdict yet, not auto-accepted
            its = [i for i in its if i["ready"] and not i["label"] and not i["auto"]]
        elif f == "auto":        # spot-check the auto-accepted plates
            its = [i for i in its if i["auto"]]
        elif f == "unrev":       # all unreviewed, but only plates that already have a second pass
            its = [i for i in its if i["ready"] and not i["label"]]
        elif f == "flag":
            its = [i for i in its if i["flagged"]]
        return its

    def add_label(self, rec):
        with self.write_lock, self.labels_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        c = self._cache.get(self.labels_path)
        if c:                                  # keep the cached read consistent with what was just written
            c[1].append(rec)
        self._reads_cache = None               # plate history rebuilds from the new label

    def save_crop(self, item, truth):
        """Leveled plate crop beside the label, for later training. Best effort."""
        if not item["box"]:
            return
        try:
            import cv2
            img = cv2.imread(str(self.frames(item["day"]) / item["image"]))
            if img is None:
                return
            x0, y0, x1, y1 = item["box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            M = cv2.getRotationMatrix2D((cx, cy), TILT, 1.0)
            rot = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]))
            hw, hh = (x1 - x0) * 0.62, (y1 - y0) * 0.5
            crop = rot[max(0, int(cy - hh)):int(cy + hh), max(0, int(cx - hw)):int(cx + hw)]
            if crop.size:
                name = f"{Path(item['image']).stem}__{truth}.png"
                cv2.imwrite(str(self.root / "crops" / name), crop)
        except Exception as e:   # never block labeling on the crop
            print("crop failed:", e)


def make_handler(store):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if ctype != "image/png" else "max-age=3600")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            f = (q.get("f") or ["need"])[0]
            if u.path == "/":
                day = store.pick_day((q.get("day") or [None])[0])
                its = store.view(day, f) if day else []
                nxt = next((i for i in its if not i["label"]), its[0] if its else None)
                qs = "&".join(x for x in ([f"f={f}"] if f else []) + ([f"day={day}"] if day else []))
                loc = (f"/i/{nxt['image']}" if nxt else "/i/none") + (f"?{qs}" if qs else "")
                return self._send(302, "", extra={"Location": loc})
            if u.path.startswith("/i/"):
                return self._send(200, (HERE / "page.html").read_bytes(), "text/html; charset=utf-8")
            if u.path.startswith("/img/"):
                name = unquote(u.path[5:])
                day = store.day_of(name)
                p = (store.frames(day) / name).resolve() if day else None
                if not p or p.parent != store.frames(day).resolve() or p.suffix != ".png" or not p.is_file():
                    return self._send(404, {"error": "not found"})
                local = store.imgcache / name
                if local.is_file():
                    return self._send(200, local.read_bytes(), "image/png")
                data = p.read_bytes()
                try:
                    tmp = local.with_suffix(".tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, local)
                except OSError:
                    pass
                return self._send(200, data, "image/png")
            if u.path == "/api/config":
                return self._send(200, {"tilt": TILT, "w": FRAME_W, "h": FRAME_H})
            if u.path == "/history":
                return self._send(200, (HERE / "history.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/history":
                return self._send(200, store.sightings((q.get("q") or [""])[0], (q.get("unverified") or ["0"])[0] == "1"))
            if u.path == "/api/plates":
                return self._send(200, store.plate_search((q.get("q") or [""])[0], (q.get("unverified") or ["0"])[0] == "1"))
            if u.path == "/api/watch":
                return self._send(200, WL.load())
            if u.path == "/api/item":
                name = (q.get("image") or [""])[0]
                day = store.day_of(name) or store.pick_day((q.get("day") or [None])[0])
                its = store.view(day, f) if day else []
                everything = store.items(day) if day else []
                pos = next((k for k, i in enumerate(its) if i["image"] == name), None)
                if pos is None and name:       # image outside this filter: show it anyway
                    full = next((i for i in everything if i["image"] == name), None)
                    its = [full] + its if full else its
                    pos = 0 if full else None
                if pos is None:
                    pos = next((k for k, i in enumerate(its) if not i["label"]), 0) if its else None
                counts = {}
                for i in everything:
                    v = i["label"]["verdict"] if i["label"] else "none"
                    counts[v] = counts.get(v, 0) + 1
                labeled = [i for i in everything if i["label"]]
                # furthest reviewed plate in time order (where you stopped), not the most recently edited one
                last = labeled[-1]["image"] if labeled else None
                if pos is not None:
                    it_ = its[pos]
                    lb_ = it_["label"]
                    reads = ([lb_["truth"]] if lb_ and lb_.get("truth") else []) + [it_["plate"]] + ([it_["alt"]["plate"]] if it_["alt"] else [])
                    m_ = WL.match(reads)
                    it_["watch"] = dict(plate=m_[0][0]["plate"], nickname=m_[0][0]["nickname"], level=m_[0][1]) if m_ else None
                resp = dict(last_reviewed=last, item=its[pos] if pos is not None else None, n=(pos or 0) + 1, total=len(its),
                            counts=counts, all=len(everything), day=day, days=store.days())
                if pos is not None:
                    resp["prev"] = its[pos - 1]["image"] if pos > 0 else None
                    resp["next"] = its[pos + 1]["image"] if pos + 1 < len(its) else None
                    resp["next_unreviewed"] = next((i["image"] for i in its[pos + 1:] if not i["label"]), None)
                    lo = max(0, pos - 12)
                    resp["window"] = [dict(n=k + 1, image=i["image"], plate=i["plate"],
                                           verdict=i["label"]["verdict"] if i["label"] else None,
                                           state=(i["label"] or {}).get("state"))
                                      for k, i in enumerate(its[lo:pos + 13], start=lo)]
                return self._send(200, resp)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if self.headers.get("X-Requested-With") != "lpr-review":
                return self._send(403, {"error": "forbidden"})
            if path in ("/api/watch", "/api/watch/remove"):
                try:
                    d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    if path == "/api/watch":
                        return self._send(200, WL.add(d["plate"], d.get("nickname", "")))
                    WL.remove(d["plate"])
                    return self._send(200, {"ok": True})
                except (KeyError, ValueError, TypeError):
                    return self._send(400, {"error": "bad watch request"})
            if path != "/api/label":
                return self._send(403, {"error": "forbidden"})
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                it = next(i for i in store.items(store.day_of(d["image"])) if i["image"] == d["image"])
                verdict, state = d["verdict"], str(d.get("state") or "UNKN").upper()
                truth = str(d.get("truth") or "").upper() or None
                assert verdict in VERDICTS and re.fullmatch(r"UNKN|[A-Z]{2,4}", state)
                if verdict == "fix":
                    assert truth and re.fullmatch(r"[A-Z0-9]{2,10}", truth) and truth != it["plate"]
                elif verdict == "ok":
                    truth = it["plate"]
                else:
                    truth = None
            except (StopIteration, KeyError, AssertionError, ValueError, TypeError):
                return self._send(400, {"error": "bad label"})
            store.add_label(dict(ts=datetime.now().astimezone().isoformat(timespec="seconds"), image=it["image"],
                                 clip=it["clip"], day=it["day"], plate_ocr=it["plate"], conf=it["conf"],
                                 plate_second=(it["alt"] or {}).get("plate"), shape=it["shape"],
                                 verdict=verdict, truth=truth, state=state))
            if truth:
                store.save_crop(it, truth)
            self._send(200, {"ok": True})
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="folder holding frames/ labels/ crops/")
    ap.add_argument("--day", help="YYYY-MM-DD to open first (default: newest day with unreviewed plates)")
    ap.add_argument("--port", type=int, default=CFG["review"]["port"])
    a = ap.parse_args(argv)
    root = Path(a.root).expanduser() if a.root else _config.data_root(CFG)
    store = Store(root, a.day)
    if not store.days():
        raise SystemExit(f"no day with index.csv under {store.base}")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(store))
    print(f"review: http://localhost:{a.port}/   days={[d['day'] for d in store.days()]}   labels={store.labels_path}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
