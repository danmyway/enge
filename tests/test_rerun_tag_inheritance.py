"""Tests for rerun tag inheritance and manifest lineage."""

import tempfile
import time
import unittest
from pathlib import Path

from enge.rerun.__main__ import (
    _extract_tags_from_filename,
    _get_next_rerun_tag,
    _unique_preserve,
)
from enge.utils.manifest import ManifestReader, ManifestWriter
from enge.utils.ulid import generate_ulid


class TestLegacyRerunTagInheritance(unittest.TestCase):
    def test_extract_tags_from_filename(self):
        path = Path("enge_jobs_archive_20260611.rerun.tier0")
        self.assertEqual(_extract_tags_from_filename(path), ["rerun", "tier0"])

    def test_get_next_rerun_tag_progression(self):
        self.assertEqual(_get_next_rerun_tag([]), "rerun")
        self.assertEqual(_get_next_rerun_tag(["rerun"]), "rerun1")
        self.assertEqual(_get_next_rerun_tag(["rerun1"]), "rerun2")

    def test_unique_preserve_base_and_inherited_ordering(self):
        base_tags = ["enge", "automated", "rerun"]
        current_tags = ["rerun", "tier0"]
        result = _unique_preserve([*base_tags, *current_tags])
        self.assertEqual(result, ["enge", "automated", "rerun", "tier0"])


class TestManifestRerunLineage(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_rerun_carries_parent_run_id(self):
        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id,
            command="test",
            argv=["enge", "test"],
            tags=["nightly"],
        )
        parent.add_request("uuid-orig", tier="tier0", arch="x86_64")
        parent.flush(self.runs_dir, self.latest)

        time.sleep(0.002)
        child = ManifestWriter(
            run_id=generate_ulid(),
            command="rerun",
            argv=["enge", "rerun"],
            tags=["nightly", "rerun"],
            parent_run_id=parent_id,
        )
        child.add_request("uuid-rerun", tier="tier0", arch="x86_64")
        child.flush(self.runs_dir, self.latest)

        latest = ManifestReader.load_latest(self.latest)
        self.assertEqual(latest["command"], "rerun")
        self.assertEqual(latest["parent_run_id"], parent_id)
        self.assertIn("nightly", latest["tags"])
        self.assertIn("rerun", latest["tags"])

    def test_rerun_child_has_own_composes(self):
        """Child requests may carry different composes than parent."""
        parent = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=["enge", "test"],
        )
        parent.add_request(
            "uuid-orig",
            source_compose="RHEL-9.7-Nightly-20260620",
            target_compose="RHEL-10.0-Nightly-20260620",
        )
        parent.flush(self.runs_dir, self.latest)

        time.sleep(0.002)
        child = ManifestWriter(
            run_id=generate_ulid(),
            command="rerun",
            argv=["enge", "rerun"],
            parent_run_id=parent.run_id,
        )
        child.add_request(
            "uuid-rerun",
            source_compose="RHEL-9.7-Nightly-20260622",
            target_compose="RHEL-10.0-Nightly-20260622",
        )
        child.flush(self.runs_dir, self.latest)

        child_data = ManifestReader.load_latest(self.latest)
        self.assertEqual(
            child_data["requests"][0]["source_compose"],
            "RHEL-9.7-Nightly-20260622",
        )
        parent_data = ManifestReader.get_run(self.runs_dir, parent.run_id)
        self.assertEqual(
            parent_data["requests"][0]["source_compose"],
            "RHEL-9.7-Nightly-20260620",
        )


if __name__ == "__main__":
    unittest.main()
