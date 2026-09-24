"""RED tests for `enge report --refresh` (ledger F7; contract D6a + section 12).

Two silent failure modes are under test here.

(1) A task harvested before Testing Farm published its xunit is stored as
`verdict: "ERROR", plans: []` and, once the run finalizes, stays that way
forever -- `upsert_task_result` raises `AlreadyFinalizedError` and the
cache layer swallows it. `--refresh` recovers such a task in place.

(2) Caches written by older enge versions lack keys the current writer
always emits. Readers tolerate the absence, but nothing told the user
their data was stale and there was no repair path. `--refresh` fills
those keys, and two new WARNINGs point users at the flag.

The guards (G1-G5) that keep the repair from becoming a data-loss vector:

- G1 key-set parity: `TASK_ENTRY_KEYS` is exactly what `TaskEntry.to_dict`
  emits, so `stale_task_keys` cannot silently under-report.
- G2 completeness: a run is refreshed only when every cached task is
  present AND terminal in this invocation. A partial invocation leaves
  the file untouched.
- G3 content gate: `verdict`/`plans`/`total_duration_seconds` are taken
  from the fresh harvest if and only if the cached entry is the
  recoverable `ERROR` + `[]` shape. `CANCELED` + `[]` is a legitimate
  terminal state and is never replaced.
- G4 fill-only metadata: every other key is filled only when the cached
  value is absent, null (or, for `build_ids`, `[]`). A populated cached
  value wins over a differing fresh one -- historical truth is not
  rewritten by a later harvest.
- G5 no silent normalization: the round-trip through `TaskEntry` turns an
  absent key into an explicit `null`. The merge must therefore fill from
  fresh BEFORE the round-trip, never after.

Caches are built here as raw JSON dicts written to a temp dir -- that is
the only way to express an absent key, which `TaskEntry.from_dict`
erases into `None` the moment it parses.
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._helpers import captured_logs, make_app_context, matching
from enge.report.concurrent_parser import TaskResult
from enge.utils.errors import ValidationError

CACHE_LOGGER = "enge.report.results_cache"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def request_meta(task_id, **overrides):
    """A manifest requests[] entry. Mirrors what dispatch writes today."""
    base = {
        "task_id": task_id,
        "set": "setA",
        "tier": "tier1",
        "arch": "x86_64",
        "plan": None,
        "source_compose": "RHEL-9.9.0-20260629.0",
        "target_compose": "RHEL-10.3.0-20260701.0",
        "artifacts_url": f"https://tf.example.com/artifacts/{task_id}",
        "dispatched_at": "2026-07-07T10:35:13Z",
        "launch_uuid": None,
        "source": "9.9",
        "target": "10.3",
        "git_ref": "main",
        "event": "preliminary",
        "build_ids": ["copr:12345"],
    }
    base.update(overrides)
    return base


def write_manifest(runs_dir, run_id, requests, context=None, created_at=None):
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
            else {
                "event": "preliminary",
                "source": "9.9",
                "target": "10.3",
                "set": "setA",
            }
        ),
        "requests": requests,
    }
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    (Path(runs_dir) / f"{run_id}.json").write_text(json.dumps(manifest))
    return manifest


def plan_block(name="/plans/p1", verdict="PASSED", duration=12.0):
    return {
        "name": name,
        "verdict": verdict,
        "tests": [
            {"name": "/tests/x", "verdict": verdict, "duration_seconds": duration}
        ],
    }


def cached_task(task_id, *, drop=(), **overrides):
    """A results.json task entry carrying all nineteen keys the current
    writer emits. `drop` removes keys outright -- the only faithful way to
    model a cache written by an older enge version."""
    base = {
        "task_id": task_id,
        "set": "setA",
        "tier": "tier1",
        "arch": "x86_64",
        "source_compose": "RHEL-9.9.0-20260629.0",
        "target_compose": "RHEL-10.3.0-20260701.0",
        "dispatched_at": "2026-07-07T10:35:13Z",
        "verdict": "PASSED",
        "total_duration_seconds": 12.0,
        "plans": [plan_block()],
        "source": "9.9",
        "target": "10.3",
        "git_ref": "main",
        "event": "preliminary",
        "build_ids": ["copr:12345"],
        "rerun_of": None,
        "artifacts_url": f"https://tf.example.com/artifacts/{task_id}",
        "plan": None,
        "plan_filter": None,
    }
    base.update(overrides)
    for key in drop:
        base.pop(key, None)
    return base


def error_empty_task(task_id, **overrides):
    """The recoverable shape: terminal, harvested before TF published an
    xunit, so verdict ERROR with no plans and a 0.0 duration sentinel."""
    return cached_task(
        task_id,
        verdict="ERROR",
        plans=[],
        total_duration_seconds=0.0,
        **overrides,
    )


def write_cache(results_dir, run_id, tasks, *, verdict="PASSED", **envelope):
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-07-07T10:35:07Z",
        "event": "preliminary",
        "source": "9.9",
        "target": "10.3",
        "verdict": verdict,
        "results": list(tasks),
    }
    payload.update(envelope)
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    path = Path(results_dir) / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def xunit_bytes(plan_name="/plans/p1", result="passed", duration="12.0"):
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuites overall-result="{result}">'
        f'<testsuite name="{plan_name}" result="{result}">'
        f'<testcase name="/tests/x" result="{result}" time="{duration}"/>'
        "</testsuite></testsuites>"
    )
    return xml.encode("utf-8")


def task_result(task_id, **overrides):
    defaults = dict(
        request_uuid=task_id,
        request_source_compose="RHEL-9.9.0-20260629.0",
        request_target_release="10.3",
        request_upgrade_path="9.9 to 10.3",
        request_arch="x86_64",
        request_state="COMPLETE",
        request_datetime_created="2026-07-07T10:35:13Z",
        request_plan="",
        request_plan_filter="",
        request_summary="Undefined",
        request_result_overall="Undefined",
        results_xml_url="https://tf.example.com/artifacts/task/results.xml",
        url="https://tf.example.com/api/task",
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


class RefreshTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_root = Path(tmp.name)
        self.runs_dir = self.tmp_root / "runs"
        self.results_dir = self.tmp_root / "results"

    def ctx(self, extra_cli=None):
        cli = {
            "run": None,
            "filter_set": None,
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
            "since": None,
            "until": None,
            "refresh": False,
        }
        cli.update(extra_cli or {})
        return make_app_context(
            action="report",
            extra_cli=cli,
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.tmp_root / "nonexistent-latest"),
        )


# ---------------------------------------------------------------------------
# G1 -- key-set parity
# ---------------------------------------------------------------------------


class TestTaskEntryKeyParity(unittest.TestCase):
    """G1. `TASK_ENTRY_KEYS` must be exactly the key set `TaskEntry.to_dict`
    emits. If it under-reports, `stale_task_keys` silently declares a stale
    cache current and the refresh never fills the missing key."""

    def _populated_entry(self):
        from enge.utils.results_parser import TaskEntry

        return TaskEntry(
            task_id="t1",
            set="setA",
            tier="tier1",
            arch="x86_64",
            source_compose="RHEL-9.9.0-1",
            target_compose="RHEL-10.3.0-1",
            dispatched_at="2026-07-07T10:35:13Z",
            verdict="PASSED",
            total_duration_seconds=12.0,
            plans=[],
            source="9.9",
            target="10.3",
            git_ref="main",
            event="preliminary",
            build_ids=["copr:12345"],
            rerun_of="01PARENTRUNIDAAAAAAAAAAAAA",
            artifacts_url="https://tf.example.com/artifacts/t1",
            plan="/plans/p1",
            plan_filter="tag:tier1",
        )

    def test_task_entry_keys_match_to_dict_emission(self):
        from enge.utils.results_parser import TASK_ENTRY_KEYS

        self.assertEqual(set(TASK_ENTRY_KEYS), set(self._populated_entry().to_dict()))

    def test_task_entry_keys_preserve_emission_order(self):
        from enge.utils.results_parser import TASK_ENTRY_KEYS

        self.assertEqual(list(TASK_ENTRY_KEYS), list(self._populated_entry().to_dict()))

    def test_stale_task_keys_empty_for_current_writer_output(self):
        from enge.utils.results_parser import stale_task_keys

        self.assertEqual(
            stale_task_keys(self._populated_entry().to_dict()), frozenset()
        )

    def test_stale_task_keys_reports_every_absent_key(self):
        from enge.utils.results_parser import stale_task_keys

        raw = self._populated_entry().to_dict()
        raw.pop("artifacts_url")
        raw.pop("plan_filter")

        self.assertEqual(
            stale_task_keys(raw), frozenset({"artifacts_url", "plan_filter"})
        )

    def test_stale_task_keys_ignores_null_values(self):
        """A key present with a null value is NOT stale -- the writer emitted
        it. Only absence is staleness."""
        from enge.utils.results_parser import stale_task_keys

        raw = self._populated_entry().to_dict()
        raw["artifacts_url"] = None

        self.assertEqual(stale_task_keys(raw), frozenset())


# ---------------------------------------------------------------------------
# read_raw_results_json
# ---------------------------------------------------------------------------


class TestReadRawResultsJson(RefreshTestCase):
    def test_absent_keys_survive_the_read(self):
        from enge.utils.results_parser import read_raw_results_json

        path = write_cache(
            self.results_dir, "run1", [cached_task("t1", drop=("artifacts_url",))]
        )

        raw = read_raw_results_json(path)

        self.assertNotIn("artifacts_url", raw["results"][0])

    def test_malformed_json_raises_validation_error(self):
        from enge.utils.results_parser import read_raw_results_json

        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.results_dir / "run1.json"
        path.write_text("{not json")

        with self.assertRaises(ValidationError):
            read_raw_results_json(path)

    def test_schema_violation_raises_validation_error(self):
        from enge.utils.results_parser import read_raw_results_json

        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.results_dir / "run1.json"
        path.write_text(json.dumps({"schema_version": 1, "run_id": "run1"}))

        with self.assertRaises(ValidationError):
            read_raw_results_json(path)


# ---------------------------------------------------------------------------
# rewrite_finalized_results -- the only sanctioned write to a finalized file
# ---------------------------------------------------------------------------


class TestRewriteFinalizedResults(RefreshTestCase):
    def _entries(self, *raw_tasks):
        from enge.utils.results_parser import TaskEntry

        return [TaskEntry.from_dict(t) for t in raw_tasks]

    def test_refuses_an_unfinalized_file_and_writes_nothing(self):
        from enge.utils.results_parser import rewrite_finalized_results

        path = write_cache(self.results_dir, "run1", [cached_task("t1")], verdict=None)
        before = path.read_bytes()

        with self.assertRaises(ValidationError):
            rewrite_finalized_results(path, self._entries(cached_task("t1")))

        self.assertEqual(path.read_bytes(), before)

    def test_refuses_an_extra_task_id_and_writes_nothing(self):
        from enge.utils.results_parser import rewrite_finalized_results

        path = write_cache(self.results_dir, "run1", [cached_task("t1")])
        before = path.read_bytes()

        with self.assertRaises(ValidationError):
            rewrite_finalized_results(
                path, self._entries(cached_task("t1"), cached_task("t2"))
            )

        self.assertEqual(path.read_bytes(), before)

    def test_refuses_a_missing_task_id_and_writes_nothing(self):
        from enge.utils.results_parser import rewrite_finalized_results

        path = write_cache(
            self.results_dir, "run1", [cached_task("t1"), cached_task("t2")]
        )
        before = path.read_bytes()

        with self.assertRaises(ValidationError):
            rewrite_finalized_results(path, self._entries(cached_task("t1")))

        self.assertEqual(path.read_bytes(), before)

    def test_refuses_duplicate_task_ids_and_writes_nothing(self):
        from enge.utils.results_parser import rewrite_finalized_results

        path = write_cache(self.results_dir, "run1", [cached_task("t1")])
        before = path.read_bytes()

        with self.assertRaises(ValidationError):
            rewrite_finalized_results(
                path, self._entries(cached_task("t1"), cached_task("t1"))
            )

        self.assertEqual(path.read_bytes(), before)

    def test_recomputes_the_root_verdict_from_the_new_entries(self):
        from enge.utils.results_parser import (
            parse_results_json,
            rewrite_finalized_results,
        )

        path = write_cache(
            self.results_dir,
            "run1",
            [error_empty_task("t1"), cached_task("t2")],
            verdict="ERROR",
        )

        returned = rewrite_finalized_results(
            path, self._entries(cached_task("t1"), cached_task("t2"))
        )

        self.assertEqual(returned, "PASSED")
        self.assertEqual(parse_results_json(path).verdict, "PASSED")

    def test_leaves_every_envelope_field_but_the_verdict_untouched(self):
        from enge.utils.results_parser import (
            read_raw_results_json,
            rewrite_finalized_results,
        )

        path = write_cache(
            self.results_dir,
            "run1",
            [error_empty_task("t1")],
            verdict="ERROR",
            created_at="2026-01-02T03:04:05Z",
            event="prod",
            source="9.8",
            target="10.2",
        )
        before = read_raw_results_json(path)

        rewrite_finalized_results(path, self._entries(cached_task("t1")))
        after = read_raw_results_json(path)

        for key in (
            "schema_version",
            "run_id",
            "created_at",
            "event",
            "source",
            "target",
        ):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["verdict"], "PASSED")


# ---------------------------------------------------------------------------
# G2 -- completeness
# ---------------------------------------------------------------------------


class TestRefreshCompleteness(RefreshTestCase):
    """G2. Refreshing from a partial invocation would have to invent the
    missing tasks' content or drop them; both are data loss. Skip the run."""

    def _two_task_run(self):
        write_manifest(
            self.runs_dir,
            "01RUNG2AAAAAAAAAAAAAAAAAAA",
            [request_meta("t1"), request_meta("t2")],
        )
        return write_cache(
            self.results_dir,
            "01RUNG2AAAAAAAAAAAAAAAAAAA",
            [error_empty_task("t1"), error_empty_task("t2")],
            verdict="ERROR",
        )

    def test_partial_invocation_leaves_the_file_byte_identical(self):
        from enge.report.results_cache import cache_report_results

        path = self._two_task_run()
        before = path.read_bytes()
        ctx = self.ctx({"run": "01RUNG2AAAAAAAAAAAAAAAAAAA", "refresh": True})

        cache_report_results(
            ctx, [task_result("t1", xunit_bytes=xunit_bytes())], refresh=True
        )

        self.assertEqual(path.read_bytes(), before)

    def test_partial_invocation_warns_naming_the_run_and_the_counts(self):
        from enge.report.results_cache import cache_report_results

        self._two_task_run()
        ctx = self.ctx({"run": "01RUNG2AAAAAAAAAAAAAAAAAAA", "refresh": True})

        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(
                ctx, [task_result("t1", xunit_bytes=xunit_bytes())], refresh=True
            )

        hits = matching(records, "cannot refresh run", level=logging.WARNING)
        self.assertEqual(len(hits), 1, records)
        self.assertIn("01RUNG2AAAAAAAAAAAAAAAAAAA", hits[0])
        self.assertIn("1 of 2 cached task(s) not available", hits[0])
        self.assertIn("run is unchanged", hits[0])

    def test_a_non_terminal_task_counts_as_unavailable(self):
        """A task still RUNNING in this invocation is not a usable source of
        fresh content; G2 must treat it exactly like an absent one."""
        from enge.report.results_cache import cache_report_results

        path = self._two_task_run()
        before = path.read_bytes()
        ctx = self.ctx({"run": "01RUNG2AAAAAAAAAAAAAAAAAAA", "refresh": True})

        cache_report_results(
            ctx,
            [
                task_result("t1", xunit_bytes=xunit_bytes()),
                task_result("t2", request_state="RUNNING"),
            ],
            refresh=True,
        )

        self.assertEqual(path.read_bytes(), before)


