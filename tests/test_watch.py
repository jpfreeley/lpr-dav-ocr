import csv
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from lpr_dav_ocr import watch as WT
from lpr_dav_ocr import watchlist as W


class ClipEndTests(unittest.TestCase):
    def test_normal_clip(self):
        e = WT.clip_end(date(2026, 1, 1), "03.15.11-03.15.30[M][0@0][0].dav")
        self.assertEqual(e, datetime(2026, 1, 1, 3, 15, 30))

    def test_clip_crossing_midnight_ends_next_day(self):
        e = WT.clip_end(date(2026, 1, 1), "23.59.50-00.00.10[M][0@0][0].dav")
        self.assertEqual(e, datetime(2026, 1, 2, 0, 0, 10))

    def test_unparseable_name(self):
        self.assertIsNone(WT.clip_end(date(2026, 1, 1), "notes.txt"))


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (W.HOME, W.WATCHLIST, W.ALERTS, W.notify)
        W.HOME = self.root / "home"
        W.WATCHLIST = W.HOME / "watchlist.csv"
        W.ALERTS = W.HOME / "alerts.jsonl"
        self.sent = []
        W.notify = lambda title, msg, pri=3, tags="": self.sent.append((title, msg, pri))
        self.frames = self.root / "frames"
        self.frames.mkdir()
        with (self.frames / "index.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"])
            w.writerow(["14.22.00-14.22.20[M].dav", "DEM0001", "0.93", 10, "", "2026-01-01_14.22.00-14.22.20-DEM0001_0.93.png", 1, 1, 1, 1])
        with (self.frames / "second_pass.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"])
            w.writerow(["2026-01-01_14.22.00-14.22.20-DEM0001_0.93.png", "DEM0001", "0.93", "DEMO001", "0.97", 0, 1, 1, 2, 2, "2.1", "ok", 1])
        self.clips = {"14.22.00-14.22.20[M].dav"}

    def tearDown(self):
        W.HOME, W.WATCHLIST, W.ALERTS, W.notify = self._old
        self.tmp.cleanup()

    def test_exact_via_second_pass_read_has_nickname_and_plate(self):
        W.add("DEMO001", "Example car")
        n = WT.check_matches(self.frames, self.clips, send=True)
        self.assertEqual(n, 1)
        title, msg, _ = self.sent[0]
        self.assertIn("Example car", title)
        self.assertIn("DEMO001", msg)
        self.assertIn("14:22", msg)

    def test_possible_match_when_only_first_read_is_close(self):
        W.add("DEMQ001", "Other car")           # one character from both reads
        WT.check_matches(self.frames, self.clips, send=True)
        title, msg, _ = self.sent[0]
        self.assertTrue(title.startswith("Possible match"))
        self.assertIn("one character off", msg)

    def test_cooldown_prevents_duplicate_alerts(self):
        W.add("DEMO001", "Example car")
        WT.check_matches(self.frames, self.clips, send=True)
        WT.check_matches(self.frames, self.clips, send=True)
        self.assertEqual(len(self.sent), 1)

    def test_dry_run_sends_nothing(self):
        W.add("DEMO001", "Example car")
        WT.check_matches(self.frames, self.clips, send=False)
        self.assertEqual(self.sent, [])

    def test_failed_send_is_retried_later(self):
        W.add("DEMO001", "Example car")
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("network down")
            self.sent.append(a)
        W.notify = flaky
        WT.check_matches(self.frames, self.clips, send=True)     # fails, must not be logged as sent
        WT.check_matches(self.frames, self.clips, send=True)     # retried
        self.assertEqual(len(self.sent), 1)

    def test_empty_watchlist(self):
        self.assertEqual(WT.check_matches(self.frames, self.clips, send=True), 0)


if __name__ == "__main__":
    unittest.main()
