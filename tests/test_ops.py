import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from lpr_dav_ocr import ops


class LogTests(unittest.TestCase):
    def test_clock_wrap_steps_back_a_day(self):
        now = datetime(2026, 1, 6, 0, 30, 0)
        ev = ops.log_events(["23:50:00 a", "00:10:00 b"], now)
        self.assertEqual(datetime.fromtimestamp(ev[0][0]).day, 5)
        self.assertEqual(datetime.fromtimestamp(ev[1][0]).day, 6)

    def test_watcher_counts_errors_and_clips(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "logs").mkdir()
            now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
            (home / "logs" / "watch.log").write_text(
                "11:00:00 2026-01-05: 3 new clip(s): a, b\n11:01:00 cycle error: PermissionError(1)\n11:59:30 ALERT sent: Watchlist: X (ABC1234)\n")
            w = ops.watcher(home, now.timestamp())
            self.assertEqual((w["clips_24h"], w["errors_24h"], w["alerts_24h"]), (3, 1, 1))
            self.assertEqual(w["clips_per_hour"][11], 3)
            self.assertEqual(w["idle_s"], 30)


class JobsAndAttentionTests(unittest.TestCase):
    def test_late_job_and_attention(self):
        with tempfile.TemporaryDirectory() as d:
            home, root = Path(d) / "h", Path(d) / "root"
            (home / "logs").mkdir(parents=True)
            (root / "labels").mkdir(parents=True)
            now = time.time()
            old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 5 * 86400))
            (home / "cleanup_last.txt").write_text(old + "\n")
            pr = dict(process_day=0, second_pass=0, cleanup=0, vehicles=0, watcher=1, review=1)
            js = {j["key"]: j for j in ops.jobs(home, root, pr, now)}
            self.assertTrue(js["cleanup"]["late"])
            self.assertFalse(js["vehicles"]["late"])                 # never run is not "late"; the page says so
            data = dict(watcher=dict(idle_s=30, errors_24h=0, recent_errors=[]), jobs=list(js.values()), lag={"available": True, "waiting": 2},
                        storage={"data_disk": {"free": 3e9}}, machine=dict(load=[1, 1, 1], cpus=8))
            lv = [(a["level"], a["text"]) for a in ops.attention(data)]
            self.assertEqual(lv[0][0], "critical")                    # 3 GB free
            self.assertTrue(any("Cleanup" in t for _, t in lv))

    def test_lock_probe(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.lock"
            self.assertFalse(ops._locked(p))
            with open(p, "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                self.assertTrue(ops._locked(p))


class StorageTests(unittest.TestCase):
    def test_snapshot_and_growth(self):
        with tempfile.TemporaryDirectory() as d:
            home, root = Path(d) / "h", Path(d) / "root"
            (root / "frames").mkdir(parents=True)
            home.mkdir()
            (root / "frames" / "a.png").write_bytes(b"x" * 1000)
            rec = ops.storage_snapshot(home, root)
            self.assertEqual(rec["parts"]["frames"], 1000)
            with open(home / "storage_history.jsonl", "a") as fh:
                fh.write(json.dumps(dict(epoch=rec["epoch"] + 86400, ts="x", parts={}, total=rec["total"] + 500)) + "\n")
            s = ops.storage(home, root)
            self.assertEqual(s["growth_per_day"], 500)

    def test_accuracy_and_confusion_parse(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r / "reports").mkdir()
            (r / "reports" / "accuracy.md").write_text("# t\n\nsub\n\n- First pass (x), all known: 10/20 = 50.0%\n- When the passes AGREE (8): right 6/8 = 75.0%\n- conf 0.99 to 1.00: 9/10 = 90.0%\n")
            (r / "reports" / "confusions.csv").write_text("kind,ocr_char,true_char,count\nsub,1,T,5\nins,,A,2\n")
            a = ops.accuracy(r)
            self.assertEqual((a["first_pass"]["right"], a["agree"]["total"], a["calibration"][0]["right"]), (10, 8, 9))
            self.assertEqual(ops.confusion(r)["subs"], [dict(kind="sub", ocr="1", true="T", n=5)])
            self.assertFalse(ops.accuracy(r / "nope")["available"])


if __name__ == "__main__":
    unittest.main()
