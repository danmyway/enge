import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enge.utils.errors import AlreadyFinalizedError, ConflictError, ValidationError
from enge.utils.results_parser import XUNIT_RESULT_MAP
from enge.utils.results_parser import PlanEntry
from enge.utils.results_parser import ResultsJsonSchema
from enge.utils.results_parser import TaskEntry

# Aliased: a bare "TestEntry" import makes pytest's default collector treat
# the name as a candidate test class (Test* prefix) purely because it's
# present in this module's namespace, emitting a spurious
# PytestCollectionWarning (same root cause as the pre-existing
# "TestingFarmEndpoint" warnings from other test modules). The alias keeps
# the import but not the "Test" prefix.
from enge.utils.results_parser import TestEntry as _TestEntry
from enge.utils.results_parser import Verdict
from enge.utils.results_parser import finalize_root_verdict
from enge.utils.results_parser import init_results_json
from enge.utils.results_parser import parse_results_json
from enge.utils.results_parser import upsert_task_result
from enge.utils.results_parser import write_xunit
from enge.utils.state_paths import results_dir


# ---------------------------------------------------------------------------
# Payload builders -- v3.1 schema fixtures inline (the on-disk golden
# fixtures are introduced separately once the schema is green; these
# validation tests intentionally do not depend on them).
# ---------------------------------------------------------------------------


def _test_payload(**overrides):
    payload = {
        "name": (
            "/tests/newstyle/upgrades/tests/nondestructive/"
            "test_var_run_symlink.py::TestVarRunSymlink"
        ),
        "verdict": "FAILED",
        "duration_seconds": 75.0,
        "start_time": "2026-07-07T10:51:27.205058+00:00",
        "end_time": "2026-07-07T10:52:48.504964+00:00",
    }
    payload.update(overrides)
    return payload


def _plan_payload(**overrides):
    payload = {
        "name": "/plans/newstyle/nondestructive/verification_99_103_ctc2",
        "verdict": "FAILED",
        "tests": [_test_payload()],
    }
    payload.update(overrides)
    return payload


def _task_payload(**overrides):
    payload = {
        "task_id": "5d67eecf-a02d-46b7-aee2-9ffb673f40df",
        "set": "verification_99_103_ctc2-ver-9to10",
        "tier": "tier3",
        "arch": "s390x",
        "source_compose": "RHEL-9.9.0-20260629.0",
        "target_compose": None,
        "dispatched_at": "2026-07-07T10:35:13Z",
        "verdict": "ERROR",
        "total_duration_seconds": 349.0,
        "plans": [_plan_payload()],
    }
    payload.update(overrides)
    return payload


def _root_payload(**overrides):
    payload = {
        "schema_version": 1,
        "run_id": "01KWY2ANGF1TCT6E3X8M7QPJTW",
        "created_at": "2026-07-07T10:35:07Z",
        "event": "preliminary",
        "source": "9.9",
        "target": "10.3",
        "verdict": None,
        "results": [_task_payload()],
    }
    payload.update(overrides)
    return payload


class TestTestEntryValidation(unittest.TestCase):
    def test_valid_test_entry_parses(self):
        entry = _TestEntry.from_dict(_test_payload())
        self.assertEqual(entry.verdict, "FAILED")
        self.assertEqual(entry.duration_seconds, 75.0)
        self.assertEqual(entry.start_time, "2026-07-07T10:51:27.205058+00:00")

    def test_missing_required_field_rejected(self):
        for field in ("name", "verdict", "duration_seconds"):
            with self.subTest(field=field):
                payload = _test_payload()
                del payload[field]
                with self.assertRaises(ValidationError):
                    _TestEntry.from_dict(payload)

    def test_unknown_verdict_rejected(self):
        with self.assertRaises(ValidationError):
            _TestEntry.from_dict(_test_payload(verdict="RUNNING"))

    def test_duration_must_be_a_number(self):
        with self.assertRaises(ValidationError):
            _TestEntry.from_dict(_test_payload(duration_seconds="fast"))

    def test_duration_zero_allowed_for_not_run(self):
        entry = _TestEntry.from_dict(_test_payload(duration_seconds=0, verdict="ERROR"))
        self.assertEqual(entry.duration_seconds, 0.0)

    def test_optional_fields_default_to_none_when_omitted(self):
        payload = {
            "name": "t1",
            "verdict": "PASSED",
            "duration_seconds": 1.0,
        }
        entry = _TestEntry.from_dict(payload)
        self.assertIsNone(entry.start_time)
        self.assertIsNone(entry.end_time)
        self.assertIsNone(entry.output)
        self.assertIsNone(entry.error_detail)

    def test_non_iso8601_start_time_rejected(self):
        with self.assertRaises(ValidationError):
            _TestEntry.from_dict(_test_payload(start_time="not-a-timestamp"))


