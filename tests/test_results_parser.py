import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enge.utils.errors import ValidationError
from enge.utils.results_parser import (
    ResultsJsonSchema,
    parse_results_json,
    write_results_json,
)
from enge.utils.state_paths import results_dir


FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_PATH = FIXTURES_DIR / "results_golden.json"
GOLDEN_CANCELED_PATH = FIXTURES_DIR / "results_golden_canceled.json"


def _load(path):
    return json.loads(Path(path).read_text())


class TestResultsJsonSchemaValidation(unittest.TestCase):
    def _valid_payload(self):
        return _load(GOLDEN_PATH)

    def test_valid_results_json_passes(self):
        schema = ResultsJsonSchema.from_dict(self._valid_payload())
        self.assertEqual(schema.run_id, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        self.assertEqual(len(schema.tests), 4)

    def test_missing_run_id_rejected(self):
        payload = self._valid_payload()
        del payload["run_id"]
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_missing_tests_field_rejected(self):
        payload = self._valid_payload()
        del payload["tests"]
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_unknown_root_verdict_enum_rejected(self):
        payload = self._valid_payload()
        payload["verdict"] = "RUNNING"
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_unknown_per_test_verdict_enum_rejected(self):
        payload = self._valid_payload()
        payload["tests"][0]["verdict"] = "RUNNING"
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_canceled_with_nonempty_tests_rejected(self):
        payload = _load(GOLDEN_CANCELED_PATH)
        payload["tests"] = [
            {"name": "leftover", "verdict": "PASSED", "duration_seconds": 1.0}
        ]
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_canceled_with_empty_tests_and_positive_duration_accepted(self):
        schema = ResultsJsonSchema.from_dict(_load(GOLDEN_CANCELED_PATH))
        self.assertEqual(schema.verdict, "CANCELED")
        self.assertEqual(schema.tests, [])
        self.assertGreater(schema.total_duration_seconds, 0)

    def test_error_detail_omitted_for_passing_test_is_allowed(self):
        payload = self._valid_payload()
        passing = next(t for t in payload["tests"] if t["verdict"] == "PASSED")
        self.assertNotIn("error_detail", passing)
        schema = ResultsJsonSchema.from_dict(payload)
        parsed_passing = next(t for t in schema.tests if t.verdict == "PASSED")
        self.assertIsNone(parsed_passing.error_detail)

    def test_error_detail_included_for_failed_test_is_allowed(self):
        schema = ResultsJsonSchema.from_dict(self._valid_payload())
        failed = next(t for t in schema.tests if t.verdict == "FAILED")
        self.assertIsNotNone(failed.error_detail)

    def test_rejects_non_iso8601_timestamp(self):
        payload = self._valid_payload()
        payload["request_timestamp"] = "not-a-timestamp"
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_duration_must_be_a_number(self):
        payload = self._valid_payload()
        payload["tests"][0]["duration_seconds"] = "fast"
        with self.assertRaises(ValidationError):
            ResultsJsonSchema.from_dict(payload)

    def test_verdict_is_caller_supplied_not_derived(self):
        # Contract gap note (see DEBRIEF.md "Verdict inheritance"): nothing
        # in the schema spec auto-derives the root verdict from per-test
        # verdicts -- verdict is a required, explicit, caller-supplied
        # value. This pins that honest contract: a root verdict that
        # disagrees with the "worst" per-test verdict is still accepted
        # verbatim, and must stay that way unless a maintainer specifies
        # auto-derivation explicitly.
        payload = self._valid_payload()
        payload["verdict"] = "PASSED"  # tests contain FAILED/ERROR entries
        schema = ResultsJsonSchema.from_dict(payload)
        self.assertEqual(schema.verdict, "PASSED")

    def test_golden_fixture_duration_arithmetic_is_internally_consistent(self):
        # Not a schema-enforced rule (see DEBRIEF.md "Duration math"); this
        # is a regression check on the fixture's own arithmetic.
        payload = self._valid_payload()
        total = payload["total_duration_seconds"]
        summed = sum(t["duration_seconds"] for t in payload["tests"])
        self.assertTrue(
            math.isclose(total, summed, rel_tol=1e-9, abs_tol=1e-6),
            f"golden fixture arithmetic drifted: total={total} sum(tests)={summed}",
        )


class TestWriteAndParseRoundTrip(unittest.TestCase):
    def test_round_trip_write_then_parse_equals_golden(self):
        payload = _load(GOLDEN_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            written_path = write_results_json(
                run_id=payload["run_id"],
                verdict=payload["verdict"],
                tests=payload["tests"],
                request_timestamp=payload["request_timestamp"],
                set=payload["set"],
                tier=payload["tier"],
                arch=payload["arch"],
                source=payload["source"],
                target=payload["target"],
                total_duration_seconds=payload["total_duration_seconds"],
                output_path=tmp,
            )
            self.assertEqual(written_path, Path(tmp) / f"{payload['run_id']}.json")
            parsed = parse_results_json(written_path)
            expected = ResultsJsonSchema.from_dict(payload)
            self.assertEqual(parsed, expected)

    def test_round_trip_preserves_failed_test_verdict(self):
        # Mutation-check readiness: forcing this FAILED verdict to PASSED in
        # a scratch copy of the golden fixture must make this assertion
        # fail. See SESSION_LOG.md for the manual mutation-check proof.
        payload = _load(GOLDEN_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            written = write_results_json(
                run_id=payload["run_id"],
                verdict=payload["verdict"],
                tests=payload["tests"],
                request_timestamp=payload["request_timestamp"],
                set=payload["set"],
                tier=payload["tier"],
                arch=payload["arch"],
                source=payload["source"],
                target=payload["target"],
                total_duration_seconds=payload["total_duration_seconds"],
                output_path=tmp,
            )
            parsed = parse_results_json(written)
        failed_test = next(
            t for t in parsed.tests if t.name == "test_rollback_scenario"
        )
        self.assertEqual(failed_test.verdict, "FAILED")

    def test_write_accepts_explicit_metadata_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = write_results_json(
                run_id="01TESTRUNIDXXXXXXXXXXXXXXX",
                verdict="PASSED",
                tests=[{"name": "t1", "verdict": "PASSED", "duration_seconds": 1.5}],
                request_timestamp="2026-07-13T00:00:00Z",
                set="my-set",
                tier="tier1",
                arch="aarch64",
                source="rhel-8.10",
                target="rhel-9.4",
                total_duration_seconds=1.5,
                output_path=tmp,
            )
            parsed = parse_results_json(written)
        self.assertEqual(parsed.set, "my-set")
        self.assertEqual(parsed.tier, "tier1")
        self.assertEqual(parsed.arch, "aarch64")
        self.assertEqual(parsed.source, "rhel-8.10")
        self.assertEqual(parsed.target, "rhel-9.4")

    def test_write_rejects_invalid_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                write_results_json(
                    run_id="run-x",
                    verdict="CANCELED",
                    tests=[
                        {"name": "t1", "verdict": "PASSED", "duration_seconds": 1.0}
                    ],
                    request_timestamp="2026-07-13T00:00:00Z",
                    set="s",
                    tier="t",
                    arch="x86_64",
                    source="src",
                    target="tgt",
                    total_duration_seconds=1.0,
                    output_path=tmp,
                )

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
