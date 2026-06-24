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
from enge.report.__main__ import (
    _handle_list,
    _parse_tasks_impl,
    _resolve_manifest_tasks,
)


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


if __name__ == "__main__":
    unittest.main()
