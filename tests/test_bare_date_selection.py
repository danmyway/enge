"""Bare --since/--until are ordinary manifest selectors on every command.

Before the legacy-archive retirement, a date flag with no other manifest
selector was routed to ``~/.enge/jobs_archive/`` filename-timestamp
matching: it raised ``ValidationError("Archive path does not exist")`` on
a host without that directory, and on a host *with* one it returned
whatever files the local-time window matched -- never a manifest.
``resolve_manifests_for_invocation`` mirrored that by returning [] for
bare dates, so ``compare`` warned and hit its comparability floor.

Q-L30'e (2026-09-29): one uniform rule -- bare date flags select
manifests on every command that accepts them (report, compare, rerun,
cancel, and reportportal's task path).  Q-L30'f: a bare-date selection
matching no runs raises ``ValidationError`` naming the flags, exactly
like every other selector, owned solely by
``manifest_resolution.select_runs``.

The window is UTC (``resolve_utc_window``), and a relative ``--until``
is an exact instant -- no end-of-day rounding, which the retired legacy
leg did apply.  When ``-i``/``-f`` supply tasks, date flags are ignored
(README's "input is not filtered" rule, matching today's ``-i X --run Y``).
"""

import logging
import os
import tempfile
import unittest
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._helpers import captured_logs, make_app_context, matching
from enge.utils.errors import ValidationError
from enge.utils.manifest import ManifestWriter
from enge.utils.manifest_resolution import resolve_manifests_for_invocation
from enge.utils.task_resolver import parse_tasks, parse_tasks_with_map
from enge.utils.ulid import generate_ulid

API_ENDPOINT = "https://api.example.com"


def _make_ctx(runs_dir, latest, **cli_overrides):
    """A runtime context shaped like `tests/test_cancel_manifest.py`'s.

    `archive_tasks_latest`/`archive_tasks_default` are RETIRED attributes.
    They point nowhere, as on a host that never had a legacy archive, and
    are read by nothing after this branch; they are kept here only so the
    pre-retirement source fails these tests on the behaviour under test
    rather than on `AttributeError`.  B7/B8 override them with real paths
    to prove those paths are ignored.
    """
    cli = {
        "action": "report",
        "file": None,
        "input": None,
        "run": None,
        "filter_set": None,
        "filter_tier": None,
        "filter_arch": None,
        "filter_tag": None,
        "since": None,
        "until": None,
        "dryrun": False,
        "debug": False,
        "verbose": 0,
    }
    cli.update(cli_overrides)
    return SimpleNamespace(
        manifest_runs_dir=str(runs_dir),
        manifest_latest=str(latest),
        archive_tasks_latest="/nonexistent/legacy",
        archive_tasks_default="/nonexistent/legacy_archive",
        cli_args=SimpleNamespace(**cli),
        testing_farm_endpoint=SimpleNamespace(
            api_endpoint_url=API_ENDPOINT,
            log_artifact_baseurl="https://logs.example.com",
        ),
    )


def _url(task_id):
    return os.path.join(API_ENDPOINT, task_id)


