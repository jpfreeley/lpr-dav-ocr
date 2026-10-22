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
