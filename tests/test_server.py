import csv
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from lpr_dav_ocr import watchlist as WL
from lpr_dav_ocr.review import server as S

DAY = "2026-01-01"
HDR = {"X-Requested-With": "lpr-review", "Content-Type": "application/json"}


def write_day(root):
    """Synthetic day: 6 plates. Names sort in time order. Which are queued:
       a: auto-accepted   b: needs review (passes disagree)   c: needs review (low confidence)
       d: waiting for second pass   e: not plate-shaped (needs review)   f: auto-accepted"""
    day = Path(root) / "frames" / DAY
    day.mkdir(parents=True)
    plates = [  # image time, plate, conf, second plate, conf2, shape, has_second_pass
        ("01.00.00", "DEMO001", 0.999, "DEMO001", 0.99, "ok", True),
        ("02.00.00", "DEMO002", 0.97, "DEMO0O2", 0.96, "ok", True),
        ("03.00.00", "DEMO003", 0.85, "DEMO003", 0.90, "ok", True),
        ("04.00.00", "DEMO004", 0.999, None, None, None, False),
        ("05.00.00", "SIGNTXT", 0.99, "SIGNTXT", 0.99, "wide", True),
        ("06.00.00", "DEMO006", 0.995, "DEMO006", 0.99, "ok", True),
    ]
    with (day / "index.csv").open("w", newline="") as f, (day / "second_pass.csv").open("w", newline="") as g:
        w, v = csv.writer(f), csv.writer(g)
        w.writerow(["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"])
        v.writerow(["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"])
        for t, p, c, p2, c2, shape, has in plates:
            image = f"{DAY}_{t}-{t}-{p}_{c:.2f}.png"
            w.writerow([f"{t}-{t}[M].dav", p, c, 30, "", image, 500, 1, 3, 5])
            if has:
                v.writerow([image, p, c, p2, c2, int(p == p2), 100, 100, 340, 220, "4.2" if shape == "wide" else "2.1", shape, 2])
            # frames themselves are not needed for the API tests
    return [f"{DAY}_{t}-{t}-{p}_{c:.2f}.png" for t, p, c, *_ in plates]


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.images = write_day(cls.root)
        cls._wl = (WL.HOME, WL.WATCHLIST)
        WL.HOME = cls.root / "home"
        WL.WATCHLIST = WL.HOME / "watchlist.csv"
        cls.store = S.Store(cls.root)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(cls.store))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        WL.HOME, WL.WATCHLIST = cls._wl
        cls.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def item(self, f="need", image=""):
        return self.call("GET", f"/api/item?f={f}&image={image}")[1]

    # -- plate history routes
    def test_history_routes(self):
        st, body = self.call("GET", "/api/history?q=DEMO001")
        self.assertEqual((st, body["plate"], body["count"]), (200, "DEMO001", 1))      # the auto-accepted one
        st, body = self.call("GET", "/api/plates?q=DEMO")
        self.assertEqual(st, 200)
        self.assertTrue(body)
        st, page = self.call("GET", "/history")
        self.assertEqual(st, 200)
        self.assertIn(b"Plate history", page)

    # -- queue rules
    def test_default_queue_excludes_auto_accepted_and_unready(self):
        d = self.item("need")
        queue = []
        img = ""
        # walk the queue
        seen = set()
        while True:
            d = self.item("need", img)
            if not d["item"] or d["item"]["image"] in seen:
                break
            seen.add(d["item"]["image"])
            queue.append(d["item"]["plate"])
            if not d["next"]:
                break
            img = d["next"]
        self.assertEqual(queue, ["DEMO002", "DEMO003", "SIGNTXT"])

    def test_day_counts(self):
        day = self.item("need")["days"][0]
        self.assertEqual((day["total"], day["left"], day["auto"], day["waiting"]), (6, 3, 2, 1))

    def test_unready_plate_is_hidden_from_unreviewed_but_listed_in_everything(self):
        self.assertNotIn("DEMO004", [i for i in self._plates("unrev")])
        self.assertIn("DEMO004", self._plates("all"))

    def _plates(self, f):
        out, img = [], ""
        for _ in range(10):
            d = self.item(f, img)
            if not d["item"] or d["item"]["plate"] in out:
                break
            out.append(d["item"]["plate"])
            if not d["next"]:
                break
            img = d["next"]
        return out

    def test_auto_filter_lists_the_auto_accepted(self):
        self.assertEqual(self._plates("auto"), ["DEMO001", "DEMO006"])

    def test_shape_and_alt_fields(self):
        d = self.item("need", self.images[4])
        self.assertEqual(d["item"]["shape"], "wide")
        d = self.item("need", self.images[1])
        self.assertEqual(d["item"]["alt"]["plate"], "DEMO0O2")

    # -- security and validation
    def test_label_requires_the_header(self):
        st, _ = self.call("POST", "/api/label", {"image": self.images[1], "verdict": "ok"}, {"Content-Type": "application/json"})
        self.assertEqual(st, 403)

    def test_label_validation(self):
        for body in (
            {"image": "nope.png", "verdict": "ok"},
            {"image": self.images[1], "verdict": "bogus"},
            {"image": self.images[1], "verdict": "fix"},                           # no truth
            {"image": self.images[1], "verdict": "fix", "truth": "DEMO002"},       # truth equals the read
            {"image": self.images[1], "verdict": "ok", "state": "not valid!"},
        ):
            st, _ = self.call("POST", "/api/label", body, HDR)
            self.assertEqual(st, 400, body)

    def test_path_traversal_is_refused(self):
        st, _ = self.call("GET", "/img/../index.csv")
        self.assertEqual(st, 404)
        st, _ = self.call("GET", f"/img/{DAY}_x/../../../etc/passwd")
        self.assertEqual(st, 404)

    def test_server_binds_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    # -- labelling
    def test_label_flow_and_last_reviewed(self):
        img = self.images[1]
        st, _ = self.call("POST", "/api/label", {"image": img, "verdict": "fix", "truth": "demo0o2x"[:7].replace("O", "0"), "state": "NY"}, HDR)
        self.assertEqual(st, 200)
        d = self.item("need", img)
        self.assertEqual(d["item"]["label"]["verdict"], "fix")
        self.assertEqual(d["item"]["label"]["state"], "NY")
        self.assertEqual(d["last_reviewed"], img)
        rec = json.loads((self.root / "labels" / "labels.jsonl").read_text().splitlines()[-1])
        self.assertEqual((rec["plate_ocr"], rec["verdict"]), ("DEMO002", "fix"))
        # the labelled plate leaves the default queue
        self.assertNotIn("DEMO002", self._plates("need"))

    # -- config and watchlist
    def test_config_endpoint(self):
        st, d = self.call("GET", "/api/config")
        self.assertEqual(st, 200)
        self.assertEqual((d["w"], d["h"]), (1920, 1080))

    def test_watch_add_and_near_match_in_item(self):
        st, _ = self.call("POST", "/api/watch", {"plate": "DEMO0O3", "nickname": "near"}, HDR)
        self.assertEqual(st, 200)
        d = self.item("need", self.images[2])
        self.assertEqual(d["item"]["watch"]["level"], "possible")
        self.call("POST", "/api/watch/remove", {"plate": "DEMO0O3"}, HDR)
        self.assertIsNone(self.item("need", self.images[2])["item"]["watch"])

    def test_page_is_served(self):
        st, body = self.call("GET", "/i/anything.png")
        self.assertEqual(st, 200)
        self.assertIn(b"Plate review", body)


