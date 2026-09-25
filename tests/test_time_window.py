"""Tests for the manifest ``--since``/``--until`` time window.

``parse_date_arg`` keeps its naive, local-time behavior (maintainer ruling
Q-C3-1): the ``reportportal`` subcommands and the legacy archive lookup in
``task_resolver`` depend on it. The manifest selection paths resolve their
own timezone-aware UTC bounds instead. The characterization tests here pin
the parts of that split which must not move.
"""

import argparse
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from enge.report.__main__ import _handle_list
from enge.utils import parse_date_arg, resolve_utc_window
from enge.utils.arg_parser import build_parser
from enge.utils.manifest import ManifestReader, ManifestWriter
from enge.utils.manifest_resolution import _build_find_kwargs
from enge.utils.ulid import generate_ulid


HAS_TZSET = hasattr(time, "tzset")

FROZEN_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


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


def _date_filter_help(parser):
    """Map ``{"--since": help, "--until": help}`` for one parser."""
    return {
        option: action.help
        for action in parser._actions
        for option in action.option_strings
        if option in ("--since", "--until")
    }


def _subparsers(parser):
    """Map ``{name: subparser}`` for *parser*'s subcommand action."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("parser has no subparsers")


class TestDateFilterHelpText(unittest.TestCase):
    """The UTC wording is scoped to the manifest subcommands only.

    The ``reportportal`` parsers keep the generic wording because they
    still filter in local time (ruling Q-C3-1). Nothing else in the suite
    fails when that split is collapsed: `test_cli_docs_drift` compares
    `docs/cli.md` against whatever the parser currently emits, so a flip
    plus a regeneration stays green.
    """

    MANIFEST_SUBCOMMANDS = ("report", "compare", "rerun", "cancel")
    RP_SUBCOMMANDS = ("finish", "enrich", "delete-stale")

    def setUp(self):
        self.top = _subparsers(build_parser())

    def test_manifest_subcommands_document_utc(self):
        for name in self.MANIFEST_SUBCOMMANDS:
            with self.subTest(subcommand=name):
                helps = _date_filter_help(self.top[name])

                self.assertIn("runs created on or after DATE, in UTC", helps["--since"])
                self.assertIn(
                    "runs created on or before DATE, in UTC", helps["--until"]
                )
                self.assertIn("00:00:00 UTC", helps["--since"])
                self.assertIn("23:59:59 UTC", helps["--until"])
                self.assertIn("exactly that long before now", helps["--until"])

    def test_reportportal_keeps_the_generic_local_time_wording(self):
        parsers = {"reportportal": self.top["reportportal"]}
        parsers.update(
            (name, sub)
            for name, sub in _subparsers(self.top["reportportal"]).items()
            if name in self.RP_SUBCOMMANDS
        )
        self.assertEqual(len(parsers), 1 + len(self.RP_SUBCOMMANDS))

        for name, parser in parsers.items():
            with self.subTest(subcommand=name):
                helps = _date_filter_help(parser)

                self.assertEqual(
                    helps["--since"],
                    "Only consider items from on or after DATE "
                    "(YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).",
                )
                self.assertEqual(
                    helps["--until"],
                    "Only consider items from on or before DATE "
                    "(YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).",
                )
                self.assertNotIn("UTC", helps["--since"])
                self.assertNotIn("UTC", helps["--until"])


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


class TestResolveUtcWindow(unittest.TestCase):
    """The helper resolves both bounds against an injected aware-UTC clock."""

    def test_relative_since_counts_back_from_now(self):
        cases = {
            "6h": datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc),
            "3d": datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
            "2w": datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
            "1m": datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            "1y": datetime(2025, 9, 25, 12, 0, tzinfo=timezone.utc),
        }
        for alias, expected in cases.items():
            with self.subTest(alias=alias):
                since, until = resolve_utc_window(alias, None, now=FROZEN_NOW)

                self.assertEqual(since, expected)
                self.assertIs(since.tzinfo, timezone.utc)
                self.assertIsNone(until)

    def test_relative_until_is_an_exact_instant_not_end_of_day(self):
        _, until = resolve_utc_window(None, "6h", now=FROZEN_NOW)

        self.assertEqual(until, datetime(2026, 9, 25, 6, 0, 0, tzinfo=timezone.utc))
        self.assertNotEqual(
            until, datetime(2026, 9, 25, 23, 59, 59, tzinfo=timezone.utc)
        )

    def test_absolute_since_is_start_of_the_utc_day(self):
        since, _ = resolve_utc_window("2026-09-20", None, now=FROZEN_NOW)

        self.assertEqual(since, datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc))

    def test_absolute_until_is_end_of_the_utc_day(self):
        _, until = resolve_utc_window(None, "2026-09-20", now=FROZEN_NOW)

        self.assertEqual(until, datetime(2026, 9, 20, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(until.microsecond, 0)

    def test_both_bounds_resolve_independently(self):
        since, until = resolve_utc_window("2026-09-20", "3d", now=FROZEN_NOW)

        self.assertEqual(since, datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(until, datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc))

    def test_absent_bounds_stay_absent(self):
        self.assertEqual(resolve_utc_window(None, None, now=FROZEN_NOW), (None, None))

    def test_default_clock_is_aware_utc(self):
        since, _ = resolve_utc_window("6h", None)
        expected = datetime.now(timezone.utc) - timedelta(hours=6)

        self.assertIsNotNone(since.tzinfo)
        self.assertLess(abs((since - expected).total_seconds()), 60)

    def test_unparseable_value_raises_value_error(self):
        with self.assertRaises(ValueError):
            resolve_utc_window("garbage", None, now=FROZEN_NOW)
        with self.assertRaises(ValueError):
            resolve_utc_window(None, "garbage", now=FROZEN_NOW)

    def test_naive_injected_clock_raises_value_error(self):
        with self.assertRaises(ValueError):
            resolve_utc_window("6h", None, now=datetime(2026, 9, 25, 12, 0))


@unittest.skipUnless(HAS_TZSET, "requires time.tzset()")
class TestRelativeBoundsIgnoreHostTimezone(unittest.TestCase):
    """Relative aliases count back from the current UTC instant, not local."""

    ZONES = ("Europe/Prague", "America/Los_Angeles")

    def _assert_six_hours_ago(self, value):
        expected = datetime.now(timezone.utc) - timedelta(hours=6)

        self.assertIsNotNone(value.tzinfo)
        self.assertLess(abs((value - expected).total_seconds()), 60)

    def test_build_find_kwargs_since_is_six_hours_before_now_utc(self):
        for zone in self.ZONES:
            with self.subTest(zone=zone), _tz(zone):
                self.assertNotEqual(time.localtime().tm_gmtoff, 0)

                kwargs = _build_find_kwargs([], [], [], [], "6h", None)

                self._assert_six_hours_ago(kwargs["since"])

    def test_build_find_kwargs_until_is_six_hours_before_now_utc(self):
        for zone in self.ZONES:
            with self.subTest(zone=zone), _tz(zone):
                self.assertNotEqual(time.localtime().tm_gmtoff, 0)

                kwargs = _build_find_kwargs([], [], [], [], None, "6h")

                self._assert_six_hours_ago(kwargs["until"])

    def _handle_list_kwargs(self, **cli_overrides):
        cli = {
            "output_format": "terminal",
            "filter_set": None,
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
            "since": None,
            "until": None,
            "list": True,
            "run": None,
            "file": None,
            "input": None,
            "get_tag": [],
        }
        cli.update(cli_overrides)
        ctx = SimpleNamespace(
            manifest_runs_dir="/nonexistent/runs",
            manifest_latest="/nonexistent/latest",
            archive_tasks_latest="/nonexistent/legacy",
            archive_tasks_default="/nonexistent/legacy_archive",
            cli_args=SimpleNamespace(**cli),
        )
        with patch(
            "enge.report.__main__.ManifestReader.find_runs", return_value=[]
        ) as find_runs:
            _handle_list(ctx)
        return find_runs.call_args.kwargs

    def test_handle_list_since_is_six_hours_before_now_utc(self):
        for zone in self.ZONES:
            with self.subTest(zone=zone), _tz(zone):
                self.assertNotEqual(time.localtime().tm_gmtoff, 0)

                kwargs = self._handle_list_kwargs(since="6h")

                self._assert_six_hours_ago(kwargs["since"])

    def test_handle_list_until_is_six_hours_before_now_utc(self):
        for zone in self.ZONES:
            with self.subTest(zone=zone), _tz(zone):
                self.assertNotEqual(time.localtime().tm_gmtoff, 0)

                kwargs = self._handle_list_kwargs(until="6h")

                self._assert_six_hours_ago(kwargs["until"])


@unittest.skipUnless(HAS_TZSET, "requires time.tzset()")
class TestRelativeWindowSelectsRuns(unittest.TestCase):
    """End-to-end: `--since 6h` selects the same runs in every host zone."""

    ZONES = ("Europe/Prague", "America/Los_Angeles")

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, hours_ago):
        run_id = generate_ulid()
        writer = ManifestWriter(run_id=run_id, command="test", argv=["enge", "test"])
        created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        writer.created_at = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        writer.add_request("uuid-dummy", tier="tier0", arch="x86_64")
        writer.flush(self.runs, self.latest)
        return run_id

    def test_six_hour_window_selects_only_the_recent_run(self):
        recent = self._write(hours_ago=5)
        self._write(hours_ago=7)

        for zone in self.ZONES:
            with self.subTest(zone=zone), _tz(zone):
                self.assertNotEqual(time.localtime().tm_gmtoff, 0)

                kwargs = _build_find_kwargs([], [], [], [], "6h", None)
                found = ManifestReader.find_runs(self.runs, **kwargs)

                self.assertEqual([r["run_id"] for r in found], [recent])
