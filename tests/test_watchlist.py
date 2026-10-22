import tempfile
import unittest
from pathlib import Path

from lpr_dav_ocr import watchlist as W

ENTRIES = [
    dict(plate="DEMO001", nickname="Example car", added=""),
    dict(plate="AB12", nickname="short", added=""),
]


class MatchTests(unittest.TestCase):
    def test_within_one(self):
        self.assertTrue(W.within_one("DEMO001", "DEMO001"))
        self.assertTrue(W.within_one("DEMO001", "DEMO0O1"))      # substitution
        self.assertTrue(W.within_one("DEMO001", "DEMO01"))       # deletion
        self.assertTrue(W.within_one("DEMO001", "DEMO0011"))     # insertion
        self.assertFalse(W.within_one("DEMO001", "DEMO0OO"))     # two substitutions
        self.assertFalse(W.within_one("DEMO001", "DEM"))

    def test_exact_and_possible(self):
        m = W.match(["DEMO001"], ENTRIES)
        self.assertEqual([(e["nickname"], lvl) for e, lvl, _ in m], [("Example car", "exact")])
        m = W.match(["DEM0001"], ENTRIES)
        self.assertEqual([(lvl, read) for _, lvl, read in m], [("possible", "DEM0001")])

    def test_exact_wins_over_possible_when_several_reads(self):
        m = W.match(["DEM0001", "DEMO001"], ENTRIES)
        self.assertEqual(m[0][1], "exact")

    def test_short_plates_never_fuzzy_match(self):
        self.assertEqual(W.match(["AB13"], ENTRIES), [])
        self.assertEqual(W.match(["AB12"], ENTRIES)[0][1], "exact")

    def test_no_match(self):
        self.assertEqual(W.match(["ZZZ9999"], ENTRIES), [])
        self.assertEqual(W.match([], ENTRIES), [])

    def test_norm(self):
        self.assertEqual(W.norm(" demo-001 "), "DEMO001")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = (W.HOME, W.WATCHLIST, W.TOPIC_FILE, W.ALERTS)
        W.HOME = Path(self.tmp.name)
        W.WATCHLIST = W.HOME / "watchlist.csv"
        W.TOPIC_FILE = W.HOME / "ntfy_topic"
        W.ALERTS = W.HOME / "alerts.jsonl"

    def tearDown(self):
        W.HOME, W.WATCHLIST, W.TOPIC_FILE, W.ALERTS = self._old
        self.tmp.cleanup()

    def test_add_list_replace_remove(self):
        W.add("demo 001", "First")
        W.add("DEMO001", "Renamed")            # same plate replaces
        W.add("DEMO002", "<b>x</b>; rm -rf")   # nickname is sanitised
        rows = {r["plate"]: r["nickname"] for r in W.load()}
        self.assertEqual(rows["DEMO001"], "Renamed")
        self.assertNotIn("<", rows["DEMO002"])
        W.remove("DEMO001")
        self.assertEqual([r["plate"] for r in W.load()], ["DEMO002"])

    def test_bad_plate_rejected(self):
        with self.assertRaises(ValueError):
            W.add("!", "x")

    def test_topic_is_random_private_and_stable(self):
        t = W.topic(create=True)
        self.assertTrue(t.startswith("lpr-") and len(t) >= 24)
        self.assertEqual(W.topic(), t)
        self.assertEqual(oct(W.TOPIC_FILE.stat().st_mode & 0o777), "0o600")

    def test_notify_without_topic_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            W.notify("t", "m")

    def test_recent_alerts_window(self):
        W.log_alert(watch="DEMO001", level="exact", clip="c")
        self.assertEqual(len(W.recent_alerts(60)), 1)
        self.assertEqual(len(W.recent_alerts(0)), 0)


if __name__ == "__main__":
    unittest.main()
