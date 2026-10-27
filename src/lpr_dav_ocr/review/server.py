"""Local plate review server. Binds 127.0.0.1 only; nothing leaves this Mac.

  lpr review [--root DIR] [--day YYYY-MM-DD] [--port N]

Reads  <root>/frames/<day>/index.csv  (+ second_pass.csv when present) and the saved best frames.
Writes <root>/labels/labels.jsonl (append only, latest line per image wins) and, for correct/fixed
plates, a leveled crop in <root>/crops/ (training data).
Default root: the data root from the configuration (paths.data_root, see docs/configuration.md).
Pages: /  (first unreviewed)   /i/<image-file>   ?f=need|auto|unrev|flag|all filters the queue   ?day=YYYY-MM-DD picks a day.
All days under <root>/frames/ are available from the day selector; labels are shared.
"""
import argparse, collections, csv, json, os, re, subprocess, sys, threading, time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
from .. import config as _config
from .. import watchlist as WL
from . import reports as _reports
from .. import vehicles as _veh
from .. import ops as _ops

CFG = _config.load()
TILT, FPS = CFG["camera"]["tilt_degrees"], CFG["camera"]["fps"]
FRAME_W, FRAME_H = CFG["camera"]["frame_width"], CFG["camera"]["frame_height"]
VERDICTS = {"ok", "fix", "notplate", "obstructed", "skip"}
# Auto-accept: skip the review queue when the read is very likely right (confidence at or above this,
# both passes agree, plate-shaped). Validate on your own labels before trusting it (docs/accuracy.md),
# and audit it with the "auto" filter.
AUTO_CONF = CFG["review"]["auto_accept_conf"]
RECENT_DAYS = CFG["review"]["recent_days"]
REJECT_ASPECT = CFG["review"]["reject_aspect"]
RECORD_CONF = CFG["review"]["record_accept_conf"]      # a plate that has a ticket record is auto-accepted from this confidence (0 turns it off)
CONFIRMED_DAYS = CFG["review"]["confirmed_days"]        # a plate whose vehicle you confirmed vouches for its sightings this many days either side
VALUE_QUEUE = CFG["review"]["value_queue"]      # size of the "most valuable first" queue
NOT_PLATE_TEXTS = {t.strip().upper() for t in CFG["review"]["not_plate_texts"]}      # words that are never a plate (state banner)



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


# In the three letter positions of a standard plate (AAANNNN) a digit is a misread letter. On the author's labels a 0 there
# was really D about 80% of the time, a 6 a G and a 2 a Z about 80 to 90% (an 8 or a 5 was close to a coin flip and a 1
# had no clear winner, so those are left alone). It is only ever offered, never applied: some real plates do start with a digit.
_STD_LETTER = {"0": "D", "6": "G", "2": "Z"}


def std_fix(p):
    """The AAANNNN plate this read most likely is, when the last four are digits and the first three are letters or
    0/6/2 with at least one letter among them. None otherwise."""
    if not p or len(p) != 7 or not p[3:].isdigit():
        return None
    head = p[:3]
    if not any(c.isalpha() for c in head) or not any(c in _STD_LETTER for c in head) or not all(c.isalpha() or c in _STD_LETTER for c in head):
        return None
    return "".join(_STD_LETTER.get(c, c) for c in head) + p[3:]


# Plate patterns used as a hint when choosing between reads. NYS standard passenger plates are three letters then four
# digits (AAANNNN); NYC TLC plates are in the TLC pattern above. Many valid plates fit neither (vanity, older
# styles, other states), so a fit only ever breaks a tie. It never rejects a read.
STD_RX = re.compile(r"[A-Z]{3}\d{4}")
NJ_RX = re.compile(r"[A-Z]\d{2}[A-Z]{3}")        # NJ passenger plates are often a letter, two digits, three letters
COM_RX = re.compile(r"\d{5}[A-Z]{2}")            # NY commercial plates are often five digits then two letters
MOP_RX = re.compile(r"\d{3}[A-Z]{2}\d")           # NYS moped plates are often three digits, two letters, a digit