class TestPlanEntryValidation(unittest.TestCase):
    def test_valid_plan_entry_parses(self):
        plan = PlanEntry.from_dict(_plan_payload())
        self.assertEqual(plan.verdict, "FAILED")
        self.assertEqual(len(plan.tests), 1)

    def test_missing_required_field_rejected(self):
        for field in ("name", "verdict", "tests"):
            with self.subTest(field=field):
                payload = _plan_payload()
                del payload[field]
                with self.assertRaises(ValidationError):
                    PlanEntry.from_dict(payload)

    def test_skipped_plan_with_empty_tests_accepted(self):
        plan = PlanEntry.from_dict(_plan_payload(verdict="SKIPPED", tests=[]))
        self.assertEqual(plan.tests, [])

    def test_error_plan_with_empty_tests_accepted(self):
        plan = PlanEntry.from_dict(_plan_payload(verdict="ERROR", tests=[]))
        self.assertEqual(plan.tests, [])

    def test_passed_plan_with_empty_tests_rejected(self):
        with self.assertRaises(ValidationError):
            PlanEntry.from_dict(_plan_payload(verdict="PASSED", tests=[]))

    def test_failed_plan_with_empty_tests_rejected(self):
        with self.assertRaises(ValidationError):
            PlanEntry.from_dict(_plan_payload(verdict="FAILED", tests=[]))

    def test_unknown_plan_verdict_rejected(self):
        with self.assertRaises(ValidationError):
            PlanEntry.from_dict(_plan_payload(verdict="RUNNING"))


