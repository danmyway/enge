"""Tests for ReportPortal launch startTime normalization."""

import unittest
from datetime import datetime, timezone

from enge.reportportal.operations import _launch_start_time_ms


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


if __name__ == "__main__":
    unittest.main()
