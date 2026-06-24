"""Tests for the manifest-based dispatch state recording.

Replaces the legacy record_task_ids tests that tested the /tmp/enge_latest_jobs
and filename-tagged archive writing. The new system writes JSON manifests
via ManifestWriter — one per invocation, flushed after each dispatch.
"""

import tempfile
import time
import unittest
from pathlib import Path

from enge.utils.manifest import ManifestWriter, ManifestReader
from enge.utils.ulid import generate_ulid


class TestManifestDispatchRecording(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_all_task_ids_present_in_manifest(self):
        """Three add_request calls produce one manifest with 3 entries."""
        w = ManifestWriter(
            run_id=generate_ulid(), command="test", argv=["enge", "test"]
        )
        ids = ["task-aaa", "task-bbb", "task-ccc"]
        for task_id in ids:
            w.add_request(task_id, tier="tier0", arch="x86_64")
        w.flush(self.runs_dir, self.latest)

        manifest = ManifestReader.load_latest(self.latest)
        self.assertIsNotNone(manifest)
        recorded = ManifestReader.get_task_ids(manifest)
        self.assertEqual(recorded, ids)

    def test_second_run_produces_separate_manifest(self):
        """Two runs write separate manifests; latest points to the second."""
        w1 = ManifestWriter(
            run_id=generate_ulid(), command="test", argv=["enge", "test"]
        )
        w1.add_request("run1-aaa")
        w1.add_request("run1-bbb")
        w1.flush(self.runs_dir, self.latest)

        time.sleep(0.002)
        w2 = ManifestWriter(
            run_id=generate_ulid(), command="test", argv=["enge", "test"]
        )
        w2.add_request("run2-zzz")
        w2.flush(self.runs_dir, self.latest)

        latest = ManifestReader.load_latest(self.latest)
        self.assertEqual(ManifestReader.get_task_ids(latest), ["run2-zzz"])

        manifests = list(self.runs_dir.glob("*.json"))
        self.assertEqual(len(manifests), 2)

    def test_dryrun_writes_nothing(self):
        """When flush() is never called (dry-run), no files are created."""
        w = ManifestWriter(
            run_id=generate_ulid(), command="test", argv=["enge", "test", "-n"]
        )
        w.add_request("uuid-dryrun")
        self.assertFalse(self.runs_dir.exists())
        self.assertFalse(self.latest.exists())

    def test_manifest_contains_per_request_metadata(self):
        """Each request entry carries set/tier/arch/plan/compose fields."""
        w = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=["enge", "test"],
            tags=["smoke"],
            context={"set": "base-8to9"},
        )
        w.add_request(
            "uuid-1",
            set_name="base-8to9",
            tier="tier0",
            arch="x86_64",
            plan="/plans/upgrade",
            source_compose="RHEL-8.10",
            target_compose="RHEL-9.4",
            artifacts_url="https://artifacts.example.com/uuid-1/",
        )
        w.flush(self.runs_dir, self.latest)
        data = ManifestReader.load_latest(self.latest)
        req = data["requests"][0]
        self.assertEqual(req["set"], "base-8to9")
        self.assertEqual(req["tier"], "tier0")
        self.assertEqual(req["arch"], "x86_64")
        self.assertEqual(req["plan"], "/plans/upgrade")
        self.assertEqual(req["source_compose"], "RHEL-8.10")
        self.assertEqual(req["target_compose"], "RHEL-9.4")

    def test_incremental_flush_preserves_all_requests(self):
        """Flushing after each add_request accumulates all entries."""
        w = ManifestWriter(
            run_id=generate_ulid(), command="test", argv=["enge", "test"]
        )
        for i in range(5):
            w.add_request(f"uuid-{i}")
            w.flush(self.runs_dir, self.latest)
        data = ManifestReader.load_latest(self.latest)
        self.assertEqual(len(data["requests"]), 5)


if __name__ == "__main__":
    unittest.main()