class TestTaskEntryValidation(unittest.TestCase):
    def test_valid_task_entry_parses(self):
        task = TaskEntry.from_dict(_task_payload())
        self.assertEqual(task.task_id, "5d67eecf-a02d-46b7-aee2-9ffb673f40df")
        self.assertIsNone(task.target_compose)
        self.assertEqual(len(task.plans), 1)

    def test_missing_required_field_rejected(self):
        for field in (
            "task_id",
            "set",
            "tier",
            "arch",
            "source_compose",
            "target_compose",
            "dispatched_at",
            "verdict",
            "total_duration_seconds",
            "plans",
        ):
            with self.subTest(field=field):
                payload = _task_payload()
                del payload[field]
                with self.assertRaises(ValidationError):
                    TaskEntry.from_dict(payload)

    def test_source_compose_null_value_accepted_when_key_present(self):
        task = TaskEntry.from_dict(
            _task_payload(source_compose=None, verdict="CANCELED", plans=[])
        )
        self.assertIsNone(task.source_compose)

    def test_canceled_task_with_nonempty_plans_rejected(self):
        with self.assertRaises(ValidationError):
            TaskEntry.from_dict(_task_payload(verdict="CANCELED"))

    def test_canceled_task_with_empty_plans_accepted(self):
        task = TaskEntry.from_dict(_task_payload(verdict="CANCELED", plans=[]))
        self.assertEqual(task.plans, [])

    def test_error_task_with_empty_plans_accepted(self):
        task = TaskEntry.from_dict(_task_payload(verdict="ERROR", plans=[]))
        self.assertEqual(task.plans, [])

    def test_error_task_with_populated_plans_accepted(self):
        task = TaskEntry.from_dict(_task_payload(verdict="ERROR"))
        self.assertEqual(len(task.plans), 1)

    def test_passed_task_with_empty_plans_rejected(self):
        with self.assertRaises(ValidationError):
            TaskEntry.from_dict(_task_payload(verdict="PASSED", plans=[]))

    def test_failed_task_with_empty_plans_rejected(self):
        with self.assertRaises(ValidationError):
            TaskEntry.from_dict(_task_payload(verdict="FAILED", plans=[]))

    def test_passed_task_with_populated_plans_accepted(self):
        task = TaskEntry.from_dict(
            _task_payload(verdict="PASSED", plans=[_plan_payload(verdict="PASSED")])
        )
        self.assertEqual(task.verdict, "PASSED")

    def test_task_verdict_is_required_and_non_null(self):
        payload = _task_payload()
        payload["verdict"] = None
        with self.assertRaises(ValidationError):
            TaskEntry.from_dict(payload)

    def test_unknown_task_verdict_rejected(self):
        with self.assertRaises(ValidationError):
            TaskEntry.from_dict(_task_payload(verdict="RUNNING"))

    def test_dispatch_context_fields_absent_default_to_none_and_empty_list(self):
        """A pre-existing (pre-branch) cached task entry has none of the 5
        new keys. These are optional, not required -- from_dict() must not
        raise, and must default source/target/git_ref/event to None and
        build_ids to []."""
        payload = _task_payload()
        self.assertNotIn("source", payload)
        task = TaskEntry.from_dict(payload)
        self.assertIsNone(task.source)
        self.assertIsNone(task.target)
        self.assertIsNone(task.git_ref)
        self.assertIsNone(task.event)
        self.assertEqual(task.build_ids, [])

    def test_dispatch_context_fields_round_trip(self):
        task = TaskEntry.from_dict(
            _task_payload(
                source="9.9",
                target="10.3",
                git_ref="rhsm-branch",
                event="preliminary",
                build_ids=["12345:centos-stream9-x86_64"],
            )
        )
        self.assertEqual(task.source, "9.9")
        self.assertEqual(task.target, "10.3")
        self.assertEqual(task.git_ref, "rhsm-branch")
        self.assertEqual(task.event, "preliminary")
        self.assertEqual(task.build_ids, ["12345:centos-stream9-x86_64"])

        d = task.to_dict()
        self.assertEqual(d["source"], "9.9")
        self.assertEqual(d["target"], "10.3")
        self.assertEqual(d["git_ref"], "rhsm-branch")
        self.assertEqual(d["event"], "preliminary")
        self.assertEqual(d["build_ids"], ["12345:centos-stream9-x86_64"])

    def test_to_dict_always_emits_dispatch_context_keys_even_when_absent(self):
        """Freshly round-tripped entries must always carry all 5 keys, even
        when their values are the None/[] defaults -- emit-always applies
        to results.json task entries too, not just manifest requests."""
        task = TaskEntry.from_dict(_task_payload())
        d = task.to_dict()
        for key in ("source", "target", "git_ref", "event"):
            self.assertIn(key, d)
            self.assertIsNone(d[key])
        self.assertIn("build_ids", d)
        self.assertEqual(d["build_ids"], [])

    def test_rerun_artifacts_plan_metadata_absent_default_to_none(self):
        """rerun_of/artifacts_url/plan/plan_filter are optional -- a cached
        task entry predating this extension has none of them, and
        from_dict() must not raise, defaulting all four to None."""
        payload = _task_payload()
        for key in ("rerun_of", "artifacts_url", "plan", "plan_filter"):
            self.assertNotIn(key, payload)
        task = TaskEntry.from_dict(payload)
        self.assertIsNone(task.rerun_of)
        self.assertIsNone(task.artifacts_url)
        self.assertIsNone(task.plan)
        self.assertIsNone(task.plan_filter)

    def test_rerun_artifacts_plan_metadata_round_trip(self):
        task = TaskEntry.from_dict(
            _task_payload(
                rerun_of="parent-uuid",
                artifacts_url="https://tf.example.com/artifacts/task",
                plan="plans",
                plan_filter="tag:verification_99_103_ctc2",
            )
        )
        self.assertEqual(task.rerun_of, "parent-uuid")
        self.assertEqual(task.artifacts_url, "https://tf.example.com/artifacts/task")
        self.assertEqual(task.plan, "plans")
        self.assertEqual(task.plan_filter, "tag:verification_99_103_ctc2")

        d = task.to_dict()
        self.assertEqual(d["rerun_of"], "parent-uuid")
        self.assertEqual(d["artifacts_url"], "https://tf.example.com/artifacts/task")
        self.assertEqual(d["plan"], "plans")
        self.assertEqual(d["plan_filter"], "tag:verification_99_103_ctc2")

    def test_to_dict_always_emits_rerun_artifacts_plan_keys_even_when_absent(self):
        """Emit-always applies to these four keys too -- always present
        with a null value when not applicable, never omitted."""
        task = TaskEntry.from_dict(_task_payload())
        d = task.to_dict()
        for key in ("rerun_of", "artifacts_url", "plan", "plan_filter"):
            self.assertIn(key, d)
            self.assertIsNone(d[key])


