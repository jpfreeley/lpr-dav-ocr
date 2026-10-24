import fcntl
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from lpr_dav_ocr import cleanup as C
from lpr_dav_ocr import pipeline as P

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


def gray3(seed=0):
    rng = np.random.default_rng(seed)
    g = (rng.random((60, 80)) * 40).astype(np.uint8) + np.tile(np.arange(80, dtype=np.uint8), (60, 1))
    return np.dstack([g, g, g])


@unittest.skipUnless(HAVE_CV2, "needs opencv")
class FrameTests(unittest.TestCase):
    def test_as_saved_only_changes_pure_gray_frames(self):
        g = gray3()
        self.assertEqual(P.as_saved(g).ndim, 2)
        col = g.copy()
        col[0, 0, 2] += 1
        self.assertEqual(P.as_saved(col).shape, col.shape)

    def test_to_gray_keeps_every_pixel_and_the_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.png"
            im = gray3()
            cv2.imwrite(str(p), im)
            before = p.stat().st_size
            self.assertEqual(C.png_color_type(p), 2)
            saved = C.to_gray(p)
            self.assertGreater(saved, 0)
            self.assertEqual(before - p.stat().st_size, saved)
            self.assertEqual(C.png_color_type(p), 0)
            self.assertTrue(np.array_equal(cv2.imread(str(p)), im))                  # readers see the identical image
            self.assertEqual(C.to_gray(p), 0)                                        # already single-channel
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_a_color_frame_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.png"
            im = gray3()
            im[:, :, 2] = 255 - im[:, :, 2]
            cv2.imwrite(str(p), im)
            raw = p.read_bytes()
            self.assertEqual(C.to_gray(p), 0)
            self.assertEqual(p.read_bytes(), raw)

    def test_step_frames_skips_fresh_files_marks_old_days_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"):
                (root / "frames" / d).mkdir(parents=True)
                cv2.imwrite(str(root / "frames" / d / "a.png"), gray3())
            old = time.time() - 3600
            for p in (root / "frames").rglob("a.png"):
                os.utime(p, (old, old))
            fresh = root / "frames" / "2026-01-04" / "fresh.png"
            cv2.imwrite(str(fresh), gray3(1))                                        # written just now: may still be in use
            msgs = []
            est = C.step_frames(root, False, time.time() + 60, msgs.append)
            self.assertGreater(est, 0)
            self.assertEqual(C.png_color_type(root / "frames" / "2026-01-01" / "a.png"), 2)   # a dry run changes nothing
            saved = C.step_frames(root, True, time.time() + 60, msgs.append)
            self.assertGreater(saved, 0)
            self.assertEqual(C.png_color_type(root / "frames" / "2026-01-01" / "a.png"), 0)
            self.assertEqual(C.png_color_type(fresh), 2)
            self.assertTrue((root / "frames" / "2026-01-01" / C.DONE_MARK).exists())          # old day: done
            self.assertFalse((root / "frames" / "2026-01-04" / C.DONE_MARK).exists())         # newest days are always rechecked
            self.assertEqual(len(C.rgb_frames(root)), 0)


class CacheTrashLogTests(unittest.TestCase):
    def test_cache_prunes_oldest_first_and_dry_run_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "imgcache").mkdir()
            now = time.time()
            for i in range(5):
                p = home / "imgcache" / f"{i}.png"
                p.write_bytes(b"x" * 1024 * 1024)
                os.utime(p, (now - (5 - i) * 100, now - (5 - i) * 100))
            freed = C.step_cache(home, False, 3, 0, lambda *_: None, now)
            self.assertEqual((freed, len(list((home / "imgcache").iterdir()))), (2 * 1024 * 1024, 5))
            C.step_cache(home, True, 3, 0, lambda *_: None, now)
            self.assertEqual(sorted(p.name for p in (home / "imgcache").iterdir()), ["2.png", "3.png", "4.png"])
            C.step_cache(home, True, 0, 1, lambda *_: None, now + 86400 * 2)                 # everything older than a day
            self.assertEqual(list((home / "imgcache").iterdir()), [])

    def test_trash_is_only_touched_when_asked_and_only_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "trash" / "2026-01-01").mkdir(parents=True)
            old, new = root / "trash" / "2026-01-01" / "old.png", root / "trash" / "2026-01-01" / "new.png"
            old.write_bytes(b"1234")
            new.write_bytes(b"1234")
            t = time.time()
            os.utime(old, (t - 40 * 86400, t - 40 * 86400))
            self.assertEqual(C.step_trash(root, True, 0, lambda *_: None), 0)               # default: never
            self.assertTrue(old.exists())
            self.assertEqual(C.step_trash(root, True, 30, lambda *_: None), 4)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())

    def test_big_logs_keep_their_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "logs").mkdir()
            log = home / "logs" / "w.log"
            log.write_bytes(b"".join(b"line %d\n" % i for i in range(400000)))
            self.assertGreater(log.stat().st_size, 2 * 1024 * 1024)
            C.step_logs(home, True, lambda *_: None, max_mb=2, keep=1024)
            data = log.read_bytes()
            self.assertLessEqual(len(data), 1024)
            self.assertTrue(data.endswith(b"line 399999\n"))
            self.assertTrue(data.startswith(b"line "))                                      # cut on a line boundary


class VacuumTests(unittest.TestCase):
    def make(self, home):
        db = sqlite3.connect(str(home / "vehicles.sqlite"))
        db.execute("CREATE TABLE v (a TEXT)")
        db.executemany("INSERT INTO v VALUES (?)", [("x" * 500,)] * 4000)
        db.commit()
        db.execute("DELETE FROM v")
        db.commit()
        db.close()

    def test_vacuum_shrinks_a_mostly_empty_index_and_waits_for_a_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.make(home)
            size = (home / "vehicles.sqlite").stat().st_size
            held = open(home / "vehicles.lock", "w")
            fcntl.flock(held, fcntl.LOCK_EX)                                                # a refresh is running
            self.assertEqual(C.step_vacuum(home, True, lambda *_: None), 0)
            self.assertEqual((home / "vehicles.sqlite").stat().st_size, size)
            held.close()
            self.assertGreater(C.step_vacuum(home, True, lambda *_: None), 0)
            self.assertLess((home / "vehicles.sqlite").stat().st_size, size)


class ScheduleTests(unittest.TestCase):
    def test_due_only_in_the_night_window_and_when_old_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            night = time.mktime((2026, 10, 24, 3, 0, 0, 0, 0, -1))
            noon = time.mktime((2026, 10, 24, 12, 0, 0, 0, 0, -1))
            self.assertTrue(C.due(1, home, night))                  # never run: due in the night window
            self.assertFalse(C.due(1, home, noon))                  # but not at midday
            self.assertFalse(C.due(0, home, night))                 # 0 switches it off
            C.stamp_path(home).write_text(time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(night - 3 * 3600)) + "\n")
            self.assertFalse(C.due(1, home, night))                 # ran three hours ago
            self.assertTrue(C.due(1, home, night + 86400))          # a day later


if __name__ == "__main__":
    unittest.main()
