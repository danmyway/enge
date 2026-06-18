"""Tests for rerun module AppContext DI migration."""

import unittest
from unittest.mock import patch, MagicMock

from tests._helpers import make_app_context


PARSED_DICT_FAIL = {
    "uuid-1": {
        "source_compose": "RHEL-9.0-nightly",
        "testsuites": [
            {
                "testsuite_name": "/plan/tier0",
                "testsuite_result": "FAILED",
                "testsuite_arch": "x86_64",
                "testcases": [
                    {
                        "testcase_name": "test::case_one",
                        "testcase_result": "FAILED",
                    },
                ],
            },
            {
                "testsuite_name": "/plan/tier1",
                "testsuite_result": "PASSED",
                "testsuite_arch": "x86_64",
                "testcases": [],
            },
        ],
    },
}

PARSED_DICT_ERROR = {
    "uuid-2": {
        "source_compose": "RHEL-9.0-nightly",
        "testsuites": [
            {
                "testsuite_name": "/plan/tier0",
                "testsuite_result": "ERROR",
                "testsuite_arch": "x86_64",
                "testcases": [],
            },
        ],
    },
}

PARSED_DICT_MIXED = {
    "uuid-mix": {
        "source_compose": "RHEL-9.0-nightly",
        "testsuites": [
            {
                "testsuite_name": "/plan/fail_plan",
                "testsuite_result": "FAILED",
                "testsuite_arch": "x86_64",
                "testcases": [
                    {
                        "testcase_name": "test::broken",
                        "testcase_result": "FAILED",
                    },
                ],
            },
            {
                "testsuite_name": "/plan/error_plan",
                "testsuite_result": "ERROR",
                "testsuite_arch": "x86_64",
                "testcases": [],
            },
            {
                "testsuite_name": "/plan/pass_plan",
                "testsuite_result": "PASSED",
                "testsuite_arch": "x86_64",
                "testcases": [],
            },
        ],
    },
}


class TestRerunJobsInit(unittest.TestCase):
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_init_stores_ctx(self, mock_parse):
        mock_parse.return_value = ([], None, {})
        ctx = make_app_context(action="rerun")

        from enge.rerun.__main__ import RerunJobs

        rj = RerunJobs(ctx)

        self.assertIs(rj.ctx, ctx)

    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_init_passes_ctx_to_parse_tasks_with_map(self, mock_parse):
        mock_parse.return_value = ([], None, {})
        ctx = make_app_context(action="rerun")

        from enge.rerun.__main__ import RerunJobs

        RerunJobs(ctx)

        mock_parse.assert_called_once_with(ctx)


class TestQualifyResults(unittest.TestCase):
    def _make_jobs(self, mock_parse, mock_xunit, parsed_dict, extra_cli=None):
        req_urls = [f"https://tf.example.com/api/{uid}" for uid in parsed_dict]
        uuid_map = {uid: None for uid in parsed_dict}
        mock_parse.return_value = (req_urls, "latest", uuid_map)
        mock_xunit.return_value = parsed_dict
        cli = {"error": False, "fail": False, "dryrun": False}
        if extra_cli:
            cli.update(extra_cli)
        ctx = make_app_context(action="rerun", extra_cli=cli)

        from enge.rerun.__main__ import RerunJobs

        return RerunJobs(ctx)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_default_qualifies_both_failed_and_error(self, mock_parse, mock_xunit):
        jobs = self._make_jobs(mock_parse, mock_xunit, PARSED_DICT_MIXED)
        jobs.qualify_results()

        self.assertIn("uuid-mix", jobs.rerun_uuids)
        suite_names = jobs.processed_data["uuid-mix"][0]
        self.assertIn("fail_plan", suite_names)
        self.assertIn("error_plan", suite_names)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_error_flag_excludes_failed(self, mock_parse, mock_xunit):
        jobs = self._make_jobs(
            mock_parse, mock_xunit, PARSED_DICT_MIXED, {"error": True}
        )
        jobs.qualify_results()

        suite_names = jobs.processed_data["uuid-mix"][0]
        self.assertIn("error_plan", suite_names)
        self.assertNotIn("fail_plan", suite_names)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_fail_flag_excludes_error(self, mock_parse, mock_xunit):
        jobs = self._make_jobs(
            mock_parse, mock_xunit, PARSED_DICT_MIXED, {"fail": True}
        )
        jobs.qualify_results()

        suite_names = jobs.processed_data["uuid-mix"][0]
        self.assertIn("fail_plan", suite_names)
        self.assertNotIn("error_plan", suite_names)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_dryrun_returns_early(self, mock_parse, mock_xunit):
        jobs = self._make_jobs(
            mock_parse, mock_xunit, PARSED_DICT_FAIL, {"dryrun": True}
        )
        result = jobs.qualify_results()

        self.assertIsNone(result)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_passes_ctx_to_parse_request_xunit(self, mock_parse, mock_xunit):
        jobs = self._make_jobs(mock_parse, mock_xunit, PARSED_DICT_FAIL)
        jobs.qualify_results()

        _, kwargs = mock_xunit.call_args
        self.assertIs(kwargs["ctx"], jobs.ctx)

    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_missing_uuid_becomes_fallback_candidate(self, mock_parse, mock_xunit):
        req_urls = ["https://tf.example.com/api/uuid-missing"]
        mock_parse.return_value = (req_urls, "latest", {"uuid-missing": None})
        mock_xunit.return_value = {}
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        from enge.rerun.__main__ import RerunJobs

        jobs = RerunJobs(ctx)
        jobs.qualify_results()

        self.assertIn("uuid-missing", jobs.rerun_uuids)
        entry = jobs.processed_data["uuid-missing"]
        self.assertIsNone(entry[0])


