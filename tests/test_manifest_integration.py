"""Integration tests for manifest-based report --list and --run."""

import json
import tempfile
import time
import unittest
import uuid as uuid_mod
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from enge.utils.manifest import ManifestWriter
from enge.utils.ulid import generate_ulid
from enge.report.__main__ import _handle_list
from enge.utils.task_resolver import _parse_tasks_impl, _resolve_manifest_tasks


def _make_ctx(runs_dir, latest, **cli_overrides):
    cli = {
        "output_format": "terminal",
        "filter_set": None,
        "filter_tier": None,
        "filter_arch": None,
        "filter_tag": None,
        "since": None,
        "until": None,
        "list": False,
        "run": None,
        "file": None,
        "input": None,
        "get_tag": [],
    }
    cli.update(cli_overrides)
    return SimpleNamespace(
        manifest_runs_dir=str(runs_dir),
        manifest_latest=str(latest),
        archive_tasks_latest="/nonexistent/legacy",
        archive_tasks_default="/nonexistent/legacy_archive",
        cli_args=SimpleNamespace(**cli),
        testing_farm_endpoint=SimpleNamespace(
            api_endpoint_url="https://api.example.com",
            log_artifact_baseurl="https://logs.example.com",
        ),
    )


class TestHandleList(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, **kwargs):
        defaults = {
            "run_id": generate_ulid(),
            "command": "test",
            "argv": ["enge", "test"],
        }
        defaults.update(kwargs)
        w = ManifestWriter(**defaults)
        w.add_request("uuid-dummy", tier="tier0", arch="x86_64")
        w.flush(self.runs, self.latest)
        return defaults["run_id"]

    def test_list_renders_all_runs_newest_first(self):
        ids = []
        for _ in range(3):
            ids.append(self._write())
            time.sleep(0.002)

        ctx = _make_ctx(self.runs, self.latest, output_format="json")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = json.loads(mock_out.getvalue())

        self.assertEqual(len(output), 3)
        self.assertEqual(output[0]["run_id"], ids[2])
        self.assertEqual(output[2]["run_id"], ids[0])

    def test_list_includes_migrated(self):
        self._write(context={"set": "native-set"})
        time.sleep(0.002)

        migrated_id = generate_ulid()
        migrated = {
            "schema_version": 1,
            "run_id": migrated_id,
            "created_at": "2026-01-01T00:00:00Z",
            "command": "test",
            "argv": [],
            "tags": [],
            "parent_run_id": None,
            "origin": "migrated",
            "context": {"set": "old-set"},
            "requests": [{"task_id": "old-uuid"}],
        }
        self.runs.mkdir(parents=True, exist_ok=True)
        (self.runs / f"{migrated_id}.json").write_text(json.dumps(migrated))

        ctx = _make_ctx(self.runs, self.latest, output_format="json")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = json.loads(mock_out.getvalue())

        origins = [r["origin"] for r in output]
        self.assertIn("native", origins)
        self.assertIn("migrated", origins)

    def test_list_filter_by_set(self):
        self._write(context={"set": "smoke"})
        time.sleep(0.002)
        self._write(context={"set": "regression"})

        ctx = _make_ctx(
            self.runs,
            self.latest,
            output_format="json",
            filter_set="smoke",
        )
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = json.loads(mock_out.getvalue())

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["context"]["set"], "smoke")

    def test_list_empty_store(self):
        ctx = _make_ctx(self.runs, self.latest, output_format="json")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = mock_out.getvalue().strip()
        self.assertEqual(output, "[]")


class TestResolveManifestTasks(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_run_id_resolves_to_task_ids(self):
        rid = generate_ulid()
        w = ManifestWriter(run_id=rid, command="test", argv=["enge", "test"])
        w.add_request("uuid-aaa")
        w.add_request("uuid-bbb")
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, run=rid)
        result = _resolve_manifest_tasks(ctx)
        self.assertIsNotNone(result)
        task_ids, source = result
        self.assertEqual(task_ids, ["uuid-aaa", "uuid-bbb"])
        self.assertIn(rid, source)

    def test_missing_run_id_raises(self):
        from enge.utils.errors import ValidationError

        self.runs.mkdir(parents=True, exist_ok=True)
        ctx = _make_ctx(self.runs, self.latest, run="NONEXISTENT")
        with self.assertRaises(ValidationError):
            _resolve_manifest_tasks(ctx)

    def test_filter_by_set_resolves(self):
        w = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=[],
            context={"set": "smoke"},
        )
        w.add_request("uuid-filtered")
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, filter_set="smoke")
        result = _resolve_manifest_tasks(ctx)
        self.assertIsNotNone(result)
        task_ids, _ = result
        self.assertEqual(task_ids, ["uuid-filtered"])


