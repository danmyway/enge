"""Tests for the manifest ``--since``/``--until`` time window.

``parse_date_arg`` keeps its naive, local-time behavior (maintainer ruling
Q-C3-1): the ``reportportal`` subcommands and the legacy archive lookup in
``task_resolver`` depend on it. The manifest selection paths resolve their
own timezone-aware UTC bounds instead. The characterization tests here pin
the parts of that split which must not move.
"""

import os
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from enge.utils import parse_date_arg
from enge.utils.manifest_resolution import _build_find_kwargs


HAS_TZSET = hasattr(time, "tzset")


@contextmanager
def _tz(name):
    """Temporarily run the process under the ``name`` timezone.

    Verifies the switch actually took effect before handing control to the
    test: a silently-failed switch would make every assertion inside
    vacuous. The per-test ``tm_gmtoff != 0`` guards cannot catch that on
    their own, because a host that is already on a non-UTC zone keeps a
    non-zero offset whether the switch worked or not.

    Restores the previous ``TZ`` (including its absence) on exit.
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        expected = datetime.now(ZoneInfo(name)).utcoffset()
        actual = timedelta(seconds=time.localtime().tm_gmtoff)
        if actual != expected:
            raise AssertionError(
                f"TZ switch to {name} did not take effect "
                f"(offset {actual}, expected {expected})"
            )
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


class TestParseDateArgCharacterization(unittest.TestCase):
    """`parse_date_arg` is frozen: these pin what must not change."""

    def test_absolute_date_is_naive_midnight(self):
        result = parse_date_arg("2026-09-20")

        self.assertIsNone(result.tzinfo)
        self.assertEqual(result, datetime(2026, 9, 20))

    def test_relative_alias_is_naive_local(self):
        expected = datetime.now() - timedelta(hours=6)

        result = parse_date_arg("6h")

        self.assertIsNone(result.tzinfo)
        self.assertLess(abs((result - expected).total_seconds()), 60)


@unittest.skipUnless(HAS_TZSET, "requires time.tzset()")
class TestAbsoluteBoundsCharacterization(unittest.TestCase):
    """Absolute dates mean the UTC calendar day, on every host (Q-C3-2)."""

    def test_absolute_until_is_end_of_utc_day_under_prague(self):
        with _tz("Europe/Prague"):
            self.assertNotEqual(time.localtime().tm_gmtoff, 0)

            kwargs = _build_find_kwargs([], [], [], [], None, "2026-09-20")

        self.assertEqual(
            kwargs["until"],
            datetime(2026, 9, 20, 23, 59, 59, tzinfo=timezone.utc),
        )

    def test_absolute_since_is_start_of_utc_day_under_prague(self):
        with _tz("Europe/Prague"):
            self.assertNotEqual(time.localtime().tm_gmtoff, 0)

            kwargs = _build_find_kwargs([], [], [], [], "2026-09-20", None)

        self.assertEqual(
            kwargs["since"],
            datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc),
        )