class TestResultsJsonSchemaValidation(unittest.TestCase):
    def test_valid_root_payload_parses(self):
        schema = ResultsJsonSchema.from_dict(_root_payload())
        self.assertEqual(schema.run_id, "01KWY2ANGF1TCT6E3X8M7QPJTW")
        self.assertEqual(schema.schema_version, 1)
        self.assertIsNone(schema.verdict)
        self.assertEqual(len(schema.results), 1)

    def test_empty_results_list_with_null_verdict_accepted(self):
        schema = ResultsJsonSchema.from_dict(_root_payload(results=[]))
        self.assertEqual(schema.results, [])
        self.assertIsNone(schema.verdict)

    def test_schema_version_not_1_rejected(self):
        for bad_version in (0, 2, "1"):
            with self.subTest(bad_version=bad_version):
                with self.assertRaises(ValidationError):
                    ResultsJsonSchema.from_dict(
                        _root_payload(schema_version=bad_version)
                    )

    def test_missing_required_root_field_rejected(self):
        for field in (
            "schema_version",
            "run_id",
            "created_at",
            "event",
            "source",
            "target",
            "verdict",
            "results",
        ):
            with self.subTest(field=field):
                payload = _root_payload()
                del payload[field]
                with self.assertRaises(ValidationError):
                    ResultsJsonSchema.from_dict(payload)

    def test_root_verdict_null_accepted(self):
        schema = ResultsJsonSchema.from_dict(_root_payload(verdict=None))
        self.assertIsNone(schema.verdict)

    def test_root_verdict_unknown_enum_rejected(self):
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(_root_payload(verdict="RUNNING"))

    def test_root_verdict_non_null_enum_accepted(self):
        schema = ResultsJsonSchema.from_dict(_root_payload(verdict="ERROR"))
        self.assertEqual(schema.verdict, "ERROR")

    def test_duplicate_task_id_rejected(self):
        task = _task_payload()
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(_root_payload(results=[task, dict(task)]))

    def test_results_not_a_list_rejected(self):
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(_root_payload(results={}))

    def test_envelope_event_source_target_accept_null(self):
        """Root envelope keeps 'only things relevant for the whole run as
        a batch' (maintainer principle, 2026-07-24): a multi-set run has
        no single correct event/source/target, so these become nullable
        -- required keys, nullable values, mirroring the existing
        source_compose/target_compose precedent at the task level."""
        schema = ResultsJsonSchema.from_dict(
            _root_payload(event=None, source=None, target=None)
        )
        self.assertIsNone(schema.event)
        self.assertIsNone(schema.source)
        self.assertIsNone(schema.target)

    def test_envelope_event_source_target_key_still_required(self):
        """Nullable value, but the key itself is still required -- unlike
        the fully-optional per-task dispatch-context fields, a missing
        key here is still rejected."""
        for field in ("event", "source", "target"):
            with self.subTest(field=field):
                payload = _root_payload()
                del payload[field]
                with self.assertRaises(ValidationError):
                    ResultsJsonSchema.from_dict(payload)

    def test_envelope_event_source_target_round_trip_when_populated(self):
        schema = ResultsJsonSchema.from_dict(
            _root_payload(event="preliminary", source="9.9", target="10.3")
        )
        d = schema.to_dict()
        self.assertEqual(d["event"], "preliminary")
        self.assertEqual(d["source"], "9.9")
        self.assertEqual(d["target"], "10.3")

    def test_to_dict_emits_null_envelope_event_source_target(self):
        schema = ResultsJsonSchema.from_dict(
            _root_payload(event=None, source=None, target=None)
        )
        d = schema.to_dict()
        self.assertIn("event", d)
        self.assertIn("source", d)
        self.assertIn("target", d)
        self.assertIsNone(d["event"])
        self.assertIsNone(d["source"])
        self.assertIsNone(d["target"])


