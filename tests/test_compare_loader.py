"""RED tests for enge.compare.loader: manifest resolution -> results.json
loading -> unified floor policy -> per-task descriptor sourcing.

Data source is results.json caches ONLY (parse_results_json) -- resolution
of *which* manifests/run_ids are in scope goes through the shared
`resolve_manifests_for_invocation` (not re-forked here). Missing-cache
policy (fire-time ruling, unchanged by the redesign): a matched run with
no results.json logs an ERROR naming the run and the exact fix command
(`enge report --run <run_id>`).

Unified floor (C1, replaces the old mode-aware >=2-manifests /
>=1-column split): a single floor of >=1 comparable column, else
ExitCode.CONFIG_ERROR -- the established usage/configuration-error code;
TEST_FAILURE/TEST_ERROR/MISSING_RESULTS describe grading outcomes and no
grading occurs here. The old consolidation-mode >=2-matched-manifest
floor is gone entirely; a single manifest fanned across many requests
(one enge dispatch) is now always sufficient on its own.

Descriptor sourcing (item 7, the read-side half of the M4 fix):
`ExecutionColumn.source`/`.target` come from the PER-TASK
`TaskEntry.source`/`.target` fields written by the dispatch-context-schema
branch. Fallback to the run envelope's `source`/`target` applies ONLY
when the per-task value is None AND the manifest is single-set (mirrors
`report/results_cache.py`'s own `is_single_set` harvest-time fallback);
a multi-set manifest with no per-task value gets no fallback and stays
None.
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


class TestUnifiedFloorAndMissingCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs_dir = Path(self.tmp.name) / "runs"
        self.results_dir = Path(self.tmp.name) / "results"

    def _ctx(self, **cli_overrides):
        cli = {
            "run": None,
            "filter_set": ["setA"],
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
        }
        cli.update(cli_overrides)
        return make_app_context(
            action="compare",
            extra_cli=cli,
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

    def test_a_single_matched_run_is_now_sufficient_on_its_own(self):
        """C1: the old consolidation-mode >=2-matched-manifest floor is
        gone. A lone manifest with a usable cache is enough."""
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_results_json(self.results_dir, "run1", tasks=[_task_entry("t1")])

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        self.assertEqual({c.task_id for c in columns}, {"t1"})

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

    def test_zero_comparable_columns_returns_config_error(self):
        from enge.compare.loader import load_columns

        columns, error_code = load_columns(self._ctx())

        self.assertEqual(error_code, ExitCode.CONFIG_ERROR)
        self.assertEqual(columns, [])

    def test_a_single_manifest_fanned_across_many_arches_is_sufficient(self):
        """Generalizes the old AMENDMENT-1 flakiness-only case: one enge
        dispatch producing one manifest with N requests (e.g. per arch)
        is now, unconditionally, enough columns to compare -- there is no
        separate mode with a stricter floor anymore."""
        from enge.compare.loader import load_columns

        requests = [
            _request(f"t{i}", arch=arch)
            for i, arch in enumerate(["x86_64", "aarch64", "s390x", "ppc64le"], start=1)
        ]
        _write_manifest(self.runs_dir, "run1", requests, context={"set": "setA"})
        tasks = [
            _task_entry(f"t{i}", arch=arch)
            for i, arch in enumerate(["x86_64", "aarch64", "s390x", "ppc64le"], start=1)
        ]
        _write_results_json(self.results_dir, "run1", tasks=tasks)

        columns, error_code = load_columns(self._ctx(run="run1", filter_set=None))

        self.assertIsNone(error_code)
        self.assertEqual(len(columns), 4)

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


class TestPerTaskDescriptorSourcing(unittest.TestCase):
    """Item 7: ExecutionColumn.source/target come from the per-task
    TaskEntry fields, falling back to the run envelope only for
    single-set manifests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs_dir = Path(self.tmp.name) / "runs"
        self.results_dir = Path(self.tmp.name) / "results"

    def _ctx(self, **cli_overrides):
        cli = {
            "run": "run1",
            "filter_set": None,
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
        }
        cli.update(cli_overrides)
        return make_app_context(
            action="compare",
            extra_cli=cli,
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
        )

    def test_per_task_value_wins_over_the_envelope_even_when_both_present(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir, "run1", [_request("t1")], context={"set": "setA"}
        )
        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[_task_entry("t1", source="9.6", target="10.0")],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        (column,) = columns
        self.assertEqual(column.source, "9.6")
        self.assertEqual(column.target, "10.0")

    def test_single_set_manifest_falls_back_to_envelope_when_task_value_missing(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir,
            "run1",
            [_request("t1", set="setA"), _request("t2", set="setA")],
            context={"set": "setA"},
        )
        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[
                _task_entry("t1", set="setA"),  # no per-task source/target
                _task_entry("t2", set="setA"),
            ],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        for col in columns:
            self.assertEqual(col.source, "9.9")
            self.assertEqual(col.target, "10.3")

    def test_multi_set_manifest_with_no_task_value_gets_no_fallback(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir,
            "run1",
            [_request("t1", set="setA"), _request("t2", set="setB")],
            context={"set": "multi"},
        )
        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[
                _task_entry("t1", set="setA"),  # no per-task source/target
                _task_entry("t2", set="setB"),
            ],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        for col in columns:
            self.assertIsNone(col.source)
            self.assertIsNone(col.target)

    def test_multi_set_manifest_still_uses_per_task_value_when_present(self):
        from enge.compare.loader import load_columns

        _write_manifest(
            self.runs_dir,
            "run1",
            [_request("t1", set="setA"), _request("t2", set="setB")],
            context={"set": "multi"},
        )
        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[
                _task_entry("t1", set="setA", source="9.9", target="10.3"),
                _task_entry("t2", set="setB", source="10.3", target="10.4"),
            ],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        by_id = {c.task_id: c for c in columns}
        self.assertEqual(by_id["t1"].source, "9.9")
        self.assertEqual(by_id["t1"].target, "10.3")
        self.assertEqual(by_id["t2"].source, "10.3")
        self.assertEqual(by_id["t2"].target, "10.4")