if __name__ == "__main__":
    unittest.main()


class HistoryTests(unittest.TestCase):
    """Plate history: exact match, verified or auto-accepted reads only unless asked."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.images = write_day(cls.root)
        # a second day with a repeat of DEMO001 (auto-accepted) and one unreviewed read of it
        day2 = cls.root / "frames" / "2026-01-02"
        day2.mkdir(parents=True)
        rows = [("09.00.00", "DEMO001", 0.999, "DEMO001"), ("10.00.00", "DEMO001", 0.90, "DEMO001")]
        with (day2 / "index.csv").open("w", newline="") as f, (day2 / "second_pass.csv").open("w", newline="") as g:
            w, v = csv.writer(f), csv.writer(g)
            w.writerow(["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"])
            v.writerow(["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"])
            for t, p, c, p2 in rows:
                img = f"2026-01-02_{t}-{t}-{p}_{c:.2f}.png"
                w.writerow([f"{t}-{t}[M].dav", p, c, 30, "", img, 500, 1, 3, 5])
                v.writerow([img, p, c, p2, 0.99, 1, 100, 100, 340, 220, "2.1", "ok", 2])
        cls.store = S.Store(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_default_counts_only_confirmed_or_auto(self):
        r = self.store.sightings("demo001")                # case and punctuation are ignored
        self.assertEqual(r["plate"], "DEMO001")
        self.assertEqual(r["count"], 2)                    # day-1 auto + day-2 auto; the 0.90 read is unreviewed
        self.assertEqual({s["basis"] for s in r["sightings"]}, {"auto"})
        self.assertEqual(r["days"], 2)
        self.assertEqual(r["sightings"][0]["day"], "2026-01-02")      # newest first

    def test_unverified_widens_the_search(self):
        r = self.store.sightings("DEMO001", unverified=True)
        self.assertEqual(r["count"], 3)
        self.assertIn("unverified", {s["basis"] for s in r["sightings"]})

    def test_exact_match_only(self):
        self.assertEqual(self.store.sightings("DEMO00")["count"], 0)
        self.assertEqual(self.store.sightings("DEM0001")["count"], 0)

    def test_human_verdicts_decide_the_plate(self):
        img = self.images[1]                                # DEMO002 needs review
        self.store.add_label({"image": img, "verdict": "fix", "truth": "XYZ789", "state": "NY"})
        self.assertEqual(self.store.sightings("XYZ789")["count"], 1)
        self.assertEqual(self.store.sightings("XYZ789")["states"], {"NY": 1})
        self.assertEqual(self.store.sightings("DEMO002", unverified=True)["count"], 0)     # the wrong read is gone
        self.store.add_label({"image": self.images[2], "verdict": "notplate", "state": "UNKN"})
        self.assertEqual(self.store.sightings("DEMO003", unverified=True)["count"], 0)     # not a plate: never listed

    def test_search_suggestions(self):
        s = self.store.plate_search("DEMO0")
        self.assertEqual(s[0]["plate"], "DEMO001")          # most seen first
        self.assertEqual(s[0]["count"], 2)
        self.assertNotIn("DEMO004", [e["plate"] for e in s])   # waiting for a second pass: not confirmed


class TlcHintTests(unittest.TestCase):
    def test_common_misreads_are_fixed(self):
        self.assertEqual(S.tlc_fix("11234560"), "T123456C")      # T read as 1, C read as 0
        self.assertEqual(S.tlc_fix("Y1234560"), "Y123456C")
        self.assertEqual(S.tlc_fix("TI2345O0"), "T123450C")      # I and O in the digit positions
        self.assertEqual(S.tlc_fix("T123456C"), "T123456C")      # already fits

    def test_does_not_force_other_plates(self):
        self.assertIsNone(S.tlc_fix("ABC1234"))                  # wrong length
        self.assertIsNone(S.tlc_fix("ABCD1234"))                 # eight characters but not the pattern
        self.assertIsNone(S.tlc_fix("T12345C"))                  # seven characters
        self.assertIsNone(S.tlc_fix(""))
        self.assertIsNone(S.tlc_fix(None))

    def test_hint_for_the_page(self):
        self.assertEqual(S.Store._tlc("T123456C", None, None), {"plate": "T123456C", "shaped": True})
        self.assertEqual(S.Store._tlc("11234560", None, None), {"plate": "T123456C", "shaped": False})
        self.assertEqual(S.Store._tlc("DEMO001", "DEMO002", None), None)
        self.assertEqual(S.Store._tlc("DEMO0012", "11234560", None)["plate"], "T123456C")   # second pass can supply it


class PickReadTests(unittest.TestCase):
    def test_pattern_fit_beats_confidence(self):
        # the second pass is more confident but only the first read fits AAANNNN
        self.assertEqual(S.pick_read("ABC1234", 0.80, "AB01234", 0.95, None), ("ABC1234", "fits the pattern"))

    def test_tlc_pattern_counts(self):
        self.assertEqual(S.pick_read("11234560", 0.9, "T123456C", 0.8, None)[0], "T123456C")

    def test_crop_read_breaks_a_tie_when_nothing_fits(self):
        self.assertEqual(S.pick_read("DEMOX01", 0.9, "DEMO001", 0.8, "DEMO001"), ("DEMO001", "the crop read backs it"))

    def test_falls_back_to_confidence(self):
        self.assertEqual(S.pick_read("DEMOX01", 0.7, "DEMO001", 0.9, None), ("DEMO001", "more confident"))

    def test_both_fit_uses_crop_then_confidence(self):
        self.assertEqual(S.pick_read("ABC1234", 0.7, "ABD1234", 0.9, "ABC1234")[0], "ABC1234")
        self.assertEqual(S.pick_read("ABC1234", 0.7, "ABD1234", 0.9, None)[0], "ABD1234")

    def test_no_pick_when_passes_agree_or_second_missing(self):
        self.assertIsNone(S.pick_read("ABC1234", 0.9, "ABC1234", 0.9, None))
        self.assertIsNone(S.pick_read("ABC1234", 0.9, None, 0.0, None))

    def test_fits(self):
        self.assertEqual(S.fits("ABC1234"), "NYS")
        self.assertEqual(S.fits("T123456C"), "TLC")
        self.assertIsNone(S.fits("DEMO001"))
        self.assertIsNone(S.fits(None))


class RecentTests(unittest.TestCase):
    """Auto-approve a read equal to a plate you verified within recent_days; offer a one-character correction."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        plan = {"2026-01-01": [("01.00.00", "DEMO001")],            # will be verified
                "2026-01-10": [("02.00.00", "DEMO001"), ("03.00.00", "DEM0001"), ("04.00.00", "ZZZZ999")],
                "2026-03-01": [("05.00.00", "DEMO001")]}            # too far away
        cls.img = {}
        for day, items in plan.items():
            d = cls.root / "frames" / day
            d.mkdir(parents=True)
            with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
                w, v = csv.writer(f), csv.writer(g)
                w.writerow(hdr_i)
                v.writerow(hdr_s)
                for t, p in items:
                    img = f"{day}_{t}-{t}-{p}_0.80.png"
                    cls.img[(day, p)] = img
                    w.writerow([f"{t}-{t}[M].dav", p, 0.80, 30, "", img, 500, 1, 3, 5])
                    v.writerow([img, p, 0.80, p, 0.70, 1, 100, 100, 340, 220, "2.1", "ok", 2])    # low confidence: no auto by conf
        cls.store = S.Store(cls.root)
        cls.store.add_label({"image": cls.img[("2026-01-01", "DEMO001")], "verdict": "ok", "truth": "DEMO001", "state": "NY"})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def item(self, day, plate):
        return next(i for i in self.store.items(day) if i["plate"] == plate)

    def test_exact_match_within_window_is_auto_approved(self):
        i = self.item("2026-01-10", "DEMO001")
        self.assertTrue(i["auto"])
        self.assertEqual(i["auto_why"], "recent")
        self.assertEqual(i["recent"], {"plate": "DEMO001", "n": 1, "exact": True})

    def test_outside_the_window_is_not(self):
        i = self.item("2026-03-01", "DEMO001")
        self.assertFalse(i["auto"])
        self.assertIsNone(i["recent"])

    def test_one_character_off_is_offered_not_approved(self):
        i = self.item("2026-01-10", "DEM0001")
        self.assertFalse(i["auto"])
        self.assertEqual(i["recent"], {"plate": "DEMO001", "n": 1, "exact": False})

    def test_unrelated_plate_gets_nothing(self):
        i = self.item("2026-01-10", "ZZZZ999")
        self.assertFalse(i["auto"])
        self.assertIsNone(i["recent"])

    def test_a_plate_never_vouches_for_itself(self):
        self.assertIsNone(self.item("2026-01-01", "DEMO001")["recent"])       # its only verified sighting is itself

    def test_auto_approved_plates_do_not_vouch_for_others(self):
        # the Jan 10 read is auto-approved; Mar 1 is within 30 days of it but must not count
        self.assertFalse(self.item("2026-03-01", "DEMO001")["auto"])

    def test_one_off(self):
        self.assertTrue(S.one_off("DEMO001", "DEM0001"))
        self.assertTrue(S.one_off("DEMO001", "DEMO01"))
        self.assertTrue(S.one_off("DEMO01", "DEMO001"))
        self.assertFalse(S.one_off("DEMO001", "DEMO001"))
        self.assertFalse(S.one_off("DEMO001", "DEM0O01"))
        self.assertFalse(S.one_off("ABC", "ABCDE"))

    def test_switched_off_by_zero_days(self):
        old = S.RECENT_DAYS
        S.RECENT_DAYS = 0
        try:
            self.assertIsNone(self.store.recent("DEMO001", self.img[("2026-01-10", "DEMO001")]))
        finally:
            S.RECENT_DAYS = old


