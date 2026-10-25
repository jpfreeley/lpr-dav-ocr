import unittest
from pathlib import Path

import numpy as np

from lpr_dav_ocr import pipeline as P

try:
    import cv2  # noqa: F401
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


class SimilarTests(unittest.TestCase):
    def test_same_plate_variants(self):
        self.assertTrue(P.similar("DEMO001", "DEMO001"))
        self.assertTrue(P.similar("DEMO001", "DEM0001"))      # one substitution
        self.assertTrue(P.similar("DEMO001", "EMO001"))       # clipped first character (also a substring)
        self.assertTrue(P.similar("DEMO001", "DEMO01"))       # one deletion

    def test_different_plates(self):
        self.assertFalse(P.similar("DEMO001", "DEMO999"))
        self.assertFalse(P.similar("ABCDE", "VWXYZ"))


class GroupingTests(unittest.TestCase):
    def test_variants_group_and_best_read_wins(self):
        groups = []
        P.add_read(groups, "DEM0001", 0.90, 1, "f1")
        P.add_read(groups, "DEMO001", 0.99, 2, "f2")
        P.add_read(groups, "XYZ7777", 0.95, 3, "f3")
        self.assertEqual(len(groups), 2)
        g = next(g for g in groups if g["text"].startswith("DEM"))
        self.assertEqual((g["text"], g["frame"]), ("DEMO001", "f2"))
        self.assertEqual(g["variants"], {"DEM0001", "DEMO001"})

    def test_longer_read_preferred_when_confidence_close(self):
        groups = []
        P.add_read(groups, "EMO001", 0.995, 1, "clipped")
        P.add_read(groups, "DEMO001", 0.99, 2, "full")
        self.assertEqual(groups[0]["text"], "DEMO001")


class ClipNameTests(unittest.TestCase):
    def test_clip_stamp(self):
        p = Path("/share/CAM/2026-01-01/03.15.11-03.15.30[M][0@0][0].dav")
        self.assertEqual(P.clip_stamp(p), "2026-01-01_03.15.11-03.15.30")

    def test_unknown_date_folder(self):
        self.assertTrue(P.clip_stamp(Path("/x/misc/03.15.11-03.15.30.dav")).startswith("unknown-date_"))


class PickFramesTests(unittest.TestCase):
    @staticmethod
    def motion(n=300, bursts=()):
        d = np.full(n, 0.5)
        for start, length, height in bursts:
            d[start:start + length] = height
        return d

    def test_no_motion_gives_nothing(self):
        picks, nseg = P.pick_frames(self.motion(), 3.0, 6.0, 2, 16)
        self.assertEqual((picks, nseg), ([], 0))

    def test_one_burst_one_segment(self):
        picks, nseg = P.pick_frames(self.motion(bursts=[(100, 12, 20)]), 3.0, 6.0, 2, 16, stride=1)
        self.assertEqual(nseg, 1)
        frames = [f for f, _ in picks]
        self.assertEqual((min(frames), max(frames)), (98, 113))      # burst plus the 2-frame pad

    def test_strongest_burst_is_segment_zero(self):
        d = self.motion(bursts=[(50, 8, 6), (200, 20, 30)])
        picks, nseg = P.pick_frames(d, 3.0, 6.0, 0, 16, stride=1)
        self.assertEqual(nseg, 2)
        seg0 = [f for f, s in picks if s == 0]
        self.assertTrue(all(200 <= f < 220 for f in seg0))

    def test_max_segments_and_frame_cap(self):
        d = self.motion(bursts=[(20 * i, 5, 10 + i) for i in range(1, 12)])
        picks, nseg = P.pick_frames(d, 3.0, 6.0, 0, 3, stride=1, max_segments=4)
        self.assertEqual(nseg, 4)
        for sid in range(4):
            self.assertLessEqual(sum(1 for _, s in picks if s == sid), 3)

    def test_stride_thins_frames(self):
        d = self.motion(bursts=[(100, 20, 20)])
        every, _ = P.pick_frames(d, 3.0, 6.0, 0, 99, stride=1)
        every2, _ = P.pick_frames(d, 3.0, 6.0, 0, 99, stride=2)
        self.assertEqual(len(every), 20)
        self.assertEqual(len(every2), 10)

    def test_empty_input(self):
        self.assertEqual(P.pick_frames(np.array([]), 3.0, 6.0, 2, 16), ([], 0))


@unittest.skipUnless(HAVE_CV2, "OpenCV not installed")
class PrepTests(unittest.TestCase):
    def test_overlays_are_blanked_and_input_untouched(self):
        img = np.full((1080, 1920, 3), 200, np.uint8)
        out = P.prep(img, 0)
        self.assertEqual(out[50, 1500].sum(), 0)                  # inside the timestamp box
        self.assertEqual(out[540, 960].tolist(), [200, 200, 200])
        self.assertEqual(img[50, 1500].tolist(), [200, 200, 200])  # caller's frame is not modified

    def test_rotation_grows_the_canvas(self):
        img = np.zeros((1080, 1920, 3), np.uint8)
        out = P.prep(img, 20.0)
        self.assertGreater(out.shape[0], 1080)
        self.assertGreater(out.shape[1], 1920)


class OutputFolderTests(unittest.TestCase):
    def test_default_out_uses_the_data_root(self):
        out = P.default_out(Path("/nowhere/CAM/2026-01-01/x.dav"), "2026-01-01")
        self.assertTrue(str(out).endswith("frames/2026-01-01"))


if __name__ == "__main__":
    unittest.main()


class OverlayLayoutTests(unittest.TestCase):
    def setUp(self):
        self.saved = (list(P.OVERLAYS), list(P._BASE_OVERLAYS), list(P.OVERLAY_CHANGES))

    def tearDown(self):
        P.OVERLAYS[:] = self.saved[0]
        P._BASE_OVERLAYS[:] = self.saved[1]
        P.OVERLAY_CHANGES[:] = self.saved[2]

    def test_layout_follows_the_clip_start_time(self):
        P._BASE_OVERLAYS[:] = [(0.66, 0.02, 0.97, 0.10)]
        P.OVERLAY_CHANGES[:] = [("2026-01-05T09:08:30", [(0.0, 0.88, 0.4, 1.0)])]
        self.assertEqual(P.use_layout("2026-01-05", "09.08.29"), [(0.66, 0.02, 0.97, 0.10)])
        self.assertEqual(P.use_layout("2026-01-05", "09.08.30"), [(0.0, 0.88, 0.4, 1.0)])
        self.assertEqual(P.use_layout("2026-01-06", "00:00:01"), [(0.0, 0.88, 0.4, 1.0)])
        self.assertEqual(P.use_layout("2026-01-04", "23.59.59"), [(0.66, 0.02, 0.97, 0.10)])   # earlier days keep the old boxes

    @unittest.skipUnless(HAVE_CV2, "needs opencv")
    def test_prep_blanks_the_current_boxes(self):
        P._BASE_OVERLAYS[:] = [(0.0, 0.0, 0.5, 0.5)]
        P.OVERLAY_CHANGES[:] = [("2026-01-05T00:00:00", [(0.5, 0.5, 1.0, 1.0)])]
        frame = np.full((100, 200, 3), 200, np.uint8)
        P.use_layout("2026-01-04", "10.00.00")
        self.assertEqual(int(P.prep(frame, 0)[10, 10, 0]), 0)
        P.use_layout("2026-01-05", "10.00.00")
        out = P.prep(frame, 0)
        self.assertEqual((int(out[10, 10, 0]), int(out[90, 190, 0])), (200, 0))