# ---------------------------------------------------------------------------
# G3 -- content gate
# ---------------------------------------------------------------------------


class TestRefreshContentGate(RefreshTestCase):
    """G3. Only the recoverable `ERROR` + `[]` shape may take fresh content.
    Every other cached verdict is historical truth."""

    RUN = "01RUNG3AAAAAAAAAAAAAAAAAAA"

    def _run(self, cached_entry, root_verdict):
        write_manifest(self.runs_dir, self.RUN, [request_meta("t1")])
        return write_cache(
            self.results_dir, self.RUN, [cached_entry], verdict=root_verdict
        )

    def _refresh(self, results):
        from enge.report.results_cache import cache_report_results

        ctx = self.ctx({"run": self.RUN, "refresh": True})
        cache_report_results(ctx, results, refresh=True)

    def test_error_empty_entry_takes_the_fresh_results(self):
        from enge.utils.results_parser import parse_results_json

        path = self._run(error_empty_task("t1"), "ERROR")

        self._refresh([task_result("t1", xunit_bytes=xunit_bytes())])

        schema = parse_results_json(path)
        self.assertEqual(schema.results[0].verdict, "PASSED")
        self.assertEqual(len(schema.results[0].plans), 1)
        self.assertEqual(schema.results[0].total_duration_seconds, 12.0)

    def test_error_empty_recovery_recomputes_the_root_verdict(self):
        from enge.utils.results_parser import parse_results_json

        path = self._run(error_empty_task("t1"), "ERROR")

        self._refresh([task_result("t1", xunit_bytes=xunit_bytes())])

        self.assertEqual(parse_results_json(path).verdict, "PASSED")

    def test_error_empty_recovery_writes_the_xunit(self):
        self._run(error_empty_task("t1"), "ERROR")
        payload = xunit_bytes()

        self._refresh([task_result("t1", xunit_bytes=payload)])

        self.assertEqual((self.results_dir / self.RUN / "t1.xml").read_bytes(), payload)

    def test_failed_entry_with_plans_is_never_replaced(self):
        from enge.utils.results_parser import parse_results_json

        cached = cached_task(
            "t1",
            verdict="FAILED",
            plans=[plan_block(verdict="FAILED", duration=99.0)],
            total_duration_seconds=99.0,
        )
        path = self._run(cached, "FAILED")

        self._refresh([task_result("t1", xunit_bytes=xunit_bytes())])

        schema = parse_results_json(path)
        self.assertEqual(schema.results[0].verdict, "FAILED")
        self.assertEqual(schema.results[0].total_duration_seconds, 99.0)
        self.assertEqual(schema.results[0].plans[0].verdict, "FAILED")
        self.assertEqual(schema.verdict, "FAILED")

    def test_canceled_empty_entry_is_never_replaced(self):
        from enge.utils.results_parser import parse_results_json

        cached = cached_task(
            "t1", verdict="CANCELED", plans=[], total_duration_seconds=0.0
        )
        path = self._run(cached, "CANCELED")

        self._refresh([task_result("t1", xunit_bytes=xunit_bytes())])

        schema = parse_results_json(path)
        self.assertEqual(schema.results[0].verdict, "CANCELED")
        self.assertEqual(schema.results[0].plans, [])
        self.assertEqual(schema.verdict, "CANCELED")

    def test_error_empty_entry_stays_when_the_harvest_is_still_empty(self):
        from enge.utils.results_parser import parse_results_json

        path = self._run(error_empty_task("t1"), "ERROR")

        self._refresh([task_result("t1", request_state="ERROR")])

        schema = parse_results_json(path)
        self.assertEqual(schema.results[0].verdict, "ERROR")
        self.assertEqual(schema.results[0].plans, [])
        self.assertEqual(schema.verdict, "ERROR")

    def test_error_entry_with_recorded_plans_is_never_replaced(self):
        """The gate is `ERROR` AND no plans, not `ERROR` alone. A task that
        genuinely errored *and* published an xunit is a complete, correct
        record; a later harvest that disagrees (TF re-ran the plan, the
        artifact expired into a shorter xunit) must not overwrite it."""
        from enge.utils.results_parser import parse_results_json

        cached = cached_task(
            "t1",
            verdict="ERROR",
            plans=[plan_block(verdict="ERROR", duration=77.0)],
            total_duration_seconds=77.0,
        )
        path = self._run(cached, "ERROR")

        self._refresh([task_result("t1", xunit_bytes=xunit_bytes())])

        schema = parse_results_json(path)
        self.assertEqual(schema.results[0].verdict, "ERROR")
        self.assertEqual(schema.results[0].total_duration_seconds, 77.0)
        self.assertEqual(schema.results[0].plans[0].verdict, "ERROR")
        self.assertEqual(schema.verdict, "ERROR")