def fits(p):
    """'TLC', 'NYS', 'NJ', 'COM', 'MOP' or None: which common pattern a read already fits."""
    if not p:
        return None
    return "TLC" if TLC_RX.fullmatch(p) else "NYS" if STD_RX.fullmatch(p) else "NJ" if NJ_RX.fullmatch(p) else "COM" if COM_RX.fullmatch(p) else "MOP" if MOP_RX.fullmatch(p) else None


def pick_read(p1, c1, p2, c2, p3):
    """Best guess between the two passes and the crop read when they disagree: (plate, why) or None.
    A read that fits a common pattern wins over one that does not; otherwise a crop read that backs one of the
    passes decides; otherwise the more confident pass. On the author's labels this was right 81 times out of 144
    disagreements, against 76 for the crop tie-break alone and 69 for 'higher confidence'."""
    if not p1 or not p2 or p1 == p2:
        return None
    cands = list(dict.fromkeys(x for x in (p1, p2, p3) if x))
    fit = [x for x in cands if fits(x)]
    pool, why = (fit, "fits the pattern") if len(fit) == 1 else (None, None)
    if pool:
        return pool[0], why
    pool = fit or [p1, p2]
    if p3 and p3 in pool and (p3 == p1 or p3 == p2):
        return p3, "the crop read backs it"
    best = max(pool, key=lambda x: (c1 if x == p1 else c2 if x == p2 else 0))
    return best, "more confident"


