"""Tests for ReportPortal launch startTime normalization."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from enge.reportportal.operations import _launch_start_time_ms
from enge.reportportal.__main__ import ReportPortalLaunch


class TestLaunchStartTimeMs(unittest.TestCase):
    def test_epoch_milliseconds_int(self):
        self.assertEqual(_launch_start_time_ms(1740986570878), 1740986570878)

    def test_epoch_milliseconds_string(self):
        self.assertEqual(_launch_start_time_ms("1740986570878"), 1740986570878)

    def test_iso_8601_utc_string(self):
        iso = "2026-03-03T08:42:50.878Z"
        expected = int(
            datetime(2026, 3, 3, 8, 42, 50, 878000, tzinfo=timezone.utc).timestamp()
            * 1000
        )
        self.assertEqual(_launch_start_time_ms(iso), expected)

    def test_none_for_unsupported_type(self):
        self.assertIsNone(_launch_start_time_ms(None))


_RP_CONFIG_STUB = SimpleNamespace(
    config={
        "reportportal": {
            "url": "http://rp.example.com",
            "token": "fake-token",
            "project": "test-project",
        }
    },
)


class TestGenerateLaunchPayloadExtraTags(unittest.TestCase):
    def _make_launch(self):
        with patch("enge.reportportal.__main__.parsed_opts", _RP_CONFIG_STUB):
            return ReportPortalLaunch()

    def test_extra_tags_appended_and_deduplicated(self):
        launch = self._make_launch()
        payload = launch.generate_launch_payload(
            name="RERUN~TEST~2026-06-11~tier0~x86_64",
            extra_tags=["rerun"],
        )
        self.assertIn("rerun", payload["tags"])
        self.assertIn("enge", payload["tags"])
        self.assertIn("automated", payload["tags"])
        self.assertEqual(len(payload["tags"]), len(set(payload["tags"])))

    def test_extra_tags_duplicate_not_added_twice(self):
        launch = self._make_launch()
        payload = launch.generate_launch_payload(
            name="RERUN~TEST~2026-06-11~tier0~x86_64",
            extra_tags=["enge", "rerun"],  # "enge" already in defaults
        )
        self.assertEqual(payload["tags"].count("enge"), 1)

    def test_no_extra_tags_unchanged(self):
        launch = self._make_launch()
        payload = launch.generate_launch_payload(name="TEST~LAUNCH")
        self.assertEqual(set(payload["tags"]), {"enge", "automated"})


if __name__ == "__main__":
    unittest.main()
