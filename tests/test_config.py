import tempfile
import unittest
from pathlib import Path

from lpr_dav_ocr import config


class ConfigTests(unittest.TestCase):
    def test_defaults_when_no_file(self):
        cfg = config.load(path="/nonexistent/config.toml", env={})
        self.assertEqual(cfg["review"]["port"], 8770)
        self.assertEqual(cfg["camera"]["tilt_degrees"], 20.0)
        self.assertTrue(Path(cfg["paths"]["home"]).is_absolute())
        self.assertTrue(cfg["ntfy"]["topic_file"].endswith("ntfy_topic"))

    def test_file_overrides_one_key_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text("[review]\nport = 9999\n\n[camera]\ntilt_degrees = 0\n")
            cfg = config.load(path=p, env={})
        self.assertEqual(cfg["review"]["port"], 9999)
        self.assertEqual(cfg["review"]["auto_accept_conf"], 0.99)       # untouched
        self.assertEqual(cfg["camera"]["tilt_degrees"], 0)
        self.assertEqual(cfg["camera"]["fps"], 30)                       # untouched

    def test_environment_beats_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text('[paths]\ncamera_dir = "/from/file"\n')
            cfg = config.load(path=p, env={"LPR_CAMERA_DIR": "/from/env"})
        self.assertEqual(cfg["paths"]["camera_dir"], "/from/env")

    def test_topic_file_follows_home(self):
        cfg = config.load(path="/nonexistent", env={"LPR_HOME": "/somewhere/state"})
        self.assertEqual(cfg["ntfy"]["topic_file"], "/somewhere/state/ntfy_topic")

    def test_defaults_are_not_mutated_by_load(self):
        config.load(path="/nonexistent", env={"LPR_CAMERA_DIR": "/x"})
        self.assertEqual(config.DEFAULTS["paths"]["camera_dir"], "")

    def test_data_root_explicit(self):
        cfg = config.load(path="/nonexistent", env={"LPR_DATA_ROOT": "/data/here"})
        self.assertEqual(config.data_root(cfg), Path("/data/here"))

    def test_data_root_is_sibling_of_camera_folder_when_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            cam = Path(d) / "CAM1"
            (Path(d) / "db_CAM1").mkdir()
            cam.mkdir()
            cfg = config.load(path="/nonexistent", env={"LPR_CAMERA_DIR": str(cam)})
            self.assertEqual(config.data_root(cfg), Path(d) / "db_CAM1")
            self.assertEqual(config.frames_dir(cfg, "2026-01-01"), Path(d) / "db_CAM1" / "frames" / "2026-01-01")

    def test_data_root_found_from_a_clip_path(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "CAM1" / "2026-01-01").mkdir(parents=True)
            (Path(d) / "db_CAM1").mkdir()
            cfg = config.load(path="/nonexistent", env={})
            clip = Path(d) / "CAM1" / "2026-01-01" / "x.dav"
            self.assertEqual(config.data_root(cfg, clip).resolve(), (Path(d) / "db_CAM1").resolve())

    def test_data_root_falls_back_to_home_folder(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(path="/nonexistent", env={"LPR_CAMERA_DIR": str(Path(d) / "nope")})
            self.assertEqual(config.data_root(cfg).name, "lpr-data")


if __name__ == "__main__":
    unittest.main()
