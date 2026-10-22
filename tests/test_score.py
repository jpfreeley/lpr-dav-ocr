import csv
import json
import tempfile
import unittest
from pathlib import Path

from lpr_dav_ocr import score


class AlignTests(unittest.TestCase):
    def kinds(self, a, b):
        return [(op, o, t) for op, o, t in score.align(a, b) if op != "match"]

    def test_identical(self):
        self.assertEqual(self.kinds("DEMO001", "DEMO001"), [])

    def test_substitution(self):
        self.assertEqual(self.kinds("HEMO001", "MEMO001"), [("sub", "H", "M")])

    def test_dropped_leading_character(self):
        self.assertEqual(self.kinds("EMO001", "DEMO001"), [("missed", "", "D")])

    def test_extra_character(self):
        self.assertEqual(self.kinds("DEMO0011", "DEMO001"), [("extra", "1", "")])


class EndToEndTests(unittest.TestCase):
    def test_report_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            day = root / "frames" / "2026-01-01"
            day.mkdir(parents=True)
            rows = [  # image, p1, conf, p2, conf2, truth, verdict
                ("a.png", "DEMO001", "0.995", "DEMO001", "0.99", "DEMO001", "ok"),
                ("b.png", "HEMO002", "0.95", "MEMO002", "0.97", "MEMO002", "fix"),
                ("c.png", "DEMO003", "0.99", "DEMO003", "0.99", "DEMO003", "ok"),
                ("d.png", "SIGNTEXT", "0.98", "SIGNTEXT", "0.97", None, "notplate"),
            ]
            with (day / "index.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"])
                for i, r in enumerate(rows):
                    w.writerow([f"{i:02d}.00.00-{i:02d}.00.20[M].dav", r[1], r[2], 10, "", r[0], 1, 1, 1, 1])
            with (day / "second_pass.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"])
                for r in rows:
                    w.writerow([r[0], r[1], r[2], r[3], r[4], int(r[1] == r[3]), 0, 0, 1, 1, "4.0" if r[1] == "SIGNTEXT" else "2.1",
                                "wide" if r[1] == "SIGNTEXT" else "ok", 1])
            (root / "labels").mkdir()
            with (root / "labels" / "labels.jsonl").open("w") as f:
                for r in rows:
                    f.write(json.dumps(dict(image=r[0], verdict=r[6], truth=r[5], state="UNKN")) + "\n")
            score.main(["--root", str(root)])
            text = (root / "reports" / "accuracy.md").read_text()
            self.assertIn("3 have a known plate", text)
            self.assertIn("First pass (rotated 20 deg, 640 px), all known: 2/3 = 66.7%", text)
            self.assertIn("H -> M: 1", text)                          # confusion table
            self.assertIn("flags 1 of 1 non-plates", text)            # shape hint separates the sign
            self.assertTrue((root / "reports" / "confusions.csv").exists())


if __name__ == "__main__":
    unittest.main()