# ---------------------------------------------------------------------------
# G4 -- fill-only metadata
# ---------------------------------------------------------------------------


class TestRefreshMetadataFill(RefreshTestCase):
    """G4. Metadata is filled, never overwritten. The cache is the
    historical record of what dispatch knew; a later harvest may legitimately
    see a different value and must not clobber the stored one."""

    RUN = "01RUNG4AAAAAAAAAAAAAAAAAAA"

    def _refresh(self, cached_entry, *, meta_overrides=None, result_overrides=None):
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import read_raw_results_json

        write_manifest(
            self.runs_dir,
            self.RUN,
            [request_meta("t1", **(meta_overrides or {}))],
        )
        path = write_cache(self.results_dir, self.RUN, [cached_entry])
        ctx = self.ctx({"run": self.RUN, "refresh": True})

        cache_report_results(
            ctx,
            [task_result("t1", xunit_bytes=xunit_bytes(), **(result_overrides or {}))],
            refresh=True,
        )
        return read_raw_results_json(path)["results"][0]

    def test_absent_key_is_filled_from_the_fresh_entry(self):
        entry = self._refresh(cached_task("t1", drop=("artifacts_url",)))

        self.assertEqual(entry["artifacts_url"], "https://tf.example.com/artifacts/t1")

    def test_null_key_is_filled_from_the_fresh_entry(self):
        entry = self._refresh(cached_task("t1", git_ref=None))

        self.assertEqual(entry["git_ref"], "main")

    def test_empty_build_ids_is_filled_from_the_fresh_entry(self):
        entry = self._refresh(cached_task("t1", build_ids=[]))

        self.assertEqual(entry["build_ids"], ["copr:12345"])

    def test_populated_key_is_kept_even_when_the_fresh_value_differs(self):
        entry = self._refresh(
            cached_task("t1", artifacts_url="https://old.example.com/artifacts/t1"),
            meta_overrides={"artifacts_url": "https://tf.example.com/NEW/t1"},
        )

        self.assertEqual(entry["artifacts_url"], "https://old.example.com/artifacts/t1")

    def test_populated_build_ids_is_kept_even_when_the_fresh_value_differs(self):
        entry = self._refresh(
            cached_task("t1", build_ids=["copr:OLD"]),
            meta_overrides={"build_ids": ["copr:NEW"]},
        )

        self.assertEqual(entry["build_ids"], ["copr:OLD"])

    def test_plan_filter_is_filled_from_the_live_task_result(self):
        """`plan_filter` is never written to the manifest at dispatch time --
        it is re-sourced from the live TaskResult on every harvest."""
        entry = self._refresh(
            cached_task("t1", drop=("plan_filter",)),
            result_overrides={"request_plan_filter": "tag:tier1"},
        )

        self.assertEqual(entry["plan_filter"], "tag:tier1")