class TestStructuredFilterMultiRunResolution(unittest.TestCase):
    """--set/--tier/--arch filters must collect tasks from ALL matching runs."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_set_filter_returns_tasks_from_all_matching_runs(self):
        uuid_a = str(uuid_mod.uuid4())
        uuid_b = str(uuid_mod.uuid4())

        w1 = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=[],
            context={"set": "base-8to9"},
        )
        w1.add_request(uuid_a)
        w1.flush(self.runs, self.latest)

        time.sleep(0.002)
        w2 = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=[],
            context={"set": "base-8to9"},
        )
        w2.add_request(uuid_b)
        w2.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, filter_set="base-8to9")
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved_uuids = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(uuid_a, resolved_uuids)
        self.assertIn(uuid_b, resolved_uuids)
        self.assertEqual(len(resolved_uuids), 2)

    def test_tier_filter_returns_tasks_from_all_matching_runs(self):
        uuid_a = str(uuid_mod.uuid4())
        uuid_b = str(uuid_mod.uuid4())
        uuid_other = str(uuid_mod.uuid4())

        w1 = ManifestWriter(run_id=generate_ulid(), command="test", argv=[])
        w1.add_request(uuid_a, tier="tier0", arch="x86_64")
        w1.flush(self.runs, self.latest)

        time.sleep(0.002)
        w2 = ManifestWriter(run_id=generate_ulid(), command="test", argv=[])
        w2.add_request(uuid_b, tier="tier0", arch="x86_64")
        w2.flush(self.runs, self.latest)

        time.sleep(0.002)
        w3 = ManifestWriter(run_id=generate_ulid(), command="test", argv=[])
        w3.add_request(uuid_other, tier="tier3", arch="x86_64")
        w3.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, filter_tier="tier0")
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved_uuids = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(uuid_a, resolved_uuids)
        self.assertIn(uuid_b, resolved_uuids)
        self.assertNotIn(uuid_other, resolved_uuids)


class TestDefaultResolutionPriority(unittest.TestCase):
    """Default path (no flags) reads manifest latest before /tmp fallback."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_manifest_latest_preferred_over_legacy(self):
        manifest_uuid = str(uuid_mod.uuid4())
        legacy_uuid = str(uuid_mod.uuid4())

        w = ManifestWriter(run_id=generate_ulid(), command="test", argv=[])
        w.add_request(manifest_uuid)
        w.flush(self.runs, self.latest)

        legacy_file = self.tmp / "legacy_latest"
        legacy_file.write_text(f"{legacy_uuid}\n")

        ctx = _make_ctx(self.runs, self.latest)
        ctx.archive_tasks_latest = str(legacy_file)
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved_uuids = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(manifest_uuid, resolved_uuids)
        self.assertNotIn(legacy_uuid, resolved_uuids)

    def test_falls_back_to_legacy_when_no_manifest(self):
        legacy_uuid = str(uuid_mod.uuid4())

        legacy_file = self.tmp / "legacy_latest"
        legacy_file.write_text(f"{legacy_uuid}\n")

        ctx = _make_ctx(self.runs, self.latest)
        ctx.archive_tasks_latest = str(legacy_file)
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved_uuids = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(legacy_uuid, resolved_uuids)