class TestXunitResultMap(unittest.TestCase):
    def test_xunit_result_map_matches_ratified_contract(self):
        self.assertEqual(
            XUNIT_RESULT_MAP,
            {
                "passed": "PASSED",
                "failed": "FAILED",
                "error": "ERROR",
                "skipped": "SKIPPED",
                "undefined": "ERROR",
                "pending": "ERROR",
            },
        )

    def test_xunit_result_map_values_are_all_valid_verdicts(self):
        valid = {member.value for member in Verdict}
        self.assertTrue(set(XUNIT_RESULT_MAP.values()).issubset(valid))


class TestParseResultsJson(unittest.TestCase):
    def test_parse_valid_file_returns_nested_dataclasses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            path.write_text(json.dumps(_root_payload()))
            schema = parse_results_json(path)
        self.assertIsInstance(schema, ResultsJsonSchema)
        self.assertIsInstance(schema.results[0], TaskEntry)
        self.assertIsInstance(schema.results[0].plans[0], PlanEntry)
        self.assertIsInstance(schema.results[0].plans[0].tests[0], _TestEntry)

    def test_parse_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.json"
            bad_path.write_text("{not valid json")
            with self.assertRaises(ValidationError):
                parse_results_json(bad_path)


class TestInitResultsJson(unittest.TestCase):
    def test_init_creates_file_with_null_verdict_and_empty_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = init_results_json(
                run_id="01TESTRUNID00000000000000",
                created_at="2026-07-07T10:35:07Z",
                event="preliminary",
                source="9.9",
                target="10.3",
                output_dir=tmp,
            )
            self.assertEqual(path, Path(tmp) / "01TESTRUNID00000000000000.json")
            schema = parse_results_json(path)
            self.assertEqual(schema.schema_version, 1)
            self.assertEqual(schema.run_id, "01TESTRUNID00000000000000")
            self.assertIsNone(schema.verdict)
            self.assertEqual(schema.results, [])

    def test_init_refuses_to_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_results_json(
                run_id="01TESTRUNID00000000000000",
                created_at="2026-07-07T10:35:07Z",
                event="preliminary",
                source="9.9",
                target="10.3",
                output_dir=tmp,
            )
            with self.assertRaises(FileExistsError):
                init_results_json(
                    run_id="01TESTRUNID00000000000000",
                    created_at="2026-07-07T10:35:07Z",
                    event="preliminary",
                    source="9.9",
                    target="10.3",
                    output_dir=tmp,
                )


class _GapFillTestCase(unittest.TestCase):
    def _init(self, tmp, run_id="01RUNIDXXXXXXXXXXXXXXXXXXX"):
        return init_results_json(
            run_id=run_id,
            created_at="2026-07-07T10:35:07Z",
            event="preliminary",
            source="9.9",
            target="10.3",
            output_dir=tmp,
        )


