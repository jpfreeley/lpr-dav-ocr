import tempfile
import unittest
from pathlib import Path

from lpr_dav_ocr import vehicles as V
from lpr_dav_ocr.review import server as S


def make_db(rows):
    d = tempfile.TemporaryDirectory()
    db = V.connect(Path(d.name) / "v.sqlite")
    db.executemany("INSERT INTO v VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()
    return d, db


class LookupTests(unittest.TestCase):
    def test_most_common_record_wins_and_codes_are_spelled_out(self):
        d, db = make_db([("ABC1234", "NY", "TOYOT", "SUBN", 2019, "WH", 4, 2027),
                         ("ABC1234", "NY", "TOYOT", "SUBN", 2019, "WH", 3, 2026),
                         ("ABC1234", "NY", "TOYOT", "4DSD", 0, "GY", 1, 2025)])      # a typo by a ticket writer
        with d:
            v = V.lookup(db, "abc-1234")
            self.assertEqual((v["make"], v["body"], v["year"], v["color"], v["tickets"]), ("Toyota", "SUV or wagon", 2019, "white", 8))
            self.assertEqual((v["state"], v["last_fy"], v["others"]), ("NY", 2027, 1))
            self.assertEqual(V.describe(v), "2019 Toyota SUV or wagon, white (8 tickets)")

    def test_a_year_of_zero_is_ignored_and_unknown_plates_give_none(self):
        d, db = make_db([("XYZ9999", "NJ", "ME/BE", "4DSD", 0, "BK", 2, 2026)])
        with d:
            v = V.lookup(db, "XYZ9999")
            self.assertEqual((v["make"], v["year"], v["state"]), ("Mercedes-Benz", None, "NJ"))
            self.assertIsNone(V.lookup(db, "NOPE000"))
            self.assertEqual(V.describe(None), "no ticket on record")
            self.assertIsNone(V.lookup(db, "XYZ9999", "NY"))                      # a different state is a different plate

    def test_more_ticket_writer_spellings(self):
        d, db = make_db([("AAA1111", "NY", "ROVER", "SPOR", 2018, "GREY", 3, 2027)])
        with d:
            v = V.lookup(db, "AAA1111")
            self.assertEqual((v["make"], v["body"], v["color"]), ("Land Rover", "sport utility", "gray"))

    def test_the_state_you_ask_for_is_used(self):
        d, db = make_db([("ABC1234", "NY", "FORD", "PICK", 2015, "BL", 5, 2027), ("ABC1234", "NJ", "HONDA", "4DSD", 2018, "RD", 9, 2027)])
        with d:
            self.assertEqual(V.lookup(db, "ABC1234", "NY")["make"], "Ford")
            self.assertEqual(V.lookup(db, "ABC1234")["make"], "Honda")            # no state given: the better documented one


class ImportTests(unittest.TestCase):
    def test_import_is_resumable_and_never_sends_plates(self):
        calls = []

        def fake(fy, prefix, states, offset=0, timeout=120):
            calls.append((fy, prefix, tuple(states), offset))
            if prefix != "AB":
                return []
            return [dict(plate_id="ABC1234", registration_state="NY", vehicle_make="FORD", vehicle_body_type="VAN",
                         vehicle_year="2017", vehicle_color="WH", n="3")]

        real = V._soql
        V._soql = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db = V.connect(Path(tmp) / "v.sqlite")
                self.assertEqual(V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB", "AC"]), 1)
                n = len(calls)
                V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB", "AC"])
                self.assertEqual(len(calls), n)                                   # finished prefixes are skipped
                self.assertEqual(V.lookup(db, "ABC1234")["make"], "Ford")
                self.assertTrue(all(len(c[1]) == 2 for c in calls))               # only two-letter prefixes are ever asked for
        finally:
            V._soql = real


class RefreshTests(unittest.TestCase):
    def fake(self, make):
        def f(fy, prefix, states, offset=0, timeout=120):
            return [dict(plate_id="ABC1234", registration_state="NY", vehicle_make=make, vehicle_body_type="VAN",
                         vehicle_year="2017", vehicle_color="WH", n="3")] if prefix == "AB" else []
        return f

    def test_refresh_swaps_the_year_in_and_a_second_import_does_not_duplicate(self):
        real = V._soql
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db = V.connect(Path(tmp) / "v.sqlite")
                V._soql = self.fake("FORD")
                V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB", "AC"])
                V._soql = self.fake("TOYOT")
                n = V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB", "AC"], replace=True)
                self.assertEqual(n, 1)
                self.assertEqual(V.lookup(db, "ABC1234")["make"], "Toyota")             # the new data replaced the old
                self.assertEqual(db.execute("SELECT COUNT(*) FROM v WHERE fy < 0").fetchone()[0], 0)
                V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB", "AC"])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM v WHERE fy=2027").fetchone()[0], 1)    # finished prefixes were skipped
        finally:
            V._soql = real

    def test_an_interrupted_refresh_keeps_the_old_data_and_lookups_ignore_the_staging_rows(self):
        real = V._soql
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db = V.connect(Path(tmp) / "v.sqlite")
                V._soql = self.fake("FORD")
                V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB"])

                def boom(fy, prefix, states, offset=0, timeout=120):
                    if prefix == "AC":
                        raise RuntimeError("network down")
                    return self.fake("TOYOT")(fy, prefix, states)
                V._soql = boom
                with self.assertRaises(RuntimeError):
                    V.import_fy(db, 2027, ("NY",), threads=1, log=lambda *_: None, prefixes=["AB", "AC"], replace=True)
                self.assertEqual(V.lookup(db, "ABC1234")["make"], "Ford")                 # old data still answers, no double counting
                self.assertEqual(V.lookup(db, "ABC1234")["tickets"], 3)
        finally:
            V._soql = real

    def test_current_fiscal_year_and_when_a_refresh_is_due(self):
        import datetime
        self.assertEqual(V.current_fy(datetime.date(2026, 10, 24)), 2027)
        self.assertEqual(V.current_fy(datetime.date(2026, 6, 30)), 2026)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertFalse(V.refresh_due(7, home))                                   # no index yet: nothing to refresh
            V.connect(V.db_path(home)).close()
            self.assertTrue(V.refresh_due(7, home))                                    # an index with no stamp is due
            V.mark_refreshed(home)
            self.assertFalse(V.refresh_due(7, home))
            later = datetime.datetime.now().astimezone() + datetime.timedelta(days=8)
            self.assertTrue(V.refresh_due(7, home, now=later))
            self.assertFalse(V.refresh_due(0, home, now=later))                        # 0 switches it off


