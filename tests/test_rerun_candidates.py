"""Characterization pins and new-surface tests for RerunJobs' candidate API.

The T1-T6 characterization tests pin the exact observable output of the CLI
rerun path -- processed_data tuples, rerun_uuids order, the qualifying
info_table text, and the rerun payload shape -- so that growing an explicit
candidate surface on RerunJobs can be proven behavior-preserving.
"""

import unittest
from unittest.mock import patch, MagicMock

from rich.console import Console

from tests._helpers import make_app_context
from tests.test_rerun_di import PARSED_DICT_MIXED, TWO_GROUP_PARSED_DICT


SOURCE_PATH_MIX = "/archive/mix.json"

# Exact text qualify_results() renders for TWO_GROUP_PARSED_DICT at width 120.
# Captured from the pre-refactor implementation and pinned verbatim; it must
# not be re-derived from the code under test.
EXPECTED_INFO_TABLE = (
    "╭──────────────────┬─────────────────────┬────────┬"
    "──────────────┬──────────────╮\n"
    "│ Original Request │ Source Compose Name │ Arch   │ "
    "Re-run Plans │ Re-run Tests │\n"
    "├──────────────────┼─────────────────────┼────────┼"
    "──────────────┼──────────────┤\n"
    "│ uuid-1           │ RHEL-9.0-nightly    │ x86_64 │ "
    "/plan/plan_a │              │\n"
    "│                  │                     │        │ "
    "             │ case_one     │\n"
    "│                  │                     │        │ "
    "             │ case_two     │\n"
    "├──────────────────┼─────────────────────┼────────┼"
    "──────────────┼──────────────┤\n"
    "│ uuid-2           │ RHEL-9.0-nightly    │ x86_64 │ "
    "/plan/plan_b │              │\n"
    "╰──────────────────┴─────────────────────┴────────┴"
    "──────────────┴──────────────╯\n"
)

# Keys build_rerun_payloads() strips from the Testing Farm request details.
REMOVED_PAYLOAD_KEYS = {
    "id",
    "user_id",
    "token_id",
    "notes",
    "result",
    "run",
    "user",
    "queued_time",
    "run_time",
    "created",
    "updated",
    "state",
}


def _make_cli_jobs(mock_parse, mock_xunit, parsed_dict, extra_cli=None, uuid_map=None):
    """Build a CLI-path RerunJobs over a canned parsed_dict."""
    req_urls = [f"https://tf.example.com/api/{uid}" for uid in parsed_dict]
    if uuid_map is None:
        uuid_map = {uid: None for uid in parsed_dict}
    mock_parse.return_value = (req_urls, "latest", uuid_map)
    mock_xunit.return_value = parsed_dict
    cli = {"error": False, "fail": False, "dryrun": False}
    if extra_cli:
        cli.update(extra_cli)
    ctx = make_app_context(action="rerun", extra_cli=cli)

    from enge.rerun.__main__ import RerunJobs

    return RerunJobs(ctx)


def _tf_response(uuid="uuid-mix"):
    """A single-environment Testing Farm request-details payload."""
    return {
        "id": uuid,
        "state": "complete",
        "user_id": "user-1",
        "token_id": "token-1",
        "notes": ["a note"],
        "result": {"overall": "failed"},
        "run": {"artifacts": "https://artifacts.example.com"},
        "user": {"name": "tester"},
        "queued_time": 1,
        "run_time": 2,
        "created": "2026-01-01",
        "updated": "2026-01-02",
        "environments_requested": [
            {"os": {"compose": "RHEL-9.0-nightly"}, "arch": "x86_64"}
        ],
        "test": {"fmf": {"name": "/orig/plan", "plan_filter": "tier:0"}},
    }