class TestUpsertTaskResult(_GapFillTestCase):
    def test_upsert_new_task_id_appends_and_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            result = upsert_task_result(path, _task_payload())
            self.assertTrue(result)
            schema = parse_results_json(path)
            self.assertEqual(len(schema.results), 1)
            self.assertEqual(schema.results[0].task_id, _task_payload()["task_id"])

    def test_upsert_accepts_task_entry_dataclass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            entry = TaskEntry.from_dict(_task_payload())
            result = upsert_task_result(path, entry)
            self.assertTrue(result)

    def test_upsert_identical_task_is_noop_and_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            result = upsert_task_result(path, _task_payload())
            self.assertFalse(result)

    def test_upsert_identical_task_does_not_rewrite_file(self):
        # Mutation-check target (c): if the ConflictError branch is removed
        # and upsert silently overwrites instead, this still passes -- the
        # complementary test_upsert_conflicting_task_raises_conflict_error
        # is what catches that mutation. This test instead pins the
        # separate identical-content no-op-write guarantee.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            with patch("enge.utils.results_parser._atomic_write_json") as mock_write:
                result = upsert_task_result(path, _task_payload())
            self.assertFalse(result)
            mock_write.assert_not_called()

    def test_upsert_conflicting_task_raises_conflict_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            with self.assertRaises(ConflictError):
                upsert_task_result(
                    path,
                    _task_payload(
                        verdict="PASSED",
                        plans=[_plan_payload(verdict="PASSED")],
                    ),
                )

    def test_upsert_after_finalize_raises_conflict_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            finalize_root_verdict(path, expected_count=1)
            with self.assertRaises(ConflictError):
                upsert_task_result(path, _task_payload(task_id="a-different-task-id"))

    def test_upsert_after_finalize_raises_already_finalized_error(self):
        # Split target: the finalized-file case is the expected steady
        # state on every re-report of a finalized run, not a data-drift
        # signal -- it must raise the more specific subclass so the cache
        # layer can log it quietly instead of at WARNING.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            finalize_root_verdict(path, expected_count=1)
            with self.assertRaises(AlreadyFinalizedError) as cm:
                upsert_task_result(path, _task_payload(task_id="a-different-task-id"))
            # Compat fence: existing `except ConflictError` handlers must
            # keep working unchanged.
            self.assertIsInstance(cm.exception, ConflictError)

    def test_upsert_content_drift_on_unfinalized_file_is_not_already_finalized_error(
        self,
    ):
        # Split target: same task_id, different content, on a file that is
        # NOT finalized is genuine data drift -- it must stay a plain
        # ConflictError, never the finalized-file subclass.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            with self.assertRaises(ConflictError) as cm:
                upsert_task_result(
                    path,
                    _task_payload(
                        verdict="PASSED",
                        plans=[_plan_payload(verdict="PASSED")],
                    ),
                )
            self.assertNotIsInstance(cm.exception, AlreadyFinalizedError)


class TestFinalizeRootVerdict(_GapFillTestCase):
    def test_under_expected_count_is_noop_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload())
            result = finalize_root_verdict(path, expected_count=2)
            self.assertIsNone(result)
            schema = parse_results_json(path)
            self.assertIsNone(schema.verdict)

    def test_exact_expected_count_derives_severity_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(
                path,
                _task_payload(
                    task_id="t1",
                    verdict="PASSED",
                    plans=[_plan_payload(verdict="PASSED")],
                ),
            )
            upsert_task_result(path, _task_payload(task_id="t2", verdict="FAILED"))
            result = finalize_root_verdict(path, expected_count=2)
            self.assertEqual(result, "FAILED")
            schema = parse_results_json(path)
            self.assertEqual(schema.verdict, "FAILED")

    def test_error_outranks_failed_in_severity_derivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload(task_id="t1", verdict="FAILED"))
            upsert_task_result(path, _task_payload(task_id="t2", verdict="ERROR"))
            result = finalize_root_verdict(path, expected_count=2)
            self.assertEqual(result, "ERROR")

    def test_all_skipped_derives_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(
                path, _task_payload(task_id="t1", verdict="SKIPPED", plans=[])
            )
            result = finalize_root_verdict(path, expected_count=1)
            self.assertEqual(result, "SKIPPED")

    def test_over_expected_count_raises_validation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(path, _task_payload(task_id="t1"))
            upsert_task_result(path, _task_payload(task_id="t2"))
            with self.assertRaises(ValidationError):
                finalize_root_verdict(path, expected_count=1)

    def test_zero_expected_count_raises_validation_error(self):
        # Mutation-check target: _derive_root_verdict([]) previously raised
        # the builtin ValueError ("max() arg is an empty sequence") for this
        # impossible-but-reachable input (expected_count=0 on a freshly
        # initialized file with zero results). ValidationError is this
        # module's contract for "impossible state", not a stdlib leak.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            with self.assertRaises(ValidationError):
                finalize_root_verdict(path, expected_count=0)

    def test_idempotent_finalize_returns_same_value_without_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._init(tmp)
            upsert_task_result(
                path,
                _task_payload(
                    task_id="t1",
                    verdict="PASSED",
                    plans=[_plan_payload(verdict="PASSED")],
                ),
            )
            first = finalize_root_verdict(path, expected_count=1)
            with patch("enge.utils.results_parser._atomic_write_json") as mock_write:
                second = finalize_root_verdict(path, expected_count=1)
            self.assertEqual(first, "PASSED")
            self.assertEqual(second, "PASSED")
            mock_write.assert_not_called()


