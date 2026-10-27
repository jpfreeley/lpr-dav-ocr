import csv
import http.client
import json
import tempfile
import threading
import unittest
from unittest import mock
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
                v.writerow([image, p, c, p2, c2, int(p == p2), 100, 100, 340, 220, "3.2" if shape == "wide" else "2.1", shape, 2])
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

    # -- the re-check flag is stored only for a repeated "not a plate"
    def test_recheck_flag_is_saved_for_not_a_plate(self):
        img = self.images[1]
        st, _ = self.call("POST", "/api/label", {"image": img, "verdict": "notplate", "state": "UNKN", "recheck": True}, HDR)
        self.assertEqual(st, 200)
        last = [json.loads(x) for x in (self.root / "labels" / "labels.jsonl").read_text().splitlines()][-1]
        self.assertTrue(last.get("rechecked"))
        st, _ = self.call("POST", "/api/label", {"image": img, "verdict": "ok", "state": "UNKN", "recheck": True}, HDR)
        last = [json.loads(x) for x in (self.root / "labels" / "labels.jsonl").read_text().splitlines()][-1]
        self.assertNotIn("rechecked", last)

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

    # -- live check while editing the boxes
    def test_lookup_route(self):
        st, body = self.call("GET", "/api/lookup?q=demo%2D001&image=" + self.images[0])
        self.assertEqual((st, body["plate"]), (200, "DEMO001"))                 # case and punctuation normalised
        self.assertEqual(set(body), {"plate", "fits", "like", "recent", "vehicle", "watch"})
        st, body = self.call("GET", "/api/lookup?q=")
        self.assertEqual((st, body["like"], body["watch"]), (200, [], None))

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


class StdFixTests(unittest.TestCase):
    """Letter-position digits in an AAANNNN-shaped read are offered as letters (0 as D, 6 as G, 2 as Z)."""

    def test_fix(self):
        self.assertEqual(S.std_fix("0AB1234"), "DAB1234")
        self.assertEqual(S.std_fix("A06"+"1234"), "ADG1234")
        self.assertEqual(S.std_fix("ABC1234"), None)                    # nothing to fix
        self.assertEqual(S.std_fix("8AB1234"), None)                    # 8 and 5 are coin flips, 1 is ambiguous: not offered
        self.assertEqual(S.std_fix("0001234"), None)                    # needs at least one letter to anchor the pattern
        self.assertEqual(S.std_fix("0AB123"), None)                     # wrong length or digits
        self.assertEqual(S.std_fix("0AB12X4"), None)

    def test_offer_only_when_no_read_already_fits(self):
        f = S.Store._stdfix
        self.assertEqual(f("0AB1234", None, None), dict(plate="DAB1234", read="0AB1234"))
        self.assertEqual(f("0AB1234", "DAB1234", None), None)           # the second pass already fits the pattern
        self.assertEqual(f("T123456C", None, None), None)               # a TLC plate is not touched
        self.assertEqual(f("XYZ", "2AB1234", None), dict(plate="ZAB1234", read="2AB1234"))


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

    def test_state_specific_patterns(self):
        self.assertEqual(S.fits("K12ABC"), "NJ")                # a letter, two digits, three letters
        self.assertEqual(S.fits("123AB4"), "MOP")               # NYS moped: three digits, two letters, a digit
        self.assertEqual(S.fits("12345AB"), "COM")              # NY commercial: five digits, two letters
        self.assertIsNone(S.fits("K1ABC2"))
        self.assertIsNone(S.fits("1234AB"))
        self.assertEqual(S.pick_read("K12ABC", 0.7, "K12A8C", 0.9, None)[0], "K12ABC")      # fitting a pattern beats confidence

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
        self.assertEqual(i["recent"], {"plate": "DEMO001", "n": 1, "exact": True, "state": "NY"})

    def test_outside_the_window_is_not(self):
        i = self.item("2026-03-01", "DEMO001")
        self.assertFalse(i["auto"])
        self.assertIsNone(i["recent"])

    def test_one_character_off_is_offered_not_approved(self):
        i = self.item("2026-01-10", "DEM0001")
        self.assertFalse(i["auto"])
        self.assertEqual(i["recent"], {"plate": "DEMO001", "n": 1, "exact": False, "state": "NY"})

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
        self.assertEqual(got[0], {"plate": "ABC1234", "n": 2, "d": 1, "state": None})        # auto-accepted only: no state known

    def test_the_state_you_set_on_a_plate_comes_with_the_hint(self):
        store = S.Store(self.root)
        store.labels_path = self.root / "labels" / "state.jsonl"
        store.add_label({"image": self.img[("2026-01-01", "01.00.00")], "verdict": "ok", "truth": "ABC1234", "state": "NJ"})
        store.add_label({"image": self.img[("2026-01-01", "02.00.00")], "verdict": "ok", "truth": "ABC1234", "state": "NJ"})
        it = next(i for i in store.items("2026-01-02") if i["image"] == self.img[("2026-01-02", "04.00.00")])
        self.assertEqual([(e["plate"], e["state"]) for e in store.like(it)], [("ABC1234", "NJ")])
        self.assertIsNone(store.state_of("QRS1234"))
        store.add_label({"image": self.img[("2026-01-01", "03.00.00")], "verdict": "ok", "truth": "QRS1234", "state": "UNKN"})
        self.assertIsNone(store.state_of("QRS1234"))                                 # UNKN is not a state

    def test_needs_the_same_last_four_digits(self):
        self.assertEqual(self.like("2026-01-02", "05.00.00"), [])        # XYZ9999: nothing known with 9999 except ABC9999 (unreviewed)

    def test_unreviewed_plates_are_not_suggested(self):
        self.assertNotIn("ABC9999", [e["plate"] for e in self.like("2026-01-02", "05.00.00")])

    def test_a_plate_seen_only_here_is_not_offered_to_itself(self):
        self.assertEqual(self.like("2026-01-01", "03.00.00"), [])       # QRS1234 appears once (this image only)


