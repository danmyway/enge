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


def _mixed_candidates():
    """PARSED_DICT_MIXED's default-flag qualifying set, written out by hand.

    '/plan/error_plan' is deliberately absent from tests_by_plan: the CLI path
    still records an empty test list for a kept suite that had no failed
    testcases, and the candidate path has to reproduce that.
    """
    from enge.rerun.__main__ import RerunCandidate

    return [
        RerunCandidate(
            task_id="uuid-mix",
            plans=("/plan/fail_plan", "/plan/error_plan"),
            tests_by_plan={"/plan/fail_plan": ("broken",)},
            undefined_plans=(),
            source_compose="RHEL-9.0-nightly",
            source_path=SOURCE_PATH_MIX,
        )
    ]


def _candidate_jobs(candidates, extra_cli=None, task_source=None):
    """Build a candidate-path RerunJobs; parse_tasks_with_map must stay unused."""
    cli = {"error": False, "fail": False, "dryrun": False}
    if extra_cli:
        cli.update(extra_cli)
    ctx = make_app_context(action="rerun", extra_cli=cli)

    from enge.rerun.__main__ import RerunJobs

    return RerunJobs(ctx, candidates=candidates, task_source=task_source)


class TestCandidatePathMatchesCliPath(unittest.TestCase):
    """T7/T8: the two paths agree on processed_data, rerun_uuids and payloads."""

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_qualify_candidates_matches_qualify_results(self, mock_parse, mock_xunit):
        cli_jobs = _make_cli_jobs(
            mock_parse,
            mock_xunit,
            PARSED_DICT_MIXED,
            uuid_map={"uuid-mix": SOURCE_PATH_MIX},
        )
        cli_jobs.qualify_results()

        cand_jobs = _candidate_jobs(_mixed_candidates())
        cand_jobs.qualify_candidates()

        self.assertEqual(cand_jobs.processed_data, cli_jobs.processed_data)
        self.assertEqual(cand_jobs.rerun_uuids, cli_jobs.rerun_uuids)

    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_both_paths_build_identical_payloads(
        self, mock_parse, mock_xunit, mock_get, _mock_repin
    ):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = lambda: _tf_response("uuid-mix")
        mock_get.return_value = mock_resp

        cli_jobs = _make_cli_jobs(
            mock_parse,
            mock_xunit,
            PARSED_DICT_MIXED,
            uuid_map={"uuid-mix": SOURCE_PATH_MIX},
        )
        cli_jobs.qualify_results()
        cli_payloads = cli_jobs.build_rerun_payloads(cli_jobs.rerun_uuids)

        cand_jobs = _candidate_jobs(_mixed_candidates())
        cand_jobs.qualify_candidates()
        cand_payloads = cand_jobs.build_rerun_payloads(cand_jobs.rerun_uuids)

        self.assertEqual(cand_payloads, cli_payloads)


class TestCandidateConstruction(unittest.TestCase):
    """T9/T13: what the candidate constructor sets, and how the paths refuse to mix."""

    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_candidates_bypass_parse_tasks_with_map(self, mock_parse):
        from enge.rerun.__main__ import RerunCandidate

        candidates = [
            RerunCandidate(task_id="uuid-a", source_path="/archive/a.json"),
            RerunCandidate(task_id="uuid-b", source_path=None),
        ]
        jobs = _candidate_jobs(candidates, task_source="supervised-loop")

        mock_parse.assert_not_called()
        self.assertEqual(
            jobs.req_url_list,
            [
                "https://tf.example.com/api/uuid-a",
                "https://tf.example.com/api/uuid-b",
            ],
        )
        self.assertEqual(jobs.task_source, "supervised-loop")
        self.assertEqual(
            jobs.uuid_source_map,
            {"uuid-a": "/archive/a.json", "uuid-b": None},
        )

    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_paths_are_not_mixable(self, mock_parse):
        mock_parse.return_value = ([], None, {})

        from enge.rerun.__main__ import RerunJobs

        cand_jobs = _candidate_jobs(_mixed_candidates())
        with self.assertRaises(RuntimeError):
            cand_jobs.qualify_results()

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )
        cli_jobs = RerunJobs(ctx)
        with self.assertRaises(RuntimeError):
            cli_jobs.qualify_candidates()

        with self.assertRaises(ValueError):
            RerunJobs(ctx, task_source="supervised-loop")


class TestCandidatePathIgnoresCliDerivedBehavior(unittest.TestCase):
    """T10/T11/T12: no flag filter, no fallback sweep, explicit fallback shape."""

    def test_fail_and_error_flags_do_not_change_candidate_registration(self):
        baseline = _candidate_jobs(_mixed_candidates())
        baseline.qualify_candidates()

        for flag in ("fail", "error"):
            with self.subTest(flag=flag):
                jobs = _candidate_jobs(_mixed_candidates(), extra_cli={flag: True})
                jobs.qualify_candidates()
                self.assertEqual(jobs.processed_data, baseline.processed_data)
                self.assertEqual(jobs.rerun_uuids, baseline.rerun_uuids)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_omitted_uuid_is_never_swept_in(self, mock_parse, mock_xunit):
        # The CLI path over the same inputs sweeps uuid-gone into the rerun set.
        req_urls = [
            "https://tf.example.com/api/uuid-mix",
            "https://tf.example.com/api/uuid-gone",
        ]
        mock_parse.return_value = (
            req_urls,
            "latest",
            {"uuid-mix": SOURCE_PATH_MIX, "uuid-gone": "/archive/gone.json"},
        )
        mock_xunit.return_value = PARSED_DICT_MIXED
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        from enge.rerun.__main__ import RerunJobs

        cli_jobs = RerunJobs(ctx)
        cli_jobs.qualify_results()
        self.assertIn("uuid-gone", cli_jobs.rerun_uuids)

        # The candidate path, given only uuid-mix, must not invent uuid-gone.
        cand_jobs = _candidate_jobs(_mixed_candidates())
        cand_jobs.qualify_candidates()

        self.assertEqual(cand_jobs.rerun_uuids, ["uuid-mix"])
        self.assertNotIn("uuid-gone", cand_jobs.processed_data)

    def test_empty_plans_registers_the_fallback_tuple(self):
        from enge.rerun.__main__ import RerunCandidate

        jobs = _candidate_jobs(
            [
                RerunCandidate(
                    task_id="uuid-fallback",
                    plans=(),
                    source_path="/archive/fallback.json",
                )
            ]
        )
        jobs.qualify_candidates()

        self.assertEqual(jobs.rerun_uuids, ["uuid-fallback"])
        self.assertEqual(
            jobs.processed_data["uuid-fallback"],
            (None, None, None, None, "/archive/fallback.json"),
        )


class TestMainStillUsesCliPath(unittest.TestCase):
    """T14: main() is unchanged -- it qualifies via the CLI path only."""

    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_main_calls_qualify_results_not_qualify_candidates(
        self, mock_parse, mock_xunit, mock_submit_cls
    ):
        mock_parse.return_value = ([], None, {})
        mock_xunit.return_value = {}
        mock_submit = MagicMock()
        mock_submit.set_tag = []
        mock_submit_cls.return_value = mock_submit
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        from enge.rerun.__main__ import RerunJobs, main

        with (
            patch.object(RerunJobs, "qualify_results") as mock_results,
            patch.object(RerunJobs, "qualify_candidates") as mock_candidates,
        ):
            main(ctx)

        self.assertEqual(mock_results.call_count, 1)
        self.assertEqual(mock_candidates.call_count, 0)


if __name__ == "__main__":
    unittest.main()