class TestAmbiguousPrefixIntegration(unittest.TestCase):
    """--run with ambiguous prefix must raise, not silently fall back."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ambiguous_prefix_raises_through_parse_tasks(self):
        from enge.utils.errors import ValidationError

        rid1 = "01AAAAAA" + generate_ulid()[8:]
        rid2 = "01AAAAAB" + generate_ulid()[8:]
        for rid in (rid1, rid2):
            w = ManifestWriter(run_id=rid, command="test", argv=[])
            w.add_request(str(uuid_mod.uuid4()))
            w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, run="01AAAAA")
        with self.assertRaises(ValidationError) as cm:
            _parse_tasks_impl(ctx)
        self.assertIn("matches 2 runs", str(cm.exception))

    def test_nonexistent_run_raises_through_parse_tasks(self):
        from enge.utils.errors import ValidationError

        self.runs.mkdir(parents=True, exist_ok=True)
        ctx = _make_ctx(self.runs, self.latest, run="NONEXISTENT")
        with self.assertRaises(ValidationError) as cm:
            _parse_tasks_impl(ctx)
        self.assertIn("No run matching", str(cm.exception))


class TestSinceUntilManifestRouting(unittest.TestCase):
    """--since/--until with manifest filters must not scan legacy archive."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_since_with_set_filter_skips_legacy(self):
        task_uuid = str(uuid_mod.uuid4())
        w = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=[],
            context={"set": "smoke"},
        )
        w.add_request(task_uuid)
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, filter_set="smoke", since="1d")
        ctx.archive_tasks_default = "/nonexistent/should_not_be_read"
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved_uuids = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(task_uuid, resolved_uuids)

    def test_since_with_tag_filter_routes_to_manifest(self):
        task_uuid = str(uuid_mod.uuid4())
        w = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=[],
            tags=["nightly"],
        )
        w.add_request(task_uuid)
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, filter_tag="nightly", since="1d")
        ctx.archive_tasks_default = "/nonexistent/should_not_be_read"
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved_uuids = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(task_uuid, resolved_uuids)


