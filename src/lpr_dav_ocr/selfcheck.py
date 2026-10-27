"""Self-check: say something only when something needs attention.

  lpr selfcheck [--rules] [--health] [--dry-run] [--root DIR]

Run it from a scheduler about once an hour (scripts/install-selfcheck.sh). With no option it does what is due:

  health   every run: the watcher is quiet, errors in the log, clips waiting, little disk space, a job is late,
           the machine is overloaded (the same list as the "needs attention" strip on the Operations page)
  rules    about weekly: re-measures how often each automatic rule is right on your own labels (from
           reports/accuracy.md, which `lpr score` writes) and compares it with a floor

Nothing is sent when everything is fine. A problem is sent once, again only after 24 hours (health) or 7 days (rules)
while it lasts, and a problem that clears is forgotten, so a later return alerts again. Alerts go through ntfy like
watchlist alerts and carry no plate text. The check changes no setting: it only tells you.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    from . import ops as OPS
    from . import watchlist as W
except ImportError:                      # the standalone copy in the skill folder
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ops as OPS
    import watchlist as W

# How right each rule must be on the labels you made. Auto-reject is held higher because its mistake is silent:
# a real plate is hidden from review.
FLOORS = {"accept": 0.95, "record": 0.95, "approve": 0.95, "reject": 0.97}
MIN_LABELS = 50                          # below this many labeled examples a percentage means little
REPEAT = {"health": 24 * 3600, "rules": 7 * 24 * 3600}
RULES_EVERY = 7 * 24 * 3600 - 3600


def rule_precision(root):
    """[{key, name, right, total}] parsed from reports/accuracy.md, or [] when there is no report."""
    p = Path(root) / "reports" / "accuracy.md"
    try:
        text = p.read_text()
    except OSError:
        return []
    out = []
    m = re.search(r"Among the \d+ you labeled that match the rule: real plate right (\d+)/(\d+)", text)
    if m:
        out.append(dict(key="accept", name="Auto-accept", right=int(m[1]), total=int(m[2])))
    m = re.search(r"you labeled that match the ticket-record rule: real plate right (\d+)/(\d+)", text)
    if m:
        out.append(dict(key="record", name="Auto-accept with a ticket record", right=int(m[1]), total=int(m[2])))
    m = re.search(r"Read equals a plate you verified: \d+\. Right (\d+)/(\d+)", text)
    if m:
        out.append(dict(key="approve", name="Auto-approve of verified plates", right=int(m[1]), total=int(m[2])))
    m = re.search(r"Labeled reads above the threshold: (\d+)\. Not a plate \d+, obstructed or can't tell \d+, \*\*real plates (\d+)\*\*", text)
    if m:
        out.append(dict(key="reject", name="Auto-reject", right=int(m[1]) - int(m[2]), total=int(m[1])))
    return out


def rule_findings(root):
    out = []
    for r in rule_precision(root):
        if r["total"] < MIN_LABELS:
            continue
        share = r["right"] / r["total"]
        floor = FLOORS[r["key"]]
        if share < floor:
            tail = ("real plates are being hidden from review" if r["key"] == "reject"
                    else "more reads than expected are wrong without a human check")
            out.append(dict(key="rule-" + r["key"], level="warning",
                            text=f"{r['name']} is right {share * 100:.1f}% of the time on your labels ({r['right']} of {r['total']}), "
                                 f"below {floor * 100:.0f}%: {tail}. See the Operations page, accuracy panel."))
    return out


def health_findings(home, root, cam_dir=None, now=None):
    """The Operations page's needs-attention list, built without the review store."""
    now = now or time.time()
    pr = OPS.procs()
    data = dict(watcher=OPS.watcher(home, now), jobs=OPS.jobs(home, root, pr, now), storage=OPS.storage(home, root), machine=OPS.machine())
    if cam_dir:
        data["lag"] = OPS.lag(cam_dir, Path(root) / "frames" if (Path(root) / "frames").is_dir() else root)
    return OPS.attention(data)


def _state(home):
    try:
        return json.loads((Path(home) / "selfcheck_state.json").read_text())
    except (OSError, ValueError):
        return {}


def _save(home, st):
    (Path(home) / "selfcheck_state.json").write_text(json.dumps(st, indent=1))


def due_rules(home, now):
    return now - _state(home).get("_rules_run", 0) > RULES_EVERY


def run(home, root, cam_dir=None, rules=None, health=True, send=True, now=None):
    """Run the checks that are due. Returns the findings that were new or due again (and sent, unless send is False)."""
    now = now or time.time()
    st = _state(home)
    if rules is None:
        rules = due_rules(home, now)
    found = health_findings(home, root, cam_dir, now) if health else []
    kinds = {f["key"]: "health" for f in found}
    if rules:
        for f in rule_findings(root):
            found.append(f)
            kinds[f["key"]] = "rules"
        st["_rules_run"] = now
    active = {f["key"] for f in found}
    # a problem that cleared is forgotten, so it alerts again if it comes back
    ran = (["health"] if health else []) + (["rules"] if rules else [])
    for k in [k for k in st if not k.startswith("_") and k not in active and kinds_of(k) in ran]:
        del st[k]                         # a problem that cleared
    fresh = [f for f in found if now - st.get(f["key"], 0) > REPEAT[kinds[f["key"]]]]
    if not send:
        return found                      # a dry run lists every active problem
    if fresh and send:
        worst = "critical" if any(f["level"] == "critical" for f in fresh) else "warning"
        W.notify("LPR needs attention", "\n".join("- " + f["text"] for f in fresh), priority=4 if worst == "critical" else 3, tags="warning")
        for f in fresh:
            st[f["key"]] = now
    if send:
        _save(home, st)
    return fresh


def kinds_of(key):
    return "rules" if key.startswith("rule-") else "health"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lpr selfcheck", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="data root (default: the configured one)")
    ap.add_argument("--rules", action="store_true", help="check the automatic rules now, even if not due")
    ap.add_argument("--health", action="store_true", help="only the health check")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; send nothing and change no state")
    a = ap.parse_args(argv)
    try:
        from . import config as _config
        cfg = _config.load()
        home = _config.home(cfg)
        root = Path(a.root).expanduser() if a.root else _config.data_root(cfg)
        cam = Path(cfg["paths"]["camera_dir"]).expanduser() if cfg["paths"].get("camera_dir") else None
    except ImportError:
        home, root = W.HOME, (Path(a.root).expanduser() if a.root else Path.home() / "lpr_plates")
        cam = root.parent / root.name.removeprefix("db_") if root.name.startswith("db_") else None
    fresh = run(home, root, cam, rules=True if a.rules else (False if a.health else None), send=not a.dry_run)
    if a.dry_run:
        print("\n".join(f"{f['level']}: {f['text']}" for f in fresh) or "nothing needs attention")
    elif fresh:
        print(f"sent {len(fresh)} alert(s)")


if __name__ == "__main__":
    main()
