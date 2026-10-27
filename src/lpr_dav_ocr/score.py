"""Score the OCR passes against your review labels and build the character confusion table.

  lpr score [--root DIR]

Truth = your label: "ok" means the first-pass read was right, "fix" means your typed correction.
Only those two verdicts have a known plate; "not a plate", "obstructed" and "can't tell" are
reported separately. Caveat: every image here is one where the pipeline found a plate-shaped read,
so this measures how often reads are right (precision), not how many real plates were missed.
Writes <root>/reports/accuracy.md and <root>/reports/confusions.csv.
"""
import argparse, collections, csv, json, re
from datetime import date
from pathlib import Path

from . import config as _config
from . import vehicles as _veh
from .util import read_csv, read_jsonl

CFG = _config.load()
AUTO_CONF = CFG["review"]["auto_accept_conf"]   # same setting the review page uses
RECORD_CONF = CFG["review"]["record_accept_conf"]



def _open_vehicles():
    """The local ticket index, or None when it is not built."""
    try:
        return _veh.open_db()
    except Exception:
        return None

def _hour(image):
    """Hour of day from the file name (<date>_HH.MM.SS-...), or -1 when the name has none."""
    m = re.search(r"_(\d\d)\.", image)
    return int(m[1]) if m else -1


def align(a, b):
    """Levenshtein alignment of OCR read a to truth b. Yields (op, ocr_char, true_char)."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    ops, i, j = [], n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            ops.append(("match" if a[i - 1] == b[j - 1] else "sub", a[i - 1], b[j - 1])); i -= 1; j -= 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(("extra", a[i - 1], "")); i -= 1       # OCR added a character
        else:
            ops.append(("missed", "", b[j - 1])); j -= 1      # OCR dropped a character
    return ops[::-1]


def pct(n, d):
    return f"{n}/{d} = {n / d:.1%}" if d else "n/a"


def one_off(a, b):
    """Exactly one substituted, inserted or deleted character apart."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(_config.data_root(CFG)))
    root = Path(ap.parse_args(argv).root)
    base = root / "frames" if (root / "frames").is_dir() else root

    labels_file = root / "labels" / "labels.jsonl"
    if not labels_file.is_file():
        print(f"No labels yet at {labels_file}.\nReview some plates first (lpr review), then run this again.")
        return 1
    labels = {}
    for r in read_jsonl(root / "labels" / "labels.jsonl"):
        labels[r["image"]] = r                           # latest line per image wins

    dp = root / "labels" / "duplicates.jsonl"
    dups = {json.loads(l)["image"] for l in dp.open() if l.strip()} if dp.is_file() else set()
    rows, auto_all, items_all = [], 0, 0
    for day in sorted(p for p in base.iterdir() if (p / "index.csv").exists()):
        sp = {r["image"]: r for r in read_csv(day / "second_pass.csv")} if (day / "second_pass.csv").exists() else {}
        for r in read_csv(day / "index.csv"):
            if r.get("image") in dups:
                continue                                  # merged into another frame by `dedupe`
            lb = labels.get(r.get("image"))
            s = sp.get(r.get("image"), {})
            if r.get("plate"):
                items_all += 1
                auto_all += float(r["conf"]) >= AUTO_CONF and bool(s.get("plate2")) and s.get("plate2") == r["plate"] and s.get("shape") == "ok"
            if not (r.get("plate") and lb):
                continue
            rows.append(dict(image=r["image"], day=day.name, verdict=lb["verdict"], truth=lb.get("truth"), state=lb.get("state"),
                             p1=r["plate"], c1=float(r["conf"]), p3=s.get("plate3") or "", p2=s.get("plate2") or "", c2=float(s["conf2"]) if s.get("conf2") else 0.0,
                             shape=s.get("shape") or "", aspect=s.get("aspect") or "", elsewhere=s.get("elsewhere") or "",
                             hour=_hour(r["image"])))
    known = [r for r in rows if r["verdict"] in ("ok", "fix") and r["truth"]]
    out = []
    P = out.append

    P(f"# OCR accuracy vs your labels\n\n{len(rows)} labeled images across {len({r['day'] for r in rows})} day(s); {len(known)} have a known plate (verdict ok or fix).\n")
    c = collections.Counter(r["verdict"] for r in rows)
    P("Verdicts: " + ", ".join(f"{k} {v}" for k, v in c.most_common()) + "\n")

    # 1. passes
    P("## Exact-match accuracy (whole plate right)\n")
    a1 = sum(r["p1"] == r["truth"] for r in known)
    withs = [r for r in known if r["p2"]]
    a1s = sum(r["p1"] == r["truth"] for r in withs)
    a2 = sum(r["p2"] == r["truth"] for r in withs)
    either = sum(r["p1"] == r["truth"] or r["p2"] == r["truth"] for r in withs)
    hi = sum((r["p1"] if r["c1"] >= r["c2"] else r["p2"]) == r["truth"] for r in withs)
    agree = [r for r in withs if r["p1"] == r["p2"]]
    dis = [r for r in withs if r["p1"] != r["p2"]]
    P(f"- First pass (rotated 20 deg, 640 px), all known: {pct(a1, len(known))}")
    P(f"- On the {len(withs)} that also have a second read:")
    P(f"  - first pass: {pct(a1s, len(withs))}")
    P(f"  - second pass (no rotation, 960 px): {pct(a2, len(withs))}")
    P(f"  - take the higher-confidence read: {pct(hi, len(withs))}")
    P(f"  - best case, either pass right (upper bound): {pct(either, len(withs))}")
    P(f"- When the passes AGREE ({len(agree)}): right {pct(sum(r['p1'] == r['truth'] for r in agree), len(agree))}")
    d3 = [r for r in dis if r["p3"]]
    if d3:
        tb = lambda r: r["p3"] if r["p3"] in (r["p1"], r["p2"]) else (r["p1"] if r["c1"] >= r["c2"] else r["p2"])
        P(f"- Crop read as tie-breaker, on the {len(d3)} disagreements that have one: higher-confidence {pct(sum((r['p1'] if r['c1'] >= r['c2'] else r['p2']) == r['truth'] for r in d3), len(d3))}, with crop tie-break {pct(sum(tb(r) == r['truth'] for r in d3), len(d3))}")
    P(f"- When they DISAGREE ({len(dis)}): first right {pct(sum(r['p1'] == r['truth'] for r in dis), len(dis))}, second right {pct(sum(r['p2'] == r['truth'] for r in dis), len(dis))}, neither {pct(sum(r['p1'] != r['truth'] and r['p2'] != r['truth'] for r in dis), len(dis))}\n")

    # 2. calibration
    P("## Does confidence mean anything? (first pass)\n")
    for lo, hi_ in ((0, .9), (.9, .97), (.97, .99), (.99, 1.01)):
        b = [r for r in known if lo <= r["c1"] < hi_]
        P(f"- conf {lo:.2f} to {min(hi_, 1):.2f}: {pct(sum(r['p1'] == r['truth'] for r in b), len(b))}")
    P("")

    # 3. day/night
    P("## Night vs day (first pass)\n")
    for name, f in (("night 00-06", lambda h: 0 <= h < 7), ("day 07+", lambda h: h >= 7)):
        b = [r for r in known if f(r["hour"])]
        P(f"- {name}: {pct(sum(r['p1'] == r['truth'] for r in b), len(b))}")
    P("")

    # 4. confusion table
    conf, pos = collections.Counter(), collections.Counter()
    cer_num = cer_den = 0
    for r in known:
        ops = align(r["p1"], r["truth"])
        cer_den += len(r["truth"])
        for k, (op, o, t) in enumerate(ops):
            if op != "match":
                conf[(op, o, t)] += 1
                cer_num += 1
                where = "first" if k == 0 else "last" if k == len(ops) - 1 else "middle"
                pos[where] += 1
    P("## Character errors (first pass)\n")
    P(f"- Character error rate: {cer_num}/{cer_den} = {cer_num / cer_den:.1%}")
    P("- Where errors fall: " + ", ".join(f"{k} {v}" for k, v in pos.most_common()) + "\n")
    P("Most common (OCR read -> truth):\n")
    for (op, o, t), n in conf.most_common(20):
        label = f"{o} -> {t}" if op == "sub" else f"extra {o!r} (OCR added it)" if op == "extra" else f"missed {t!r} (OCR dropped it)"
        P(f"- {label}: {n}")
    P("")

    # 5. non-plates, obstructed, shape
    P("## Non-plates and the shape hint\n")
    nonp = [r for r in rows if r["verdict"] == "notplate"]
    P(f"- Not a plate: {len(nonp)} of {len(rows)} labeled ({len(nonp) / len(rows):.1%}); obstructed {c['obstructed']}; can't tell {c['skip']}")
    sh = [r for r in rows if r["shape"]]
    if sh:
        flagged = [r for r in sh if r["shape"] != "ok"]
        P(f"- Among the {len(sh)} with a measured shape: shape not ok flags {sum(r['verdict'] == 'notplate' for r in flagged)} of {sum(r['verdict'] == 'notplate' for r in sh)} non-plates, "
          f"and wrongly flags {sum(r['verdict'] in ('ok', 'fix') for r in flagged)} of {sum(r['verdict'] in ('ok', 'fix') for r in sh)} real plates")
        asp = lambda rs: sorted(float(r["aspect"]) for r in rs if r["aspect"])
        for name, rs in (("real plates", [r for r in sh if r["verdict"] in ("ok", "fix")]), ("not a plate", [r for r in sh if r["verdict"] == "notplate"])):
            a = asp(rs)
            if a:
                P(f"- Aspect of {name} (n={len(a)}): 10th/50th/90th percentile = {a[len(a) // 10]:.2f} / {a[len(a) // 2]:.2f} / {a[-1 - len(a) // 10]:.2f}")
    au = [r for r in rows if r["c1"] >= AUTO_CONF and r["p2"] == r["p1"] and r["shape"] == "ok"]
    auk = [r for r in au if r["verdict"] in ("ok", "fix")]
    P("\n## Auto-accept rule (conf >= %.2f, both passes agree, shape ok)\n" % AUTO_CONF)
    P(f"- Covers {auto_all} of {items_all} plates found so far ({auto_all / max(items_all, 1):.0%}), including ones nobody has reviewed")
    P(f"- Among the {len(au)} you labeled that match the rule: real plate right {pct(sum(r['p1'] == r['truth'] for r in auk), len(auk))}; "
      f"{len(au) - len(auk)} were not plates or unreadable ({', '.join(f'{v} {k}' for k, v in collections.Counter(r['verdict'] for r in au if r['verdict'] not in ('ok', 'fix')).most_common())})\n")
    # 5b. the ticket-record rule: confidence below auto-accept, both passes agree, shape ok, and the plate has a vehicle in the ticket index
    db = _open_vehicles()
    if RECORD_CONF and db is not None:
        rr = [r for r in rows if RECORD_CONF <= r["c1"] < AUTO_CONF and r["p2"] == r["p1"] and r["shape"] == "ok" and _veh.lookup(db, r["p1"], None)]
        rrk = [r for r in rr if r["verdict"] in ("ok", "fix")]
        P("\n## Ticket-record rule (conf %.2f to %.2f, both passes agree, shape ok, the plate has a vehicle in the ticket data)\n" % (RECORD_CONF, AUTO_CONF))
        P(f"- Among the {len(rr)} you labeled that match the ticket-record rule: real plate right {pct(sum(r['p1'] == r['truth'] for r in rrk), len(rrk))}; "
          f"{len(rr) - len(rrk)} were not plates or unreadable\n")
    # 6. recently verified plates: how good is "equals a plate you verified within N days"?
    days = int(_config.load()["review"]["recent_days"])
    ver = [(r["image"], r["truth"], date.fromisoformat(r["image"][:10])) for r in rows if r["verdict"] in ("ok", "fix") and r["truth"] and re.match(r"\d{4}-\d\d-\d\d_", r["image"])]
    ex, nr = [], []
    for r in rows:
        if days <= 0 or not re.match(r"\d{4}-\d\d-\d\d_", r["image"]):
            continue
        d0 = date.fromisoformat(r["image"][:10])
        pool = {t for i, t, d in ver if i != r["image"] and abs((d - d0).days) <= days}
        if r["p1"] in pool:
            ex.append(r)
        elif len(r["p1"]) >= 5:
            near = [t for t in pool if len(t) >= 5 and one_off(r["p1"], t)]
            if near:
                nr.append((r, near))
    P("\n## Recently verified plates (last %d days either side)\n" % days)
    P(f"- Read equals a plate you verified: {len(ex)}. Right {pct(sum(r['verdict'] == 'ok' for r in ex), len(ex))}; "
      f"{sum(r['verdict'] == 'fix' for r in ex)} needed a fix, {sum(r['verdict'] not in ('ok', 'fix') for r in ex)} were not plates or unreadable")
    P(f"- Read is one character from a verified plate: {len(nr)}. That plate was the truth {pct(sum(r['truth'] in n for r, n in nr), len(nr))}; "
      f"the read was already right {sum(r['verdict'] == 'ok' for r, n in nr)} times")

    # 7. the auto-reject rule: how many reads wider than reject_aspect were really not plates?
    thr = float(_config.load()["review"]["reject_aspect"])
    if thr:
        wide = [r for r in rows if r["aspect"] and float(r["aspect"]) > thr and not r["elsewhere"]]      # a plate-shaped read elsewhere keeps the frame in review
        P("\n## Auto-reject rule (aspect above %.1f)\n" % thr)
        P(f"- Labeled reads above the threshold: {len(wide)}. Not a plate {sum(r['verdict'] == 'notplate' for r in wide)}, "
          f"obstructed or can't tell {sum(r['verdict'] in ('obstructed', 'skip') for r in wide)}, "
          f"**real plates {sum(r['verdict'] in ('ok', 'fix') for r in wide)}** (these would have been hidden from review)")
        P(f"- Share of all your 'not a plate' labels it catches: {pct(sum(r['verdict'] == 'notplate' for r in wide), sum(r['verdict'] == 'notplate' for r in rows))}")

    sc = collections.Counter(r["state"] for r in rows if r["state"] and r["state"] != "UNKN")
    if sc:
        P("- State labels: " + ", ".join(f"{k} {v}" for k, v in sc.most_common()))

    rep = root / "reports"
    rep.mkdir(exist_ok=True)
    (rep / "accuracy.md").write_text("\n".join(out) + "\n")
    with (rep / "confusions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "ocr_char", "true_char", "count"])
        for (op, o, t), n in conf.most_common():
            w.writerow([op, o, t, n])
    print("\n".join(out))
    print(f"\nwritten: {rep / 'accuracy.md'}, {rep / 'confusions.csv'}")


if __name__ == "__main__":
    main()