def one_off(a, b):
    """True when a and b differ by exactly one substituted, inserted or deleted character."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def _variants(p):
    """The plate and each plate with one character removed. Two plates one edit apart share a variant."""
    return {p} | {p[:i] + p[i + 1:] for i in range(len(p))}


_OPS = {"t": 0.0, "data": None, "busy": False}


def ops_cached(store, max_age=25):
    """Operations data, stale-while-revalidate: answer from the last build at once and rebuild in a thread when it is
    older than max_age seconds. Only the very first request waits."""
    home = Path(os.environ.get("LPR_HOME", Path.home() / ".lpr-ocr"))
    cam = store.root.parent / store.root.name.removeprefix("db_") if store.root.name.startswith("db_") else None

    def work():
        try:
            _OPS["data"] = _ops.build(store, home, cam)
            _OPS["t"] = time.monotonic()
        finally:
            _OPS["busy"] = False
    if _OPS["data"] is None:
        _OPS["busy"] = True
        work()
    elif time.monotonic() - _OPS["t"] > max_age and not _OPS["busy"]:
        _OPS["busy"] = True
        threading.Thread(target=work, daemon=True).start()
    return _OPS["data"]


class Store:
    def __init__(self, root, default_day=None):
        self.root = root
        self.base = root / "frames" if (root / "frames").is_dir() else root
        self.default_day = default_day
        (root / "labels").mkdir(parents=True, exist_ok=True)
        (root / "crops").mkdir(parents=True, exist_ok=True)
        self.labels_path = root / "labels" / "labels.jsonl"
        self.dups_path = root / "labels" / "duplicates.jsonl"          # frames merged into another by `dedupe`
        self.gold_path = root / "labels" / "gold.jsonl"                 # the frame you marked as the clear exemplar of a plate
        self.vflags_path = root / "labels" / "vehicle_flags.jsonl"      # plates whose NYC ticket record you saw is stale
        self.states_path = root / "labels" / "plate_states.jsonl"      # one state per plate, applied to every sighting of it
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
            except (FileNotFoundError, json.JSONDecodeError):
                rows = []                                # remembered, so a missing file is not re-opened on every call
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
            out.append(dict(day=d, total=len(its), left=sum(1 for i in its if i["ready"] and not i["label"] and not i["auto"] and not i["rejected"]),
                            auto=sum(1 for i in its if i["auto"] and not i["label"]),
                            rejected=sum(1 for i in its if i["rejected"] and not i["label"]),
                            recheck=sum(1 for i in its if self.needs_recheck(i)),
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
        labels, out = self.labels(), []
        seen = {r["image"] for r in self._read(self.dups_path, "jsonl")}      # merged duplicates are invisible everywhere
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
            rec = self.recent(r["plate"], img)
            banner = r["plate"] in NOT_PLATE_TEXTS                # a word printed on plates (NEWYORK), never the plate itself
            by_conf = conf >= AUTO_CONF and bool(alt) and alt == r["plate"] and shape == "ok" and not banner
            by_record = bool(RECORD_CONF) and not by_conf and conf >= RECORD_CONF and bool(alt) and alt == r["plate"] and shape == "ok" and not banner and self.has_record(r["plate"])
            auto = (by_conf or by_record or bool(rec and rec["exact"])) and not banner
            asp = float(s["aspect"]) if s.get("aspect") else None
            elsewhere = s.get("elsewhere") or None          # the second pass found another plate-shaped read in this frame
            wide = bool(REJECT_ASPECT and asp is not None and asp > REJECT_ASPECT)
            rejected = bool((wide or banner) and not auto and not elsewhere)
            out.append(dict(
                image=img, day=day, auto=auto, plate=r["plate"], conf=conf, clip=r["clip"].split("[")[0], time=self._time(img, r.get("frame")),
                alt=({"plate": alt, "conf": float(s["conf2"])} if alt else None), crop=s.get("plate3") or None, box=box,
                elsewhere=({"plate": elsewhere, "conf": float(s["elsewhere_conf"] or 0)} if elsewhere else None),
                tlc=self._tlc(r["plate"], alt, s.get("plate3")),
                stdfix=self._stdfix(r["plate"], alt, s.get("plate3")),
                fits=fits(r["plate"]), rejected=rejected, recent=rec, auto_why=("conf" if by_conf else "record" if by_record else "recent" if auto else None),
                pick=(dict(plate=elsewhere, why="the first read is the state banner, not a plate") if banner and elsewhere else
                      dict(zip(("plate", "why"), pick_read(r["plate"], conf, alt, float(s["conf2"]) if alt else 0.0, s.get("plate3") or None) or ())) or None),
                banner=banner,
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
    def _stdfix(p1, p2, p3):
        """Offer for the review page: {plate, read} when no read fits AAANNNN or the TLC pattern but a letter-position fix of one does."""
        reads = [p for p in (p1, p2, p3) if p]
        if any(STD_RX.fullmatch(p) or TLC_RX.fullmatch(p) for p in reads):
            return None
        for p in reads:
            f = std_fix(p)
            if f:
                return dict(plate=f, read=p)
        return None

    # --- recently verified plates: auto-approve an exact match, offer a one-character correction ----------
    def vindex(self):
        """Human-verified plates (verdict ok or fix) only, so auto-approved plates never vouch for each other."""
        rows = self._read(self.labels_path, "jsonl")
        key = (id(rows), len(rows))
        if getattr(self, "_vi", (None,))[0] != key:
            by, near, st = {}, {}, {}
            for r in {r["image"]: r for r in rows}.values():
                d = self._ordinal(r["image"])
                if r.get("verdict") in ("ok", "fix") and r.get("truth") and d:
                    by.setdefault(r["truth"].upper(), []).append((d, r["image"]))
                    if r.get("state") and r["state"] != "UNKN":
                        st.setdefault(r["truth"].upper(), collections.Counter())[r["state"]] += 1
            for t in by:
                for v in _variants(t):
                    near.setdefault(v, set()).add(t)
            self._vi = (key, by, near, st)
        return self._vi[1], self._vi[2]

    def registry(self):
        """plate -> the state you last set on it (latest line wins)."""
        return {r["plate"]: r["state"] for r in self._read(self.states_path, "jsonl") if r.get("plate") and r.get("state")}

    def state_of(self, plate):
        """The state of a plate: the one you last set on it, else the one you set most often on its verified sightings."""
        if self.registry().get(plate):
            return self.registry()[plate]
        self.vindex()
        c = self._vi[3].get(plate)
        return c.most_common(1)[0][0] if c else None

    def eff_state(self, item, plate=None):
        """State shown for one sighting: its own label if it has one, else the plate's state. A state you set on any
        sighting of a plate therefore appears on all of them without writing labels for the auto-accepted ones."""
        own = (item["label"] or {}).get("state")
        if own and own != "UNKN":
            return own
        p = plate or (item["label"] or {}).get("truth") or item["plate"]
        return self.state_of(p.upper()) if p else None

    def set_plate_state(self, plate, state, image):
        rec = dict(ts=datetime.now().astimezone().isoformat(timespec="seconds"), plate=plate, state=state, image=image)
        with self.write_lock, self.states_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        c = self._cache.get(self.states_path)
        if c:
            c[1].append(rec)

    @staticmethod
    def _ordinal(image):
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)_", image or "")
        return date(int(m[1]), int(m[2]), int(m[3])).toordinal() if m else None

    def recent(self, plate, image):
        """Was this plate (or a plate one character away) verified by you within RECENT_DAYS of this sighting?
        {plate, n, exact} or None. Other sightings only; the image itself never counts."""
        d0 = self._ordinal(image)
        if not plate or not d0 or RECENT_DAYS <= 0:
            return None
        by, near = self.vindex()
        hits = lambda t, w=RECENT_DAYS: [x for x in by.get(t, []) if x[1] != image and abs(x[0] - d0) <= w]
        h = hits(plate)
        if not h and CONFIRMED_DAYS > RECENT_DAYS and self._vflags().get(plate, {}).get("match") and self.confirmed(plate):
            h = hits(plate, CONFIRMED_DAYS)         # you confirmed the vehicle by eye: the plate vouches for a longer stretch
        if h:
            return dict(plate=plate, n=len(h), exact=True, state=self.state_of(plate))
        if len(plate) < 5:
            return None
        best = None
        for t in sorted({t for v in _variants(plate) for t in near.get(v, ())}):
            if len(t) >= 5 and one_off(plate, t):
                h = hits(t)
                if h and (best is None or len(h) > best[1]):
                    best = (t, len(h))
        return dict(plate=best[0], n=best[1], exact=False, state=self.state_of(best[0])) if best else None

    # --- "same last four digits" hints for AAANNNN plates whose letters were misread --------------------------
    def known_by_tail(self):
        """last four characters -> {plate: set of images}, from plates you verified or that were auto-accepted."""
        self.reads()                                          # (re)builds the shared cache
        c = self._reads_cache
        if getattr(self, "_known", (None,))[0] is not c:
            idx = {}
            for t, b, i in c[1]:
                if b != "unverified" and STD_RX.fullmatch(t):
                    idx.setdefault(t[3:], {}).setdefault(t, set()).add(i["image"])
            self._known = (c, idx)
        return self._known[1]

    def like(self, item, limit=3):
        """Known plates with the same last four digits as this read and one or two different leading letters,
        best first: [{plate, n, d}]. n = other sightings of that plate, d = letters that differ."""
        out = {}
        for p in (item["plate"], (item.get("alt") or {}).get("plate")):
            if not p or len(p) != 7 or not p[3:].isdigit():
                continue
            for t, imgs in self.known_by_tail().get(p[3:], {}).items():
                d = sum(a != b for a, b in zip(t[:3], p[:3]))
                n = len(imgs - {item["image"]})
                if t != item["plate"] and 1 <= d <= 2 and n:
                    out[t] = dict(plate=t, n=n, d=min(d, out.get(t, {}).get("d", 9)), state=self.state_of(t))
        return sorted(out.values(), key=lambda e: (e["d"], -e["n"], e["plate"]))[:limit]

    def _vflags(self):
        """plate -> latest stale-mark record. Rebuilt only when the cached row list changes (it is appended to in place)."""
        rows = self._read(self.vflags_path, "jsonl")
        key = (id(rows), len(rows))
        if getattr(self, "_vflags_key", None) != key:
            self._vflags_map = {r["plate"]: r for r in rows if r.get("plate")}
            self._vflags_key = key
        return self._vflags_map

    def has_record(self, plate):
        """True when the local ticket index has a vehicle for this plate text, and you have not marked that record
        verified stale. Cached per plate for the life of the process (cleared when you mark a record)."""
        c = self.__dict__.setdefault("_rec_cache", {})
        if plate not in c:
            v = self.vehicle(plate)
            c[plate] = bool(v.get("available") and not v.get("none") and not (v.get("stale") and v["stale"]["same_record"]))
        return c[plate]

    def confirmed(self, plate):
        """True when you confirmed by eye that the vehicle matches this plate's ticket record, and the record is unchanged."""
        f = self._vflags().get(plate)
        if not f or not f.get("match") or f.get("stale"):
            return False
        v = self.vehicle(plate)
        return bool(v.get("confirmed") and v["confirmed"]["same_record"])

    def vehicle(self, plate, state=None):
        """What the local NYC ticket index says about a plate (see vehicles.py): {available, make, body, year, color, ...}."""
        db = getattr(self, "_vdb", None)
        if db is None:
            db = self._vdb = _veh.open_db()              # None until `vehicles import` has been run
        if db is None:
            return dict(available=False)
        v = _veh.lookup(db, plate, state)
        if not v:
            return dict(available=True, none=True)
        out = dict(available=True, text=_veh.describe(v), **v)
        flag = self._vflags().get(plate)
        if flag and flag.get("stale"):          # you looked at the car and the record did not match
            out["stale"] = dict(ts=flag["ts"], same_record=flag.get("record") == out["text"], record=flag.get("record"))
        if flag and flag.get("match"):          # you looked at the car and it matched the record
            out["confirmed"] = dict(ts=flag["ts"], same_record=flag.get("record") == out["text"])
        return out

    def set_vehicle_stale(self, plate, stale, image=None, match=False):
        """Mark (or unmark) the ticket record for a plate as verified stale: you saw the vehicle and it does not match.
        Nothing about the plate or its sightings changes; the record you saw is kept with the mark."""
        v = self.vehicle(plate)
        rec = dict(ts=datetime.now().astimezone().isoformat(timespec="seconds"), plate=plate, stale=bool(stale), match=bool(match) and not stale,
                   image=image, record=v.get("text") if v.get("available") and not v.get("none") else None)
        with self.write_lock, self.vflags_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        c = self._cache.get(self.vflags_path)
        if c:
            c[1].append(rec)
        self.__dict__.pop("_rec_cache", None)
        self._reads_cache = None               # plate history and the recent-verified window depend on the mark
        return rec

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
        if i["rejected"]:
            return None, None                    # auto-rejected: not plate-shaped
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

    # --- gold standard frames: one very clear, unquestionable image per plate -------------------------------
    def gold_map(self):
        """plate -> {image, ts, box} for the frame you marked as the gold standard (latest mark wins, an unmark removes it)."""
        out = {}
        for r in self._read(self.gold_path, "jsonl"):
            if not (r.get("plate") and r.get("image")):
                continue
            if r.get("gold"):
                out[r["plate"]] = r
            elif out.get(r["plate"], {}).get("image") == r["image"]:
                del out[r["plate"]]
        return out

    def _verify_ok(self, item):
        """Save the first-pass read of a frame as verified (Correct). Used when you mark an unreviewed or auto-accepted frame as the
        gold standard: you looked at it, and there is no question what it says."""
        self.add_label(dict(ts=datetime.now().astimezone().isoformat(timespec="seconds"), image=item["image"], clip=item["clip"], day=item["day"],
                            plate_ocr=item["plate"], conf=item["conf"], plate_second=(item["alt"] or {}).get("plate"), shape=item["shape"],
                            verdict="ok", truth=item["plate"], state="UNKN", via="gold"))
        self.save_crop(item, item["plate"])

    def set_gold(self, image, on, verify=False):
        """Mark (or unmark) a frame as the gold standard for its plate: the frame where there is no question what the read is.
        A frame you verified qualifies. With verify=True an unreviewed or auto-accepted frame qualifies too, and is saved as verified
        (Correct) as part of marking it. A frame you marked not a plate, obstructed or can't tell cannot be one. One gold frame per plate:
        a new one replaces the old."""
        day = self.day_of(image)
        item = next((i for i in self.items(day) if i["image"] == image), None) if day else None
        if item is None:
            raise ValueError("unknown frame")
        lb = item["label"]
        if lb and lb.get("verdict") not in ("ok", "fix"):
            raise ValueError("a frame you marked not a plate, obstructed or can't tell cannot be the gold standard")
        if not lb:
            if not (verify and on):
                raise ValueError("only a frame you verified can be the gold standard")
            self._verify_ok(item)
            item = next(i for i in self.items(day) if i["image"] == image)
            lb = item["label"]
        plate = ((lb.get("truth") or "") if lb["verdict"] == "fix" else item["plate"]).upper()
        if not plate:
            raise ValueError("no plate text")
        rec = dict(ts=datetime.now().astimezone().isoformat(timespec="seconds"), plate=plate, image=image, gold=bool(on), box=item["box"])
        with self.write_lock, self.gold_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        c = self._cache.get(self.gold_path)
        if c:
            c[1].append(rec)
        return rec

    def gold_for(self, item):
        """The gold standard frame to compare this plate with: for its own text, else for the plate it is one character from.
        {plate, exact, image, box, ts} or None. The frame itself is never offered as its own comparison."""
        gm = self.gold_map()
        if not gm:
            return None
        truth = ((item["label"] or {}).get("truth") or "").upper()
        own = truth or item["plate"]
        texts = [own, item["plate"]] + ([item["recent"]["plate"]] if item.get("recent") else []) + [x["plate"] for x in item.get("like") or []]
        for t in texts:
            g = gm.get(t)
            if g and g["image"] != item["image"]:
                return dict(plate=t, exact=(t == own), image=g["image"], box=g.get("box"), ts=g["ts"])
        return None

    def sightings(self, plate, unverified=False):
        """Exact-match history of one plate, newest first."""
        plate = re.sub(r"[^A-Z0-9]", "", (plate or "").upper())
        gm = self.gold_map()
        goldimg = (gm.get(plate) or {}).get("image")
        rows = [dict(image=i["image"], day=i["day"], time=i["time"], clip=i["clip"], conf=i["conf"], basis=b, box=i["box"],
                     state=self.eff_state(i, t), gold=i["image"] == goldimg)
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
                    by_hour=hours, states=states, sightings=rows,
                    gold=next((dict(image=r["image"], day=r["day"], time=r["time"], box=r["box"]) for r in rows if r["gold"]), None))

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

    @staticmethod
    def needs_recheck(i):
        """A 'not a plate' label that may be wrong: the second pass found different, plate-shaped text, so a real
        plate may be in the frame (for example the first pass read a model badge). Cleared by relabeling it or by
        confirming 'not a plate' again from the re-check view."""
        lb = i["label"]
        return bool(lb and lb["verdict"] == "notplate" and not lb.get("rechecked")
                    and i["alt"] and i["alt"]["plate"] != i["plate"] and i["shape"] == "ok")

    def view(self, day, f):
        if f == "value":       # most valuable first, across all days
            return self.value_queue()
        its = self.items(day)
        if f == "need":          # default queue: second pass done, no verdict yet, not auto-accepted
            its = [i for i in its if i["ready"] and not i["label"] and not i["auto"] and not i["rejected"]]
        elif f == "recheck":     # earlier 'not a plate' labels that may hide a real plate
            its = [i for i in its if self.needs_recheck(i)]
        elif f == "rejected":    # spot-check the auto-rejected (not plate-shaped) reads
            its = [i for i in its if i["rejected"] and not i["label"]]
        elif f == "auto":        # spot-check the auto-accepted plates
            its = [i for i in its if i["auto"]]
        elif f == "unrev":       # all unreviewed, but only plates that already have a second pass
            its = [i for i in its if i["ready"] and not i["label"] and not i["rejected"]]
        elif f == "flag":
            its = [i for i in its if i["flagged"]]
        return its

    def value_queue(self, limit=None):
        """Unreviewed plates whose label would change the results most, best first, across all days. A watchlist match
        ranks highest, then plates where one label approves many other sightings of the same text (within RECENT_DAYS),
        a read one character from a plate you verified, and repeat plates whose two reads disagree. A plate appears once
        (a label on it approves its other sightings within RECENT_DAYS). Each item gets value={score, why}. Short-lived cache, dropped when a label is saved."""
        limit = limit or VALUE_QUEUE
        c = getattr(self, "_value_cache", None)
        if c and time.monotonic() - c[0] < 20 and c[2] == limit:
            return c[1]
        cands, same = [], collections.defaultdict(list)
        for d in self._day_names():
            for i in self.items(d):
                if i["rejected"]:
                    continue
                same[i["plate"]].append(i)
                if i["ready"] and not i["label"] and not i["auto"]:
                    cands.append(i)
        pend = collections.defaultdict(list)
        for i in cands:
            pend[i["plate"]].append(self._ordinal(i["image"]))
        out = []
        for i in cands:
            why, score = [], 0
            reads = [i["plate"]] + ([i["alt"]["plate"]] if i["alt"] else []) + ([i["crop"]] if i["crop"] else [])
            m = WL.match(reads)
            if m:
                score += 1000 if any(x[1] == "exact" for x in m) else 600
                why.append("watchlist: " + m[0][0]["nickname"])
            o, n = self._ordinal(i["image"]), 0
            if RECENT_DAYS > 0 and o and not i["banner"]:
                n = sum(1 for x in pend[i["plate"]] if x and abs(x - o) <= RECENT_DAYS) - 1
            if n > 0:
                score += 12 * min(n, 25)
                why.append(f"one label clears {n} more")
            rec = i["recent"]
            if rec and not rec["exact"]:
                score += 60
                why.append(f"one character from {rec['plate']} you verified")
            days = len({x["day"] for x in same[i["plate"]]})
            if i["alt"] and i["alt"]["plate"] != i["plate"] and (n > 0 or days >= 2):
                score += 40
                why.append("repeat plate, the two reads disagree")
            if days >= 2:
                why.append(f"seen on {days} days")
            score += 4 * min(days, 5) + (2 if i["fits"] else 0)
            i["value"] = dict(score=score, why=why or ["no strong signal"])
            out.append(i)
        out.sort(key=lambda i: (-i["value"]["score"], i["image"]))
        res, taken = [], collections.defaultdict(list)
        for i in out:                          # one entry per plate: a label on it approves the rest within RECENT_DAYS
            o = self._ordinal(i["image"])
            key = i["image"] if i["banner"] else i["plate"]            # banner reads are separate frames, not repeats of a plate
            if any(o and x and abs(o - x) <= RECENT_DAYS for x in taken[key]):
                continue
            taken[key].append(o)
            res.append(i)
            if len(res) >= limit:
                break
        self._value_cache = (time.monotonic(), res, limit)
        return res

    def last_labeled(self):
        """Image of the label saved most recently (the last line of labels.jsonl), or None."""
        for r in reversed(self._read(self.labels_path, "jsonl")):
            if r.get("image"):
                return r["image"]
        return None

    def add_label(self, rec):
        with self.write_lock, self.labels_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        c = self._cache.get(self.labels_path)
        if c:                                  # keep the cached read consistent with what was just written
            c[1].append(rec)
        self._reads_cache = None               # plate history rebuilds from the new label
        self._value_cache = None

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
            if u.path == "/reports":
                return self._send(200, (HERE / "reports.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/ops":
                return self._send(200, (HERE / "ops.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/ops":
                return self._send(200, ops_cached(store))
            if u.path == "/api/report":
                days = [d for d in ((q.get("days") or [""])[0]).split(",") if re.fullmatch(r"\d{4}-\d\d-\d\d", d)]
                return self._send(200, _reports.build(store, days or None, (q.get("unverified") or ["0"])[0] == "1", WL.load()))
            if u.path == "/history":
                return self._send(200, (HERE / "history.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/history":
                h = store.sightings((q.get("q") or [""])[0], (q.get("unverified") or ["0"])[0] == "1")
                st = max(h["states"], key=h["states"].get) if h["states"] else None
                h["vehicle"] = store.vehicle(h["plate"], st) if h["plate"] else None
                return self._send(200, h)
            if u.path == "/api/plates":
                return self._send(200, store.plate_search((q.get("q") or [""])[0], (q.get("unverified") or ["0"])[0] == "1"))
            if u.path == "/api/lookup":
                # live check while you edit the boxes: hints, recent-verified match and watch status for that text
                p = re.sub(r"[^A-Z0-9]", "", (q.get("q") or [""])[0].upper())[:10]
                img = (q.get("image") or [""])[0]
                m = WL.match([p]) if p else []
                return self._send(200, dict(plate=p, fits=fits(p), like=store.like(dict(plate=p, image=img, alt=None)) if p else [],
                                            recent=store.recent(p, img) if p else None, vehicle=store.vehicle(p) if p else None,
                                            watch=dict(plate=m[0][0]["plate"], nickname=m[0][0]["nickname"], level=m[0][1]) if m else None))
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
                if f == "value":               # the value queue spans days and drops reviewed plates: use the latest label you saved
                    last = store.last_labeled() or last
                if pos is not None:
                    it_ = its[pos]
                    lb_ = it_["label"]
                    reads = ([lb_["truth"]] if lb_ and lb_.get("truth") else []) + [it_["plate"]] + ([it_["alt"]["plate"]] if it_["alt"] else [])
                    m_ = WL.match(reads)
                    it_["like"] = store.like(it_)
                    it_["gold"] = store.gold_for(it_)
                    gm_ = store.gold_map()
                    it_["is_gold"] = bool(gm_.get(((lb_ or {}).get("truth") or it_["plate"]).upper(), {}).get("image") == it_["image"])
                    it_["vehicle"] = store.vehicle(((it_["label"] or {}).get("truth") or it_["plate"]).upper(), (it_["label"] or {}).get("state"))
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
            if path == "/api/gold":
                try:
                    d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    return self._send(200, store.set_gold(str(d["image"]), bool(d.get("on", True)), bool(d.get("verify"))) | {"ok": True})
                except (KeyError, ValueError, TypeError) as e:
                    return self._send(400, {"error": str(e) or "bad gold request"})
            if path == "/api/ops/score":
                if _OPS.get("score") and _OPS["score"].poll() is None:
                    return self._send(200, {"ok": True, "running": True})
                _OPS["score"] = subprocess.Popen([sys.executable, "-m", "lpr_dav_ocr.score", "--root", str(store.root)],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return self._send(200, {"ok": True, "running": False})
            if path == "/api/vehicle/flag":
                try:
                    d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    plate = re.sub(r"[^A-Z0-9]", "", str(d["plate"]).upper())[:10]
                    assert len(plate) >= 2
                    return self._send(200, store.set_vehicle_stale(plate, bool(d.get("stale", True)), d.get("image"), bool(d.get("match"))) | {"ok": True})
                except (KeyError, ValueError, TypeError, AssertionError):
                    return self._send(400, {"error": "bad vehicle flag"})
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
            plate_t = (truth or it["plate"]).upper() if verdict in ("ok", "fix") else None
            applied = 0
            if plate_t and state != "UNKN":              # a state you set on one sighting is the plate's state
                applied = sum(1 for t, b, i in store.reads(True) if t == plate_t and i["image"] != it["image"] and not store.eff_state(i, t))
            store.add_label(dict(ts=datetime.now().astimezone().isoformat(timespec="seconds"), image=it["image"],
                                 clip=it["clip"], day=it["day"], plate_ocr=it["plate"], conf=it["conf"],
                                 plate_second=(it["alt"] or {}).get("plate"), shape=it["shape"],
                                 verdict=verdict, truth=truth, state=state,
                                 **({"rechecked": True} if d.get("recheck") and verdict == "notplate" else {})))
            if plate_t and state != "UNKN":
                store.set_plate_state(plate_t, state, it["image"])
            if truth:
                store.save_crop(it, truth)
            self._send(200, {"ok": True, "plate": plate_t, "state": state, "applied": applied})
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