# ---------------------------------------------------------------------------
# G5 -- no silent normalization
# ---------------------------------------------------------------------------


class TestRefreshNoSilentNormalization(RefreshTestCase):
    """G5. `TaskEntry.from_dict` turns an absent key into `None`, and
    `to_dict` then writes it back as an explicit `null`. If the merge ran
    after that round-trip instead of before it, a refresh would "repair" a
    stale cache into one that is schema-current and permanently empty."""

    RUN = "01RUNG5AAAAAAAAAAAAAAAAAAA"

    STALE_KEYS = (
        "source",
        "target",
        "git_ref",
        "event",
        "build_ids",
        "rerun_of",
        "artifacts_url",
        "plan",
        "plan_filter",
    )

    def _refresh_stale_run(self):
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import read_raw_results_json

        write_manifest(
            self.runs_dir,
            self.RUN,
            [request_meta("t1", plan="/plans/p1")],
        )
        path = write_cache(
            self.results_dir, self.RUN, [cached_task("t1", drop=self.STALE_KEYS)]
        )
        ctx = self.ctx({"run": self.RUN, "refresh": True})

        cache_report_results(
            ctx,
            [
                task_result(
                    "t1",
                    xunit_bytes=xunit_bytes(),
                    request_plan_filter="tag:tier1",
                )
            ],
            refresh=True,
        )
        return read_raw_results_json(path)["results"][0]

    def test_every_key_is_present_after_the_refresh(self):
        from enge.utils.results_parser import TASK_ENTRY_KEYS, stale_task_keys

        entry = self._refresh_stale_run()

        self.assertEqual(list(entry), list(TASK_ENTRY_KEYS))
        self.assertEqual(stale_task_keys(entry), frozenset())

    def test_previously_absent_keys_hold_the_fresh_value_not_null(self):
        entry = self._refresh_stale_run()

        self.assertEqual(entry["source"], "9.9")
        self.assertEqual(entry["target"], "10.3")
        self.assertEqual(entry["git_ref"], "main")
        self.assertEqual(entry["event"], "preliminary")
        self.assertEqual(entry["build_ids"], ["copr:12345"])
        self.assertEqual(entry["artifacts_url"], "https://tf.example.com/artifacts/t1")
        self.assertEqual(entry["plan"], "/plans/p1")
        self.assertEqual(entry["plan_filter"], "tag:tier1")


