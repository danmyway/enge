"""Item 5.2: `enge.compare.loader.load_lineage_columns` -- the lineage-mode
loader (contract v3 §5a, ruling Q-L5-1).

Selection reads manifests for `run_id` only; lineage discovery walks every
cached `<run_id>.json` under `results_dir` via `rerun_of`. The returned
columns are the CLOSURE of the selected tasks' lineages -- a rerun's run
resolves to the same closure as its root's run -- and every WARNING the
directory walk can raise is aggregated into one line per invocation.

`load_columns` (normal mode) is characterized here too: the shared
task -> ExecutionColumn mapping must leave its output field-for-field
unchanged on `origin/devel`'s field set.
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path

from tests._helpers import captured_logs, make_app_context, matching
from enge.utils.globals import ExitCode

A1 = "/plans/a"

# ExecutionColumn's fields on origin/devel, before item 5.2 appended the
# lineage fields. The characterization test below pins exactly these.
_DEVEL_FIELDS = (
    "task_id",
    "run_id",
    "set",
    "tier",
    "arch",
    "source",
    "target",
    "source_compose",
    "target_compose",
    "dispatched_at",
    "run_created_at",
    "plans",
    "artifacts_url",
)


def _write_manifest(runs_dir, run_id, task_ids=()):
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-10-01T00:00:00Z",
        "command": "test",
        "argv": ["enge", "test"],
        "tags": [],
        "parent_run_id": None,
        "origin": "native",
        "context": {"event": "preliminary", "source": "8.10", "target": "9.6"},
        "requests": [
            {
                "task_id": task_id,
                "set": "s",
                "tier": "tier0",
                "arch": "x86_64",
                "plan": None,
                "source_compose": "RHEL-8.10.0-Nightly",
                "target_compose": None,
                "artifacts_url": None,
                "dispatched_at": "2026-10-01T00:00:00Z",
                "launch_uuid": None,
            }
            for task_id in task_ids
        ],
    }
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    path = Path(runs_dir) / f"{run_id}.json"
    path.write_text(json.dumps(manifest))
    return path


def _task(task_id, verdict="FAILED", **overrides):
    """A task entry carrying every key the current writer emits."""
    base = {
        "task_id": task_id,
        "set": "s",
        "tier": "tier0",
        "arch": "x86_64",
        "source_compose": "RHEL-8.10.0-Nightly",
        "target_compose": "RHEL-9.6.0-Nightly",
        "dispatched_at": "2026-10-01T00:00:00Z",
        "verdict": verdict,
        "total_duration_seconds": 1.0,
        "plans": [
            {
                "name": A1,
                "verdict": verdict,
                "tests": [
                    {
                        "name": "/t/x.py::t1",
                        "verdict": verdict,
                        "duration_seconds": 1.0,
                    }
                ],
            }
        ],
        "source": "8.10",
        "target": "9.6",
        "git_ref": None,
        "event": None,
        "build_ids": [],
        "rerun_of": None,
        "artifacts_url": f"https://a/{task_id}",
        "plan": None,
        "plan_filter": None,
        "tests": [],
    }
    base.update(overrides)
    return base


def _rerun(task_id, parent, dispatched_at, **overrides):
    return _task(
        task_id,
        rerun_of=parent,
        plan=A1,
        tests=["t1"],
        dispatched_at=dispatched_at,
        **overrides,
    )


def _write_results(results_dir, run_id, tasks, *, file_name=None):
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-10-01T00:00:00Z",
        "event": "preliminary",
        "source": "8.10",
        "target": "9.6",
        "verdict": "FAILED",
        "results": tasks,
    }
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    path = Path(results_dir) / (file_name or f"{run_id}.json")
    path.write_text(json.dumps(payload))
    return path


class _LoaderCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs_dir = self.root / "runs"
        self.results_dir = self.root / "results"

    def _ctx(self, run=None, latest=None):
        return make_app_context(
            action="compare",
            extra_cli={
                "run": run,
                "filter_set": None,
                "filter_tier": None,
                "filter_arch": None,
                "filter_tag": None,
                "lineage": True,
            },
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(latest or self.root / "latest"),
        )

    def _chain(self):
        """RUN0: root r; RUN1: c1 (rerun of r); RUN2: c2 (rerun of c1)."""
        _write_manifest(self.runs_dir, "RUN0", ["r"])
        _write_manifest(self.runs_dir, "RUN1", ["c1"])
        _write_manifest(self.runs_dir, "RUN2", ["c2"])
        _write_results(self.results_dir, "RUN0", [_task("r")])
        _write_results(
            self.results_dir, "RUN1", [_rerun("c1", "r", "2026-10-02T00:00:00Z")]
        )
        _write_results(
            self.results_dir,
            "RUN2",
            [_rerun("c2", "c1", "2026-10-03T00:00:00Z", verdict="PASSED")],
        )

    def _load(self, ctx):
        from enge.compare.loader import load_lineage_columns

        with captured_logs("enge.compare.loader") as records:
            result = load_lineage_columns(ctx)
        return result, records


class TestLineageClosure(_LoaderCase):
    def test_l1_selecting_the_root_run_yields_the_whole_lineage(self):
        self._chain()

        (columns, selected, error), _ = self._load(self._ctx(run=["RUN0"]))

        self.assertIsNone(error)
        self.assertEqual(selected, ["r"])
        self.assertEqual(sorted(c.task_id for c in columns), ["c1", "c2", "r"])

    def test_l2_selecting_a_rerun_run_yields_the_same_closure(self):
        self._chain()

        (columns, selected, error), _ = self._load(self._ctx(run=["RUN2"]))

        self.assertIsNone(error)
        self.assertEqual(selected, ["c2"])
        self.assertIn("r", {c.task_id for c in columns})
        self.assertEqual(sorted(c.task_id for c in columns), ["c1", "c2", "r"])

    def test_unrelated_lineages_stay_outside_the_closure(self):
        self._chain()
        _write_manifest(self.runs_dir, "RUN5", ["z"])
        _write_results(self.results_dir, "RUN5", [_task("z")])

        (columns, _, _), _ = self._load(self._ctx(run=["RUN0"]))

        self.assertNotIn("z", {c.task_id for c in columns})

    def test_no_run_selects_the_latest_manifest(self):
        self._chain()
        latest = self.root / "latest"
        latest.write_text(str(self.runs_dir / "RUN1.json"))

        (columns, selected, error), _ = self._load(self._ctx(latest=latest))

        self.assertIsNone(error)
        self.assertEqual(selected, ["c1"])
        self.assertEqual(sorted(c.task_id for c in columns), ["c1", "c2", "r"])

    def test_lineage_fields_are_carried_onto_the_columns(self):
        self._chain()

        (columns, _, _), _ = self._load(self._ctx(run=["RUN0"]))

        by_id = {c.task_id: c for c in columns}
        self.assertEqual(by_id["c2"].rerun_of, "c1")
        self.assertEqual(by_id["c2"].verdict, "PASSED")
        self.assertEqual(by_id["c2"].plan, A1)
        self.assertEqual(list(by_id["c2"].tests), ["t1"])
        self.assertEqual(by_id["c2"].run_id, "RUN2")


class TestLineageWarnings(_LoaderCase):
    def test_l3_unreadable_caches_give_exactly_one_warning(self):
        self._chain()
        self.results_dir.joinpath("zz_bad1.json").write_text("{not json")
        self.results_dir.joinpath("zz_bad2.json").write_text(json.dumps({"x": 1}))

        (columns, _, error), records = self._load(self._ctx(run=["RUN0"]))

        self.assertIsNone(error)
        self.assertEqual(len(columns), 3)
        warnings = matching(records, "unreadable results cache", level=logging.WARNING)
        self.assertEqual(
            warnings,
            [
                "compare: skipped 2 unreadable results cache(s) while resolving "
                "lineages (e.g. zz_bad1.json)"
            ],
        )

    def test_l4_duplicate_task_id_warns_once_and_first_file_wins(self):
        self._chain()
        _write_results(
            self.results_dir,
            "RUN1z",
            [
                _rerun(
                    "c1",
                    "r",
                    "2026-10-02T00:00:00Z",
                    artifacts_url="https://a/second",
                )
            ],
        )

        (columns, _, _), records = self._load(self._ctx(run=["RUN0"]))

        by_id = {c.task_id: c for c in columns}
        self.assertEqual(by_id["c1"].run_id, "RUN1")
        self.assertEqual(by_id["c1"].artifacts_url, "https://a/c1")
        self.assertEqual(
            matching(records, "more than one results cache", level=logging.WARNING),
            [
                "compare: 1 task id(s) appear in more than one results cache "
                "(e.g. c1); using the first cache in file-name order"
            ],
        )

    def test_l5_orphan_warning_counts_closure_members_only(self):
        self._chain()
        _write_manifest(self.runs_dir, "RUN3", ["o"])
        _write_results(
            self.results_dir, "RUN3", [_rerun("o", "gone", "2026-10-02T00:00:00Z")]
        )
        # An unrelated orphan elsewhere in the cache directory.
        _write_results(
            self.results_dir, "RUN4", [_rerun("o2", "gone2", "2026-10-02T00:00:00Z")]
        )

        (columns, _, _), records = self._load(self._ctx(run=["RUN3"]))

        self.assertEqual([c.task_id for c in columns], ["o"])
        self.assertEqual(
            matching(records, "no cached parent", level=logging.WARNING),
            [
                "compare: 1 rerun task(s) have no cached parent and are shown "
                "as their own lineage root (e.g. o)"
            ],
        )

    def test_l5_no_orphan_in_the_closure_means_no_warning(self):
        self._chain()
        _write_results(
            self.results_dir, "RUN4", [_rerun("o2", "gone2", "2026-10-02T00:00:00Z")]
        )

        _, records = self._load(self._ctx(run=["RUN0"]))

        self.assertEqual(matching(records, "no cached parent", level=None), [])

    def test_l6_stale_unselected_parent_cache_is_reported(self):
        _write_manifest(self.runs_dir, "RUN0", ["r"])
        _write_manifest(self.runs_dir, "RUN2", ["c2"])
        stale_root = _task("r")
        del stale_root["tests"]  # written by an enge that predates the key
        _write_results(self.results_dir, "RUN0", [stale_root])
        _write_results(
            self.results_dir, "RUN1", [_rerun("c1", "r", "2026-10-02T00:00:00Z")]
        )
        _write_results(
            self.results_dir, "RUN2", [_rerun("c2", "c1", "2026-10-03T00:00:00Z")]
        )

        _, records = self._load(self._ctx(run=["RUN2"]))

        self.assertEqual(
            matching(records, "older enge version", level=logging.WARNING),
            [
                "compare: 1 of 3 selected run(s) were cached by an older enge "
                "version (e.g. RUN0); refresh them with 'enge report "
                "<same selectors> --refresh'"
            ],
        )

    def test_stale_cache_outside_the_closure_is_not_reported(self):
        self._chain()
        stale = _task("z")
        del stale["tests"]
        _write_results(self.results_dir, "RUN5", [stale])

        _, records = self._load(self._ctx(run=["RUN0"]))

        self.assertEqual(matching(records, "older enge version", level=None), [])


class TestLineageFloor(_LoaderCase):
    def test_l7_missing_selected_cache_logs_error_and_hits_the_floor(self):
        _write_manifest(self.runs_dir, "RUN9", ["t9"])
        _write_results(self.results_dir, "RUN0", [_task("r")])

        (columns, selected, error), records = self._load(self._ctx(run=["RUN9"]))

        self.assertEqual((columns, selected), ([], []))
        self.assertEqual(error, ExitCode.CONFIG_ERROR)
        self.assertEqual(
            matching(records, "RUN9", level=logging.ERROR),
            [
                "compare: no results cache for run RUN9; generate it first "
                "with 'enge report --run RUN9'"
            ],
        )

    def test_corrupted_selected_cache_logs_error_and_is_skipped(self):
        self._chain()
        _write_manifest(self.runs_dir, "RUN7", ["t7"])
        self.results_dir.joinpath("RUN7.json").write_text(json.dumps({"x": 1}))

        (columns, selected, error), records = self._load(
            self._ctx(run=["RUN0", "RUN7"])
        )

        self.assertIsNone(error)
        self.assertEqual(selected, ["r"])
        self.assertEqual(
            matching(records, "RUN7", level=logging.ERROR),
            [
                "compare: results cache for run RUN7 is corrupted; "
                "regenerate it with 'enge report --run RUN7'"
            ],
        )


class TestNormalModeCharacterization(_LoaderCase):
    def _normal_ctx(self):
        ctx = self._ctx(run=["RUN1"])
        del ctx.cli_args.lineage
        return ctx

    def test_l8_load_columns_output_is_unchanged(self):
        from enge.compare.engine import ExecutionColumn
        from enge.compare.loader import load_columns
        from enge.utils.results_parser import PlanEntry, TestEntry

        self._chain()

        columns, error = load_columns(self._normal_ctx())

        self.assertIsNone(error)
        self.assertEqual(len(columns), 1)
        expected = ExecutionColumn(
            task_id="c1",
            run_id="RUN1",
            set="s",
            tier="tier0",
            arch="x86_64",
            source="8.10",
            target="9.6",
            source_compose="RHEL-8.10.0-Nightly",
            target_compose="RHEL-9.6.0-Nightly",
            dispatched_at="2026-10-02T00:00:00Z",
            run_created_at="2026-10-01T00:00:00Z",
            plans=[
                PlanEntry(
                    name=A1,
                    verdict="FAILED",
                    tests=[
                        TestEntry(
                            name="/t/x.py::t1", verdict="FAILED", duration_seconds=1.0
                        )
                    ],
                )
            ],
            artifacts_url="https://a/c1",
        )
        for name in _DEVEL_FIELDS:
            with self.subTest(field=name):
                self.assertEqual(getattr(columns[0], name), getattr(expected, name))

    def test_load_columns_fills_the_lineage_fields_too(self):
        from enge.compare.loader import load_columns

        self._chain()

        columns, _ = load_columns(self._normal_ctx())

        self.assertEqual(columns[0].rerun_of, "r")
        self.assertEqual(columns[0].verdict, "FAILED")
        self.assertEqual(columns[0].plan, A1)
        self.assertEqual(list(columns[0].tests), ["t1"])

    def test_load_columns_never_walks_other_caches(self):
        from enge.compare.loader import load_columns

        self._chain()

        columns, _ = load_columns(self._normal_ctx())

        self.assertEqual([c.task_id for c in columns], ["c1"])


if __name__ == "__main__":
    unittest.main()
