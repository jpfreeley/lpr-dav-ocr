"""A local index of vehicle make, body type, year and color by plate, built from NYC's public parking-ticket data.

  lpr vehicles import [--fy 2027 --fy 2026 --fy 2025] [--states NY,NJ,PA,CT,MA,FL] [--threads 3]
  lpr vehicles lookup ABC1234 [--state NY]
  lpr vehicles stats

NYC Open Data publishes every parking ticket (dataset "Parking Violations Issued - Fiscal Year ..."). Each row has the
plate, its state, and what the ticket writer recorded: the make, the body type, the year and the color. This command
downloads those rows once, grouped by plate, into a SQLite file under your home folder. After that a lookup is a local
query: nothing about your plates is ever sent anywhere. The download asks the city for aggregate rows by plate prefix and
carries none of your plates.

What it can and cannot tell you:
  - only vehicles that were ticketed in New York City during the years you import, so many plates have no entry
  - make, body type, year and color as a ticket writer typed them: typos and old registrations happen
  - no model and no trim. The city does not record them.
The answer for a plate is the most common record, with how many tickets it rests on. Treat it as a hint.
"""
import argparse
import csv
import io
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOST = "https://data.cityofnewyork.us/resource/"
# fiscal year (July to June, named for the year it ends) -> NYC Open Data dataset id. Look others up with
# https://data.cityofnewyork.us (dataset "Parking Violations Issued - Fiscal Year N").
DATASETS = {2027: "pvqr-7yc4", 2026: "9mwx-gamw", 2025: "m5vz-tzqv", 2024: "8zf9-spf8", 2023: "869v-vr48"}
DEFAULT_STATES = ("NY", "NJ", "PA", "CT", "MA", "FL")
PAGE = 50000
PREFIX_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

BODY = {"SUBN": "SUV or wagon", "4DSD": "4-door sedan", "2DSD": "2-door sedan", "SEDN": "sedan", "PICK": "pickup",
        "VAN": "van", "DELV": "delivery truck", "UTIL": "utility vehicle", "CONV": "convertible", "TAXI": "taxi",
        "TRAC": "tractor", "TRLR": "trailer", "REFG": "refrigerated truck", "FLAT": "flatbed", "BUS": "bus",
        "MCY": "motorcycle", "MOPD": "moped", "LIMO": "limousine", "H/WH": "hearse", "AMBU": "ambulance"}
COLOR = {"WH": "white", "WHITE": "white", "BK": "black", "BLACK": "black", "GY": "gray", "GRAY": "gray", "BL": "blue",
         "BLUE": "blue", "RD": "red", "RED": "red", "GR": "green", "GN": "green", "GREEN": "green", "SL": "silver",
         "SILVE": "silver", "TN": "tan", "BR": "brown", "BRN": "brown", "YW": "yellow", "YELLO": "yellow", "OR": "orange",
         "MR": "maroon", "GL": "gold", "GOLD": "gold", "PR": "purple"}
MAKE = {"TOYOT": "Toyota", "NISSA": "Nissan", "CHEVR": "Chevrolet", "HYUND": "Hyundai", "VOLKS": "Volkswagen",
        "ME/BE": "Mercedes-Benz", "MERCE": "Mercedes-Benz", "INFIN": "Infiniti", "LINCO": "Lincoln", "CADIL": "Cadillac",
        "CHRYS": "Chrysler", "MITSU": "Mitsubishi", "SUBAR": "Subaru", "VOLVO": "Volvo", "MAZDA": "Mazda",
        "ACURA": "Acura", "HONDA": "Honda", "FORD": "Ford", "DODGE": "Dodge", "JEEP": "Jeep", "LEXUS": "Lexus",
        "BMW": "BMW", "AUDI": "Audi", "KIA": "Kia", "TESLA": "Tesla", "BUICK": "Buick", "GMC": "GMC", "RAM": "Ram",
        "LAND": "Land Rover", "PORSC": "Porsche", "MINI": "Mini", "JAGUA": "Jaguar", "FIAT": "Fiat", "GENES": "Genesis",
        "HINO": "Hino", "ISUZU": "Isuzu", "FRHT": "Freightliner", "INTER": "International", "MACK": "Mack",
        "PETER": "Peterbilt", "KENWO": "Kenworth", "SATUR": "Saturn", "PONTI": "Pontiac", "OLDSM": "Oldsmobile",
        "MERCU": "Mercury", "SCION": "Scion", "SUZUK": "Suzuki", "HARLE": "Harley-Davidson", "YAMAH": "Yamaha",
        "KAWAS": "Kawasaki", "VESPA": "Vespa", "RIVIA": "Rivian"}