class TestQualifyResultsProcessedDataPins(unittest.TestCase):
    """T1-T4: exact processed_data tuples and rerun_uuids for the CLI path."""

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_default_flags_processed_tuple_is_exact(self, mock_parse, mock_xunit):
        jobs = _make_cli_jobs(
            mock_parse,
            mock_xunit,
            PARSED_DICT_MIXED,
            uuid_map={"uuid-mix": SOURCE_PATH_MIX},
        )
        jobs.qualify_results()

        self.assertEqual(jobs.rerun_uuids, ["uuid-mix"])
        self.assertEqual(
            jobs.processed_data,
            {
                "uuid-mix": (
                    "/plan/fail_plan$|/plan/error_plan$",
                    "RHEL-9.0-nightly",
                    {"/plan/fail_plan": ["broken$"], "/plan/error_plan": []},
                    [],
                    SOURCE_PATH_MIX,
                )
            },
        )

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_fail_flag_processed_tuple_is_exact(self, mock_parse, mock_xunit):
        jobs = _make_cli_jobs(
            mock_parse,
            mock_xunit,
            PARSED_DICT_MIXED,
            extra_cli={"fail": True},
            uuid_map={"uuid-mix": SOURCE_PATH_MIX},
        )
        jobs.qualify_results()

        self.assertEqual(jobs.rerun_uuids, ["uuid-mix"])
        self.assertEqual(
            jobs.processed_data,
            {
                "uuid-mix": (
                    "/plan/fail_plan$",
                    "RHEL-9.0-nightly",
                    {"/plan/fail_plan": ["broken$"]},
                    [],
                    SOURCE_PATH_MIX,
                )
            },
        )

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_error_flag_processed_tuple_is_exact(self, mock_parse, mock_xunit):
        jobs = _make_cli_jobs(
            mock_parse,
            mock_xunit,
            PARSED_DICT_MIXED,
            extra_cli={"error": True},
            uuid_map={"uuid-mix": SOURCE_PATH_MIX},
        )
        jobs.qualify_results()

        self.assertEqual(jobs.rerun_uuids, ["uuid-mix"])
        self.assertEqual(
            jobs.processed_data,
            {
                "uuid-mix": (
                    "/plan/error_plan$",
                    "RHEL-9.0-nightly",
                    {"/plan/error_plan": []},
                    [],
                    SOURCE_PATH_MIX,
                )
            },
        )

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_missing_uuid_sweep_registers_exact_fallback_tuple(
        self, mock_parse, mock_xunit
    ):
        req_urls = [
            "https://tf.example.com/api/uuid-1",
            "https://tf.example.com/api/uuid-2",
            "https://tf.example.com/api/uuid-gone",
        ]
        mock_parse.return_value = (
            req_urls,
            "latest",
            {
                "uuid-1": "/archive/one.json",
                "uuid-2": "/archive/two.json",
                "uuid-gone": "/archive/gone.json",
            },
        )
        mock_xunit.return_value = TWO_GROUP_PARSED_DICT
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        from enge.rerun.__main__ import RerunJobs

        jobs = RerunJobs(ctx)
        jobs.qualify_results()

        self.assertEqual(jobs.rerun_uuids, ["uuid-1", "uuid-2", "uuid-gone"])
        self.assertEqual(
            jobs.processed_data["uuid-gone"],
            (None, None, None, None, "/archive/gone.json"),
        )


class TestQualifyResultsInfoTableText(unittest.TestCase):
    """T5: the rendered qualifying table, pinned character for character."""

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_info_table_renders_expected_text(self, mock_parse, mock_xunit):
        req_urls = [
            f"https://tf.example.com/api/{uid}" for uid in TWO_GROUP_PARSED_DICT
        ]
        mock_parse.return_value = (
            req_urls,
            "latest",
            {uid: None for uid in TWO_GROUP_PARSED_DICT},
        )
        mock_xunit.return_value = TWO_GROUP_PARSED_DICT
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": True},
        )

        from enge.rerun.__main__ import RerunJobs, console

        jobs = RerunJobs(ctx)
        with patch.object(console, "print") as mock_print:
            jobs.qualify_results()

        mock_print.assert_called_once()
        capture = Console(record=True, width=120, no_color=True)
        capture.print(mock_print.call_args[0][0])

        self.assertEqual(capture.export_text(), EXPECTED_INFO_TABLE)


class TestBuildRerunPayloadsShape(unittest.TestCase):
    """T6: payload shape for a task with one tested plan and one UNDEFINED plan."""

    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_undefined_plan_yields_second_plan_only_payload(
        self, mock_parse, mock_get, _mock_repin
    ):
        mock_parse.return_value = ([], None, {})
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = _tf_response("uuid-mix")
        mock_get.return_value = mock_resp

        from enge.rerun.__main__ import RerunJobs

        jobs = RerunJobs(ctx)
        jobs.processed_data["uuid-mix"] = (
            "/plan/with_test$|/plan/undefined_plan$",
            "RHEL-9.0-nightly",
            {"/plan/with_test": ["broken$"], "/plan/undefined_plan": []},
            ["/plan/undefined_plan$"],
            SOURCE_PATH_MIX,
        )
        payloads = jobs.build_rerun_payloads(["uuid-mix"])

        self.assertEqual(len(payloads), 2)

        main_payload, plan_only = payloads

        self.assertEqual(
            main_payload["test"]["fmf"]["name"],
            "/plan/with_test$|/plan/undefined_plan$",
        )
        self.assertIsNone(main_payload["test"]["fmf"]["plan_filter"])
        self.assertEqual(main_payload["test"]["fmf"]["test_name"], "broken$")
        self.assertEqual(main_payload["_original_uuid"], "uuid-mix")
        self.assertEqual(main_payload["_enge_source_path"], SOURCE_PATH_MIX)
        self.assertEqual(
            main_payload["environments"],
            [{"os": {"compose": "RHEL-9.0-nightly"}, "arch": "x86_64"}],
        )
        self.assertNotIn("environments_requested", main_payload)
        for key in REMOVED_PAYLOAD_KEYS:
            self.assertNotIn(key, main_payload)

        self.assertEqual(plan_only["test"]["fmf"]["name"], "/plan/undefined_plan$")
        self.assertIsNone(plan_only["test"]["fmf"]["plan_filter"])
        self.assertNotIn("test_name", plan_only["test"]["fmf"])
        self.assertEqual(plan_only["_original_uuid"], "uuid-mix")
        self.assertEqual(plan_only["_enge_source_path"], SOURCE_PATH_MIX)
        self.assertEqual(
            plan_only["environments"],
            [{"os": {"compose": "RHEL-9.0-nightly"}, "arch": "x86_64"}],
        )
        self.assertNotIn("environments_requested", plan_only)
        for key in REMOVED_PAYLOAD_KEYS:
            self.assertNotIn(key, plan_only)


if __name__ == "__main__":
    unittest.main()