# ---------------------------------------------------------------------------
# Refresh reporting and fallbacks
# ---------------------------------------------------------------------------


class TestRefreshInfoLine(RefreshTestCase):
    RUN = "01RUNINFOAAAAAAAAAAAAAAAAA"

    def test_info_reports_recovered_filled_and_the_verdict_transition(self):
        from enge.report.results_cache import cache_report_results

        write_manifest(
            self.runs_dir, self.RUN, [request_meta("t1"), request_meta("t2")]
        )
        write_cache(
            self.results_dir,
            self.RUN,
            [error_empty_task("t1"), cached_task("t2", drop=("artifacts_url",))],
            verdict="ERROR",
        )
        ctx = self.ctx({"run": self.RUN, "refresh": True})

        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(
                ctx,
                [
                    task_result("t1", xunit_bytes=xunit_bytes()),
                    task_result("t2", xunit_bytes=xunit_bytes()),
                ],
                refresh=True,
            )

        hits = matching(records, "refreshed run", level=logging.INFO)
        self.assertEqual(len(hits), 1, records)
        self.assertIn(self.RUN, hits[0])
        self.assertIn("1 task(s) recovered", hits[0])
        self.assertIn("1 task(s) had metadata filled", hits[0])
        self.assertIn("root verdict ERROR -> PASSED", hits[0])


class TestRefreshFallsBackToNormalGapFill(RefreshTestCase):
    RUN = "01RUNFALLBACKAAAAAAAAAAAAA"

    def test_absent_cache_is_created_by_the_normal_gap_fill(self):
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import parse_results_json

        write_manifest(self.runs_dir, self.RUN, [request_meta("t1")])
        ctx = self.ctx({"run": self.RUN, "refresh": True})

        cache_report_results(
            ctx, [task_result("t1", xunit_bytes=xunit_bytes())], refresh=True
        )

        schema = parse_results_json(self.results_dir / f"{self.RUN}.json")
        self.assertEqual(schema.results[0].verdict, "PASSED")
        self.assertEqual(schema.verdict, "PASSED")

    def test_unfinalized_cache_is_gap_filled_not_rewritten(self):
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import parse_results_json

        write_manifest(
            self.runs_dir, self.RUN, [request_meta("t1"), request_meta("t2")]
        )
        write_cache(self.results_dir, self.RUN, [cached_task("t1")], verdict=None)
        ctx = self.ctx({"run": self.RUN, "refresh": True})

        cache_report_results(
            ctx, [task_result("t2", xunit_bytes=xunit_bytes())], refresh=True
        )

        schema = parse_results_json(self.results_dir / f"{self.RUN}.json")
        self.assertEqual({t.task_id for t in schema.results}, {"t1", "t2"})
        self.assertEqual(schema.verdict, "PASSED")


