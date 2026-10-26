import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lpr_dav_ocr import selfcheck as SC

T0 = 1_800_000_000.0
REPORT = """# OCR accuracy vs your labels

## Auto-accept rule (conf >= 0.99, both passes agree, shape ok)

- Among the 461 you labeled that match the rule: real plate right 410/421 = 97.4%; 40 were not plates

## Recently verified plates (last 30 days either side)

- Read equals a plate you verified: 384. Right 376/384 = 97.9%; 3 needed a fix

## Auto-reject rule (aspect above 3.5)

- Labeled reads above the threshold: 185. Not a plate 161, obstructed or can't tell 13, **real plates 11** (these would have been hidden from review)
"""


def make_root(text=REPORT):
    d = tempfile.TemporaryDirectory()
    (Path(d.name) / "reports").mkdir()
    (Path(d.name) / "reports" / "accuracy.md").write_text(text)
    return d


class RuleTests(unittest.TestCase):
    def test_parse_and_floors(self):
        d = make_root()
        got = {r["key"]: (r["right"], r["total"]) for r in SC.rule_precision(d.name)}
        self.assertEqual(got, {"accept": (410, 421), "approve": (376, 384), "reject": (174, 185)})
        f = SC.rule_findings(d.name)
        self.assertEqual([x["key"] for x in f], ["rule-reject"])         # 94.1% is under the 97% floor; the others pass
        d.cleanup()

    def test_few_labels_say_nothing(self):
        d = make_root(REPORT.replace("185. Not a plate 161, obstructed or can't tell 13, **real plates 11**", "20. Not a plate 5, obstructed or can't tell 0, **real plates 15**"))
        self.assertEqual(SC.rule_findings(d.name), [])
        d.cleanup()

    def test_no_report(self):
        self.assertEqual(SC.rule_findings("/nonexistent"), [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.d = make_root()
        self.home = tempfile.TemporaryDirectory()
        p = mock.patch.object(SC.W, "notify")
        self.notify = p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.d.cleanup()
        self.home.cleanup()

    def health(self, items):
        return mock.patch.object(SC, "health_findings", return_value=items)

    def test_silent_when_all_is_well(self):
        good = make_root(REPORT.replace("**real plates 11**", "**real plates 1**"))
        with self.health([]):
            self.assertEqual(SC.run(self.home.name, good.name, rules=True, now=T0), [])
        self.notify.assert_not_called()
        good.cleanup()

    def test_alert_once_then_repeat_after_a_day_and_again_after_clearing(self):
        bad = [dict(key="disk-low", level="critical", text="Only 3 GB free on the data disk.")]
        with self.health(bad):
            self.assertEqual(len(SC.run(self.home.name, self.d.name, rules=False, now=T0)), 1)
            self.assertEqual(SC.run(self.home.name, self.d.name, rules=False, now=T0 + 3600), [])        # same problem, same day
            self.assertEqual(len(SC.run(self.home.name, self.d.name, rules=False, now=T0 + 90000)), 1)   # a day later
        self.assertEqual(self.notify.call_count, 2)
        self.assertEqual(self.notify.call_args.kwargs["priority"], 4)                                         # critical
        with self.health([]):
            SC.run(self.home.name, self.d.name, rules=False, now=T0 + 200000.0)                                    # it cleared
        with self.health(bad):
            self.assertEqual(len(SC.run(self.home.name, self.d.name, rules=False, now=T0 + 200100.0)), 1)         # and came back
        self.assertEqual(self.notify.call_count, 3)

    def test_rules_run_weekly_and_message_has_no_plate_text(self):
        with self.health([]):
            self.assertTrue(SC.due_rules(self.home.name, T0))
            SC.run(self.home.name, self.d.name, now=T0)
            self.assertFalse(SC.due_rules(self.home.name, T0 + 86400))
        msg = self.notify.call_args.args[1]
        self.assertIn("Auto-reject", msg)
        self.assertNotRegex(msg, r"[A-Z]{3}\d{4}")

    def test_dry_run_sends_nothing_and_lists_everything(self):
        with self.health([dict(key="overloaded", level="warning", text="The machine is overloaded (load 30).")]):
            got = SC.run(self.home.name, self.d.name, rules=True, send=False, now=T0)
        self.assertEqual({f["key"] for f in got}, {"overloaded", "rule-reject"})
        self.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
