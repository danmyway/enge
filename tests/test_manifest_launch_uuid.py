"""Tests for launch_uuid recording in manifest request entries.

Red-first: these tests exercise the launch_uuid kwarg on add_request
and the reader's tolerance of its absence, before the implementation lands.
"""

import json
import tempfile
import unittest
from pathlib import Path

from enge.utils.manifest import ManifestReader, ManifestWriter
from enge.utils.ulid import generate_ulid


class TestManifestLaunchUuidRecording(unittest.TestCase):
    """Recording the RP launch UUID on manifest request entries."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _writer(self, **kwargs):
        defaults = {
            "run_id": generate_ulid(),
            "command": "test",
            "argv": ["enge", "test", "-S", "smoke"],
        }
        defaults.update(kwargs)
        return ManifestWriter(**defaults)

    def test_dispatch_records_launch_uuid(self):
        """add_request with launch_uuid stores the UUID on the request entry."""
        w = self._writer()
        w.add_request(
            "task-aaa",
            set_name="smoke",
            tier="tier0",
            arch="x86_64",
            launch_uuid="11111111-1111-1111-1111-111111111111",
        )
        d = w.to_dict()
        req = d["requests"][0]
        self.assertEqual(req["launch_uuid"], "11111111-1111-1111-1111-111111111111")

    def test_dispatch_records_launch_uuid_mutation_check(self):
        """Mutation check: different UUID in, different UUID out."""
        w = self._writer()
        w.add_request(
            "task-bbb",
            tier="tier0",
            arch="x86_64",
            launch_uuid="22222222-2222-2222-2222-222222222222",
        )
        d = w.to_dict()
        self.assertEqual(
            d["requests"][0]["launch_uuid"],
            "22222222-2222-2222-2222-222222222222",
        )

    def test_non_launch_path_records_null(self):
        """When no launch is created, launch_uuid is JSON null (key present)."""
        w = self._writer()
        w.add_request("task-ccc", tier="tier0", arch="x86_64", launch_uuid=None)
        d = w.to_dict()
        self.assertIn("launch_uuid", d["requests"][0])
        self.assertIsNone(d["requests"][0]["launch_uuid"])

    def test_launch_uuid_survives_flush_roundtrip(self):
        """launch_uuid persists through flush → load."""
        w = self._writer()
        w.add_request(
            "task-ddd",
            tier="tier0",
            arch="x86_64",
            launch_uuid="33333333-3333-3333-3333-333333333333",
        )
        w.flush(self.runs_dir, self.latest)
        data = ManifestReader.load_latest(self.latest)
        self.assertEqual(
            data["requests"][0]["launch_uuid"],
            "33333333-3333-3333-3333-333333333333",
        )

    def test_reader_tolerates_missing_launch_uuid(self):
        """Pre-change manifests (no launch_uuid key) parse without error."""
        rid = generate_ulid()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "run_id": rid,
            "created_at": "2026-01-01T00:00:00Z",
            "command": "test",
            "argv": [],
            "tags": [],
            "parent_run_id": None,
            "origin": "native",
            "context": {},
            "requests": [
                {
                    "task_id": "old-uuid",
                    "set": None,
                    "tier": "tier0",
                    "arch": "x86_64",
                    "plan": None,
                    "source_compose": None,
                    "target_compose": None,
                    "artifacts_url": None,
                    "dispatched_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        (self.runs_dir / f"{rid}.json").write_text(json.dumps(manifest))
        (self.latest).write_text(str(self.runs_dir / f"{rid}.json"))

        data = ManifestReader.load_latest(self.latest)
        req = data["requests"][0]
        self.assertIsNone(req.get("launch_uuid"))

    def test_dryrun_no_manifest_write(self):
        """Dry-run guard: flush is never called, so no files are created."""
        w = self._writer()
        w.add_request("uuid-dryrun", launch_uuid=None)
        self.assertFalse(self.runs_dir.exists())
        self.assertFalse(self.latest.exists())


class TestRerunManifestLaunchUuid(unittest.TestCase):
    """Rerun flow records the CHILD launch UUID, not the parent's."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_rerun_records_child_launch_uuid(self):
        """A rerun manifest entry carries the child launch UUID."""
        parent_id = generate_ulid()
        w = ManifestWriter(
            run_id=generate_ulid(),
            command="rerun",
            argv=["enge", "rerun"],
            parent_run_id=parent_id,
            tags=["rerun"],
        )
        child_uuid = "44444444-4444-4444-4444-444444444444"
        w.add_request(
            "rerun-task-1",
            tier="tier0",
            arch="x86_64",
            launch_uuid=child_uuid,
        )
        d = w.to_dict()
        self.assertEqual(d["requests"][0]["launch_uuid"], child_uuid)
        self.assertEqual(d["parent_run_id"], parent_id)


if __name__ == "__main__":
    unittest.main()
