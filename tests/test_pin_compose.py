#!/usr/bin/env python3
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from enge.dispatch import pin_compose
from enge.dispatch.pin_compose import _pin_compose_with_fallback, repin_compose
from enge.utils.errors import ValidationError

from tests._helpers import captured_logs, matching


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


AGED_OUT_COMPOSE = "RHEL-9.9.0-20260101.0"
LISTED_COMPOSE = "RHEL-9.9.0-20260629.0"
NIGHTLY_TARGET = "RHEL-9.9.0-20260701.0"
COMPOSES_URL = "http://fake"


def _composes_data():
    """Composes payload in which AGED_OUT_COMPOSE is no longer listed.

    LISTED_COMPOSE is a dated compose Testing Farm still offers, so the two
    dated shapes -- aged out and still current -- can be told apart.
    """
    return {
        "SYMBOLIC_COMPOSES": [{"RHEL-9.9.0-Nightly": NIGHTLY_TARGET}],
        "COMPOSES": ["RHEL-9.9.0-Nightly", LISTED_COMPOSE],
        "OTHER_COMPOSES": [],
    }


class TestRepinComposeRerunFallback(unittest.TestCase):
    """The rerun age-out fallback as reached through repin_compose().

    _repin_cache is a process-global, so every test here empties it both
    before and after itself; a leaked entry would let one test answer
    another test's call without touching the code under test.
    """

    def setUp(self):
        pin_compose._repin_cache.clear()
        self.addCleanup(pin_compose._repin_cache.clear)

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_rerun_flag_falls_back_to_the_latest_nightly(self, mock_fetch):
        mock_fetch.return_value = _composes_data()

        with captured_logs("enge.dispatch.pin_compose", level=logging.DEBUG) as records:
            result = repin_compose(AGED_OUT_COMPOSE, COMPOSES_URL, rerun=True)

        self.assertEqual(result, NIGHTLY_TARGET)
        self.assertEqual(
            len(
                matching(
                    records,
                    "falling back to the latest Nightly",
                    level=logging.WARNING,
                )
            ),
            1,
        )
        self.assertEqual(
            len(matching(records, "Compose re-pinned", level=logging.WARNING)),
            1,
        )

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_without_the_flag_an_aged_out_compose_still_raises(self, mock_fetch):
        mock_fetch.return_value = _composes_data()

        with self.assertRaises(ValidationError):
            repin_compose(AGED_OUT_COMPOSE, COMPOSES_URL)

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_still_listed_dated_compose_is_untouched_either_way(self, mock_fetch):
        mock_fetch.return_value = _composes_data()

        self.assertEqual(
            repin_compose(LISTED_COMPOSE, COMPOSES_URL, rerun=True), LISTED_COMPOSE
        )
        self.assertEqual(repin_compose(LISTED_COMPOSE, COMPOSES_URL), LISTED_COMPOSE)

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_symbolic_compose_resolves_either_way(self, mock_fetch):
        mock_fetch.return_value = _composes_data()

        self.assertEqual(
            repin_compose("RHEL-9.9.0-Nightly", COMPOSES_URL, rerun=True),
            NIGHTLY_TARGET,
        )
        self.assertEqual(
            repin_compose("RHEL-9.9.0-Nightly", COMPOSES_URL), NIGHTLY_TARGET
        )

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_rerun_flag_raises_when_there_is_no_nightly_either(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [],
            "COMPOSES": [LISTED_COMPOSE],
            "OTHER_COMPOSES": [],
        }

        with self.assertRaises(ValidationError):
            repin_compose(AGED_OUT_COMPOSE, COMPOSES_URL, rerun=True)

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_rerun_flag_falls_back_through_the_no_micro_nightly(self, mock_fetch):
        mock_fetch.return_value = {
            "SYMBOLIC_COMPOSES": [{"RHEL-10.1-Nightly": "RHEL-10.1-20260701.0"}],
            "COMPOSES": ["RHEL-10.1-Nightly"],
            "OTHER_COMPOSES": [],
        }

        self.assertEqual(
            repin_compose("RHEL-10.1-20260101.0", COMPOSES_URL, rerun=True),
            "RHEL-10.1-20260701.0",
        )

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_a_cached_rerun_fallback_is_not_served_to_a_non_rerun_call(
        self, mock_fetch
    ):
        mock_fetch.return_value = _composes_data()

        self.assertEqual(
            repin_compose(AGED_OUT_COMPOSE, COMPOSES_URL, rerun=True), NIGHTLY_TARGET
        )
        with self.assertRaises(ValidationError):
            repin_compose(AGED_OUT_COMPOSE, COMPOSES_URL)

    @patch("enge.dispatch.pin_compose.fetch_data_from_url")
    def test_cli_args_rerun_detection_is_unchanged(self, mock_fetch):
        mock_fetch.return_value = _composes_data()

        self.assertEqual(
            repin_compose(
                AGED_OUT_COMPOSE,
                COMPOSES_URL,
                cli_args=SimpleNamespace(action="rerun"),
            ),
            NIGHTLY_TARGET,
        )

        # The cache key is pinned by
        # test_a_cached_rerun_fallback_is_not_served_to_a_non_rerun_call; empty
        # the cache here so the non-rerun half resolves for real instead of
        # reading back what the rerun half just stored.
        pin_compose._repin_cache.clear()

        with self.assertRaises(ValidationError):
            repin_compose(
                AGED_OUT_COMPOSE,
                COMPOSES_URL,
                cli_args=SimpleNamespace(action="test"),
            )


if __name__ == "__main__":
    unittest.main()
