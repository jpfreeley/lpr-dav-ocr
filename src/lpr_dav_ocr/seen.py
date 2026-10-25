"""Every time one plate was seen, from the command line.

  lpr seen ABC1234 [--root DIR] [--days N] [--unverified]

Exact match on plates you confirmed or corrected, or that passed the auto-accept rule. --unverified also
includes reads nobody has reviewed. --days N limits it to the last N days back from the newest sighting's day.
"""
import argparse
from datetime import date, timedelta
from pathlib import Path

from . import config as _config
from .review import server as S


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lpr seen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plate")
    ap.add_argument("--root", help="folder holding frames/ labels/ crops/ (default: the configured data root)")
    ap.add_argument("--days", type=int, default=0, help="only the last N days (0 = all)")
    ap.add_argument("--unverified", action="store_true", help="include reads nobody has reviewed")
    a = ap.parse_args(argv)
    root = Path(a.root).expanduser() if a.root else _config.data_root(S.CFG)
    r = S.Store(root).sightings(a.plate, a.unverified)
    rows = r["sightings"]
    if a.days and rows:
        newest = date.fromisoformat(rows[0]["day"])
        cut = (newest - timedelta(days=a.days)).isoformat()
        rows = [x for x in rows if x["day"] >= cut]
    if not rows:
        print(f"no {'' if a.unverified else 'confirmed '}sightings of {r['plate']}")
        return
    print(f"{r['plate']}: {len(rows)} sighting(s), {len({x['day'] for x in rows})} day(s)")
    for x in rows:
        print(f"  {x['day']} {x['time']}  {x['basis']:<10} conf {x['conf']:.2f}  {x['state'] or ''}")


if __name__ == "__main__":
    main()