class LikeTests(unittest.TestCase):
    """Hints for AAANNNN plates whose leading letters were misread: known plates with the same last four digits."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        plan = {"2026-01-01": [("01.00.00", "ABC1234", 0.999), ("02.00.00", "ABC1234", 0.999), ("03.00.00", "QRS1234", 0.999)],
                "2026-01-02": [("04.00.00", "A8C1234", 0.80), ("05.00.00", "XYZ9999", 0.80), ("06.00.00", "ABC9999", 0.80)]}
        cls.img = {}
        for day, items in plan.items():
            d = cls.root / "frames" / day
            d.mkdir(parents=True)
            with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
                w, v = csv.writer(f), csv.writer(g)
                w.writerow(hdr_i)
                v.writerow(hdr_s)
                for t, p, c in items:
                    img = f"{day}_{t}-{t}-{p}_{c:.2f}.png"
                    cls.img[(day, t)] = img
                    w.writerow([f"{t}-{t}[M].dav", p, c, 30, "", img, 500, 1, 3, 5])
                    v.writerow([img, p, c, p, 0.99, 1, 100, 100, 340, 220, "2.1", "ok", 2])
        cls.store = S.Store(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def like(self, day, t):
        it = next(i for i in self.store.items(day) if i["image"] == self.img[(day, t)])
        return self.store.like(it)

    def test_offers_known_plates_with_the_same_tail(self):
        got = self.like("2026-01-02", "04.00.00")                 # A8C1234 read: letters misread
        self.assertEqual([e["plate"] for e in got], ["ABC1234"])          # QRS1234 differs in all 3 letters
        self.assertEqual(got[0], {"plate": "ABC1234", "n": 2, "d": 1})

    def test_needs_the_same_last_four_digits(self):
        self.assertEqual(self.like("2026-01-02", "05.00.00"), [])        # XYZ9999: nothing known with 9999 except ABC9999 (unreviewed)

    def test_unreviewed_plates_are_not_suggested(self):
        self.assertNotIn("ABC9999", [e["plate"] for e in self.like("2026-01-02", "05.00.00")])

    def test_a_plate_seen_only_here_is_not_offered_to_itself(self):
        self.assertEqual(self.like("2026-01-01", "03.00.00"), [])       # QRS1234 appears once (this image only)
