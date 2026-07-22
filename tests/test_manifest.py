import json
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from enge.utils.manifest import ManifestReader, ManifestWriter, SCHEMA_VERSION
from enge.utils.ulid import generate_ulid


class TestManifestWriter(unittest.TestCase):
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

    def test_to_dict_schema(self):
        w = self._writer(tags=["pre-release"], context={"set": "smoke"})
        w.add_request("uuid-1", set_name="smoke", tier="tier0", arch="x86_64")
        d = w.to_dict()
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        self.assertEqual(d["command"], "test")
        self.assertEqual(d["origin"], "native")
        self.assertIsNone(d["parent_run_id"])
        self.assertEqual(len(d["requests"]), 1)
        self.assertEqual(d["requests"][0]["task_id"], "uuid-1")

    def test_add_request_emits_rerun_of_key_null_by_default(self):
        w = self._writer()
        w.add_request("uuid-1", tier="tier0", arch="x86_64")
        entry = w.to_dict()["requests"][0]
        self.assertIn("rerun_of", entry)
        self.assertIsNone(entry["rerun_of"])

    def test_add_request_emits_rerun_of_value_when_provided(self):
        w = self._writer()
        w.add_request("uuid-1", tier="tier0", arch="x86_64", rerun_of="parent-uuid")
        entry = w.to_dict()["requests"][0]
        self.assertEqual(entry["rerun_of"], "parent-uuid")

    def test_add_request_emits_dispatch_context_keys_default_when_absent(self):
        """source/target/git_ref/event default to None, build_ids to []."""
        w = self._writer()
        w.add_request("uuid-1", tier="tier0", arch="x86_64")
        entry = w.to_dict()["requests"][0]
        for key in ("source", "target", "git_ref", "event"):
            self.assertIn(key, entry)
            self.assertIsNone(entry[key])
        self.assertIn("build_ids", entry)
        self.assertEqual(entry["build_ids"], [])

    def test_add_request_emits_dispatch_context_keys_when_provided(self):
        w = self._writer()
        w.add_request(
            "uuid-1",
            tier="tier0",
            arch="x86_64",
            source="9.9",
            target="10.3",
            git_ref="main",
            event="preliminary",
            build_ids=["12345:centos-stream9-x86_64"],
        )
        entry = w.to_dict()["requests"][0]
        self.assertEqual(entry["source"], "9.9")
        self.assertEqual(entry["target"], "10.3")
        self.assertEqual(entry["git_ref"], "main")
        self.assertEqual(entry["event"], "preliminary")
        self.assertEqual(entry["build_ids"], ["12345:centos-stream9-x86_64"])

    def test_flush_creates_valid_json(self):
        w = self._writer()
        for i in range(3):
            w.add_request(f"uuid-{i}", tier="tier0", arch="x86_64")
        path = w.flush(self.runs_dir, self.latest)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(len(data["requests"]), 3)

    def test_crash_safety_partial_flush(self):
        """After 2 requests + flush, on-disk manifest has 2 entries."""
        w = self._writer()
        w.add_request("uuid-0", tier="tier0", arch="x86_64")
        w.add_request("uuid-1", tier="tier1", arch="x86_64")
        path = w.flush(self.runs_dir, self.latest)
        data = json.loads(path.read_text())
        self.assertEqual(len(data["requests"]), 2)
        self.assertEqual(data["requests"][0]["task_id"], "uuid-0")
        self.assertEqual(data["requests"][1]["task_id"], "uuid-1")

    def test_latest_pointer_tracks_newest_run(self):
        w1 = self._writer(run_id=generate_ulid())
        w1.add_request("uuid-a")
        path1 = w1.flush(self.runs_dir, self.latest)

        time.sleep(0.002)
        w2 = self._writer(run_id=generate_ulid())
        w2.add_request("uuid-b")
        path2 = w2.flush(self.runs_dir, self.latest)

        self.assertEqual(self.latest.read_text().strip(), str(path2))
        self.assertTrue(path1.exists())

    def test_rerun_lineage(self):
        parent_id = generate_ulid()
        w = self._writer(
            command="rerun",
            parent_run_id=parent_id,
            tags=["inherited-tag", "rerun"],
        )
        w.add_request("child-uuid", tier="tier0", arch="x86_64")
        d = w.to_dict()
        self.assertEqual(d["parent_run_id"], parent_id)
        self.assertEqual(d["command"], "rerun")
        self.assertIn("inherited-tag", d["tags"])

    def test_no_tmp_files_left_after_flush(self):
        w = self._writer()
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)
        tmp_files = list(self.runs_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])


