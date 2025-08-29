#!/usr/bin/env python3
import unittest
from unittest.mock import patch

from enge.dispatch.pin_compose import _pin_compose_with_fallback, _pin_compose
from enge.utils.errors import ValidationError, NetworkError


class TestPinCompose(unittest.TestCase):
    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_with_fallback_prefers_micro(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-9.7.0-Nightly": "RHEL-9.7.0-20240101.0"}],
            "COMPOSES": ["RHEL-9.7.0-Nightly"],
            "OTHER_COMPOSES": [],
        }
        res = _pin_compose_with_fallback(9, 7, "http://fake")
        self.assertEqual(res, "RHEL-9.7.0-20240101.0")

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_with_fallback_no_micro_uses_non_micro(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-10.1-Nightly": "RHEL-10.1-20250101.0"}],
            "COMPOSES": ["RHEL-10.1-Nightly"],
            "OTHER_COMPOSES": [],
        }
        res = _pin_compose_with_fallback(10, 1, "http://fake")
        self.assertEqual(res, "RHEL-10.1-20250101.0")

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_validates_name(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-9.7.0-Nightly": "RHEL-9.7.0-20240101.0"}],
            "COMPOSES": ["RHEL-9.7.0-Nightly"],
            "OTHER_COMPOSES": [],
        }
        res = _pin_compose("RHEL-9.7.0-Nightly", "http://fake")
        self.assertEqual(res, "RHEL-9.7.0-Nightly")


if __name__ == "__main__":
    unittest.main()
