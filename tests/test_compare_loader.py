"""RED tests for enge.compare.loader: manifest resolution -> results.json
loading -> missing-cache policy.

Data source is results.json caches ONLY (parse_results_json) -- resolution
of *which* manifests/run_ids are in scope goes through the shared
`resolve_manifests_for_invocation` (not re-forked here). Missing-cache
policy (fire-time ruling, no override): a matched run with no results.json
logs an ERROR naming the run and the exact fix command
(`enge report --run <run_id>`); compare proceeds if >=2 result sources
still have a usable cache, else returns the usage/data error code
(ExitCode.CONFIG_ERROR -- the established usage/configuration-error code;
TEST_FAILURE/TEST_ERROR/MISSING_RESULTS describe grading outcomes and no
grading occurred).
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests._helpers import make_app_context
from enge.utils.globals import ExitCode


def _write_manifest(runs_dir, run_id, requests, context=None, created_at=None):
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created_at or "2026-07-07T10:35:07Z",
        "command": "test",
        "argv": ["enge", "test"],
        "tags": [],
        "parent_run_id": None,
        "origin": "native",
        "context": (
            context
            if context is not None
            else {"event": "preliminary", "source": "9.9", "target": "10.3"}
        ),
        "requests": requests,
    }
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    (Path(runs_dir) / f"{run_id}.json").write_text(json.dumps(manifest))
    return manifest


def _request(task_id, **overrides):
    base = {
        "task_id": task_id,
        "set": "setA",
        "tier": "tier1",
        "arch": "x86_64",
        "plan": None,
        "source_compose": "RHEL-9.9.0-1",
        "target_compose": None,
        "artifacts_url": f"https://tf.example.com/artifacts/{task_id}",
        "dispatched_at": "2026-07-07T10:35:13Z",
        "launch_uuid": None,
    }
    base.update(overrides)
    return base


def _write_results_json(
    results_dir_path,
    run_id,
    *,
    source="9.9",
    target="10.3",
    created_at=None,
    tasks=None,
):
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": created_at or "2026-07-07T10:35:07Z",
        "event": "preliminary",
        "source": source,
        "target": target,
        "verdict": "PASSED",
        "results": tasks or [],
    }
    Path(results_dir_path).mkdir(parents=True, exist_ok=True)
    (Path(results_dir_path) / f"{run_id}.json").write_text(json.dumps(payload))


def _task_entry(task_id, **overrides):
    base = {
        "task_id": task_id,
        "set": "setA",
        "tier": "tier1",
        "arch": "x86_64",
        "source_compose": "RHEL-9.9.0-1",
        "target_compose": None,
        "dispatched_at": "2026-07-07T10:35:13Z",
        "verdict": "PASSED",
        "total_duration_seconds": 12.0,
        "plans": [
            {
                "name": "/plans/p1",
                "verdict": "PASSED",
                "tests": [
                    {"name": "/tests/x", "verdict": "PASSED", "duration_seconds": 12.0}
                ],
            }
        ],
    }
    base.update(overrides)
    return base


class TestMissingCachePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs_dir = Path(self.tmp.name) / "runs"
        self.results_dir = Path(self.tmp.name) / "results"

    def _ctx(self):
        return make_app_context(
            action="compare",
            extra_cli={
                "run": None,
                "filter_set": ["setA"],
                "filter_tier": None,
                "filter_arch": None,
                "filter_tag": None,
            },
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
        )

    def test_two_valid_runs_load_successfully(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_manifest(
            self.runs_dir, "run2", [_request("t2")], context={"set": "setA"}
        )
        _write_results_json(self.results_dir, "run1", tasks=[_task_entry("t1")])
        _write_results_json(self.results_dir, "run2", tasks=[_task_entry("t2")])

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        self.assertEqual({c.task_id for c in columns}, {"t1", "t2"})

    def test_one_missing_cache_of_two_runs_still_succeeds_and_logs_error(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_manifest(
            self.runs_dir, "run2", [_request("t2")], context={"set": "setA"}
        )
        _write_manifest(
            self.runs_dir, "run3", [_request("t3")], context={"set": "setA"}
        )
        _write_results_json(self.results_dir, "run1", tasks=[_task_entry("t1")])
        _write_results_json(self.results_dir, "run2", tasks=[_task_entry("t2")])
        # run3 has no results.json cache at all.

        with self.assertLogs("enge.compare.loader", level="ERROR") as cm:
            columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        self.assertEqual({c.task_id for c in columns}, {"t1", "t2"})
        self.assertTrue(any("run3" in msg for msg in cm.output))
        self.assertTrue(any("enge report --run run3" in msg for msg in cm.output))

    def test_fewer_than_two_valid_sources_returns_config_error(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_manifest(
            self.runs_dir, "run2", [_request("t2")], context={"set": "setA"}
        )
        _write_results_json(self.results_dir, "run1", tasks=[_task_entry("t1")])
        # run2 has no results.json cache -- only 1 valid source remains.

        with self.assertLogs("enge.compare.loader", level="ERROR"):
            columns, error_code = load_columns(self._ctx())

        self.assertEqual(error_code, ExitCode.CONFIG_ERROR)
        self.assertEqual(columns, [])

    def test_zero_matched_runs_returns_config_error(self):
        from enge.compare.loader import load_columns

        columns, error_code = load_columns(self._ctx())

        self.assertEqual(error_code, ExitCode.CONFIG_ERROR)
        self.assertEqual(columns, [])

    def test_columns_carry_run_envelope_source_and_target_not_task_compose(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_manifest(
            self.runs_dir, "run2", [_request("t2")], context={"set": "setA"}
        )
        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[_task_entry("t1")],
        )
        _write_results_json(
            self.results_dir,
            "run2",
            source="9.9",
            target="10.3",
            tasks=[_task_entry("t2")],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        for col in columns:
            self.assertEqual(col.source, "9.9")
            self.assertEqual(col.target, "10.3")

    def test_columns_carry_artifacts_url_from_manifest_request_metadata(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_manifest(
            self.runs_dir, "run2", [_request("t2")], context={"set": "setA"}
        )
        _write_results_json(self.results_dir, "run1", tasks=[_task_entry("t1")])
        _write_results_json(self.results_dir, "run2", tasks=[_task_entry("t2")])

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        by_id = {c.task_id: c for c in columns}
        self.assertEqual(
            by_id["t1"].artifacts_url, "https://tf.example.com/artifacts/t1"
        )


class TestModeAwareGating(unittest.TestCase):
    """AMENDMENT-1: a single manifest fanned across multiple arches (the
    documented one-dispatch-N-requests shape) is exactly ONE matched run,
    but carries multiple comparable columns. Flakiness mode must gate on
    column count (>=1); consolidation mode's >=2-matched-run floor is
    unchanged (see CLAUDE.md "Compare consolidation policy" -- untouched
    by this amendment)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs_dir = Path(self.tmp.name) / "runs"
        self.results_dir = Path(self.tmp.name) / "results"

    def _ctx(self, run_id):
        return make_app_context(
            action="compare",
            extra_cli={
                "run": run_id,
                "filter_set": None,
                "filter_tier": None,
                "filter_arch": None,
                "filter_tag": None,
            },
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
        )

    def _write_single_manifest_multi_arch(self, run_id, arches):
        requests = [
            _request(f"t{i}", arch=arch) for i, arch in enumerate(arches, start=1)
        ]
        _write_manifest(self.runs_dir, run_id, requests, context={"set": "setA"})
        tasks = [
            _task_entry(f"t{i}", arch=arch) for i, arch in enumerate(arches, start=1)
        ]
        _write_results_json(self.results_dir, run_id, tasks=tasks)

    def test_flakiness_mode_renders_from_a_single_run_id_manifest(self):
        from enge.compare.engine import build_flakiness_tables
        from enge.compare.loader import load_columns

        self._write_single_manifest_multi_arch(
            "run1", ["x86_64", "aarch64", "s390x", "ppc64le"]
        )

        columns, error_code = load_columns(self._ctx("run1"), flakiness=True)

        self.assertIsNone(error_code)
        self.assertEqual(len(columns), 4)

        (table,) = build_flakiness_tables(columns, show_tests=False)
        self.assertEqual(len(table.columns), 4)

    def test_consolidation_mode_still_refuses_a_single_run_id_manifest(self):
        from enge.compare.loader import load_columns

        self._write_single_manifest_multi_arch(
            "run1", ["x86_64", "aarch64", "s390x", "ppc64le"]
        )

        columns, error_code = load_columns(self._ctx("run1"))

        self.assertEqual(error_code, ExitCode.CONFIG_ERROR)
        self.assertEqual(columns, [])

    def test_flakiness_mode_zero_comparable_columns_returns_config_error(self):
        """CLAUDE.md ruling-relay pin (2026-07-21): flakiness mode's floor
        is >=1 comparable column, not >=0 -- a selector matching zero
        manifests is still a usage error, same CONFIG_ERROR (99) as
        consolidation mode's floor miss. Characterization pin: already
        green under the AMENDMENT-1 implementation, disclosed per the
        conditional-disclosure rule rather than forced red."""
        from enge.compare.loader import load_columns

        ctx = make_app_context(
            action="compare",
            extra_cli={
                "run": None,
                "filter_set": None,
                "filter_tier": ["no-such-tier"],
                "filter_arch": None,
                "filter_tag": None,
            },
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
        )

        columns, error_code = load_columns(ctx, flakiness=True)

        self.assertEqual(error_code, ExitCode.CONFIG_ERROR)
        self.assertEqual(columns, [])


if __name__ == "__main__":
    unittest.main()