# ---------------------------------------------------------------------------
# WARNING (item 7) -- ERROR + empty entries in a finalized cache
# ---------------------------------------------------------------------------


class TestErrorEmptyWarning(RefreshTestCase):
    RUN = "01RUNWARN7AAAAAAAAAAAAAAAA"

    def _report(self, tasks, root_verdict, *, refresh=False, pending=0):
        """Report over a cache holding `tasks`.

        `pending` adds manifest requests this invocation does not harvest, so
        `finalize_root_verdict` has more expected entries than recorded ones
        and no-ops -- the only way to keep the file unfinalized through a
        report that writes to it.
        """
        from enge.report.results_cache import cache_report_results

        write_manifest(
            self.runs_dir,
            self.RUN,
            [request_meta(t["task_id"]) for t in tasks]
            + [request_meta(f"pending{i}") for i in range(pending)],
        )
        write_cache(self.results_dir, self.RUN, tasks, verdict=root_verdict)
        ctx = self.ctx({"run": self.RUN, "refresh": refresh})

        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(
                ctx,
                [task_result(t["task_id"], request_state="ERROR") for t in tasks],
                refresh=refresh,
            )
        return records

    def test_warns_once_naming_the_run_and_the_count(self):
        records = self._report(
            [
                error_empty_task("t1"),
                error_empty_task("t2"),
                cached_task("t3"),
            ],
            "ERROR",
        )

        hits = matching(
            records,
            "task(s) with no test results recorded",
            level=logging.WARNING,
        )
        self.assertEqual(len(hits), 1, records)
        self.assertIn(self.RUN, hits[0])
        self.assertIn("has 2 task(s)", hits[0])
        self.assertIn(f"enge report --run {self.RUN} --refresh", hits[0])

    def test_no_warning_under_refresh(self):
        records = self._report([error_empty_task("t1")], "ERROR", refresh=True)

        self.assertEqual(matching(records, "task(s) with no test results recorded"), [])

    def test_no_warning_under_refresh_when_the_cache_is_written_by_this_run(self):
        """The suppression must hold on the path that *creates* the offending
        entry, not only on the one that finds it already cached.

        With no cache at invocation time, --refresh falls through to the
        normal gap-fill, which records the ERROR + [] entry and finalizes the
        run -- so the warning's precondition is met for the first time inside
        the very invocation that is suppressing it.
        """
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import parse_results_json

        write_manifest(self.runs_dir, self.RUN, [request_meta("t1")])
        ctx = self.ctx({"run": self.RUN, "refresh": True})

        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(
                ctx, [task_result("t1", request_state="ERROR")], refresh=True
            )

        # Guard the premise: the invocation must genuinely finalize a cache
        # holding the recoverable shape, otherwise nothing was suppressed.
        schema = parse_results_json(self.results_dir / f"{self.RUN}.json")
        self.assertEqual(schema.verdict, "ERROR")
        self.assertEqual([t.verdict for t in schema.results], ["ERROR"])
        self.assertEqual(schema.results[0].plans, [])
        self.assertEqual(matching(records, "task(s) with no test results recorded"), [])

    def test_no_warning_for_canceled_empty_entries(self):
        records = self._report(
            [
                cached_task(
                    "t1", verdict="CANCELED", plans=[], total_duration_seconds=0.0
                )
            ],
            "CANCELED",
        )

        self.assertEqual(matching(records, "task(s) with no test results recorded"), [])

    def test_no_warning_for_an_unfinalized_cache(self):
        """An unfinalized run has a live gap-fill path already; --refresh
        does not apply to it, so pointing the user at the flag would be
        wrong advice."""
        from enge.utils.results_parser import parse_results_json

        records = self._report([error_empty_task("t1")], None, pending=1)

        # Guard the premise: the invocation must genuinely leave the file
        # unfinalized, otherwise this test passes for the wrong reason.
        schema = parse_results_json(self.results_dir / f"{self.RUN}.json")
        self.assertIsNone(schema.verdict)
        self.assertEqual(matching(records, "task(s) with no test results recorded"), [])


# ---------------------------------------------------------------------------
# WARNING (item 8) -- aggregated staleness, report side
# ---------------------------------------------------------------------------


