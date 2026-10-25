import csv
import json
import tempfile
import unittest
from pathlib import Path

from lpr_dav_ocr import dedupe as D
from lpr_dav_ocr.review import server as S

HDR_I = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
HDR_S = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
DAY = "2026-01-01"


def c(plate, t, clip="a", conf=0.9, verdict=None, agree=False, image=None):
    return dict(image=image or f"{plate}-{t}.png", clip=clip, plate=plate, t=t, conf=conf, verdict=verdict, agree=agree, box=None)


class GroupTests(unittest.TestCase):
    def test_same_plate_within_the_window_is_one_sighting(self):
        self.assertEqual(len(D.group([c("ABC1234", 0), c("ABC1234", 30)])), 1)
        self.assertEqual(D.group([c("ABC1234", 0), c("ABC1234", 31)]), [])

    def test_a_chain_of_short_gaps_is_one_sighting(self):
        g = D.group([c("ABC1234", 0), c("ABC1234", 25), c("ABC1234", 50)])
        self.assertEqual([len(x) for x in g], [3])

    def test_different_plates_are_never_merged_across_clips(self):
        self.assertEqual(D.group([c("ABC1234", 0, "a"), c("ABC1235", 1, "b")]), [])

    def test_a_misread_in_the_same_clip_is_the_same_plate(self):
        self.assertEqual(len(D.group([c("T123456C", 5, "a"), c("1123456", 5, "a")])), 1)      # clipped read
        self.assertEqual(D.group([c("ABC1234", 5, "a"), c("XYZ9999", 5, "a")]), [])           # a different vehicle
        self.assertEqual(D.group([c("ABC", 5, "a"), c("ABD", 5, "a")]), [])                   # too short to trust

    def test_two_plates_far_apart_in_time_stay_apart(self):
        self.assertEqual(D.group([c("ABC1234", 0), c("ABC1234", 600)]), [])


class ChooseTests(unittest.TestCase):
    sharp = staticmethod(lambda path, box: 0.0)

    def pick(self, g):
        k, others, why = D.choose(g, ".", sharp=self.sharp)
        return k, others, why

    def test_your_verified_frame_wins_over_a_more_confident_one(self):
        k, others, why = self.pick([c("ABC1234", 0, conf=0.99), c("ABC1234", 1, conf=0.80, verdict="fix")])
        self.assertEqual((k["conf"], why), (0.80, "kept the frame you verified"))
        self.assertEqual(len(others), 1)

    def test_a_clipped_read_does_not_win_on_confidence(self):
        k, _, why = self.pick([c("1123456", 0, conf=0.999), c("T123456C", 0, conf=0.992)])
        self.assertEqual(k["plate"], "T123456C")
        self.assertIn("complete", why)

    def test_sharper_crop_decides_between_equals(self):
        sharp = lambda path, box: 900.0 if "b.png" in str(path) else 100.0
        k, _, why = D.choose([c("ABC1234", 0, image="a.png"), c("ABC1234", 1, image="b.png")], ".", sharp=sharp)
        self.assertEqual((k["image"], why), ("b.png", "sharpest plate"))


def write_day(root, rows, labels=()):
    """rows: (clip, frame, plate, conf, plate2, aspect). Creates index.csv, second_pass.csv and a stub PNG per row."""
    day = Path(root) / "frames" / DAY
    day.mkdir(parents=True)
    names = []
    with (day / "index.csv").open("w", newline="") as f, (day / "second_pass.csv").open("w", newline="") as g:
        w, v = csv.writer(f), csv.writer(g)
        w.writerow(HDR_I)
        v.writerow(HDR_S)
        for clip, frame, plate, conf, p2, asp in rows:
            name = f"{DAY}_{clip}-{plate}_{conf:.2f}.png"
            names.append(name)
            (day / name).write_bytes(b"png")
            w.writerow([clip + "[M].dav", plate, conf, frame, "", name, 500, 1, 3, 5])
            v.writerow([name, plate, conf, p2, 0.9, int(plate == p2), 1, 1, 9, 9, asp, "ok" if asp <= 3.0 else "wide", 1])
    if labels:
        (Path(root) / "labels").mkdir()
        (Path(root) / "labels" / "labels.jsonl").write_text("".join(json.dumps(x) + "\n" for x in labels))
    return names


