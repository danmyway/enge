"""Run-selector flattening (F2): repeatable --run, AND-composing filters,
and hard-error empty selection, shared across report/compare/rerun/cancel.

Covers three layers:
  * argparse    -- --run is repeatable; rerun/cancel gain --set/--tier/
                   --arch/--tag; compare/rerun/cancel gain --since/--until.
  * select_runs -- the shared run-selection primitive (union/dedup/order,
                   AND-applied filters over --run, empty -> ValidationError).
  * end-to-end  -- rerun/cancel resolve task IDs through the same selection
                   via task_resolver._parse_tasks_impl.
"""

import json
import tempfile
import unittest
import uuid as uuid_mod
from pathlib import Path
from types import SimpleNamespace

from enge.utils.arg_parser import get_arguments
from enge.utils.errors import ValidationError
from enge.utils.manifest import ManifestWriter
from enge.utils.task_resolver import _parse_tasks_impl, _resolve_manifest_tasks
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


# ==================== argparse level ====================


class TestRunFlagIsRepeatable(unittest.TestCase):
    """--run collects into a list on every command that carries it."""

    def test_report_run_repeatable(self):
        args = get_arguments(args=["report", "--run", "aaa", "--run", "bbb"])
        self.assertEqual(args.run, ["aaa", "bbb"])

    def test_compare_run_repeatable(self):
        args = get_arguments(args=["compare", "--run", "aaa", "--run", "bbb"])
        self.assertEqual(args.run, ["aaa", "bbb"])

    def test_rerun_run_repeatable(self):
        args = get_arguments(args=["rerun", "--run", "aaa", "--run", "bbb"])
        self.assertEqual(args.run, ["aaa", "bbb"])

    def test_cancel_run_repeatable(self):
        args = get_arguments(args=["cancel", "--run", "aaa", "--run", "bbb"])
        self.assertEqual(args.run, ["aaa", "bbb"])

    def test_single_run_still_yields_one_element_list(self):
        args = get_arguments(args=["cancel", "--run", "aaa"])
        self.assertEqual(args.run, ["aaa"])


class TestRerunGainsManifestFilters(unittest.TestCase):
    """rerun mirrors report's --set/--tier/--arch/--tag and date flags."""

    def test_rerun_accepts_filters(self):
        args = get_arguments(
            args=[
                "rerun",
                "--set",
                "smoke",
                "--tier",
                "tier0",
                "--arch",
                "x86_64",
                "--tag",
                "nightly",
            ]
        )
        self.assertEqual(args.filter_set, ["smoke"])
        self.assertEqual(args.filter_tier, ["tier0"])
        self.assertEqual(args.filter_arch, ["x86_64"])
        self.assertEqual(args.filter_tag, ["nightly"])

    def test_rerun_accepts_date_flags(self):
        args = get_arguments(args=["rerun", "--since", "1d", "--until", "2026-01-01"])
        self.assertEqual(args.since, "1d")
        self.assertEqual(args.until, "2026-01-01")


class TestCancelGainsManifestFilters(unittest.TestCase):
    """cancel mirrors report's --set/--tier/--arch/--tag and date flags."""

    def test_cancel_accepts_filters(self):
        args = get_arguments(
            args=[
                "cancel",
                "--set",
                "smoke",
                "--tier",
                "tier0",
                "--arch",
                "x86_64",
                "--tag",
                "nightly",
            ]
        )
        self.assertEqual(args.filter_set, ["smoke"])
        self.assertEqual(args.filter_tier, ["tier0"])
        self.assertEqual(args.filter_arch, ["x86_64"])
        self.assertEqual(args.filter_tag, ["nightly"])

    def test_cancel_accepts_date_flags(self):
        args = get_arguments(args=["cancel", "--since", "1d", "--until", "2026-01-01"])
        self.assertEqual(args.since, "1d")
        self.assertEqual(args.until, "2026-01-01")


