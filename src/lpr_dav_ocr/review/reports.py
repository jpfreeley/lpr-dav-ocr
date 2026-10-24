"""Data for the Reports page: traffic, repeat visitors, plate mix, watchlist sightings and review effort.

Pure functions over a review `Store` (see server.py). Nothing here reads files or serves HTTP, so it is easy to test.
Sightings are the confirmed ones used by the history page: plates you verified or fixed and auto-accepted ones.
Plates you marked not a plate, obstructed or can't tell, and auto-rejected reads, are never counted.
Times come from the clip file names, which is the camera's own clock (CamTime).
"""
import collections
import datetime
import re

TLC_RX = re.compile(r"[TY]\d{6}[A-Z]")
STD_RX = re.compile(r"[A-Z]{3}\d{4}")
NJ_RX = re.compile(r"[A-Z]\d{2}[A-Z]{3}")
COM_RX = re.compile(r"\d{5}[A-Z]{2}")
MOP_RX = re.compile(r"\d{3}[A-Z]{2}\d")
GAP_BINS = [(2, "under 2 min"), (10, "2 to 10 min"), (30, "10 to 30 min"), (60, "30 to 60 min"), (180, "1 to 3 h"), (10 ** 9, "over 3 h")]
MAX_REGULARS = 200


def minutes(t):
    """'HH:MM:SS' -> minutes since midnight (None when the time is missing)."""
    try:
        h, m, _ = t.split(":")
        return int(h) * 60 + int(m)
    except (AttributeError, ValueError):
        return None


def plate_type(p):
    return ("TLC" if TLC_RX.fullmatch(p) else "standard" if STD_RX.fullmatch(p) else "NJ" if NJ_RX.fullmatch(p)
            else "commercial" if COM_RX.fullmatch(p) else "moped" if MOP_RX.fullmatch(p) else "other")


KINDS = ["SUV or wagon", "Sedan or coupe", "Van, truck or pickup", "Taxi (TLC plate)", "Other"]


def vehicle_kind(plate, v):
    """A coarse vehicle kind from the ticket record's body type. A taxi is told by its plate pattern, because ticket
    writers record taxis under many different body types."""
    if TLC_RX.fullmatch(plate):
        return KINDS[3]
    b = ((v or {}).get("body") or "").lower()
    if any(x in b for x in ("suv", "wagon", "sport utility")):
        return KINDS[0]
    if any(x in b for x in ("sedan", "coupe", "hatchback", "convertible")):
        return KINDS[1]
    if any(x in b for x in ("van", "truck", "pickup", "delivery", "utility", "flatbed", "tractor", "bus")):
        return KINDS[2]
    return KINDS[4]


