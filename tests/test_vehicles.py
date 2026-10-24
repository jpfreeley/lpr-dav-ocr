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


if __name__ == "__main__":
    unittest.main()
