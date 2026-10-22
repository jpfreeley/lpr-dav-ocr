import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lpr_dav_ocr import second_pass as S

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


class FakeOCR:
    """Returns the same text for each call, only when `sees` says the input looks right."""
    def __init__(self, text, score=0.9, when=lambda img: True, poly=((10, 10), (110, 10), (110, 60), (10, 60))):
        self.text, self.score, self.when, self.poly, self.calls = text, score, when, poly, 0

    def predict(self, img):
        self.calls += 1
        if not self.when(img):
            return [{"rec_texts": [], "rec_scores": [], "rec_polys": []}]
        return [{"rec_texts": [self.text], "rec_scores": [self.score], "rec_polys": [np.array(self.poly, np.float32)]}]


@unittest.skipUnless(HAVE_CV2, "needs opencv")
class RotationTests(unittest.TestCase):
    def test_matches_opencv_rotation_plus_canvas_shift(self):
        shape = (1080, 1920, 3)
        M, (nw, nh) = S.rotation(shape, 20)
        ref = cv2.getRotationMatrix2D((1920 / 2, 1080 / 2), 20, 1.0)
        c, s = abs(ref[0, 0]), abs(ref[0, 1])
        ref[0, 2] += int(1080 * s + 1920 * c) / 2 - 1920 / 2
        ref[1, 2] += int(1080 * c + 1920 * s) / 2 - 1080 / 2
        self.assertEqual((nw, nh), (int(1080 * s + 1920 * c), int(1080 * c + 1920 * s)))
        np.testing.assert_allclose(M, ref, atol=1e-6)

    def test_map_back_inverts_the_rotation(self):
        M, _ = S.rotation((1080, 1920, 3), 20)
        pts = np.array([[100.0, 200.0], [1500.0, 900.0]])
        fwd = pts @ M[:, :2].T + M[:, 2]
        np.testing.assert_allclose(S.map_back(fwd, M), pts, atol=1e-6)


class ShapeTests(unittest.TestCase):
    def test_shape_classes(self):
        self.assertEqual(S.shape_of(2.0), "ok")
        self.assertEqual(S.shape_of(3.5), "wide")
        self.assertEqual(S.shape_of(1.2), "narrow")


@unittest.skipUnless(HAVE_CV2, "needs opencv")
class LadderTests(unittest.TestCase):
    img = np.zeros((1080, 1920, 3), np.uint8)

    def test_first_rung_wins_and_later_ones_are_not_tried(self):
        full, fast = FakeOCR("DEMO001"), FakeOCR("OTHER99")
        res = S.second_read(full, lambda: fast, self.img, "DEMO001", 20, cv2, np)
        self.assertEqual(res[0], "DEMO001")
        self.assertEqual(full.calls, 1)
        self.assertEqual(fast.calls, 0)

    def test_rotated_rung_runs_when_unrotated_finds_nothing(self):
        # the unrotated frame is 1920 wide; the rotated canvas is wider
        full = FakeOCR("DEMO001", when=lambda im: im.shape[1] > 1920)
        res = S.second_read(full, lambda: FakeOCR("X"), self.img, "DEMO001", 20, cv2, np)
        self.assertEqual(res[0], "DEMO001")
        self.assertEqual(full.calls, 2)

    def test_outline_only_never_counts_as_a_read(self):
        full = FakeOCR("DEMO001", when=lambda im: False)
        fast = FakeOCR("DEMO001")
        res = S.second_read(full, lambda: fast, self.img, "DEMO001", 20, cv2, np)
        self.assertEqual(res[0], "")
        self.assertIsNone(res[1])
        self.assertEqual(len(res[2]), 4)
        row = S.row_for({"image": "a.png", "plate": "DEMO001", "conf": "0.99"}, res, 1.0)
        self.assertEqual(row[3], "")                 # plate2 blank
        self.assertEqual(row[5], 0)                  # so it can never count as agreement

    def test_nothing_found_gives_none(self):
        none = FakeOCR("X", when=lambda im: False)
        self.assertIsNone(S.second_read(none, lambda: none, self.img, "DEMO001", 20, cv2, np))


class RowTests(unittest.TestCase):
    r = {"image": "a.png", "plate": "DEMO001", "conf": "0.9"}

    def test_columns_line_up_with_the_header(self):
        row = S.row_for(self.r, ("DEMO002", 0.8, [1, 2, 3, 4], 2.0), 1.0, ("DEMO001", 0.7))
        self.assertEqual(len(row), len(S.HEADER))
        self.assertEqual(dict(zip(S.HEADER, row))["plate3"], "DEMO001")

    def test_tried_and_empty_is_marked_untried_is_blank(self):
        res = ("DEMO002", 0.8, [1, 2, 3, 4], 2.0)
        self.assertEqual(S.row_for(self.r, res, 1.0)[-2:], ["", ""])
        self.assertEqual(S.row_for(self.r, res, 1.0, ())[-2:], ["", "0"])

    def test_crop_only_when_passes_disagree(self):
        self.assertTrue(S.needs_crop("DEMO001", ("DEMO002", 0.8, [1, 2, 3, 4], 2.0)))
        self.assertFalse(S.needs_crop("DEMO001", ("DEMO001", 0.8, [1, 2, 3, 4], 2.0)))
        self.assertFalse(S.needs_crop("DEMO001", ("", None, [1, 2, 3, 4], 2.0)))     # outline only
        self.assertFalse(S.needs_crop("DEMO001", None))


@unittest.skipUnless(HAVE_CV2, "needs opencv")
class CropReadTests(unittest.TestCase):
    def test_reads_an_enlarged_crop(self):
        seen = []
        ocr = FakeOCR("DEMO001")
        orig = ocr.predict
        ocr.predict = lambda im: (seen.append(im.shape), orig(im))[1]
        got = S.crop_read(ocr, np.zeros((1080, 1920, 3), np.uint8), [500, 400, 700, 460], cv2)
        self.assertEqual(got, ("DEMO001", 0.9))
        self.assertEqual(seen[0][0], 200)             # enlarged to 200 px tall

    def test_empty_crop_is_none(self):
        self.assertIsNone(S.crop_read(FakeOCR("X"), np.zeros((100, 100, 3), np.uint8), [500, 500, 600, 600], cv2))


class RewriteTests(unittest.TestCase):
    def test_old_file_gains_columns_and_replaced_rows_are_swapped(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d)
            out = day / "second_pass.csv"
            old = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
            with out.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(old)
                w.writerow(["a.png", "DEMO001", "0.9", "DEMO002", "0.8", 0, 1, 2, 3, 4, "2.0", "ok", "1"])
                w.writerow(["b.png", "DEMO003", "0.9", "", "", 0, "", "", "", "", "", "", "1"])
            existing = list(csv.DictReader(out.open()))
            S.rewrite(day, out, existing, {"a.png": S.row_for({"image": "a.png", "plate": "DEMO001", "conf": "0.9"},
                                                                ("DEMO002", 0.8, [1, 2, 3, 4], 2.0), 1.0, ("DEMO001", 0.7))})
            rows = list(csv.DictReader(out.open()))
            self.assertEqual(list(rows[0].keys()), S.HEADER)
            self.assertEqual(rows[0]["plate3"], "DEMO001")
            self.assertEqual(rows[1]["plate3"], "")
            self.assertEqual(rows[1]["image"], "b.png")


if __name__ == "__main__":
    unittest.main()