class ServerTests(unittest.TestCase):
    def test_store_vehicle_reports_missing_index_and_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = S.Store(Path(tmp))
            store._vdb = None
            real = V.open_db
            V.open_db = lambda home=None: None
            try:
                self.assertEqual(store.vehicle("ABC1234"), {"available": False})
            finally:
                V.open_db = real
            d, db = make_db([("ABC1234", "NY", "TOYOT", "SUBN", 2019, "WH", 4, 2027)])
            with d:
                store._vdb = db
                v = store.vehicle("ABC1234")
                self.assertEqual((v["available"], v["make"], v["text"]), (True, "Toyota", "2019 Toyota SUV or wagon, white (4 tickets)"))
                self.assertEqual(store.vehicle("ZZZ0000"), {"available": True, "none": True})

    def test_verified_stale_mark_follows_the_record_it_was_made_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = S.Store(Path(tmp))
            d, db = make_db([("ABC1234", "NY", "TOYOT", "SUBN", 2019, "WH", 4, 2027)])
            with d:
                store._vdb = db
                self.assertNotIn("stale", store.vehicle("ABC1234"))
                rec = store.set_vehicle_stale("ABC1234", True, "img.png")
                self.assertEqual(rec["record"], "2019 Toyota SUV or wagon, white (4 tickets)")
                v = store.vehicle("ABC1234")
                self.assertTrue(v["stale"]["same_record"])
                db.execute("UPDATE v SET n = 9")                                   # a refresh changed the record
                db.commit()
                self.assertFalse(store.vehicle("ABC1234")["stale"]["same_record"])
                store.set_vehicle_stale("ABC1234", False)
                self.assertNotIn("stale", store.vehicle("ABC1234"))                # undone
                self.assertEqual(len((Path(tmp) / "labels" / "vehicle_flags.jsonl").read_text().splitlines()), 2)   # append-only

    def test_flag_route_validates_the_plate(self):
        import http.client
        import json
        import threading
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            store = S.Store(Path(tmp))
            d, db = make_db([("ABC1234", "NY", "FORD", "VAN", 2017, "WH", 3, 2027)])
            with d:
                store._vdb = db
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(store))
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                try:
                    hdr = {"X-Requested-With": "lpr-review", "Content-Type": "application/json"}
                    c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
                    c.request("POST", "/api/vehicle/flag", body=json.dumps({"plate": "abc-1234", "stale": True}), headers=hdr)
                    r = c.getresponse()
                    self.assertEqual((r.status, json.loads(r.read())["plate"]), (200, "ABC1234"))
                    c.request("POST", "/api/vehicle/flag", body=json.dumps({"plate": "!"}), headers=hdr)
                    self.assertEqual(c.getresponse().status, 400)
                    c.request("POST", "/api/vehicle/flag", body=json.dumps({"plate": "ABC1234"}))      # no header: refused
                    self.assertEqual(c.getresponse().status, 403)
                    c.close()
                finally:
                    httpd.shutdown()
                    httpd.server_close()


if __name__ == "__main__":
    unittest.main()


class NullCountTests(unittest.TestCase):
    def test_a_row_with_no_ticket_count_counts_as_zero(self):
        d, db = make_db([("ABC1234", "NY", "TOYOT", "SUBN", 2019, "WH", None, 2027),
                         ("ABC1234", "NY", "HONDA", "SEDN", 2018, "BK", 3, 2026)])
        with d:
            v = V.lookup(db, "ABC1234")
            self.assertEqual((v["tickets"], v["make_code"]), (3, "HONDA"))


class ParallelLookupTests(unittest.TestCase):
    def test_many_threads_can_look_up_plates_through_one_store_without_errors(self):
        import threading
        rows = [(f"AB{n:05d}", "NY", "TOYOT", "SUBN", 2019, "WH", n % 5, 2027) for n in range(300)]
        d, db = make_db(rows)
        errors = []
        with d, tempfile.TemporaryDirectory() as tmp:
            store = S.Store(Path(tmp))
            store._vdb = db

            def work(k):
                try:
                    for n in range(k, 300, 6):
                        v = store.vehicle(f"AB{n:05d}")
                        assert v["available"] and (v.get("none") or v["make"] == "Toyota"), v
                except Exception as e:           # noqa: BLE001 - collect every failure from the threads
                    errors.append(repr(e))

            threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errors, [])