class TestBuildRerunPayloads(unittest.TestCase):
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_uses_ctx_endpoint(self, mock_parse, mock_get, _mock_repin):
        mock_parse.return_value = ([], None, {})
        ctx = make_app_context(
            action="rerun",
            api_endpoint_url="https://custom-tf.example.com/api",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        tf_response = {
            "id": "uuid-1",
            "environments_requested": [{"os": {"compose": "RHEL-9"}, "arch": "x86_64"}],
            "test": {"fmf": {"name": "/plan/tier0"}},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = tf_response
        mock_get.return_value = mock_resp

        from enge.rerun.__main__ import RerunJobs

        jobs = RerunJobs(ctx)
        jobs.processed_data["uuid-1"] = ("/plan/tier0$", "RHEL-9", {}, [], None)
        jobs.build_rerun_payloads(["uuid-1"])

        call_url = mock_get.call_args[0][0]
        self.assertTrue(
            call_url.startswith("https://custom-tf.example.com/api"),
            f"Expected URL to use ctx endpoint, got: {call_url}",
        )

    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_uses_ctx_composes_prod_url(self, mock_parse, mock_get, mock_repin):
        mock_parse.return_value = ([], None, {})
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            extra_config={
                "testing_farm": {"composes_prod_url": "https://composes.custom.com"}
            },
        )

        tf_response = {
            "id": "uuid-1",
            "environments_requested": [{"os": {"compose": "RHEL-9"}, "arch": "x86_64"}],
            "test": {"fmf": {"name": "/plan/tier0"}},
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = tf_response
        mock_get.return_value = mock_resp

        from enge.rerun.__main__ import RerunJobs

        jobs = RerunJobs(ctx)
        jobs.processed_data["uuid-1"] = ("/plan/tier0$", "RHEL-9", {}, [], None)
        jobs.build_rerun_payloads(["uuid-1"])

        mock_repin.assert_called_with("RHEL-9", "https://composes.custom.com")

    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_multi_env_raises_validation_error(self, mock_parse, mock_get, _):
        mock_parse.return_value = ([], None, {})
        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        tf_response = {
            "id": "uuid-multi",
            "environments_requested": [
                {"os": {"compose": "RHEL-9"}, "arch": "x86_64"},
                {"os": {"compose": "RHEL-9"}, "arch": "aarch64"},
            ],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = tf_response
        mock_get.return_value = mock_resp

        from enge.rerun.__main__ import RerunJobs
        from enge.utils.errors import ValidationError

        jobs = RerunJobs(ctx)
        jobs.processed_data["uuid-multi"] = ("/plan/x$", "RHEL-9", {}, [], None)

        with self.assertRaises(ValidationError):
            jobs.build_rerun_payloads(["uuid-multi"])


class TestRerunMain(unittest.TestCase):
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.maybe_clear_latest_jobs_file")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_main_threads_ctx_to_rerun_jobs(
        self, mock_parse, mock_xunit, mock_clear, mock_submit_cls
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

        from enge.rerun.__main__ import main

        main(ctx)

        mock_parse.assert_called_once_with(ctx)

    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.maybe_clear_latest_jobs_file")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_main_uses_ctx_api_key(
        self, mock_parse, mock_xunit, mock_clear, mock_submit_cls
    ):
        mock_parse.return_value = ([], None, {})
        mock_xunit.return_value = {}
        mock_submit = MagicMock()
        mock_submit.set_tag = []
        mock_submit_cls.return_value = mock_submit
        ctx = make_app_context(
            action="rerun",
            api_key="rerun-test-key",
            extra_cli={"error": False, "fail": False, "dryrun": False},
        )

        from enge.rerun.__main__ import main

        main(ctx)

        self.assertEqual(mock_submit.api_key, "rerun-test-key")


class TestNoParsedOptsReference(unittest.TestCase):
    def test_no_parsed_opts_in_module_source(self):
        import enge.rerun.__main__ as rerun_mod

        with open(rerun_mod.__file__) as f:
            source = f.read()
        self.assertNotIn("parsed_opts", source)


if __name__ == "__main__":
    unittest.main()