class TestReportStalenessWarning(RefreshTestCase):
    NEEDLE = "were cached by an older enge version"

    def _three_runs(self, stale_run_ids):
        run_ids = [
            "01RUNSTALE1AAAAAAAAAAAAAAA",
            "01RUNSTALE2AAAAAAAAAAAAAAA",
            "01RUNSTALE3AAAAAAAAAAAAAAA",
        ]
        for run_id in run_ids:
            write_manifest(self.runs_dir, run_id, [request_meta("t1")])
            drop = ("artifacts_url",) if run_id in stale_run_ids else ()
            write_cache(self.results_dir, run_id, [cached_task("t1", drop=drop)])
        return run_ids

    def _report(self, *, refresh=False):
        from enge.report.results_cache import cache_report_results

        ctx = self.ctx({"filter_set": ["setA"], "refresh": refresh})
        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(
                ctx,
                [task_result("t1", xunit_bytes=xunit_bytes())],
                refresh=refresh,
            )
        return ctx, records

    def _first_stale_in_resolution_order(self, ctx, stale_run_ids):
        from enge.utils.manifest_resolution import resolve_manifests_for_invocation

        for manifest in resolve_manifests_for_invocation(ctx):
            if manifest["run_id"] in stale_run_ids:
                return manifest["run_id"]
        self.fail("no stale run in resolution order")

    def test_one_warning_for_two_stale_runs_of_three(self):
        run_ids = self._three_runs(
            {"01RUNSTALE1AAAAAAAAAAAAAAA", "01RUNSTALE3AAAAAAAAAAAAAAA"}
        )
        stale = {run_ids[0], run_ids[2]}

        ctx, records = self._report()

        hits = matching(records, self.NEEDLE, level=logging.WARNING)
        self.assertEqual(len(hits), 1, records)
        self.assertIn("2 of 3 selected run(s)", hits[0])
        self.assertIn(self._first_stale_in_resolution_order(ctx, stale), hits[0])
        self.assertIn("--refresh", hits[0])

    def test_no_warning_when_no_run_is_stale(self):
        self._three_runs(set())

        _ctx, records = self._report()

        self.assertEqual(matching(records, self.NEEDLE), [])

    def test_no_warning_under_refresh(self):
        self._three_runs({"01RUNSTALE1AAAAAAAAAAAAAAA"})

        _ctx, records = self._report(refresh=True)

        self.assertEqual(matching(records, self.NEEDLE), [])

    def test_no_warning_under_refresh_even_when_the_run_stays_stale(self):
        """--refresh suppresses the pointer-at-the-flag advice unconditionally,
        including when the repair it advertises did not happen.

        A partial invocation makes G2 skip the run, so the stale cache is
        still stale when the invocation ends -- and the user is still told
        nothing about it, because they already passed the flag. What they get
        instead is the G2 WARNING naming the run.
        """
        from enge.report.results_cache import cache_report_results

        run_id = "01RUNSTALEG2AAAAAAAAAAAAAA"
        write_manifest(self.runs_dir, run_id, [request_meta("t1"), request_meta("t2")])
        path = write_cache(
            self.results_dir,
            run_id,
            [cached_task("t1", drop=("artifacts_url",)), cached_task("t2")],
        )
        before = path.read_bytes()
        ctx = self.ctx({"run": run_id, "refresh": True})

        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(
                ctx, [task_result("t1", xunit_bytes=xunit_bytes())], refresh=True
            )

        # Guard the premise: G2 must genuinely have left the stale file alone.
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(
            len(matching(records, "cannot refresh run", level=logging.WARNING)),
            1,
            records,
        )
        self.assertEqual(matching(records, self.NEEDLE), [])

    def test_an_unfinalized_cache_is_never_reported_as_stale(self):
        """Report-side mirror of the compare-side rule: --refresh refuses an
        unfinalized cache, so advertising it against one would be advice the
        user cannot act on. The missing key is filled by the ordinary
        gap-fill when the remaining task lands."""
        from enge.report.results_cache import cache_report_results

        run_id = "01RUNSTALEOPENAAAAAAAAAAAA"
        write_manifest(self.runs_dir, run_id, [request_meta("t1"), request_meta("t2")])
        path = write_cache(
            self.results_dir,
            run_id,
            [cached_task("t1", drop=("artifacts_url",))],
            verdict=None,
        )
        ctx = self.ctx({"run": run_id, "refresh": False})

        with captured_logs(CACHE_LOGGER) as records:
            cache_report_results(ctx, [], refresh=False)

        # Guard the premise: the file must still be unfinalized and still be
        # missing the key, or it is not the case under test any more.
        raw = json.loads(path.read_text())
        self.assertIsNone(raw["verdict"])
        self.assertNotIn("artifacts_url", raw["results"][0])
        self.assertEqual(matching(records, self.NEEDLE), [])


# ---------------------------------------------------------------------------
# CLI wiring and validation
# ---------------------------------------------------------------------------


