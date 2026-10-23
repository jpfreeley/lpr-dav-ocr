"""Configuration: defaults, a TOML file, then environment overrides.

Precedence (lowest to highest): built-in defaults, config file, environment, command-line flags.
The config file is looked up at $LPR_CONFIG, then ~/.config/lpr-dav-ocr/config.toml. If neither
exists the defaults apply. `config/config.example.toml` in the repository documents every key.

Nothing in this module touches the network or the camera share.
"""
import copy
import os
import tomllib
from pathlib import Path

DEFAULTS = {
    "paths": {
        # Local, per-user state: OCR models, watchlist, notification topic, logs, caches.
        # Keep it on a local disk. It holds secrets (the notification topic) and the watchlist.
        "home": "~/.lpr-ocr",
        # Folder that contains one YYYY-MM-DD folder per day of .dav clips.
        "camera_dir": "",
        # Where frames, indexes, labels and reports are written. Empty means: a folder named
        # db_<camera folder name> next to the camera folder if it exists, else ~/lpr-data.
        "data_root": "",
    },
    "camera": {
        "frame_width": 1920,
        "frame_height": 1080,
        "fps": 30,
        # Rotate frames counter-clockwise by this many degrees before OCR (0 disables). For a fixed
        # camera the plates share a tilt; measure it on your own footage (see docs/configuration.md).
        "tilt_degrees": 20.0,
        # Burned-in overlays (timestamp, channel label) as fractions of the frame: x0, y0, x1, y1.
        # They are blanked before OCR and ignored by motion detection.
        "overlays": [[0.66, 0.02, 0.97, 0.10], [0.0, 0.88, 0.12, 1.0]],
    },
    "pipeline": {
        "det_side": 640,
        "min_conf": 0.80,
        "plate_regex": "[A-Z0-9]{5,8}",
        "max_segments": 6,
        "max_frames": 16,
        "stride": 2,
        "workers": 3,
        "stop_conf": 0.98,
        "stop_hits": 2,
        "min_frames": 4,
        "fallback_segments": 2,
        "fallback_frames": 6,
        "min_motion": 3.0,
        "motion_k": 6.0,
        "pad": 2,
    },
    "review": {
        "port": 8770,
        # A plate with confidence at or above this, both passes agreeing, and an OK shape skips the
        # review queue. Validate on your own labels before relying on it (docs/accuracy.md).
        "auto_accept_conf": 0.99,
        "recent_days": 30,
        # A read whose text region is wider than this (length / height) is auto-rejected as not plate-shaped. US plates
        # are about 2. 0 switches it off. Validate on your own labels (lpr score prints the precision).
        "reject_aspect": 3.5,
    },
    "watch": {
        "poll_seconds": 60,
        "lookback_minutes": 90,
        "settle_seconds": 45,
        "cooldown_seconds": 600,
        "workers": 1,
    },
    "ntfy": {
        "enabled": True,
        "server": "https://ntfy.sh/",
        # File holding the topic name. Created by `lpr watch --init`. Treat it as a password.
        "topic_file": "{home}/ntfy_topic",
    },
}


def _merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def config_path(env=None):
    env = os.environ if env is None else env
    if env.get("LPR_CONFIG"):
        return Path(env["LPR_CONFIG"]).expanduser()
    return Path.home() / ".config" / "lpr-dav-ocr" / "config.toml"


def load(path=None, env=None):
    """Return the effective configuration as a nested dict, with paths expanded."""
    env = os.environ if env is None else env
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else config_path(env)
    if p.is_file():
        with p.open("rb") as f:
            _merge(cfg, tomllib.load(f))
    # environment overrides for the settings people most often need to change
    if env.get("LPR_HOME"):
        cfg["paths"]["home"] = env["LPR_HOME"]
    if env.get("LPR_CAMERA_DIR"):
        cfg["paths"]["camera_dir"] = env["LPR_CAMERA_DIR"]
    if env.get("LPR_DATA_ROOT"):
        cfg["paths"]["data_root"] = env["LPR_DATA_ROOT"]
    home = Path(cfg["paths"]["home"]).expanduser()
    cfg["paths"]["home"] = str(home)
    for k in ("camera_dir", "data_root"):
        if cfg["paths"][k]:
            cfg["paths"][k] = str(Path(cfg["paths"][k]).expanduser())
    cfg["ntfy"]["topic_file"] = str(Path(cfg["ntfy"]["topic_file"].replace("{home}", str(home))).expanduser())
    return cfg


def home(cfg=None):
    return Path((cfg or load())["paths"]["home"])


def data_root(cfg=None, clip=None):
    """Resolve the data root. `clip` (a path to a .dav inside <camera>/<date>/) is used to find the
    sibling db_<camera> folder when neither data_root nor camera_dir is configured."""
    cfg = cfg or load()
    if cfg["paths"]["data_root"]:
        return Path(cfg["paths"]["data_root"])
    cam = Path(cfg["paths"]["camera_dir"]) if cfg["paths"]["camera_dir"] else (Path(clip).resolve().parent.parent if clip else None)
    if cam is not None:
        sibling = cam.parent / f"db_{cam.name}"
        if sibling.is_dir():
            return sibling
    return Path.home() / "lpr-data"


def frames_dir(cfg, day, clip=None):
    """<data_root>/frames/<day>."""
    return data_root(cfg, clip) / "frames" / day
