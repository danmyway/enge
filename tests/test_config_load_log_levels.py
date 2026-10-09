"""Config-load success lines are DEBUG, not INFO (DX-12 partial).

Two lines announce a *successful* config load and fired at INFO on every
invocation, so they landed in every CI log at default verbosity:

* ``Successfully loaded configuration from: {path}``
* ``Using user-defined default configuration at {override_path}``

They are diagnostics for someone debugging config discovery, so they sit at
DEBUG. The negative tests are written premise-first: each one first proves the
line IS emitted (at any level) and only then asserts it is absent at INFO, so
deleting the line cannot make them pass vacuously.
"""

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enge.utils.config_parser import load_config
from tests._helpers import captured_logs, matching

LOADED_NEEDLE = "Successfully loaded configuration from"
OVERRIDE_NEEDLE = "Using user-defined default configuration at"


class TestConfigLoadLogLevels(unittest.TestCase):
    def setUp(self):
        self._enge_logger = logging.getLogger("enge")
        self._saved_level = self._enge_logger.level
        self._saved_handlers = list(self._enge_logger.handlers)
        self.addCleanup(self._restore_logger)

    def _restore_logger(self):
        self._enge_logger.setLevel(self._saved_level)
        self._enge_logger.handlers[:] = self._saved_handlers

    def _write(self, directory, filename, content):
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return path

    def _load(self, user_cfg_path, level):
        """Run the real load_config; return the `enge` records seen at `level`."""
        with (
            patch(
                "enge.utils.config_parser._load_bundled_config",
                return_value={"version": "1.0.0"},
            ),
            patch("enge.utils.config_parser.SYSTEM_CONFIG_PATHS", new=()),
            patch("enge.utils.config_parser.USER_CONFIG_PATHS", new=()),
            captured_logs("enge", level) as records,
        ):
            load_config([str(user_cfg_path)])
        return records

    def _plain_user_config(self, td):
        return self._write(td, "user.toml", '[common]\nlogs_directory = "/tmp/x"\n')

    def _override_user_config(self, td):
        """A user config whose [common].default_config_path is a readable file."""
        override = self._write(
            td, "override.toml", 'version = "1.0.0"\n[common]\nlogs_directory = "/y"\n'
        )
        return self._write(
            td, "user.toml", f'[common]\ndefault_config_path = "{override}"\n'
        )

    def test_user_config_loaded_line_is_debug(self):
        with tempfile.TemporaryDirectory() as td:
            records = self._load(self._plain_user_config(td), logging.DEBUG)
        self.assertEqual(
            len(matching(records, LOADED_NEEDLE, level=None)),
            1,
            "premise: the line must be emitted exactly once at some level",
        )
        self.assertEqual(len(matching(records, LOADED_NEEDLE, level=logging.DEBUG)), 1)

    def test_user_config_loaded_line_absent_at_info(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._plain_user_config(td)
            at_debug = self._load(cfg, logging.DEBUG)
            self.assertTrue(
                matching(at_debug, LOADED_NEEDLE, level=None),
                "premise: the line must exist before its absence means anything",
            )
            at_info = self._load(cfg, logging.INFO)
        self.assertEqual(matching(at_info, LOADED_NEEDLE, level=None), [])

    def test_default_override_line_is_debug(self):
        with tempfile.TemporaryDirectory() as td:
            records = self._load(self._override_user_config(td), logging.DEBUG)
        self.assertEqual(
            len(matching(records, OVERRIDE_NEEDLE, level=None)),
            1,
            "premise: the line must be emitted exactly once at some level",
        )
        self.assertEqual(
            len(matching(records, OVERRIDE_NEEDLE, level=logging.DEBUG)), 1
        )

    def test_default_override_line_absent_at_info(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._override_user_config(td)
            at_debug = self._load(cfg, logging.DEBUG)
            self.assertTrue(
                matching(at_debug, OVERRIDE_NEEDLE, level=None),
                "premise: the line must exist before its absence means anything",
            )
            at_info = self._load(cfg, logging.INFO)
        self.assertEqual(matching(at_info, OVERRIDE_NEEDLE, level=None), [])


if __name__ == "__main__":
    unittest.main()