def default_home():
    try:
        from . import config as _config
        return Path(_config.home(_config.load()))
    except Exception:
        return Path(os.environ.get("LPR_HOME") or Path.home() / ".lpr-ocr")


def db_path(home=None):
    return Path(home or default_home()) / "vehicles.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS v (plate TEXT, state TEXT, make TEXT, body TEXT, year INTEGER, color TEXT, n INTEGER, fy INTEGER);
CREATE TABLE IF NOT EXISTS done (fy INTEGER, prefix TEXT, rows INTEGER, PRIMARY KEY (fy, prefix));
CREATE INDEX IF NOT EXISTS v_plate ON v (plate, state);
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


def _soql(fy, prefix, states, offset=0, timeout=120):
    ds = DATASETS[fy]
    st = ",".join("'" + s + "'" for s in states)
    q = {"$select": "plate_id,registration_state,vehicle_make,vehicle_body_type,vehicle_year,vehicle_color,count(*) as n",
         "$where": f"plate_id like '{prefix}%' and registration_state in({st})",
         "$group": "plate_id,registration_state,vehicle_make,vehicle_body_type,vehicle_year,vehicle_color",
         "$order": "plate_id,registration_state,vehicle_make,vehicle_body_type,vehicle_year,vehicle_color",
         "$limit": str(PAGE), "$offset": str(offset)}
    url = HOST + ds + ".csv?" + urllib.parse.urlencode(q)
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lpr-dav-ocr vehicles import"}), timeout=timeout) as r:
                return list(csv.DictReader(io.TextIOWrapper(r, encoding="utf-8", newline="")))
        except Exception as e:                      # a busy server or a dropped connection: back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"NYC Open Data request failed for {prefix}: {last}")


def _int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _fetch_prefix(fy, prefix, states):
    rows, off = [], 0
    while True:
        page = _soql(fy, prefix, states, off)
        rows += [(r["plate_id"].upper(), r["registration_state"].upper(), (r.get("vehicle_make") or "").upper(),
                  (r.get("vehicle_body_type") or "").upper(), _int(r.get("vehicle_year")), (r.get("vehicle_color") or "").upper(),
                  _int(r.get("n")), fy) for r in page]
        if len(page) < PAGE:
            return rows
        off += PAGE


def import_fy(db, fy, states=DEFAULT_STATES, threads=3, log=print, prefixes=None):
    """Download one fiscal year. Resumable: finished prefixes are skipped. Returns the number of rows stored."""
    if fy not in DATASETS:
        raise SystemExit(f"fiscal year {fy} is not in the table of dataset ids ({sorted(DATASETS)})")
    todo = [a + b for a in PREFIX_CHARS for b in PREFIX_CHARS] if prefixes is None else list(prefixes)
    have = {p for (p,) in db.execute("SELECT prefix FROM done WHERE fy=?", (fy,))}
    todo = [p for p in todo if p not in have]
    lock, total, t0 = threading.Lock(), [0, 0], time.time()

    def work(p):
        rows = _fetch_prefix(fy, p, states)
        with lock:
            db.executemany("INSERT INTO v VALUES (?,?,?,?,?,?,?,?)", rows)
            db.execute("INSERT OR REPLACE INTO done VALUES (?,?,?)", (fy, p, len(rows)))
            db.commit()
            total[0] += len(rows)
            total[1] += 1
            if total[1] % 50 == 0 or total[1] == len(todo):
                log(f"  FY{fy}: {total[1]}/{len(todo)} prefixes, {total[0]:,} rows, {time.time() - t0:.0f}s")
        return len(rows)

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
            list(ex.map(work, todo))
    return db.execute("SELECT COUNT(*) FROM v WHERE fy=?", (fy,)).fetchone()[0]


