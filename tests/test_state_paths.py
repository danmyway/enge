import os
import unittest
from pathlib import Path
from unittest.mock import patch

from enge.utils.state_paths import (
    resolve_latest_pointer,
    resolve_logs_dir,
    resolve_runs_dir,
)


class TestStatePaths(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            os.environ,
            {"XDG_DATA_HOME": "", "XDG_STATE_HOME": ""},
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_default_runs_dir(self):
        result = resolve_runs_dir({})
        self.assertEqual(result, Path.home() / ".local/share/enge/runs")

    def test_default_latest_pointer(self):
        result = resolve_latest_pointer({})
        self.assertEqual(result, Path.home() / ".local/state/enge/latest")

    def test_default_logs_dir(self):
        result = resolve_logs_dir({})
        self.assertEqual(result, Path.home() / ".local/state/enge/logs")

    def test_xdg_data_home_overrides_runs_dir(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/custom/data"}):
            result = resolve_runs_dir({})
        self.assertEqual(result, Path("/custom/data/enge/runs"))

    def test_xdg_state_home_overrides_latest(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/custom/state"}):
            result = resolve_latest_pointer({})
        self.assertEqual(result, Path("/custom/state/enge/latest"))

    def test_config_overrides_xdg(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg/data"}):
            config = {"common": {"manifest_runs_dir": "/explicit/runs"}}
            result = resolve_runs_dir(config)
        self.assertEqual(result, Path("/explicit/runs"))

    def test_config_overrides_latest(self):
        config = {"common": {"manifest_latest": "/explicit/latest"}}
        result = resolve_latest_pointer(config)
        self.assertEqual(result, Path("/explicit/latest"))

    def test_config_overrides_logs(self):
        config = {"common": {"logs_directory": "/explicit/logs"}}
        result = resolve_logs_dir(config)
        self.assertEqual(result, Path("/explicit/logs"))

    def test_empty_config_string_uses_xdg(self):
        config = {"common": {"manifest_runs_dir": ""}}
        result = resolve_runs_dir(config)
        self.assertEqual(result, Path.home() / ".local/share/enge/runs")


if __name__ == "__main__":
    unittest.main()