class _BareDateFixture(unittest.TestCase):
    """Two runs: R5 (5 h old, task U5) and R7 (7 h old, task U7)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

        self.u7 = str(uuid_mod.uuid4())
        self.u5 = str(uuid_mod.uuid4())
        # Oldest first, so the latest pointer ends up on R5.
        self.r7 = self._write(hours_ago=7, task_id=self.u7)
        self.r5 = self._write(hours_ago=5, task_id=self.u5)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, hours_ago, task_id):
        run_id = generate_ulid()
        writer = ManifestWriter(run_id=run_id, command="test", argv=["enge", "test"])
        created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        writer.created_at = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        writer.add_request(task_id, tier="tier0", arch="x86_64")
        writer.flush(self.runs, self.latest)
        return run_id

    def ctx(self, **cli_overrides):
        return _make_ctx(self.runs, self.latest, **cli_overrides)


class TestBareDateSelectsManifests(_BareDateFixture):

    def test_b1_bare_since_selects_the_run_inside_the_window(self):
        urls, source = parse_tasks(self.ctx(since="6h"))

        self.assertEqual(urls, [_url(self.u5)])
        self.assertEqual(source, f"manifest:{self.r5}")

    def test_b2_bare_until_is_an_exact_instant_not_end_of_day(self):
        # A relative --until resolves to "6 h ago" exactly; the retired
        # legacy leg rounded it up to 23:59:59 local, which would have
        # swept in R5 as well.
        urls, _source = parse_tasks(self.ctx(until="6h"))

        self.assertEqual(urls, [_url(self.u7)])

    def test_b3_bare_since_matching_no_runs_raises_naming_the_flag(self):
        with self.assertRaises(ValidationError) as cm:
            parse_tasks(self.ctx(since="1h"))

        self.assertIn("--since 1h", str(cm.exception))

    def test_b4_resolve_manifests_bare_until_matching_nothing_raises(self):
        with self.assertRaises(ValidationError) as cm:
            resolve_manifests_for_invocation(self.ctx(until="10h"))

        self.assertIn("--until 10h", str(cm.exception))

    def test_b5_resolve_manifests_returns_the_matched_manifest(self):
        manifests = resolve_manifests_for_invocation(self.ctx(since="6h"))

        self.assertEqual([m["run_id"] for m in manifests], [self.r5])

    def test_b6_explicit_input_wins_and_date_flags_are_ignored(self):
        explicit = str(uuid_mod.uuid4())

        urls, _source = parse_tasks(self.ctx(input=[explicit], since="6h"))

        self.assertEqual(urls, [_url(explicit)])


class TestLegacyArchiveFilesAreIgnored(_BareDateFixture):

    def test_b7_no_selector_and_no_manifest_raises_without_reading_legacy(self):
        legacy_uuid = str(uuid_mod.uuid4())
        legacy_latest = self.tmp / "legacy_latest"
        legacy_latest.write_text(f"{legacy_uuid}\n")
        # Premise guard: the legacy latest file really is there to be read.
        self.assertTrue(legacy_latest.exists())
        self.assertIn(legacy_uuid, legacy_latest.read_text())

        ctx = _make_ctx(self.runs, self.tmp / "absent_latest")
        ctx.archive_tasks_latest = str(legacy_latest)

        with captured_logs("enge.utils.task_resolver") as records:
            with self.assertRaises(ValidationError):
                parse_tasks(ctx)

        critical = matching(records, "Use --file", level=logging.CRITICAL)
        self.assertTrue(
            critical, f"expected a CRITICAL 'no task source' record, got: {records}"
        )
        self.assertNotIn("legacy", " ".join(critical).lower())

    def test_b8_bare_since_ignores_a_matching_legacy_archive_file(self):
        archive_uuid = str(uuid_mod.uuid4())
        archive_dir = self.tmp / "jobs_archive"
        archive_dir.mkdir()
        stamp = (datetime.now() - timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
        archive_file = archive_dir / f"enge_jobs_archive_{stamp}"
        archive_file.write_text(f"{archive_uuid}\n")
        # Premise guard: the retired leg would have matched this file.
        self.assertTrue(archive_file.exists())

        ctx = self.ctx(since="6h")
        ctx.archive_tasks_default = str(archive_dir)

        urls, _source = parse_tasks(ctx)

        self.assertEqual(urls, [_url(self.u5)])
        self.assertNotIn(_url(archive_uuid), urls)


class TestBareDateAcrossSubcommands(_BareDateFixture):

    def test_b9_cancel_resolves_a_bare_window(self):
        from enge.cancel.__main__ import CancelJobs

        ctx = self.ctx(action="cancel", since="6h")

        self.assertEqual(CancelJobs(ctx).req_url_list, [_url(self.u5)])

    def test_b10_rerun_resolves_a_bare_window_to_a_parent_run(self):
        from enge.rerun.__main__ import _resolve_parent_lineage

        ctx = self.ctx(action="rerun", since="6h")
        _urls, source, _uuid_map = parse_tasks_with_map(ctx)

        parent_run_id, _tags, _context = _resolve_parent_lineage(
            source, ctx.manifest_runs_dir
        )

        self.assertEqual(parent_run_id, self.r5)

    def test_b11_reportportal_task_path_resolves_a_bare_window(self):
        from enge.report.concurrent_parser import ConcurrentRequestParser
        from enge.reportportal.operations import resolve_from_tasks

        ctx = self.ctx(action="reportportal", since="6h")

        with patch.object(
            ConcurrentRequestParser, "_fetch_task_info", return_value=None
        ) as fetch:
            resolve_from_tasks(MagicMock(), ctx)

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], _url(self.u5))


class TestCompareBareDate(unittest.TestCase):
    """compare's F2-f warning branch is gone: a bare window that matches no
    runs raises before the comparability floor, like any other selector."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.runs_dir.mkdir(parents=True)
        self.results_dir = self.tmp / "results"
        self.results_dir.mkdir(parents=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_b12_compare_bare_since_matching_nothing_raises(self):
        from enge.compare.loader import load_columns

        ctx = make_app_context(
            action="compare",
            extra_cli={
                "run": None,
                "filter_set": None,
                "filter_tier": None,
                "filter_arch": None,
                "filter_tag": None,
                "since": "1h",
                "until": None,
            },
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
        )

        with self.assertRaises(ValidationError) as cm:
            load_columns(ctx)

        self.assertIn("--since 1h", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