def lookup(db, plate, state=None):
    """The most likely vehicle for a plate, or None. {make, body, year, color, tickets, years, state, others}."""
    plate = re.sub(r"[^A-Z0-9]", "", (plate or "").upper())
    if not plate or db is None:
        return None
    args = [plate] + ([state.upper()] if state else [])
    rows = db.execute("SELECT state, make, body, year, color, SUM(n), MAX(fy) FROM v WHERE plate=?"
                      + (" AND state=?" if state else "") + " GROUP BY state, make, body, year, color", args).fetchall()
    if not rows:
        return None
    best_state = max({r[0] for r in rows}, key=lambda s: sum(r[5] for r in rows if r[0] == s))
    rows = [r for r in rows if r[0] == best_state]
    tickets = sum(r[5] for r in rows)

    def top(idx, skip=()):
        tally = {}
        for r in rows:
            if r[idx] and r[idx] not in skip:
                tally[r[idx]] = tally.get(r[idx], 0) + r[5]
        return max(tally, key=tally.get) if tally else None

    make, body, color, year = top(1), top(2), top(4), top(3, skip=(0,))
    fys = sorted({r[6] for r in rows})
    return dict(plate=plate, state=best_state, make=MAKE.get(make, (make or "").title()) if make else None, make_code=make,
                body=BODY.get(body, body.lower() if body else None), body_code=body,
                color=COLOR.get(color, color.lower() if color else None), year=year, tickets=tickets,
                last_fy=max(fys), others=max(0, len({(r[1], r[2]) for r in rows}) - 1))


def describe(v):
    """One short line: '2019 Toyota SUV or wagon, white (5 tickets)'."""
    if not v:
        return "no ticket on record"
    bits = " ".join(str(x) for x in (v["year"], v["make"], v["body"]) if x)
    return (bits or "unknown vehicle") + (f", {v['color']}" if v["color"] else "") + f" ({v['tickets']} ticket{'s' if v['tickets'] != 1 else ''})"


def open_db(home=None):
    """A read-only handle on the index, or None when it has not been built."""
    p = db_path(home)
    if not p.is_file():
        return None
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30, check_same_thread=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lpr vehicles", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    im = sub.add_parser("import", help="download ticket data into the local index (one-time, resumable)")
    im.add_argument("--fy", type=int, action="append", help="fiscal year to import; repeat for several (default 2027, 2026, 2025)")
    im.add_argument("--states", default=",".join(DEFAULT_STATES), help="registration states to keep (default %(default)s)")
    im.add_argument("--threads", type=int, default=3, help="parallel requests (default 3; be polite to the city's servers)")
    lk = sub.add_parser("lookup", help="what the index knows about a plate")
    lk.add_argument("plate")
    lk.add_argument("--state")
    sub.add_parser("stats", help="size of the index")
    for p in (im, lk, ap):
        p.add_argument("--home", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    home = getattr(a, "home", None)
    if a.cmd == "import":
        db = connect(db_path(home))
        states = tuple(s.strip().upper() for s in a.states.split(",") if s.strip())
        for fy in a.fy or [2027, 2026, 2025]:
            print(f"FY{fy}: downloading plates in {', '.join(states)} ...", flush=True)
            n = import_fy(db, fy, states, a.threads)
            print(f"FY{fy}: {n:,} rows in the index")
        print(f"done: {db_path(home)} ({db_path(home).stat().st_size / 1e6:.0f} MB)")
    elif a.cmd == "lookup":
        db = open_db(home)
        if db is None:
            sys.exit("no vehicle index yet: run `lpr vehicles import`")
        v = lookup(db, a.plate, a.state)
        print(describe(v) if v else "no ticket on record for that plate in the imported years")
        if v:
            print(f"  state {v['state']}, last seen in fiscal year {v['last_fy']}" + (f", {v['others']} other recorded variant(s)" if v["others"] else ""))
    else:
        db = open_db(home)
        if db is None:
            sys.exit("no vehicle index yet: run `lpr vehicles import`")
        for fy, n, plates in db.execute("SELECT fy, COUNT(*), COUNT(DISTINCT plate || state) FROM v GROUP BY fy ORDER BY fy DESC"):
            print(f"FY{fy}: {n:,} rows, {plates:,} plates")


if __name__ == "__main__":
    main()
