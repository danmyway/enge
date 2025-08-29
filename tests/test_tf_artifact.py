#!/usr/bin/env python3
import unittest
from unittest.mock import MagicMock

from enge.utils.tf_artifact import BrewRef
from enge.utils.errors import ValidationError, ConfigurationError


class TestBrewRef(unittest.TestCase):
    def test_parse_package_name_from_nvr(self):
        self.assertEqual(
            BrewRef._parse_package_name_from_nvr("leapp-0.16.0-1.el8"), "leapp"
        )
        self.assertEqual(
            BrewRef._parse_package_name_from_nvr("python3-leapp-0.16.0-1.el8"),
            "python3-leapp",
        )
        self.assertIsNone(BrewRef._parse_package_name_from_nvr("invalid"))

    def test_validate_reference_format(self):
        self.assertEqual(BrewRef._validate_reference_format("123")[1], "task_id")
        self.assertEqual(
            BrewRef._validate_reference_format("leapp-0.16.0-1.el8")[1], "nvr"
        )
        self.assertEqual(BrewRef._validate_reference_format("")[1], "invalid")

    def test_get_info_requires_package_for_task_id(self):
        br = BrewRef(["123"])
        options = MagicMock()
        options.brew_api = {"session_url": "http://example"}
        options.source_spec = {"compose_name": "RHEL-9.7.0-Nightly"}
        # when using task id and package empty -> ConfigurationError
        with self.assertRaises(ConfigurationError):
            br.get_info("", ["123"], ["RHEL-9.7.0-Nightly"], options)


if __name__ == "__main__":
    unittest.main()
