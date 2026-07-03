"""Integration tests: cancel resolves task IDs from the manifest store."""

import tempfile
import time
import unittest
import uuid as uuid_mod
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from enge.utils.manifest import ManifestWriter
from enge.utils.task_resolver import _parse_tasks_impl
from enge.utils.ulid import generate_ulid


def _make_ctx(runs_dir, latest, **cli_overrides):
    cli = {
        "action": "cancel",
        "file": None,
        "input": None,
        "get_tag": [],
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
            api_endpoint_url="https://api.example.com",
            log_artifact_baseurl="https://logs.example.com",
        ),
    )


class TestCancelResolvesFromLatestManifest(unittest.TestCase):
    """Default cancel (no flags) reads task IDs from the manifest latest pointer."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_default_cancel_resolves_from_manifest(self):
        task_uuid = str(uuid_mod.uuid4())
        w = ManifestWriter(run_id=generate_ulid(), command="test", argv=[])
        w.add_request(task_uuid)
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest)
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(task_uuid, resolved)

    def test_multiple_tasks_in_manifest(self):
        uuids = [str(uuid_mod.uuid4()) for _ in range(3)]
        w = ManifestWriter(run_id=generate_ulid(), command="test", argv=[])
        for u in uuids:
            w.add_request(u)
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest)
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        for u in uuids:
            self.assertIn(u, resolved)


class TestCancelRunFlag(unittest.TestCase):
    """--run <id> selects a specific manifest's tasks."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_run_selects_specific_manifest(self):
        uuid_old = str(uuid_mod.uuid4())
        rid_old = generate_ulid()
        w1 = ManifestWriter(run_id=rid_old, command="test", argv=[])
        w1.add_request(uuid_old)
        w1.flush(self.runs, self.latest)

        time.sleep(0.002)
        uuid_new = str(uuid_mod.uuid4())
        rid_new = generate_ulid()
        w2 = ManifestWriter(run_id=rid_new, command="test", argv=[])
        w2.add_request(uuid_new)
        w2.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, run=rid_old)
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(uuid_old, resolved)
        self.assertNotIn(uuid_new, resolved)

    def test_run_prefix_match(self):
        task_uuid = str(uuid_mod.uuid4())
        rid = generate_ulid()
        w = ManifestWriter(run_id=rid, command="test", argv=[])
        w.add_request(task_uuid)
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, run=rid[:10])
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(task_uuid, resolved)


class TestCancelAmbiguousPrefix(unittest.TestCase):
    """Ambiguous --run prefix must raise ValidationError."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ambiguous_prefix_raises(self):
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

    def test_nonexistent_run_raises(self):
        from enge.utils.errors import ValidationError

        self.runs.mkdir(parents=True, exist_ok=True)
        ctx = _make_ctx(self.runs, self.latest, run="NONEXISTENT")
        with self.assertRaises(ValidationError) as cm:
            _parse_tasks_impl(ctx)
        self.assertIn("No run matching", str(cm.exception))


class TestCancelLegacyFallback(unittest.TestCase):
    """Legacy fallback works when no manifest exists."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_falls_back_to_legacy_when_no_manifest(self):
        legacy_uuid = str(uuid_mod.uuid4())
        legacy_file = self.tmp / "legacy_latest"
        legacy_file.write_text(f"{legacy_uuid}\n")

        ctx = _make_ctx(self.runs, self.latest)
        ctx.archive_tasks_latest = str(legacy_file)
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(legacy_uuid, resolved)

    def test_manifest_preferred_over_legacy(self):
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
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertIn(manifest_uuid, resolved)
        self.assertNotIn(legacy_uuid, resolved)


class TestGetTagDeprecationWarning(unittest.TestCase):
    """--get-tag emits a deprecation warning; --tag (filter_tag) does not."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"
        self.archive = self.tmp / "archive"
        self.archive.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_tag_emits_warning(self):
        task_uuid = str(uuid_mod.uuid4())
        archive_file = self.archive / "enge_jobs_archive_20260701120000.nightly"
        archive_file.write_text(f"{task_uuid}\n")

        ctx = _make_ctx(self.runs, self.latest, get_tag=["nightly"])
        ctx.archive_tasks_default = str(self.archive)

        with self.assertLogs("enge.utils.task_resolver", level="WARNING") as cm:
            _parse_tasks_impl(ctx)
        self.assertTrue(
            any("--get-tag is deprecated" in msg for msg in cm.output),
            f"Expected deprecation warning, got: {cm.output}",
        )

    def test_filter_tag_no_warning(self):
        task_uuid = str(uuid_mod.uuid4())
        w = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=[],
            tags=["nightly"],
        )
        w.add_request(task_uuid)
        w.flush(self.runs, self.latest)

        ctx = _make_ctx(self.runs, self.latest, filter_tag="nightly")
        import logging

        logger = logging.getLogger("enge.utils.task_resolver")
        with patch.object(logger, "warning") as mock_warn:
            _parse_tasks_impl(ctx)
            for call in mock_warn.call_args_list:
                self.assertNotIn("--get-tag is deprecated", str(call))


class TestCancelRunThroughCancelModule(unittest.TestCase):
    """Verify cancel module's CancelJobs uses task_resolver, not report directly."""

    def test_cancel_imports_from_task_resolver(self):
        import enge.cancel.__main__ as cancel_mod

        with open(cancel_mod.__file__) as f:
            source = f.read()
        self.assertIn("from enge.utils.task_resolver import parse_tasks", source)
        self.assertNotIn("from enge.report.__main__ import parse_tasks", source)


if __name__ == "__main__":
    unittest.main()
