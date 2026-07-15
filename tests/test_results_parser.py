import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enge.utils.errors import ValidationError
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
from enge.utils.results_parser import parse_results_json
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
