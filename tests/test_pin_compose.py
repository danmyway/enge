#!/usr/bin/env python3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from enge.dispatch.pin_compose import _pin_compose_with_fallback


class TestPinCompose(unittest.TestCase):
    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_with_fallback_prefers_micro(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-9.7.0-Nightly": "RHEL-9.7.0-20240101.0"}],
            "COMPOSES": ["RHEL-9.7.0-Nightly"],
            "OTHER_COMPOSES": [],
        }
        res = _pin_compose_with_fallback(9, 7, "Nightly", "http://fake")
        self.assertEqual(res, "RHEL-9.7.0-20240101.0")

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_with_fallback_no_micro_uses_non_micro(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-10.1-Nightly": "RHEL-10.1-20250101.0"}],
            "COMPOSES": ["RHEL-10.1-Nightly"],
            "OTHER_COMPOSES": [],
        }
        res = _pin_compose_with_fallback(10, 1, "Nightly", "http://fake")
        self.assertEqual(res, "RHEL-10.1-20250101.0")

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_with_fallback_rerun_mode_uses_nightly_fallback(
        self, mock_fetch
    ):
        """Test that rerun mode falls back to Nightly when original compose not found"""
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-9.7.0-Nightly": "RHEL-9.7.0-20250101.0"}],
            "COMPOSES": ["RHEL-9.7.0-Nightly"],
            "OTHER_COMPOSES": [],
        }
        cli_args = SimpleNamespace(action="rerun")
        # Request a compose with suffix "UpdateBuildD-9.7-20240615.0" that doesn't exist
        res = _pin_compose_with_fallback(
            9, 7, "UpdateBuildD-9.7-20240615.0", "http://fake", cli_args=cli_args
        )
        # Should fall back to Nightly
        self.assertEqual(res, "RHEL-9.7.0-20250101.0")

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_pin_compose_with_fallback_non_rerun_mode_raises_on_missing(
        self, mock_fetch
    ):
        """Test that non-rerun mode raises ValidationError when compose not found"""
        from enge.utils.errors import ValidationError

        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-9.7.0-Nightly": "RHEL-9.7.0-20250101.0"}],
            "COMPOSES": ["RHEL-9.7.0-Nightly"],
            "OTHER_COMPOSES": [],
        }
        # No cli_args = not rerun mode
        with self.assertRaises(ValidationError):
            _pin_compose_with_fallback(
                9, 7, "UpdateBuildD-9.7-20240615.0", "http://fake"
            )


if __name__ == "__main__":
    unittest.main()