class RejectTests(unittest.TestCase):
    """Reads whose text region is much wider than a plate are auto-rejected: out of the queue, history and alerts."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs", "plate3", "conf3", "elsewhere", "elsewhere_conf"]
        d = cls.root / "frames" / "2026-01-01"
        d.mkdir(parents=True)
        cls.img = {}
        with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
            w, v = csv.writer(f), csv.writer(g)
            w.writerow(hdr_i)
            v.writerow(hdr_s)
            for t, p, asp in (("01.00.00", "DEMO001", "2.1"), ("02.00.00", "SIGNTXT", "4.2"), ("03.00.00", "WIDEBUT", "3.2"), ("04.00.00", "SIGNTWO", "4.5"), ("05.00.00", "SIGNKEEP", "4.6")):
                img = f"2026-01-01_{t}-{t}-{p}_0.80.png"
                cls.img[p] = img
                shape = "ok" if float(asp) <= 3.0 else "wide"
                w.writerow([f"{t}-{t}[M].dav", p, 0.80, 30, "", img, 500, 1, 3, 5])
                # SIGNKEEP is wide too, but the second pass found a plate-shaped read elsewhere in the frame, so it stays in review
                v.writerow([img, p, 0.80, p, 0.70, 1, 100, 100, 340, 220, asp, shape, 2, "", "", *(("DEMO009", "0.8800") if p == "SIGNKEEP" else ("", ""))])
        cls.store = S.Store(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_wide_reads_are_rejected_at_the_threshold(self):
        got = {i["plate"]: i["rejected"] for i in self.store.items("2026-01-01")}
        self.assertEqual(got, {"DEMO001": False, "SIGNTXT": True, "WIDEBUT": False, "SIGNTWO": True, "SIGNKEEP": False})   # 3.2 is under 3.5; SIGNKEEP has a plate-shaped read elsewhere

    def test_rejected_reads_leave_the_queue_but_have_their_own_view(self):
        need = [i["plate"] for i in self.store.view("2026-01-01", "need")]
        self.assertEqual(need, ["DEMO001", "WIDEBUT", "SIGNKEEP"])
        self.assertEqual([i["plate"] for i in self.store.view("2026-01-01", "rejected")], ["SIGNTXT", "SIGNTWO"])
        day = self.store.days()[0]
        self.assertEqual((day["left"], day["rejected"]), (3, 2))

    def test_the_frame_offers_the_plate_shaped_read_elsewhere(self):
        keep = next(i for i in self.store.items("2026-01-01") if i["plate"] == "SIGNKEEP")
        self.assertEqual(keep["elsewhere"], {"plate": "DEMO009", "conf": 0.88})
        self.assertIsNone(next(i for i in self.store.items("2026-01-01") if i["plate"] == "SIGNTXT")["elsewhere"])

    def test_rejected_reads_are_not_plate_history(self):
        self.assertEqual(self.store.sightings("SIGNTXT", unverified=True)["count"], 0)
        self.assertEqual(self.store.sightings("WIDEBUT", unverified=True)["count"], 1)

    def test_a_human_label_overrides(self):
        store = S.Store(self.root)                       # its own store, so the label does not leak into other tests
        store.labels_path = self.root / "labels" / "override.jsonl"
        store.add_label({"image": self.img["SIGNTWO"], "verdict": "ok", "truth": "SIGNTWO", "state": "UNKN"})
        self.assertEqual([i["plate"] for i in store.view("2026-01-01", "rejected")], ["SIGNTXT"])
        self.assertEqual(store.sightings("SIGNTWO")["count"], 1)          # you said it is a plate

    def test_switched_off_by_zero(self):
        old = S.REJECT_ASPECT
        S.REJECT_ASPECT = 0
        try:
            self.assertFalse(any(i["rejected"] for i in self.store.items("2026-01-01")))
        finally:
            S.REJECT_ASPECT = old


class RecheckTests(unittest.TestCase):
    """Earlier 'not a plate' labels the second pass disagrees with (plate-shaped, different text) get a second look."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.images = write_day(self.root)            # DEMO002 has a differing second read and an ok shape; SIGNTXT is wide
        self.store = S.Store(self.root)
        for i in (1, 2, 4):                           # DEMO002, DEMO003 (passes agree), SIGNTXT (wide shape)
            self.store.add_label({"image": self.images[i], "verdict": "notplate", "state": "UNKN"})

    def tearDown(self):
        self.tmp.cleanup()

    def plates(self):
        return [i["plate"] for i in self.store.view(DAY, "recheck")]

    def test_only_disagreeing_plate_shaped_reads_are_listed(self):
        self.assertEqual(self.plates(), ["DEMO002"])
        self.assertEqual(self.store.days()[0]["recheck"], 1)

    def test_confirming_not_a_plate_again_clears_it(self):
        self.store.add_label({"image": self.images[1], "verdict": "notplate", "state": "UNKN", "rechecked": True})
        self.assertEqual(self.plates(), [])

    def test_relabeling_it_clears_it(self):
        self.store.add_label({"image": self.images[1], "verdict": "fix", "truth": "DEMO0O2", "state": "NY"})
        self.assertEqual(self.plates(), [])

    def test_unlabeled_and_correct_plates_are_never_listed(self):
        self.assertNotIn("DEMO001", self.plates())
        self.assertNotIn("DEMO006", self.plates())


