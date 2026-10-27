import unittest
from pathlib import Path
import tempfile

import numpy as np

from lpr_dav_ocr import clarity as C

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


def stats(sharp=1000.0, h=60, contrast=120.0, clip=0.0):
    return dict(sharp=sharp, h=h, w=2 * h, contrast=contrast, clip=clip)


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.pop = C.Population([stats(s, h, c) for s, h, c in ((50, 30, 30), (200, 40, 60), (800, 55, 90), (2000, 70, 120), (5000, 90, 160))])

    def score(self, st, conf=0.99, agree=True, crop=True, aspect=2.1):
        return C.score(st, conf, agree, crop, aspect, self.pop)

    def test_sharper_bigger_clearer_scores_higher(self):
        self.assertGreater(self.score(stats(5000, 90, 160)), self.score(stats(50, 30, 30)))

    def test_clipping_is_penalized(self):
        self.assertGreater(self.score(stats(clip=0.0)), self.score(stats(clip=0.4)))

    def test_reads_and_shape_count(self):
        good = self.score(stats())
        self.assertGreater(good, self.score(stats(), conf=0.80, agree=False, crop=False))      # a doubtful read
        self.assertGreater(good, self.score(stats(), aspect=3.2))                              # a steep angle
        self.assertTrue(0 <= self.score(stats(), conf=0.0, agree=False, crop=False, aspect=9) <= 100)

    def test_unmeasurable_frame_has_no_score(self):
        self.assertIsNone(self.score(None))
        self.assertIsNone(self.score({}))

    @unittest.skipUnless(HAVE_CV2, "needs opencv")
    def test_frame_stats_see_blur_and_clipping(self):
        rng = np.random.default_rng(0)
        sharp = (rng.integers(0, 2, (80, 160)) * 200 + 20).astype(np.uint8)
        blur = cv2.GaussianBlur(sharp, (0, 0), 4)
        white = np.full((80, 160), 255, np.uint8)
        with tempfile.TemporaryDirectory() as d:
            for name, im in (("sharp", sharp), ("blur", blur), ("white", white)):
                cv2.imwrite(str(Path(d) / f"{name}.png"), im)
            a, b, w = (C.frame_stats(Path(d) / f"{n}.png", [0, 0, 160, 80]) for n in ("sharp", "blur", "white"))
            self.assertGreater(a["sharp"], b["sharp"] * 5)
            self.assertEqual(w["clip"], 1.0)
            self.assertEqual(C.frame_stats(Path(d) / "sharp.png", None), None)
            self.assertIsNone(C.frame_stats(Path(d) / "missing.png", [0, 0, 10, 10]))