class TestWriteXunit(unittest.TestCase):
    # Deliberately quirky bytes (CRLF, non-ASCII, no trailing newline) to
    # prove the write is a verbatim byte copy -- no re-encoding, no
    # transformation, no re-serialization through an XML parser.
    RAW_XUNIT = (
        b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
        b'<testsuite name="upgrade" tests="1">\n'
        b'  <testcase name="test_upgrade_9_to_10" time="120.5"/>\n'
        b"  <!-- non-ascii byte check: \xc3\xa9 -->\n"
        b"</testsuite>"
    )

    def test_write_xunit_stores_byte_identical_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_xunit(
                run_id="01RUNIDXXXXXXXXXXXXXXXXXXX",
                task_id="5d67eecf-a02d-46b7-aee2-9ffb673f40df",
                xunit_bytes=self.RAW_XUNIT,
                output_dir=tmp,
            )
            self.assertEqual(
                path,
                Path(tmp)
                / "01RUNIDXXXXXXXXXXXXXXXXXXX"
                / "5d67eecf-a02d-46b7-aee2-9ffb673f40df.xml",
            )
            self.assertEqual(path.read_bytes(), self.RAW_XUNIT)

    def test_write_xunit_identical_rewrite_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_xunit("01RUNID", "task-1", self.RAW_XUNIT, tmp)
            with patch("enge.utils.results_parser._atomic_write_bytes") as mock_write:
                result = write_xunit("01RUNID", "task-1", self.RAW_XUNIT, tmp)
            self.assertEqual(result, path)
            mock_write.assert_not_called()

    def test_write_xunit_conflicting_rewrite_raises_conflict_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_xunit("01RUNID", "task-1", self.RAW_XUNIT, tmp)
            with self.assertRaises(ConflictError):
                write_xunit("01RUNID", "task-1", self.RAW_XUNIT + b"tampered", tmp)

    def test_no_xunit_error_task_is_fully_representable_without_xml_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = init_results_json(
                run_id="01NOXUNIT0000000000000000",
                created_at="2026-07-07T10:35:07Z",
                event="preliminary",
                source="9.9",
                target="10.3",
                output_dir=tmp,
            )
            upsert_task_result(
                results_path,
                _task_payload(task_id="no-xunit-task", verdict="ERROR", plans=[]),
            )
            schema = parse_results_json(results_path)
            self.assertEqual(schema.results[0].verdict, "ERROR")
            self.assertEqual(schema.results[0].plans, [])
            xunit_path = Path(tmp) / "01NOXUNIT0000000000000000" / "no-xunit-task.xml"
            self.assertFalse(xunit_path.exists())


class TestResultsDirStatePath(unittest.TestCase):
    def test_results_dir_creates_directory_on_first_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            xdg_data_home = Path(tmp) / "xdg-data"
            self.assertFalse(xdg_data_home.exists())
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg_data_home)}):
                path = results_dir({})
            self.assertTrue(path.exists())
            self.assertTrue(path.is_dir())
            self.assertEqual(path, xdg_data_home / "enge" / "results")

    def test_results_dir_respects_config_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "custom-results"
            path = results_dir({"common": {"results_dir": str(override)}})
            self.assertEqual(path, override)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