class TestRefreshCli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_root = Path(tmp.name)
        self.runs_dir = self.tmp_root / "runs"
        self.results_dir = self.tmp_root / "results"

    def _ctx(self, **cli_overrides):
        cli = {
            "list": False,
            "show_ids": False,
            "compare": False,
            "jira": False,
            "short": False,
            "skip_pass": False,
            "show_tests": False,
            "refresh": True,
            "run": None,
            "filter_set": None,
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
            "since": None,
            "until": None,
        }
        cli.update(cli_overrides)
        return make_app_context(
            action="report",
            extra_cli=cli,
            extra_config={"common": {"results_dir": str(self.results_dir)}},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.tmp_root / "nonexistent-latest"),
        )

    def test_report_subparser_exposes_refresh(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(args=["report", "--refresh"])

        self.assertTrue(args.refresh)

    def test_refresh_defaults_to_false(self):
        from enge.utils.arg_parser import get_arguments

        self.assertFalse(get_arguments(args=["report"]).refresh)

    def _assert_rejected_without_fetching(self, ctx, *, mentioning=None):
        import enge.report.__main__ as rm

        with patch.object(rm, "build_table") as mock_build_table:
            with self.assertRaises(ValidationError) as caught:
                rm.main(ctx)

        mock_build_table.assert_not_called()
        self.assertIn("--refresh", str(caught.exception))
        if mentioning is not None:
            self.assertIn(mentioning, str(caught.exception))

    def _selected_run(self):
        """A run this ctx resolves to.

        Without it the invocation has no run selection at all, and the
        raw-input branch of `_validate_refresh` rejects it first -- which
        would make a --list/--compare test pass no matter what the
        flag-combination branch does.
        """
        run_id = "01RUNCLIAAAAAAAAAAAAAAAAAA"
        write_manifest(self.runs_dir, run_id, [request_meta("t1")])
        return run_id

    def test_refresh_with_input_is_rejected_before_the_fetch(self):
        self._assert_rejected_without_fetching(
            self._ctx(input=["5d67eecf-a02d-46b7-aee2-9ffb673f40df"])
        )

    def test_refresh_with_file_is_rejected_before_the_fetch(self):
        self._assert_rejected_without_fetching(self._ctx(file=["/tmp/tasks.txt"]))

    def test_refresh_with_list_is_rejected(self):
        self._assert_rejected_without_fetching(
            self._ctx(run=self._selected_run(), list=True), mentioning="--list"
        )

    def test_refresh_with_compare_is_rejected(self):
        self._assert_rejected_without_fetching(
            self._ctx(run=self._selected_run(), compare=True), mentioning="--compare"
        )

    @patch("enge.__main__.get_arguments")
    @patch("enge.utils.opt_manager.ParsedOpts")
    def test_refresh_with_input_exits_two_end_to_end(self, mock_po_cls, mock_get_args):
        """The guard raises ValidationError; `enge.__main__` maps that to
        exit 2. Driven through the real dispatch so the two halves are
        pinned together, not just individually."""
        from types import SimpleNamespace

        import enge.__main__ as enge_main
        from enge.utils.globals import ExitCode
        from enge.utils.opt_manager import TestingFarmEndpoint

        mock_get_args.return_value = SimpleNamespace(debug=False)
        mock_po_cls.return_value = SimpleNamespace(
            cli_args=SimpleNamespace(
                action="report",
                debug=False,
                refresh=True,
                input=["5d67eecf-a02d-46b7-aee2-9ffb673f40df"],
                file=None,
                list=False,
                compare=False,
                show_ids=False,
            ),
            config={
                "testing_farm": {
                    "api_key": "k",
                    "api_endpoint_url": "https://tf.example.com/api",
                    "log_artifact_baseurl": "https://tf.example.com/artifacts",
                },
                "common": {
                    "archive_tasks_latest": "/tmp/l",
                    "archive_tasks_default": "/tmp/d",
                },
                "project": {},
                "tests": {},
                "reportportal": {},
            },
            testing_farm_endpoint=TestingFarmEndpoint(
                "https://tf.example.com/api",
                "https://tf.example.com/artifacts",
            ),
            archive_tasks_latest="/tmp/l",
            archive_tasks_default="/tmp/d",
        )

        import enge.report.__main__ as rm

        with patch.object(rm, "build_table") as mock_build_table:
            code = enge_main.main()

        mock_build_table.assert_not_called()
        self.assertEqual(code, ExitCode.TEST_FAILURE)

    def test_refresh_flag_reaches_the_cache_writer(self):
        import enge.report.__main__ as rm
        from rich.table import Table
        from enge.utils.globals import ExitCode

        run_id = "01RUNCLIAAAAAAAAAAAAAAAAAA"
        write_manifest(self.runs_dir, run_id, [request_meta("t1")])
        ctx = self._ctx(run=run_id)

        table = Table()
        table.add_column("Test")
        table.add_row("dummy")

        with (
            patch.object(
                rm,
                "build_table",
                return_value=([(table, {"k": "v"})], ExitCode.SUCCESS, ["tr"]),
            ),
            patch("enge.report.results_cache.cache_report_results") as mock_cache,
        ):
            rm.main(ctx)

        mock_cache.assert_called_once_with(ctx, ["tr"], refresh=True)


# ---------------------------------------------------------------------------
# The refresh=False path is byte-for-byte what it was before this branch
# ---------------------------------------------------------------------------


class TestDefaultPathUnchanged(RefreshTestCase):
    RUN = "01RUNDEFAULTAAAAAAAAAAAAAA"

    def test_a_fresh_run_caches_identically_with_refresh_false(self):
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import read_raw_results_json

        write_manifest(self.runs_dir, self.RUN, [request_meta("t1")])
        ctx = self.ctx({"run": self.RUN})

        cache_report_results(
            ctx, [task_result("t1", xunit_bytes=xunit_bytes())], refresh=False
        )
        path = self.results_dir / f"{self.RUN}.json"
        written = read_raw_results_json(path)

        self.assertEqual(written["verdict"], "PASSED")
        self.assertEqual(len(written["results"]), 1)
        entry = written["results"][0]
        self.assertEqual(entry["verdict"], "PASSED")
        self.assertEqual(entry["artifacts_url"], "https://tf.example.com/artifacts/t1")
        self.assertEqual((self.results_dir / self.RUN / "t1.xml").exists(), True)

    def test_refresh_defaults_to_false_for_positional_callers(self):
        """The new keyword must be optional -- every existing caller passes
        two positional arguments and nothing else."""
        from enge.report.results_cache import cache_report_results
        from enge.utils.results_parser import parse_results_json

        write_manifest(self.runs_dir, self.RUN, [request_meta("t1")])
        ctx = self.ctx({"run": self.RUN})

        cache_report_results(ctx, [task_result("t1", xunit_bytes=xunit_bytes())])

        schema = parse_results_json(self.results_dir / f"{self.RUN}.json")
        self.assertEqual(schema.verdict, "PASSED")

    def test_finalized_cache_is_not_rewritten_without_refresh(self):
        from enge.report.results_cache import cache_report_results

        write_manifest(self.runs_dir, self.RUN, [request_meta("t1")])
        path = write_cache(
            self.results_dir, self.RUN, [error_empty_task("t1")], verdict="ERROR"
        )
        before = path.read_bytes()
        ctx = self.ctx({"run": self.RUN})

        cache_report_results(
            ctx, [task_result("t1", xunit_bytes=xunit_bytes())], refresh=False
        )

        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