class PlanApplyTests(unittest.TestCase):
    sharp = staticmethod(lambda path, box: 0.0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self):
        # same vehicle twice in one clip (frames 51 and 53); an unrelated plate; a not-a-plate read of the same text
        return write_day(self.root, [("00.02.11-00.02.31", 51, "ABC1234", 0.98, "ABC1234", 2.0),
                                     ("00.02.11-00.02.31", 53, "ABC1234", 0.91, "ABD1234", 2.0),
                                     ("00.10.00-00.10.20", 10, "QRS5678", 0.95, "QRS5678", 2.0),
                                     ("00.20.00-00.20.20", 10, "WIDE000", 0.95, "WIDE000", 4.2),
                                     ("00.20.00-00.20.20", 12, "WIDE000", 0.94, "WIDE000", 4.2)],
                          labels=[{"image": f"{DAY}_00.02.11-00.02.31-ABC1234_0.91.png", "verdict": "ok", "truth": "ABC1234", "state": "UNKN"}])

    def test_plan_finds_the_pair_and_ignores_the_rest(self):
        self.make()
        dec = D.plan(self.root, sharp=self.sharp)
        self.assertEqual(len(dec), 1)                                           # the wide reads are auto-rejected, not merged
        day, keeper, others, why = dec[0]
        self.assertEqual(keeper["image"], f"{DAY}_00.02.11-00.02.31-ABC1234_0.91.png")     # the one you verified
        self.assertEqual([o["image"] for o in others], [f"{DAY}_00.02.11-00.02.31-ABC1234_0.98.png"])

    def test_apply_moves_the_extra_frame_records_it_and_views_ignore_it(self):
        names = self.make()
        moved, missing, skipped = D.apply(self.root, D.plan(self.root, sharp=self.sharp))
        self.assertEqual((moved, missing, skipped), (1, 0, 0))
        self.assertFalse((self.root / "frames" / DAY / names[0]).exists())
        self.assertTrue((self.root / "trash" / DAY / names[0]).is_file())              # recoverable until --purge
        rec = json.loads((self.root / "labels" / "duplicates.jsonl").read_text().splitlines()[0])
        self.assertEqual((rec["image"], rec["kept"]), (names[0], names[1]))
        store = S.Store(self.root)
        self.assertNotIn(names[0], [i["image"] for i in store.items(DAY)])
        self.assertIn(names[1], [i["image"] for i in store.items(DAY)])
        self.assertEqual(D.plan(self.root, sharp=self.sharp), [])                       # running again finds nothing

    def test_a_group_you_verified_meanwhile_is_left_alone(self):
        names = self.make()
        dec = D.plan(self.root, sharp=self.sharp)
        # after the plan was made you verified the frame that was going to be dropped, and not the keeper
        labels = self.root / "labels" / "labels.jsonl"
        labels.write_text(json.dumps({"image": names[0], "verdict": "ok", "truth": "ABC1234", "state": "UNKN"}) + "\n")
        dec[0][1]["verdict"] = None
        moved, _, skipped = D.apply(self.root, dec)
        self.assertEqual((moved, skipped), (0, 1))
        self.assertTrue((self.root / "frames" / DAY / names[0]).exists())

    def test_purge_deletes_the_trash(self):
        self.make()
        D.apply(self.root, D.plan(self.root, sharp=self.sharp))
        n, size = D.purge(self.root)
        self.assertEqual((n, size), (1, 3))
        self.assertFalse((self.root / "trash").exists())


if __name__ == "__main__":
    unittest.main()