class TestCompareGainsDateFlags(unittest.TestCase):
    """compare gains --since/--until (RULING F2-e)."""

    def test_compare_accepts_date_flags(self):
        args = get_arguments(args=["compare", "--since", "1d", "--until", "2026-01-01"])
        self.assertEqual(args.since, "1d")
        self.assertEqual(args.until, "2026-01-01")


# ==================== select_runs semantics ====================


class TestSelectRuns(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"
        self.runs.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(
        self, run_id, *, created_at=None, context=None, tags=None, requests=None
    ):
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": created_at or "2026-07-01T00:00:00Z",
            "command": "test",
            "argv": ["enge", "test"],
            "tags": tags or [],
            "parent_run_id": None,
            "origin": "native",
            "context": context or {},
            "requests": requests or [{"task_id": f"task-{run_id}"}],
        }
        (self.runs / f"{run_id}.json").write_text(json.dumps(manifest))
        return run_id

    def test_multiple_run_values_select_union_in_order(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA")
        self._write("01BBB")
        ctx = _make_ctx(self.runs, self.latest, run=["01AAA", "01BBB"])
        result = select_runs(ctx)
        self.assertEqual([m["run_id"] for m in result], ["01AAA", "01BBB"])

    def test_run_values_preserve_first_seen_order(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA")
        self._write("01BBB")
        ctx = _make_ctx(self.runs, self.latest, run=["01BBB", "01AAA"])
        result = select_runs(ctx)
        self.assertEqual([m["run_id"] for m in result], ["01BBB", "01AAA"])

    def test_duplicate_run_values_are_deduped(self):
        from enge.utils.manifest_resolution import select_runs

        rid = self._write("01AAAAAAAAAAAAAAAAAAAAAAAA")
        # Exact id plus a prefix of the same run must collapse to one entry.
        ctx = _make_ctx(self.runs, self.latest, run=[rid, rid[:8]])
        result = select_runs(ctx)
        self.assertEqual([m["run_id"] for m in result], [rid])

    def test_run_absent_falls_back_to_find_runs(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA", context={"set": "smoke"})
        self._write("01BBB", context={"set": "regression"})
        ctx = _make_ctx(self.runs, self.latest, filter_set="smoke")
        result = select_runs(ctx)
        self.assertEqual([m["run_id"] for m in result], ["01AAA"])

    def test_run_plus_tier_includes_matching_excludes_non_matching(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA", requests=[{"task_id": "t-a", "tier": "tier0"}])
        self._write("01BBB", requests=[{"task_id": "t-b", "tier": "tier3"}])
        ctx = _make_ctx(
            self.runs, self.latest, run=["01AAA", "01BBB"], filter_tier="tier0"
        )
        result = select_runs(ctx)
        self.assertEqual([m["run_id"] for m in result], ["01AAA"])

    def test_run_plus_since_excludes_older_includes_newer(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA", created_at="2026-01-01T00:00:00Z")
        self._write("01BBB", created_at="2026-12-01T00:00:00Z")
        ctx = _make_ctx(
            self.runs, self.latest, run=["01AAA", "01BBB"], since="2026-06-01"
        )
        result = select_runs(ctx)
        self.assertEqual([m["run_id"] for m in result], ["01BBB"])

    def test_empty_selection_with_selectors_raises_naming_them(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA", requests=[{"task_id": "t-a", "tier": "tier0"}])
        ctx = _make_ctx(self.runs, self.latest, run=["01AAA"], filter_tier="tier9")
        with self.assertRaises(ValidationError) as cm:
            select_runs(ctx)
        msg = str(cm.exception)
        self.assertIn("01AAA", msg)
        self.assertIn("tier9", msg)

    def test_filter_only_matching_nothing_raises(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAA", context={"set": "smoke"})
        ctx = _make_ctx(self.runs, self.latest, filter_set="does-not-exist")
        with self.assertRaises(ValidationError) as cm:
            select_runs(ctx)
        self.assertIn("does-not-exist", str(cm.exception))

    def test_unknown_run_value_raises(self):
        from enge.utils.manifest_resolution import select_runs

        ctx = _make_ctx(self.runs, self.latest, run=["NOSUCHRUN"])
        with self.assertRaises(ValidationError) as cm:
            select_runs(ctx)
        self.assertIn("No run matching", str(cm.exception))

    def test_ambiguous_run_prefix_raises(self):
        from enge.utils.manifest_resolution import select_runs

        self._write("01AAAAAAAAA")
        self._write("01AAAAAAAAB")
        ctx = _make_ctx(self.runs, self.latest, run=["01AAAA"])
        with self.assertRaises(ValidationError) as cm:
            select_runs(ctx)
        self.assertIn("matches 2 runs", str(cm.exception))


# ==================== resolve_manifests_for_invocation ====================


class TestResolveManifestsForInvocation(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"
        self.runs.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, run_id, **context):
        w = ManifestWriter(
            run_id=run_id, command="test", argv=[], context=context or None
        )
        w.add_request(str(uuid_mod.uuid4()))
        w.flush(self.runs, self.latest)
        return run_id

    def test_bare_since_takes_legacy_path_returns_empty(self):
        # Characterization pin (RULING F2-f): bare date flags with no
        # manifest selector still short-circuit to the legacy archive and
        # yield [] -- NOT the ValidationError selector path. Passes both
        # pre- and post-change.
        from enge.utils.manifest_resolution import resolve_manifests_for_invocation

        self._write("01AAA", set="smoke")
        ctx = _make_ctx(self.runs, self.latest, since="1d")
        self.assertEqual(resolve_manifests_for_invocation(ctx), [])

    def test_empty_selector_raises(self):
        from enge.utils.manifest_resolution import resolve_manifests_for_invocation

        self._write("01AAA", set="smoke")
        ctx = _make_ctx(self.runs, self.latest, filter_set="nope")
        with self.assertRaises(ValidationError):
            resolve_manifests_for_invocation(ctx)


# ==================== end-to-end via task_resolver ====================


class TestTaskResolverMultiRunAndFilters(unittest.TestCase):
    """rerun/cancel resolve task IDs through the shared selection."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, uuids, *, run_id=None, tier=None, arch=None, set_name=None):
        rid = run_id or generate_ulid()
        w = ManifestWriter(run_id=rid, command="test", argv=[])
        for u in uuids:
            w.add_request(u, tier=tier, arch=arch, set_name=set_name)
        w.flush(self.runs, self.latest)
        return rid

    def test_multi_run_collects_union_of_task_ids(self):
        u_a = str(uuid_mod.uuid4())
        u_b = str(uuid_mod.uuid4())
        rid_a = self._write([u_a])
        rid_b = self._write([u_b])
        ctx = _make_ctx(self.runs, self.latest, run=[rid_a, rid_b])
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertEqual(resolved, {u_a, u_b})

    def test_run_plus_tier_filters_within_selected_runs(self):
        u_hit = str(uuid_mod.uuid4())
        u_miss = str(uuid_mod.uuid4())
        rid_hit = self._write([u_hit], tier="tier0")
        rid_miss = self._write([u_miss], tier="tier3")
        ctx = _make_ctx(
            self.runs, self.latest, run=[rid_hit, rid_miss], filter_tier="tier0"
        )
        urls, _, _ = _parse_tasks_impl(ctx)
        resolved = {u.rsplit("/", 1)[-1] for u in urls}
        self.assertEqual(resolved, {u_hit})

    def test_empty_selection_raises_through_parse_tasks(self):
        self._write([str(uuid_mod.uuid4())], tier="tier0")
        ctx = _make_ctx(self.runs, self.latest, filter_tier="tier9")
        with self.assertRaises(ValidationError):
            _parse_tasks_impl(ctx)

    def test_single_run_source_preserves_lineage_id(self):
        rid = self._write(["id-a", "id-b"])
        ctx = _make_ctx(self.runs, self.latest, run=[rid])
        task_ids, source = _resolve_manifest_tasks(ctx)
        self.assertEqual(task_ids, ["id-a", "id-b"])
        self.assertEqual(source, f"manifest:{rid}")

    def test_multi_run_source_is_filter_sentinel(self):
        rid_a = self._write(["id-a"])
        rid_b = self._write(["id-b"])
        ctx = _make_ctx(self.runs, self.latest, run=[rid_a, rid_b])
        task_ids, source = _resolve_manifest_tasks(ctx)
        self.assertEqual(source, "manifest:filter")
        self.assertEqual(set(task_ids), {"id-a", "id-b"})

    def test_filter_source_is_filter_sentinel(self):
        self._write(["id-a"], set_name="smoke")
        ctx = _make_ctx(self.runs, self.latest, filter_set="smoke")
        _, source = _resolve_manifest_tasks(ctx)
        self.assertEqual(source, "manifest:filter")

    def test_task_ids_are_deduplicated_across_runs(self):
        shared = "shared-task-id"
        rid_a = self._write([shared, "only-a"])
        rid_b = self._write([shared, "only-b"])
        ctx = _make_ctx(self.runs, self.latest, run=[rid_a, rid_b])
        task_ids, _ = _resolve_manifest_tasks(ctx)
        self.assertEqual(task_ids.count(shared), 1)
        self.assertEqual(set(task_ids), {shared, "only-a", "only-b"})

    def test_unknown_run_raises_through_parse_tasks(self):
        # Characterization: an unknown --run value still raises
        # ValidationError, now via the shared selection.
        self.runs.mkdir(parents=True, exist_ok=True)
        ctx = _make_ctx(self.runs, self.latest, run=["NONEXISTENT"])
        with self.assertRaises(ValidationError):
            _parse_tasks_impl(ctx)


class TestSelectionRunIdLogging(unittest.TestCase):
    """RED/GREEN pins for the run-selection visibility log (L4)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs = self.tmp / "runs"
        self.latest = self.tmp / "latest"
        self.runs.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, run_id, *, context=None):
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": "2026-07-01T00:00:00Z",
            "command": "test",
            "argv": ["enge", "test"],
            "tags": [],
            "parent_run_id": None,
            "origin": "native",
            "context": context or {},
            "requests": [{"task_id": f"task-{run_id}"}],
        }
        (self.runs / f"{run_id}.json").write_text(json.dumps(manifest))
        return run_id

    def test_multi_run_selection_logs_both_ids_on_one_line(self):
        self._write("01AAA")
        self._write("01BBB")
        ctx = _make_ctx(self.runs, self.latest, run=["01AAA", "01BBB"])

        with self.assertLogs("enge.utils.task_resolver", level="INFO") as cm:
            _resolve_manifest_tasks(ctx)

        selected_lines = [m for m in cm.output if "Selected run(s):" in m]
        self.assertEqual(len(selected_lines), 1, cm.output)
        self.assertIn("01AAA", selected_lines[0])
        self.assertIn("01BBB", selected_lines[0])

    def test_filter_only_selection_logs_every_matched_run(self):
        self._write("01AAA", context={"set": "smoke"})
        self._write("01BBB", context={"set": "smoke"})
        self._write("01CCC", context={"set": "regression"})
        ctx = _make_ctx(self.runs, self.latest, filter_set="smoke")

        with self.assertLogs("enge.utils.task_resolver", level="INFO") as cm:
            _resolve_manifest_tasks(ctx)

        selected_lines = [m for m in cm.output if "Selected run(s):" in m]
        self.assertEqual(len(selected_lines), 1, cm.output)
        self.assertIn("01AAA", selected_lines[0])
        self.assertIn("01BBB", selected_lines[0])
        self.assertNotIn("01CCC", selected_lines[0])

    def test_no_selector_path_emits_no_selection_log(self):
        self._write("01AAA")
        ctx = _make_ctx(self.runs, self.latest)

        with self.assertNoLogs("enge.utils.task_resolver", level="INFO"):
            result = _resolve_manifest_tasks(ctx)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