class ReportsTests(unittest.TestCase):
    """The Reports page data: confirmed sightings only, with returning plates counted against the whole history."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        plan = {"2026-01-05": [("06.10.00", "ABC1234"), ("06.12.00", "ABC1234"), ("09.00.00", "T123456C"), ("10.00.00", "ZZZ0000")],
                "2026-01-06": [("06.20.00", "ABC1234"), ("11.00.00", "QRS5678")]}
        for day, items in plan.items():
            d = cls.root / "frames" / day
            d.mkdir(parents=True)
            with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
                w, v = csv.writer(f), csv.writer(g)
                w.writerow(hdr_i)
                v.writerow(hdr_s)
                for t, p in items:
                    img = f"{day}_{t}-{t}-{p}_0.999.png"
                    w.writerow([f"{t}-{t}[M].dav", p, 0.999, 30, "", img, 500, 1, 3, 5])
                    v.writerow([img, p, 0.999, p, 0.99, 1, 100, 100, 340, 220, "2.1", "ok", 2])    # all auto-accepted
        cls.store = S.Store(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_and_returning(self):
        r = S._reports.build(self.store)
        self.assertEqual((r["kpi"]["sightings"], r["kpi"]["plates"], r["kpi"]["days"]), (6, 4, 2))
        d1, d2 = r["per_day"]
        self.assertEqual((d1["sightings"], d1["new"], d1["returning"]), (4, 3, 1))     # 06.12 repeats the 06.10 sighting
        self.assertEqual((d2["sightings"], d2["new"], d2["returning"]), (2, 1, 1))     # ABC1234 was seen yesterday

    def test_regulars_and_types(self):
        r = S._reports.build(self.store)
        self.assertEqual([(x["plate"], x["days"], x["sightings"]) for x in r["regulars"]], [("ABC1234", 2, 3)])
        self.assertEqual(r["seen_hist"], [3, 1])                                       # three plates on 1 day, one on 2 days
        self.assertEqual(r["mix"]["types"][0], {"day": "2026-01-05", "standard": 3, "TLC": 1, "NJ": 0, "commercial": 0, "moped": 0, "other": 0})
        self.assertEqual(r["overlap"]["rows"], [[100, 33], [50, 100]])                 # 1 of 3 and 1 of 2 plates shared

    def test_day_filter_keeps_history_for_returning(self):
        r = S._reports.build(self.store, days=["2026-01-06"])
        self.assertEqual(r["days"], ["2026-01-06"])
        self.assertEqual(r["per_day"][0]["returning"], 1)                              # still judged against 01-05

    def test_gaps_hours_and_watch(self):
        r = S._reports.build(self.store, watch=[{"plate": "T123456C", "nickname": "Taxi"}])
        self.assertEqual(r["gaps"]["counts"][1], 1)                                    # 06:10 to 06:12 is 2 min: the "2 to 10 min" bin
        self.assertEqual(r["heat"]["rows"][0][6], 2)
        self.assertEqual(r["watch"][0]["nickname"], "Taxi")
        self.assertEqual(len(r["watch"][0]["sightings"]), 1)

    def test_vehicle_reports(self):
        from tests.test_vehicles import make_db
        store = S.Store(self.root)
        real = S._veh.open_db
        try:
            S._veh.open_db = lambda home=None: None
            store._vdb = None
            self.assertEqual(S._reports.build(store)["vehicles"], {"available": False})      # no index imported yet
            d, db = make_db([("ABC1234", "NY", "TOYOT", "SUBN", 2019, "WH", 4, 2027),
                             ("T123456C", "NY", "TOYOT", "4DSD", 2021, "BK", 4, 2027),
                             ("ZZZ0000", "NY", "FORD", "VAN", 2005, "BL", 2, 2027)])
            store._vdb = db
            v = S._reports.build(store)["vehicles"]
            # two stale marks: one on the record you saw, one on a record that has since changed
            recs = {p: store.vehicle(p, "NY")["text"] for p in ("ABC1234", "T123456C")}
            marks = [dict(ts="2026-01-01T00:00:00", plate="ABC1234", stale=True, record=recs["ABC1234"]),
                     dict(ts="2026-01-01T00:00:00", plate="T123456C", stale=True, record="2010 Old Make, red")]
            fresh = S.Store(self.root)
            fresh._vdb = db
            (self.root / "labels").mkdir(exist_ok=True)
            (self.root / "labels" / "vehicle_flags.jsonl").write_text("".join(json.dumps(m) + "\n" for m in marks))
            vs = S._reports.build(fresh)["vehicles"]
            (self.root / "labels" / "vehicle_flags.jsonl").unlink()
            d.cleanup()
        finally:
            S._veh.open_db = real
        self.assertEqual((v["plates"], v["matched"], v["stale"]), (4, 3, 0))                # QRS5678 has no record
        self.assertEqual((vs["stale"], vs["stale_old"]), (1, 1))                            # only the unchanged record counts
        self.assertEqual(v["makes"][0], {"name": "Toyota", "count": 2})
        kinds = {k["kind"]: k["hours"] for k in v["kind_hours"]}
        self.assertEqual(kinds["Taxi (TLC plate)"][9], 1)                                    # taxi is told by plate pattern
        self.assertEqual(kinds["SUV or wagon"][6], 3)                                        # ABC1234 at 06:10, 06:12 and 06:20
        self.assertEqual(kinds["Van, truck or pickup"][10], 1)
        cover = {c["type"]: (c["matched"], c["total"]) for c in v["coverage"]}
        self.assertEqual(cover["standard"], (2, 3))
        self.assertEqual({g["group"]: g["total"] for g in v["by_group"]}["Seen on 2 days"], 1)

    def test_routes(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(self.store))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            c.request("GET", "/api/report?days=2026-01-05")
            r = c.getresponse()
            body = json.loads(r.read())
            self.assertEqual((r.status, body["days"]), (200, ["2026-01-05"]))
            c.request("GET", "/reports")
            r = c.getresponse()
            self.assertIn(b"Plate reports", r.read())
            c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


class PlateStateTests(unittest.TestCase):
    """A state set on one sighting of a plate becomes the plate's state on every sighting, without writing labels."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        d = cls.root / "frames" / "2026-01-01"
        d.mkdir(parents=True)
        cls.img = {}
        with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
            w, v = csv.writer(f), csv.writer(g)
            w.writerow(hdr_i)
            v.writerow(hdr_s)
            for t, p in (("01.00.00", "ABC1234"), ("02.00.00", "ABC1234"), ("03.00.00", "ABC1234"), ("04.00.00", "QRS5678")):
                img = f"2026-01-01_{t}-{t}-{p}_0.999.png"
                cls.img[t] = img
                w.writerow([f"{t}-{t}[M].dav", p, 0.999, 30, "", img, 500, 1, 3, 5])
                v.writerow([img, p, 0.999, p, 0.99, 1, 100, 100, 340, 220, "2.1", "ok", 2])    # all auto-accepted
        cls.store = S.Store(cls.root)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(cls.store))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def post(self, img, state, verdict="ok"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/api/label", body=json.dumps({"image": img, "verdict": verdict, "state": state}), headers=HDR)
        r = c.getresponse()
        body = json.loads(r.read())
        c.close()
        return r.status, body

    def test_state_spreads_to_every_sighting_of_the_plate_once(self):
        self.assertEqual([i["label"] for i in self.store.items("2026-01-01")][:1], [None])
        st, body = self.post(self.img["01.00.00"], "NJ")
        self.assertEqual((st, body["plate"], body["state"], body["applied"]), (200, "ABC1234", "NJ", 2))    # the two other sightings
        rows = self.store.sightings("ABC1234")["sightings"]
        self.assertEqual({r["state"] for r in rows}, {"NJ"})                                                 # all three show NJ
        self.assertEqual(self.store.sightings("ABC1234")["states"], {"NJ": 3})
        self.assertIsNone(self.store.state_of("QRS5678"))                                                    # other plates untouched
        self.assertTrue((self.root / "labels" / "plate_states.jsonl").is_file())
        labels = (self.root / "labels" / "labels.jsonl").read_text().splitlines()
        self.assertEqual(len(labels), 1)                                                                     # no labels written for the others
        # setting it again, an unknown state, or a not-a-plate verdict spreads to nobody
        st, body = self.post(self.img["02.00.00"], "NJ")
        self.assertEqual(body["applied"], 0)                                                                  # the third already has NJ
        st, body = self.post(self.img["04.00.00"], "UNKN")
        self.assertEqual(body["applied"], 0)
        self.assertIsNone(self.store.state_of("QRS5678"))
        st, body = self.post(self.img["04.00.00"], "NY", verdict="notplate")
        self.assertEqual((body["plate"], body["applied"]), (None, 0))                                         # not a plate: no plate to spread to


class ValueQueueTests(unittest.TestCase):
    """The "most valuable first" queue ranks unreviewed plates by how much one label changes the results."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        plan = {"2026-01-05": [("06.00.00", "REPEAT1", "REPEAT1"), ("07.00.00", "ONLYONE", "ONLYONE"), ("08.00.00", "REPEAT1", "REPEAX1"), ("09.00.00", "WATCHED", "WATCHED")],
                "2026-01-06": [("06.00.00", "REPEAT1", "REPEAT1")]}
        for day, items in plan.items():
            d = cls.root / "frames" / day
            d.mkdir(parents=True)
            with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
                w, v = csv.writer(f), csv.writer(g)
                w.writerow(hdr_i)
                v.writerow(hdr_s)
                for t, p1, p2 in items:
                    img = f"{day}_{t}-{t}-{p1}_0.80.png"
                    w.writerow([f"{t}-{t}[M].dav", p1, 0.80, 30, "", img, 500, 1, 3, 5])
                    v.writerow([img, p1, 0.80, p2, 0.70, int(p1 == p2), 100, 100, 340, 220, "2.1", "ok", 2])    # conf 0.80: never auto-accepted
        cls.store = S.Store(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def watch(cands, entries=None):
        return [({"plate": "WATCHED", "nickname": "Taxi"}, "exact", "WATCHED")] if "WATCHED" in cands else []

    def test_order_and_reasons(self):
        with mock.patch.object(S.WL, "match", self.watch):
            q = self.store.value_queue()
        names = [i["plate"] for i in q]
        self.assertEqual(names[0], "WATCHED")                                         # a watchlist match always comes first
        self.assertEqual(names[-1], "ONLYONE")                                        # a one-off read with nothing around it is last
        top = q[0]["value"]["why"]
        self.assertIn("watchlist: Taxi", top)
        self.assertEqual(names.count("REPEAT1"), 1)                                  # a plate is listed once, not once per sighting
        rep = next(i for i in q if i["plate"] == "REPEAT1")
        self.assertIn("one label clears 2 more", rep["value"]["why"])               # the other two REPEAT1 reads are within 30 days
        self.assertIn("repeat plate, the two reads disagree", rep["value"]["why"])
        self.assertIn("seen on 2 days", rep["value"]["why"])

    def test_view_and_limit(self):
        with mock.patch.object(S.WL, "match", self.watch):
            self.assertEqual(len(self.store.view("2026-01-05", "value")), 3)         # three distinct plates, all days
            self.assertEqual(len(self.store.value_queue(limit=2)), 2)

    def test_last_labeled_is_the_most_recent_save_not_the_latest_time(self):
        store = S.Store(self.root)
        store.labels_path = self.root / "labels" / "last.jsonl"
        store.labels_path.parent.mkdir(exist_ok=True)
        self.assertIsNone(store.last_labeled())
        late = "2026-01-06_06.00.00-06.00.00-REPEAT1_0.80.png"
        early = "2026-01-05_07.00.00-07.00.00-ONLYONE_0.80.png"
        store.add_label(dict(image=late, verdict="ok", ts="2026-01-07T00:00:00"))
        store.add_label(dict(image=early, verdict="ok", ts="2026-01-07T00:01:00"))        # saved last, earlier in time order
        self.assertEqual(store.last_labeled(), early)

    def test_a_label_changes_the_queue(self):
        store = S.Store(self.root)
        store.labels_path = self.root / "labels" / "value.jsonl"
        store.labels_path.parent.mkdir(exist_ok=True)
        with mock.patch.object(S.WL, "match", self.watch):
            first = store.value_queue()[0]
            self.assertEqual(first["plate"], "WATCHED")
            store.add_label(dict(image=first["image"], verdict="ok", ts="2026-01-06T00:00:00"))
            self.assertNotIn(first["image"], [i["image"] for i in store.value_queue()])   # labeled plates leave the queue at once


class BannerTests(unittest.TestCase):
    """A read of a known non-plate word (the state banner NEWYORK) is never auto-approved and is auto-rejected unless
    the second pass found plate-shaped text elsewhere in the frame."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs", "plate3", "conf3", "elsewhere", "elsewhere_conf"]
        d = cls.root / "frames" / "2026-01-01"
        d.mkdir(parents=True)
        rows = [("01.00.00", "NEWYORK", "", ""), ("02.00.00", "NEWYORK", "DEMO010", "0.8800"), ("03.00.00", "NEWYORKS", "", ""), ("04.00.00", "DEMO001", "", "")]
        with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
            w, v = csv.writer(f), csv.writer(g)
            w.writerow(hdr_i)
            v.writerow(hdr_s)
            for t, p1, el, elc in rows:
                img = f"2026-01-01_{t}-{t}-{p1}_0.999.png"
                w.writerow([f"{t}-{t}[M].dav", p1, 0.999, 30, "", img, 500, 1, 3, 5])
                v.writerow([img, p1, 0.999, p1, 0.99, 1, 100, 100, 340, 220, "2.1", "ok", 2, "", "", el, elc])    # high confidence, both agree, plate-shaped
        cls.store = S.Store(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def item(self, plate, hour):
        return next(i for i in self.store.items("2026-01-01") if i["plate"] == plate and f"_{hour}." in i["image"])

    def test_banner_is_never_auto_approved_and_is_rejected(self):
        i = self.item("NEWYORK", "01")
        self.assertEqual((i["auto"], i["rejected"], i["banner"]), (False, True, True))     # conf 0.999 and agreeing would normally approve it

    def test_banner_with_a_plate_elsewhere_stays_in_review_with_that_plate_as_the_guess(self):
        i = self.item("NEWYORK", "02")
        self.assertEqual((i["auto"], i["rejected"]), (False, False))
        self.assertEqual(i["pick"]["plate"], "DEMO010")

    def test_the_default_list_has_the_state_banners_and_the_company_name(self):
        self.assertTrue({"NEWYORK", "EWYORK", "WYORK", "TEXAS", "FEDEX", "FEDEXCOM"} <= S.NOT_PLATE_TEXTS)

    def test_only_the_exact_word_counts(self):
        self.assertEqual((self.item("NEWYORKS", "03")["banner"], self.item("NEWYORKS", "03")["auto"]), (False, True))
        self.assertTrue(self.item("DEMO001", "04")["auto"])


class TicketRecordTests(unittest.TestCase):
    """A plate that has a vehicle in the ticket data is auto-accepted at a lower confidence; a vehicle you confirm by eye
    makes the plate vouch for its other sightings over a longer window."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        plan = {"2026-01-01": [("01.00.00", "DEMO001", 0.98), ("02.00.00", "DEMO002", 0.98), ("03.00.00", "DEMO001", 0.96)],
                "2026-04-10": [("01.00.00", "DEMO001", 0.80), ("02.00.00", "DEMO003", 0.98)]}
        cls.img = {}
        for day, items in plan.items():
            d = cls.root / "frames" / day
            d.mkdir(parents=True)
            with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
                w, v = csv.writer(f), csv.writer(g)
                w.writerow(hdr_i)
                v.writerow(hdr_s)
                for t, p1, conf in items:
                    img = f"{day}_{t}-{t}-{p1}_{conf:.2f}.png"
                    cls.img[(day, t)] = img
                    w.writerow([f"{t}-{t}[M].dav", p1, conf, 30, "", img, 500, 1, 3, 5])
                    v.writerow([img, p1, conf, p1, 0.99, 1, 100, 100, 340, 220, "2.1", "ok", 2])
        (cls.root / "labels").mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def make(self):
        from tests.test_vehicles import make_db
        d, db = make_db([("DEMO001", "NY", "SUBAR", "SUBN", 2019, "GY", 3, 2027), ("DEMO003", "NY", "TOYOT", "4DSD", 2020, "WH", 2, 2027)])
        store = S.Store(self.root)
        store._vdb = db
        store.labels_path = self.root / "labels" / "t.jsonl"
        store.vflags_path = self.root / "labels" / "f.jsonl"
        for p in (store.labels_path, store.vflags_path):
            p.unlink(missing_ok=True)
        self.addCleanup(d.cleanup)
        return store

    def auto(self, store, day, t):
        return next(i for i in store.items(day) if i["image"] == self.img[(day, t)])

    def test_record_lowers_the_confidence_needed(self):
        st = self.make()
        a = self.auto(st, "2026-01-01", "01.00.00")
        self.assertEqual((a["auto"], a["auto_why"]), (True, "record"))           # conf 0.98, agree, plate-shaped, has a record
        self.assertFalse(self.auto(st, "2026-01-01", "02.00.00")["auto"])         # same, but DEMO002 has no record
        self.assertFalse(self.auto(st, "2026-01-01", "03.00.00")["auto"])         # a record, but 0.96 is under the 0.97 floor

    def test_a_stale_mark_withdraws_the_record(self):
        st = self.make()
        st.set_vehicle_stale("DEMO001", True)
        self.assertFalse(self.auto(st, "2026-01-01", "01.00.00")["auto"])

    def test_the_mark_route_returns_the_fresh_vehicle(self):
        st = self.make()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(st))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            c.request("POST", "/api/vehicle/flag", body=json.dumps(dict(plate="DEMO001", stale=False, match=True)),
                      headers={"Content-Type": "application/json", "X-Requested-With": "lpr-review"})
            r = c.getresponse()
            body = json.loads(r.read())
            self.assertEqual(r.status, 200)
            self.assertTrue(body["vehicle"]["confirmed"]["same_record"])        # the page updates from this, no second request
            c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_confirming_the_vehicle_widens_the_vouching_window(self):
        st = self.make()
        st.add_label(dict(image=self.img[("2026-01-01", "01.00.00")], verdict="ok", truth="DEMO001", ts="2026-01-02T00:00:00"))
        far = self.img[("2026-04-10", "01.00.00")]                                # 99 days later, outside the 30-day window
        self.assertIsNone(st.recent("DEMO001", far))
        st.set_vehicle_stale("DEMO001", False, image=far, match=True)             # you saw the Subaru and the plate
        r = st.recent("DEMO001", far)
        self.assertTrue(r and r["exact"])
        self.assertTrue(st.vehicle("DEMO001")["confirmed"]["same_record"])
        st.set_vehicle_stale("DEMO001", False, image=far, match=False)            # undo
        self.assertIsNone(st.recent("DEMO001", far))


class GoldTests(unittest.TestCase):
    """A frame you verified can be marked as the gold standard of its plate, one per plate."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        hdr_i = ["clip", "plate", "conf", "frame", "variants", "image", "frames", "segments", "ocr_frames", "secs"]
        hdr_s = ["image", "plate1", "conf1", "plate2", "conf2", "agree", "x0", "y0", "x1", "y1", "aspect", "shape", "secs"]
        d = cls.root / "frames" / "2026-01-01"
        d.mkdir(parents=True)
        cls.img = []
        with (d / "index.csv").open("w", newline="") as f, (d / "second_pass.csv").open("w", newline="") as g:
            w, v = csv.writer(f), csv.writer(g)
            w.writerow(hdr_i)
            v.writerow(hdr_s)
            for t, p1 in (("01.00.00", "DEMO001"), ("02.00.00", "DEMO001"), ("03.00.00", "DEMO0O1"), ("04.00.00", "DEMO002")):
                img = f"2026-01-01_{t}-{t}-{p1}_0.80.png"
                cls.img.append(img)
                w.writerow([f"{t}-{t}[M].dav", p1, 0.80, 30, "", img, 500, 1, 3, 5])
                v.writerow([img, p1, 0.80, p1, 0.70, 1, 100, 100, 340, 220, "2.1", "ok", 2])
        (cls.root / "labels").mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    stores = 0

    def make(self):
        GoldTests.stores += 1                                  # a counter, not id(): Python reuses ids, which let one test's labels leak into the next
        st = S.Store(self.root)
        for name in ("labels", "gold"):
            setattr(st, name + "_path", self.root / "labels" / f"g_{name}_{GoldTests.stores}.jsonl")
        return st

    def test_only_a_verified_frame_can_be_gold(self):
        st = self.make()
        with self.assertRaises(ValueError):
            st.set_gold(self.img[0], True)                                                 # not verified yet
        st.add_label(dict(image=self.img[0], verdict="ok", truth="DEMO001", ts="x"))
        rec = st.set_gold(self.img[0], True)
        self.assertEqual((rec["plate"], rec["gold"]), ("DEMO001", True))
        self.assertEqual(st.gold_map()["DEMO001"]["image"], self.img[0])

    def test_one_gold_per_plate_and_unmark(self):
        st = self.make()
        for i in (0, 1):
            st.add_label(dict(image=self.img[i], verdict="ok", truth="DEMO001", ts="x"))
        st.set_gold(self.img[0], True)
        st.set_gold(self.img[1], True)                                                     # a new gold replaces the old one
        self.assertEqual(st.gold_map()["DEMO001"]["image"], self.img[1])
        st.set_gold(self.img[0], False)                                                    # unmarking a frame that is not the gold changes nothing
        self.assertEqual(st.gold_map()["DEMO001"]["image"], self.img[1])
        st.set_gold(self.img[1], False)
        self.assertEqual(st.gold_map(), {})

    def test_history_flags_it_and_a_fix_uses_the_truth(self):
        st = self.make()
        st.add_label(dict(image=self.img[2], verdict="fix", truth="DEMO001", ts="x"))      # the read said DEMO0O1; you fixed it
        st.set_gold(self.img[2], True)
        self.assertEqual(st.gold_map()["DEMO001"]["image"], self.img[2])
        h = st.sightings("DEMO001")
        self.assertEqual(h["gold"]["image"], self.img[2])
        self.assertEqual([r["gold"] for r in h["sightings"] if r["image"] == self.img[2]], [True])

    def test_comparison_frame_for_another_sighting_and_for_a_near_read(self):
        st = self.make()
        st.add_label(dict(image=self.img[0], verdict="ok", truth="DEMO001", ts="x"))
        st.set_gold(self.img[0], True)
        other = next(i for i in st.items("2026-01-01") if i["image"] == self.img[1])
        g = st.gold_for(other)
        self.assertEqual((g["plate"], g["exact"], g["image"]), ("DEMO001", True, self.img[0]))
        own = next(i for i in st.items("2026-01-01") if i["image"] == self.img[0])
        self.assertIsNone(st.gold_for(own))                                                # the gold frame is not compared with itself
        near = next(i for i in st.items("2026-01-01") if i["image"] == self.img[2])      # DEMO0O1 is one character from DEMO001
        near["like"] = [dict(plate="DEMO001")]
        g2 = st.gold_for(near)
        self.assertEqual((g2["plate"], g2["exact"]), ("DEMO001", False))