def vehicle_section(store, by_plate, ndays_by_plate, S):
    """Fleet mix, vehicle kind by hour, regulars versus one-time visitors, and how many plates have a ticket record."""
    probe = store.vehicle("ZZZ0000")
    if not probe.get("available"):
        return dict(available=False)
    rec, stale = {}, 0
    for p in by_plate:
        v = store.vehicle(p, store.state_of(p))
        if v.get("available") and not v.get("none"):
            rec[p] = v
            stale += bool(v.get("stale"))
    top = lambda key, n: collections.Counter(v[key] for v in rec.values() if v.get(key)).most_common(n)
    makes = collections.Counter(v["make"] for v in rec.values() if v.get("make"))
    other_makes = sum(n for _, n in makes.most_common()[10:])
    ages = collections.Counter((v["year"] // 5) * 5 for v in rec.values() if v.get("year"))
    kind_of = {p: vehicle_kind(p, v) for p, v in rec.items()}
    hours = {k: [0] * 24 for k in KINDS}
    for t, b, i in S:
        if t in kind_of:
            m = minutes(i["time"])
            if m is not None:
                hours[kind_of[t]][m // 60] += 1
    groups = {"Regulars (3+ days)": [p for p in rec if ndays_by_plate.get(p, 0) >= 3],
              "Seen on 2 days": [p for p in rec if ndays_by_plate.get(p, 0) == 2],
              "Seen on one day": [p for p in rec if ndays_by_plate.get(p, 0) == 1]}
    by_group = [dict(group=g, total=len(ps), kinds={k: sum(1 for p in ps if kind_of[p] == k) for k in KINDS}) for g, ps in groups.items()]
    cov = collections.defaultdict(lambda: [0, 0])
    for p in by_plate:
        t = plate_type(p)
        cov[t][1] += 1
        cov[t][0] += p in rec
    cover = [dict(type=t, matched=m, total=n) for t, (m, n) in sorted(cov.items(), key=lambda x: -x[1][1])]
    return dict(available=True, plates=len(by_plate), matched=len(rec), stale=stale,
                makes=[dict(name=k, count=n) for k, n in makes.most_common(10)] + ([dict(name="Other makes", count=other_makes)] if other_makes else []),
                bodies=[dict(name=k, count=n) for k, n in top("body", 8)], colors=[dict(name=k, count=n) for k, n in top("color", 8)],
                ages=[dict(band=f"{b}-{b + 4}", count=ages[b]) for b in sorted(ages) if b >= 1980], kinds=KINDS,
                kind_hours=[dict(kind=k, hours=hours[k]) for k in KINDS], by_group=by_group, coverage=cover)


def build(store, days=None, unverified=False, watch=()):
    allreads = [(t, b, i) for t, b, i in store.reads(unverified)]
    all_days = store._day_names()
    sel = [d for d in all_days if not days or d in days]
    S = [(t, b, i) for t, b, i in allreads if i["day"] in sel]

    # -- first time each plate was seen anywhere in the data (so "returning" does not depend on the day filter)
    first_key = {}
    for t, b, i in allreads:
        k = (i["day"], i["time"])
        if t not in first_key or k < first_key[t]:
            first_key[t] = k

    by_plate = collections.defaultdict(list)
    for t, b, i in S:
        by_plate[t].append(i)
    day_plates = {d: {t for t, b, i in S if i["day"] == d} for d in sel}

    # -- per day: sightings, new vs returning
    per_day = []
    for d in sel:
        rows = [(t, i) for t, b, i in S if i["day"] == d]
        new = sum(1 for t, i in rows if first_key[t] == (i["day"], i["time"]))
        per_day.append(dict(day=d, dow=datetime.date.fromisoformat(d).strftime("%a"), sightings=len(rows),
                            plates=len({t for t, i in rows}), new=new, returning=len(rows) - new))

    # -- hour x day heatmap (camera clock)
    heat = {d: [0] * 24 for d in sel}
    for t, b, i in S:
        m = minutes(i["time"])
        if m is not None:
            heat[i["day"]][m // 60] += 1
    hour_avg = [round(sum(heat[d][h] for d in sel) / max(len(sel), 1), 1) for h in range(24)]

    # -- how many different days each plate was seen
    ndays = {t: len({i["day"] for i in its}) for t, its in by_plate.items()}
    cap = max(len(sel), 1)
    seen_hist = collections.Counter(min(n, cap) for n in ndays.values())

    # -- regulars: plates seen on 2+ days, with their usual arrival window
    regulars = []
    commute = [0] * 48
    for t, its in by_plate.items():
        dset = {i["day"] for i in its}
        if len(dset) < 2:
            continue
        firsts = []
        for d in sorted(dset):
            ms = [minutes(i["time"]) for i in its if i["day"] == d and minutes(i["time"]) is not None]
            if ms:
                firsts.append(min(ms))
        win = (min(firsts), max(firsts)) if firsts else (None, None)
        steady = len(dset) >= 3 and win[0] is not None and win[1] - win[0] <= 90
        if len(dset) >= 3:
            for f in firsts:
                commute[f // 30] += 1
        states = collections.Counter(store.eff_state(i, t) for i in its)
        states.pop("UNKN", None)
        states.pop(None, None)
        last = max((i["day"], i["time"]) for i in its)
        regulars.append(dict(plate=t, days=len(dset), sightings=len(its), win_from=win[0], win_to=win[1], steady=steady,
                             vehicle=None,
                             last=f"{last[0]} {last[1]}", state=states.most_common(1)[0][0] if states else None, type=plate_type(t)))
    regulars.sort(key=lambda r: (-r["days"], -r["sightings"], r["plate"]))
    for r in regulars[:MAX_REGULARS]:
        v = store.vehicle(r["plate"], r["state"])
        r["vehicle"] = v["text"].rsplit(" (", 1)[0] if v.get("available") and not v.get("none") else None

    # -- gaps between repeat sightings on the same day
    gaps = [0] * len(GAP_BINS)
    for t, its in by_plate.items():
        per = collections.defaultdict(list)
        for i in its:
            m = minutes(i["time"])
            if m is not None:
                per[i["day"]].append(m)
        for ms in per.values():
            ms.sort()
            for a, b in zip(ms, ms[1:]):
                g = b - a
                gaps[next(k for k, (hi, _) in enumerate(GAP_BINS) if g < hi)] += 1

    # -- which share of a day's plates came back on another day
    overlap = []
    for a in sel:
        row = []
        for b in sel:
            row.append(round(100 * len(day_plates[a] & day_plates[b]) / max(len(day_plates[a]), 1)))
        overlap.append(row)

    # -- plate mix
    types_by_day = {d: collections.Counter(plate_type(t) for t, b, i in S if i["day"] == d) for d in sel}
    state_counts = collections.Counter()
    for t, b, i in S:
        s = store.eff_state(i, t)
        if s:
            state_counts[s] += 1
    state_total = sum(state_counts.values())

    # -- watched plates (nickname only: the plate text stays out of the chart)
    w_rows = []
    wanted = {w["plate"]: w for w in watch}
    if wanted:
        hits = collections.defaultdict(list)
        for t, b, i in store.reads(True):
            if t in wanted and i["day"] in sel:
                hits[t].append(dict(day=i["day"], time=i["time"], basis=b))
        for p, w in wanted.items():
            w_rows.append(dict(nickname=w.get("nickname") or "(no nickname)", plate=p, sightings=sorted(hits.get(p, []), key=lambda x: (x["day"], x["time"]))))

    # -- review effort and first-pass accuracy by hour
    quality, acc = [], [[0, 0] for _ in range(24)]
    for d in sel:
        c = collections.Counter()
        for i in store.items(d):
            if i["label"]:
                c["labeled"] += 1
                m = minutes(i["time"])
                if i["label"]["verdict"] in ("ok", "fix") and m is not None:
                    acc[m // 60][1] += 1
                    acc[m // 60][0] += i["label"]["verdict"] == "ok"
            elif i["rejected"]:
                c["rejected"] += 1
            elif i["auto"]:
                c["auto_recent" if i["auto_why"] == "recent" else "auto_conf"] += 1
            elif i["ready"]:
                c["left"] += 1
            else:
                c["waiting"] += 1
        quality.append(dict(day=d, **{k: c.get(k, 0) for k in ("labeled", "auto_conf", "auto_recent", "rejected", "left", "waiting")}))
    handled = sum(q["auto_conf"] + q["auto_recent"] + q["rejected"] for q in quality)
    items_total = sum(sum(v for k, v in q.items() if k != "day") for q in quality)

    n_s = len(S)
    returning = sum(p["returning"] for p in per_day)
    return dict(
        days=sel, all_days=all_days, unverified=bool(unverified),
        kpi=dict(sightings=n_s, plates=len(by_plate), days=len(sel),
                 returning_pct=round(100 * returning / n_s) if n_s else 0,
                 one_off_pct=round(100 * sum(1 for n in ndays.values() if n == 1) / len(by_plate)) if by_plate else 0,
                 multi_day=sum(1 for n in ndays.values() if n >= 2), steady=sum(1 for r in regulars if r["steady"]),
                 handled_pct=round(100 * handled / items_total) if items_total else 0, reads=items_total),
        per_day=per_day, heat=dict(days=sel, rows=[heat[d] for d in sel]), hour_avg=hour_avg,
        seen_hist=[seen_hist.get(k, 0) for k in range(1, cap + 1)], cap_days=cap,
        regulars=regulars[:MAX_REGULARS], regulars_total=len(regulars), commute=commute,
        gaps=dict(labels=[n for _, n in GAP_BINS], counts=gaps), overlap=dict(days=sel, rows=overlap),
        mix=dict(types=[dict(day=d, **{k: types_by_day[d].get(k, 0) for k in ("standard", "TLC", "NJ", "commercial", "moped", "other")}) for d in sel],
                 states=[dict(state=s, count=n) for s, n in state_counts.most_common(7)], state_total=state_total),
        watch=w_rows, quality=quality, vehicles=vehicle_section(store, by_plate, ndays, S),
        accuracy=[dict(hour=h, right=r, total=n) for h, (r, n) in enumerate(acc)],
    )
