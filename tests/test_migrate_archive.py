"""Tests for the migrate-archive subcommand."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from enge.migrate.__main__ import main as migrate_main


def _make_ctx(archive_dir, runs_dir):
    return SimpleNamespace(
        archive_tasks_default=str(archive_dir),
        manifest_runs_dir=str(runs_dir),
        manifest_latest=str(runs_dir.parent / "latest"),
        testing_farm_endpoint=SimpleNamespace(
            log_artifact_baseurl="https://logs.example.com/artifacts",
            api_endpoint_url="https://api.example.com",
        ),
        cli_args=SimpleNamespace(action="migrate-archive"),
    )


class TestMigrateArchive(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.archive = self.tmp / "archive"
        self.archive.mkdir()
        self.runs = self.tmp / "runs"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_produces_migrated_manifest(self):
        legacy = self.archive / "enge_jobs_archive_20260622140000.tier0.x86_64"
        legacy.write_text("uuid-aaa\nuuid-bbb\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)

        manifests = list(self.runs.glob("*.json"))
        self.assertEqual(len(manifests), 1)
        data = json.loads(manifests[0].read_text())
        self.assertEqual(data["origin"], "migrated")
        self.assertEqual(len(data["requests"]), 2)
        self.assertEqual(data["requests"][0]["task_id"], "uuid-aaa")
        self.assertIn("tier0", data["tags"])
        self.assertIn("x86_64", data["tags"])

    def test_artifacts_url_constructed(self):
        legacy = self.archive / "enge_jobs_archive_20260622140000"
        legacy.write_text("uuid-xyz\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)

        data = json.loads(list(self.runs.glob("*.json"))[0].read_text())
        self.assertEqual(
            data["requests"][0]["artifacts_url"],
            "https://logs.example.com/artifacts/uuid-xyz",
        )

    def test_compose_and_plan_are_null(self):
        legacy = self.archive / "enge_jobs_archive_20260622140000"
        legacy.write_text("uuid-1\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)

        data = json.loads(list(self.runs.glob("*.json"))[0].read_text())
        req = data["requests"][0]
        self.assertIsNone(req["source_compose"])
        self.assertIsNone(req["target_compose"])
        self.assertIsNone(req["plan"])

    def test_touchfile_created(self):
        legacy = self.archive / "enge_jobs_archive_20260622140000"
        legacy.write_text("uuid-1\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)

        touchfile = self.archive / "enge_jobs_archive_20260622140000.migrated"
        self.assertTrue(touchfile.exists())

    def test_idempotent_skips_already_migrated(self):
        legacy = self.archive / "enge_jobs_archive_20260622140000"
        legacy.write_text("uuid-1\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)
        first_manifests = set(p.name for p in self.runs.glob("*.json"))

        migrate_main(ctx)
        second_manifests = set(p.name for p in self.runs.glob("*.json"))

        self.assertEqual(first_manifests, second_manifests)

    def test_context_extraction_from_tags(self):
        legacy = self.archive / "enge_jobs_archive_20260622140000.smoke.tier0.x86_64"
        legacy.write_text("uuid-1\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)

        data = json.loads(list(self.runs.glob("*.json"))[0].read_text())
        self.assertEqual(data["context"].get("set"), "smoke")
        self.assertEqual(data["context"].get("tiers"), ["tier0"])
        self.assertEqual(data["context"].get("architectures"), ["x86_64"])

    def test_nonexistent_archive_dir(self):
        ctx = _make_ctx(self.tmp / "nonexistent", self.runs)
        result = migrate_main(ctx)
        self.assertEqual(result, 0)

    def test_timestamp_parsed_from_filename(self):
        legacy = self.archive / "enge_jobs_archive_20260622143051"
        legacy.write_text("uuid-1\n")

        ctx = _make_ctx(self.archive, self.runs)
        migrate_main(ctx)

        data = json.loads(list(self.runs.glob("*.json"))[0].read_text())
        self.assertEqual(data["created_at"], "2026-06-22T14:30:51Z")


if __name__ == "__main__":
    unittest.main()