class TestManifestReader(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_manifest(self, **kwargs):
        defaults = {
            "run_id": generate_ulid(),
            "command": "test",
            "argv": ["enge", "test"],
        }
        defaults.update(kwargs)
        w = ManifestWriter(**defaults)
        return w

    def test_load_latest(self):
        w = self._write_manifest()
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)
        loaded = ManifestReader.load_latest(self.latest)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["requests"][0]["task_id"], "uuid-1")

    def test_load_latest_missing(self):
        self.assertIsNone(ManifestReader.load_latest(self.latest))

    def test_list_runs_sorted_descending(self):
        ids = []
        for i in range(3):
            rid = generate_ulid()
            ids.append(rid)
            w = self._write_manifest(run_id=rid)
            w.add_request(f"uuid-{i}")
            w.flush(self.runs_dir, self.latest)
            time.sleep(0.002)

        summaries = ManifestReader.list_runs(self.runs_dir)
        self.assertEqual(len(summaries), 3)
        self.assertEqual(summaries[0]["run_id"], ids[2])
        self.assertEqual(summaries[2]["run_id"], ids[0])

    def test_list_runs_sets_multi_set_order(self):
        w = self._write_manifest(context={"set": "alpha"})
        w.add_request("uuid-1", set_name="alpha")
        w.add_request("uuid-2", set_name="beta")
        w.add_request("uuid-3", set_name="alpha")
        w.flush(self.runs_dir, self.latest)

        summaries = ManifestReader.list_runs(self.runs_dir)
        self.assertEqual(summaries[0]["sets"], ["alpha", "beta"])

    def test_list_runs_sets_single_set(self):
        w = self._write_manifest(context={"set": "alpha"})
        w.add_request("uuid-1", set_name="alpha")
        w.flush(self.runs_dir, self.latest)

        summaries = ManifestReader.list_runs(self.runs_dir)
        self.assertEqual(summaries[0]["sets"], ["alpha"])

    def test_list_runs_sets_fallback_and_empty(self):
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
            "context": {"set": "alpha"},
            "requests": [{"task_id": "old-uuid"}],
        }
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.runs_dir / f"{migrated_id}.json").write_text(json.dumps(migrated))

        no_set_id = generate_ulid()
        no_set = {
            "schema_version": 1,
            "run_id": no_set_id,
            "created_at": "2026-01-01T00:00:01Z",
            "command": "test",
            "argv": [],
            "tags": [],
            "parent_run_id": None,
            "origin": "migrated",
            "context": {},
            "requests": [{"task_id": "old-uuid-2"}],
        }
        (self.runs_dir / f"{no_set_id}.json").write_text(json.dumps(no_set))

        summaries = ManifestReader.list_runs(self.runs_dir)
        by_id = {s["run_id"]: s for s in summaries}
        self.assertEqual(by_id[migrated_id]["sets"], ["alpha"])
        self.assertEqual(by_id[no_set_id]["sets"], [])

    def test_find_runs_by_set(self):
        w1 = self._write_manifest(context={"set": "smoke"})
        w1.add_request("uuid-1")
        w1.flush(self.runs_dir, self.latest)

        time.sleep(0.002)
        w2 = self._write_manifest(context={"set": "regression"})
        w2.add_request("uuid-2")
        w2.flush(self.runs_dir, self.latest)

        found = ManifestReader.find_runs(self.runs_dir, set_name="smoke")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["context"]["set"], "smoke")

    def test_find_runs_by_tag(self):
        w = self._write_manifest(tags=["nightly"])
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)

        found = ManifestReader.find_runs(self.runs_dir, tag="nightly")
        self.assertEqual(len(found), 1)
        no_match = ManifestReader.find_runs(self.runs_dir, tag="other")
        self.assertEqual(len(no_match), 0)

    def test_find_runs_by_tier(self):
        w = self._write_manifest()
        w.add_request("uuid-1", tier="tier0", arch="x86_64")
        w.flush(self.runs_dir, self.latest)

        found = ManifestReader.find_runs(self.runs_dir, tier="tier0")
        self.assertEqual(len(found), 1)
        no_match = ManifestReader.find_runs(self.runs_dir, tier="tier3")
        self.assertEqual(len(no_match), 0)

    def test_find_runs_by_arch(self):
        w = self._write_manifest()
        w.add_request("uuid-1", tier="tier0", arch="ppc64le")
        w.flush(self.runs_dir, self.latest)

        found = ManifestReader.find_runs(self.runs_dir, arch="ppc64le")
        self.assertEqual(len(found), 1)
        no_match = ManifestReader.find_runs(self.runs_dir, arch="s390x")
        self.assertEqual(len(no_match), 0)

    def test_find_runs_by_since_until(self):
        w = self._write_manifest()
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)

        now = datetime.now(timezone.utc)
        found = ManifestReader.find_runs(
            self.runs_dir,
            since=now - timedelta(minutes=1),
            until=now + timedelta(minutes=1),
        )
        self.assertEqual(len(found), 1)

        too_late = ManifestReader.find_runs(
            self.runs_dir, since=now + timedelta(hours=1)
        )
        self.assertEqual(len(too_late), 0)

    def test_get_run_by_id(self):
        rid = generate_ulid()
        w = self._write_manifest(run_id=rid)
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)

        data = ManifestReader.get_run(self.runs_dir, rid)
        self.assertIsNotNone(data)
        self.assertEqual(data["run_id"], rid)

    def test_get_run_by_prefix(self):
        rid = generate_ulid()
        w = self._write_manifest(run_id=rid)
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)

        data = ManifestReader.get_run(self.runs_dir, rid[:8])
        self.assertIsNotNone(data)
        self.assertEqual(data["run_id"], rid)

    def test_get_run_ambiguous_prefix_raises(self):
        from enge.utils.errors import ValidationError

        rid1 = "01AAAAAA" + generate_ulid()[8:]
        rid2 = "01AAAAAB" + generate_ulid()[8:]
        for rid in (rid1, rid2):
            w = self._write_manifest(run_id=rid)
            w.add_request("uuid-1")
            w.flush(self.runs_dir, self.latest)

        with self.assertRaises(ValidationError) as cm:
            ManifestReader.get_run(self.runs_dir, "01AAAAA")
        self.assertIn("matches 2 runs", str(cm.exception))

    def test_get_run_missing_raises(self):
        from enge.utils.errors import ValidationError

        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(ValidationError) as cm:
            ManifestReader.get_run(self.runs_dir, "NONEXISTENT")
        self.assertIn("No run matching", str(cm.exception))

    def test_get_task_ids(self):
        w = self._write_manifest()
        w.add_request("aaa")
        w.add_request("bbb")
        w.add_request("ccc")
        ids = ManifestReader.get_task_ids(w.to_dict())
        self.assertEqual(ids, ["aaa", "bbb", "ccc"])

    def test_list_runs_empty_dir(self):
        self.assertEqual(ManifestReader.list_runs(self.runs_dir), [])

    def test_list_runs_skips_corrupt_json(self):
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.runs_dir / "corrupt.json").write_text("{bad json")
        w = self._write_manifest()
        w.add_request("uuid-1")
        w.flush(self.runs_dir, self.latest)
        summaries = ManifestReader.list_runs(self.runs_dir)
        self.assertEqual(len(summaries), 1)


if __name__ == "__main__":
    unittest.main()