class TestManifestSchemaIndependence(unittest.TestCase):
    """Compare's column data comes from results.json exclusively; a
    manifest is consulted only for run *selection*
    (resolve_manifests_for_invocation), never for column content
    (artifacts_url, the single-set gate). These tests fail on a loader
    that still reads manifest['requests'] for that data."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs_dir = Path(self.tmp.name) / "runs"
        self.results_dir = Path(self.tmp.name) / "results"

    def _write_manifest_no_requests(self, run_id):
        manifest = {"schema_version": 1, "run_id": run_id}
        Path(self.runs_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.runs_dir) / f"{run_id}.json").write_text(json.dumps(manifest))

    def _ctx(self, log_artifact_baseurl="https://artifacts.example.com/base"):
        cli = {
            "run": "run1",
            "filter_set": None,
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
        }
        return make_app_context(
            action="compare",
            extra_cli=cli,
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
            log_artifact_baseurl=log_artifact_baseurl,
        )

    def test_columns_populate_fully_when_manifest_has_no_requests_key(self):
        """The decoupling pin: a manifest with no 'requests' key at all
        still yields fully-populated columns, because artifacts_url/
        source/target come from the results.json task entry directly."""
        from enge.compare.loader import load_columns

        self._write_manifest_no_requests("run1")
        _write_results_json(
            self.results_dir,
            "run1",
            tasks=[
                _task_entry(
                    "t1",
                    set="setA",
                    source="9.6",
                    target="10.0",
                    artifacts_url="https://stored.example.com/artifacts/t1",
                )
            ],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        (column,) = columns
        self.assertEqual(
            column.artifacts_url, "https://stored.example.com/artifacts/t1"
        )
        self.assertEqual(column.source, "9.6")
        self.assertEqual(column.target, "10.0")

    def test_stored_artifacts_url_is_preferred_over_constructed_value(self):
        from enge.compare.loader import load_columns

        self._write_manifest_no_requests("run1")
        _write_results_json(
            self.results_dir,
            "run1",
            tasks=[
                _task_entry(
                    "t1", artifacts_url="https://stored.example.com/mismatched/t1"
                )
            ],
        )

        columns, error_code = load_columns(
            self._ctx(log_artifact_baseurl="https://artifacts.example.com/base")
        )

        self.assertIsNone(error_code)
        (column,) = columns
        self.assertEqual(
            column.artifacts_url, "https://stored.example.com/mismatched/t1"
        )

    def test_missing_artifacts_url_falls_back_to_constructed_url(self):
        from enge.compare.loader import load_columns

        self._write_manifest_no_requests("run1")
        _write_results_json(self.results_dir, "run1", tasks=[_task_entry("t1")])

        columns, error_code = load_columns(
            self._ctx(log_artifact_baseurl="https://artifacts.example.com/base")
        )

        self.assertIsNone(error_code)
        (column,) = columns
        self.assertEqual(column.artifacts_url, "https://artifacts.example.com/base/t1")

    def test_empty_string_artifacts_url_falls_back_to_constructed_url(self):
        from enge.compare.loader import load_columns

        self._write_manifest_no_requests("run1")
        _write_results_json(
            self.results_dir, "run1", tasks=[_task_entry("t1", artifacts_url="")]
        )

        columns, error_code = load_columns(
            self._ctx(log_artifact_baseurl="https://artifacts.example.com/base")
        )

        self.assertIsNone(error_code)
        (column,) = columns
        self.assertEqual(column.artifacts_url, "https://artifacts.example.com/base/t1")
        self.assertNotEqual(column.artifacts_url, "")

    def test_legacy_single_set_cache_gate_resourced_from_results_json_not_manifest(
        self,
    ):
        """The manifest LOOKS multi-set (two distinct request sets), but
        the results.json cache is genuinely single-set. The gate must
        follow the cache, not the manifest."""
        from enge.compare.loader import load_columns

        manifest = {
            "schema_version": 1,
            "run_id": "run1",
            "requests": [
                {"task_id": "t1", "set": "setA"},
                {"task_id": "t2", "set": "setB"},
            ],
        }
        Path(self.runs_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.runs_dir) / "run1.json").write_text(json.dumps(manifest))

        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[
                _task_entry("t1", set="setA"),
                _task_entry("t2", set="setA"),  # single set in the cache
            ],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        for col in columns:
            self.assertEqual(col.source, "9.9")
            self.assertEqual(col.target, "10.3")

    def test_legacy_multiset_cache_gate_resourced_from_results_json_not_manifest(self):
        """M4 guard, re-sourced: the manifest has no 'requests' key at
        all (the old loader's manifest-derived gate defaulted this shape
        to is_single_set=True), but the results.json cache is genuinely
        multi-set. Envelope values must NOT be smeared onto columns
        lacking a per-task source/target."""
        from enge.compare.loader import load_columns

        self._write_manifest_no_requests("run1")
        _write_results_json(
            self.results_dir,
            "run1",
            source="9.9",
            target="10.3",
            tasks=[
                _task_entry("t1", set="setA"),
                _task_entry("t2", set="setB"),
            ],
        )

        columns, error_code = load_columns(self._ctx())

        self.assertIsNone(error_code)
        for col in columns:
            self.assertIsNone(col.source)
            self.assertIsNone(col.target)


if __name__ == "__main__":
    unittest.main()
