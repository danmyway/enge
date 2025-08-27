#!/usr/bin/env python3
import unittest

from enge.utils.config_parser import load_default_config, load_config
from enge.utils.errors import ConfigurationError


class TestConfigParser(unittest.TestCase):
    def test_load_default_config_returns_dict(self):
        cfg = load_default_config()
        self.assertIsInstance(cfg, dict)
        # should contain common section per default_config.toml
        self.assertIn("common", cfg)

    def test_load_config_no_paths_raises(self):
        with self.assertRaises(ConfigurationError):
            load_config(paths=[])


if __name__ == "__main__":
    unittest.main()
