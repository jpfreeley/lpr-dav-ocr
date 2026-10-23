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
    return "TLC" if TLC_RX.fullmatch(p) else "standard" if STD_RX.fullmatch(p) else "other"


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
        states = collections.Counter((i["label"] or {}).get("state") for i in its if i["label"])
        states.pop("UNKN", None)
        states.pop(None, None)
        last = max((i["day"], i["time"]) for i in its)
        regulars.append(dict(plate=t, days=len(dset), sightings=len(its), win_from=win[0], win_to=win[1], steady=steady,
                             last=f"{last[0]} {last[1]}", state=states.most_common(1)[0][0] if states else None, type=plate_type(t)))
    regulars.sort(key=lambda r: (-r["days"], -r["sightings"], r["plate"]))

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
        s = (i["label"] or {}).get("state") if i["label"] else None
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
        mix=dict(types=[dict(day=d, **{k: types_by_day[d].get(k, 0) for k in ("standard", "TLC", "other")}) for d in sel],
                 states=[dict(state=s, count=n) for s, n in state_counts.most_common(7)], state_total=state_total),
        watch=w_rows, quality=quality,
        accuracy=[dict(hour=h, right=r, total=n) for h, (r, n) in enumerate(acc)],
    )
