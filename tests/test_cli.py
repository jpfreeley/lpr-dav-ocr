import io
import unittest
from contextlib import redirect_stdout

from lpr_dav_ocr import cli


def run(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_usage_lists_every_command(self):
        code, text = run()
        self.assertEqual(code, 0)
        for name in ("process", "second-pass", "review", "score", "watch", "watchlist", "demo", "setup", "config", "where"):
            self.assertIn(name, text)

    def test_unknown_command(self):
        code, _ = run("frobnicate")
        self.assertEqual(code, 2)

    def test_config_path_and_show(self):
        code, text = run("config", "path")
        self.assertEqual(code, 0)
        self.assertTrue(text.strip().endswith(".toml"))
        code, text = run("config", "show")
        self.assertIn('"tilt_degrees"', text)

    def test_where_prints_a_frames_folder(self):
        code, text = run("where", "/nowhere/CAM/2026-01-01")
        self.assertEqual(code, 0)
        self.assertTrue(text.strip().endswith("frames/2026-01-01"))


if __name__ == "__main__":
    unittest.main()