class TestFindRunsMultiSetMatching(unittest.TestCase):
    """find_runs must match per-request set fields, not only context.set."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_multiset_manifest(self):
        rid = generate_ulid()
        w = ManifestWriter(
            run_id=rid,
            command="test",
            argv=["enge", "test", "-S", "alpha", "-S", "beta"],
            context={"set": "alpha"},
        )
        w.add_request("uuid-alpha-1", set_name="alpha", tier="tier0", arch="x86_64")
        w.add_request("uuid-beta-1", set_name="beta", tier="tier0", arch="x86_64")
        w.flush(self.runs, self.latest)
        return rid

    def test_find_runs_matches_non_context_set_via_requests(self):
        """A multi-set run with context.set='alpha' must be found by set_name='beta'."""
        from enge.utils.manifest import ManifestReader

        self._write_multiset_manifest()
        results = ManifestReader.find_runs(self.runs, set_name="beta")
        self.assertEqual(len(results), 1, "multi-set run not found by non-context set")

    def test_find_runs_still_matches_context_set(self):
        """context.set match must still work (single-set and migrated compat)."""
        from enge.utils.manifest import ManifestReader

        self._write_multiset_manifest()
        results = ManifestReader.find_runs(self.runs, set_name="alpha")
        self.assertEqual(len(results), 1)

    def test_find_runs_no_match_for_absent_set(self):
        """A set name present in neither context nor requests must not match."""
        from enge.utils.manifest import ManifestReader

        self._write_multiset_manifest()
        results = ManifestReader.find_runs(self.runs, set_name="gamma")
        self.assertEqual(len(results), 0)


class TestMultiValueFilters(unittest.TestCase):
    """Multi-value --set/--tier/--arch/--tag: OR within dimension, AND across."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, set_name, tier="tier0", arch="x86_64", tag=None):
        rid = generate_ulid()
        w = ManifestWriter(
            run_id=rid,
            command="test",
            argv=["enge", "test"],
            context={"set": set_name},
            tags=[tag] if tag else [],
        )
        w.add_request(str(uuid_mod.uuid4()), set_name=set_name, tier=tier, arch=arch)
        w.flush(self.runs, self.latest)
        time.sleep(0.002)
        return rid

    # --- argparse level ---

    def test_argparse_repeated_set_yields_list(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(
            args=["report", "--list", "--set", "alpha", "--set", "beta"]
        )
        self.assertEqual(args.filter_set, ["alpha", "beta"])

    def test_argparse_repeated_tier_yields_list(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(
            args=["report", "--list", "--tier", "tier0", "--tier", "tier1"]
        )
        self.assertEqual(args.filter_tier, ["tier0", "tier1"])

    def test_argparse_repeated_arch_yields_list(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(
            args=["report", "--list", "--arch", "x86_64", "--arch", "ppc64le"]
        )
        self.assertEqual(args.filter_arch, ["x86_64", "ppc64le"])

    def test_argparse_repeated_tag_yields_list(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(
            args=["report", "--list", "--tag", "nightly", "--tag", "gating"]
        )
        self.assertEqual(args.filter_tag, ["nightly", "gating"])

    # --- find_runs level: OR within dimension ---

    def test_find_runs_set_or(self):
        from enge.utils.manifest import ManifestReader

        self._write("alpha")
        self._write("beta")
        self._write("gamma")
        results = ManifestReader.find_runs(self.runs, set_name=["alpha", "beta"])
        matched_sets = {r["context"]["set"] for r in results}
        self.assertEqual(matched_sets, {"alpha", "beta"})

    def test_find_runs_tier_or(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", tier="tier0")
        self._write("s2", tier="tier1")
        self._write("s3", tier="tier2")
        results = ManifestReader.find_runs(self.runs, tier=["tier0", "tier1"])
        self.assertEqual(len(results), 2)

    def test_find_runs_arch_or(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", arch="x86_64")
        self._write("s2", arch="ppc64le")
        self._write("s3", arch="s390x")
        results = ManifestReader.find_runs(self.runs, arch=["x86_64", "ppc64le"])
        self.assertEqual(len(results), 2)

    def test_find_runs_tag_or(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", tag="nightly")
        self._write("s2", tag="gating")
        self._write("s3", tag="manual")
        results = ManifestReader.find_runs(self.runs, tag=["nightly", "gating"])
        self.assertEqual(len(results), 2)

    # --- AND across dimensions ---

    def test_find_runs_set_and_tier(self):
        from enge.utils.manifest import ManifestReader

        self._write("alpha", tier="tier0")
        self._write("alpha", tier="tier1")
        self._write("beta", tier="tier0")
        results = ManifestReader.find_runs(
            self.runs, set_name=["alpha"], tier=["tier0"]
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["context"]["set"], "alpha")

    # --- backward compat: single value ---

    def test_find_runs_single_set_scalar_compat(self):
        from enge.utils.manifest import ManifestReader

        self._write("alpha")
        self._write("beta")
        results = ManifestReader.find_runs(self.runs, set_name="alpha")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["context"]["set"], "alpha")

    def test_find_runs_single_tier_scalar_compat(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", tier="tier0")
        self._write("s2", tier="tier1")
        results = ManifestReader.find_runs(self.runs, tier="tier0")
        self.assertEqual(len(results), 1)

    # --- multiset interplay: OR with per-request set matching ---

    def test_find_runs_multi_value_with_multiset_manifest(self):
        """Multi-value filter must still match per-request set fields."""
        from enge.utils.manifest import ManifestReader

        rid = generate_ulid()
        w = ManifestWriter(
            run_id=rid,
            command="test",
            argv=["enge", "test", "-S", "alpha", "-S", "beta"],
            context={"set": "alpha"},
        )
        w.add_request("uuid-alpha-1", set_name="alpha", tier="tier0", arch="x86_64")
        w.add_request("uuid-beta-1", set_name="beta", tier="tier0", arch="x86_64")
        w.flush(self.runs, self.latest)

        results = ManifestReader.find_runs(self.runs, set_name=["beta"])
        self.assertEqual(
            len(results), 1, "multi-set run not found by non-context set in list"
        )

    # --- end-to-end: _handle_list with multi-value filters ---

    def test_handle_list_multi_set_or(self):
        self._write("alpha")
        self._write("beta")
        self._write("gamma")
        ctx = _make_ctx(
            self.runs,
            self.latest,
            output_format="json",
            filter_set=["alpha", "beta"],
        )
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = json.loads(mock_out.getvalue())
        matched_sets = {r["context"]["set"] for r in output}
        self.assertEqual(matched_sets, {"alpha", "beta"})


class TestEmptyStringFilterSemantics(unittest.TestCase):
    """Empty-string filter values must mean 'no filter' (project convention)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, set_name, tag=None):

        rid = generate_ulid()
        w = ManifestWriter(
            run_id=rid,
            command="test",
            argv=["enge", "test"],
            context={"set": set_name},
            tags=[tag] if tag else [],
        )
        w.add_request(str(uuid_mod.uuid4()), set_name=set_name)
        w.flush(self.runs, self.latest)
        time.sleep(0.002)
        return rid

    def test_empty_string_set_name_matches_all(self):
        from enge.utils.manifest import ManifestReader

        self._write("alpha")
        self._write("beta")
        results = ManifestReader.find_runs(self.runs, set_name="")
        self.assertEqual(len(results), 2)

    def test_list_of_empty_string_set_name_matches_all(self):
        from enge.utils.manifest import ManifestReader

        self._write("alpha")
        self._write("beta")
        results = ManifestReader.find_runs(self.runs, set_name=[""])
        self.assertEqual(len(results), 2)

    def test_empty_string_among_real_values_dropped(self):
        from enge.utils.manifest import ManifestReader

        self._write("alpha")
        self._write("beta")
        results = ManifestReader.find_runs(self.runs, set_name=["", "alpha"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["context"]["set"], "alpha")

    def test_empty_string_tag_matches_all(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", tag="nightly")
        self._write("s2", tag="gating")
        results = ManifestReader.find_runs(self.runs, tag="")
        self.assertEqual(len(results), 2)

    def test_list_of_empty_string_tag_matches_all(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", tag="nightly")
        self._write("s2", tag="gating")
        results = ManifestReader.find_runs(self.runs, tag=[""])
        self.assertEqual(len(results), 2)

    def test_empty_string_among_real_tag_values_dropped(self):
        from enge.utils.manifest import ManifestReader

        self._write("s1", tag="nightly")
        self._write("s2", tag="gating")
        results = ManifestReader.find_runs(self.runs, tag=["", "nightly"])
        self.assertEqual(len(results), 1)


class TestReportListDisplayOrder(unittest.TestCase):
    """report --list table is oldest-first; json/gitlab are newest-first."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"
        self.ids = []
        for _ in range(3):
            rid = generate_ulid()
            self.ids.append(rid)
            w = ManifestWriter(
                run_id=rid,
                command="test",
                argv=["enge", "test"],
                context={"set": "smoke"},
            )
            w.add_request("uuid-dummy", tier="tier0", arch="x86_64")
            w.flush(self.runs, self.latest)
            time.sleep(0.002)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_table_rows_oldest_first(self):
        import enge.utils.console as console_mod
        from rich.console import Console

        buf = StringIO()
        saved = console_mod._current
        try:
            console_mod._current = Console(
                file=buf, no_color=True, width=200, highlight=False
            )
            ctx = _make_ctx(self.runs, self.latest, output_format="terminal")
            _handle_list(ctx)
        finally:
            console_mod._current = saved
        output = buf.getvalue()
        positions = [output.index(rid) for rid in self.ids]
        self.assertEqual(
            positions,
            sorted(positions),
            f"Table rows should be oldest-first: {self.ids}",
        )

    def test_json_output_newest_first(self):
        ctx = _make_ctx(self.runs, self.latest, output_format="json")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = json.loads(mock_out.getvalue())
        output_ids = [r["run_id"] for r in output]
        self.assertEqual(output_ids, list(reversed(self.ids)))

    def test_gitlab_output_newest_first(self):
        ctx = _make_ctx(self.runs, self.latest, output_format="gitlab")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            lines = mock_out.getvalue().strip().split("\n")
        data_lines = [row for row in lines if row.startswith("| 0")]
        data_ids = [row.split("|")[1].strip() for row in data_lines]
        self.assertEqual(data_ids, list(reversed(self.ids)))


class TestReportListMultiSetColumn(unittest.TestCase):
    """report --list Set column shows every dispatched set, not just the first."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"
        self.run_id = generate_ulid()
        w = ManifestWriter(
            run_id=self.run_id,
            command="test",
            argv=["enge", "test"],
            context={"set": "alpha"},
        )
        w.add_request("uuid-1", set_name="alpha", tier="tier0", arch="x86_64")
        w.add_request("uuid-2", set_name="beta", tier="tier0", arch="x86_64")
        w.flush(self.runs, self.latest)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_gitlab_output_shows_all_sets(self):
        ctx = _make_ctx(self.runs, self.latest, output_format="gitlab")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            lines = mock_out.getvalue().strip().split("\n")
        data_lines = [row for row in lines if row.startswith(f"| {self.run_id}")]
        self.assertEqual(len(data_lines), 1)
        cells = [c.strip() for c in data_lines[0].split("|")]
        # | run_id | created | command | set | tier(s) | arch(es) | tags | requests | origin |
        self.assertEqual(cells[4], "alpha, beta")

    def test_json_output_gains_additive_sets_key(self):
        ctx = _make_ctx(self.runs, self.latest, output_format="json")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _handle_list(ctx)
            output = json.loads(mock_out.getvalue())
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["sets"], ["alpha", "beta"])
        self.assertEqual(output[0]["context"]["set"], "alpha")

    def test_terminal_output_shows_all_sets(self):
        import enge.utils.console as console_mod
        from rich.console import Console

        buf = StringIO()
        saved = console_mod._current
        try:
            console_mod._current = Console(
                file=buf, no_color=True, width=200, highlight=False
            )
            ctx = _make_ctx(self.runs, self.latest, output_format="terminal")
            _handle_list(ctx)
        finally:
            console_mod._current = saved
        output = buf.getvalue()
        self.assertIn("alpha, beta", output)


if __name__ == "__main__":
    unittest.main()
