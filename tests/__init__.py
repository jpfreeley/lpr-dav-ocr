"""Test setup. Runs before any application module is imported, so the application never sees a real
home folder, a real config file or a real camera share. All test data is synthetic."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="lpr-test-")
os.environ["LPR_HOME"] = os.path.join(_TMP, "home")
os.environ["LPR_CONFIG"] = os.path.join(_TMP, "no-such-config.toml")
os.environ["LPR_NO_FORCE_EXIT"] = "1"
os.environ.pop("LPR_CAMERA_DIR", None)
os.environ.pop("LPR_DATA_ROOT", None)
