import unittest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

from enge.rerun.__main__ import (
    _unique_preserve,
    _extract_tags_from_filename,
    _resolve_archive_sources,
    _collect_inherited_tags,
    RerunJobs,
)


class TestRerunUtils(unittest.TestCase):
    def test_unique_preserve(self):
        self.assertEqual(_unique_preserve([]), [])
        self.assertEqual(_unique_preserve(["a", "b", "a"]), ["a", "b"])
        self.assertEqual(_unique_preserve(["a", "", "b"]), ["a", "b"])

    def test_extract_tags_from_filename(self):
        self.assertEqual(_extract_tags_from_filename(Path("file")), [])
        self.assertEqual(_extract_tags_from_filename(Path("file.tag1")), ["tag1"])
        self.assertEqual(
            _extract_tags_from_filename(Path("file.tag1.tag2")), ["tag1", "tag2"]
        )

    def test_resolve_archive_sources(self):
        cli_args = MagicMock()
        cli_args.file = ["/path/to/file1.txt"]
        cli_args.get_tag = None

        # Test file args only
        paths = _resolve_archive_sources(None, None, cli_args)
        self.assertEqual(paths, [Path("/path/to/file1.txt")])

        # Test get_tag with task_source
        cli_args.file = []
        cli_args.get_tag = "some_tag"
        paths = _resolve_archive_sources(["archive.txt"], "/tmp/archives", cli_args)
        self.assertEqual(paths, [Path("/tmp/archives/archive.txt")])

    @patch("enge.rerun.__main__.parsed_opts")
    def test_collect_inherited_tags(self, mock_opts):
        mock_opts.archive_tasks_default = "/tmp/archives"
        mock_opts.cli_args.file = ["/tmp/archives/job_archive.tag1.tag2"]
        mock_opts.cli_args.get_tag = None

        tags = _collect_inherited_tags(None)
        self.assertEqual(tags, ["tag1", "tag2", "rerun"])


class TestRerunJobs(unittest.TestCase):
    def setUp(self):
        # Patch parsed_opts
        self.opts_patcher = patch("enge.rerun.__main__.parsed_opts")
        self.mock_parsed_opts = self.opts_patcher.start()
        self.mock_parsed_opts.cli_args.error = False
        self.mock_parsed_opts.cli_args.fail = False
        self.mock_parsed_opts.testing_farm_endpoint.api_endpoint_url = "http://api"

        # Patch parse_tasks
        self.parse_tasks_patcher = patch("enge.rerun.__main__.parse_tasks")
        self.mock_parse_tasks = self.parse_tasks_patcher.start()
        self.mock_parse_tasks.return_value = (["http://api/req/123"], "source")

    def tearDown(self):
        self.opts_patcher.stop()
        self.parse_tasks_patcher.stop()

    @patch("enge.rerun.__main__.parse_request_xunit")
    def test_qualify_results_filter_failed(self, mock_parse_xunit):
        jobs = RerunJobs()

        # Mock parsed results with one failed suite
        mock_parse_xunit.return_value = {
            "123": {
                "source_compose": "Fedora",
                "testsuites": [
                    {
                        "testsuite_name": "suite1",
                        "testsuite_result": "FAILED",
                        "testsuite_arch": "x86_64",
                        "testcases": [
                            {
                                "testcase_name": "test::case1",
                                "testcase_result": "FAILED",
                            },
                            {
                                "testcase_name": "test::case2",
                                "testcase_result": "PASSED",
                            },
                        ],
                    }
                ],
            }
        }

        jobs.qualify_results()

        self.assertIn("123", jobs.processed_data)
        data = jobs.processed_data["123"]
        self.assertEqual(data[0], "suite1$")  # suite names
        self.assertEqual(data[1], "Fedora")  # compose
        self.assertEqual(data[2]["suite1"], ["case1$"])  # failed tests

    def test_drop_payload_keys(self):
        jobs = RerunJobs()
        jobs.rerun_payloads = [{"a": 1, "b": {"c": 2}, "d": [1, 2]}]

        jobs.drop_payload_keys(["a", "b.c"])

        self.assertNotIn("a", jobs.rerun_payloads[0])
        self.assertNotIn("c", jobs.rerun_payloads[0]["b"])
        self.assertIn("d", jobs.rerun_payloads[0])

    def test_drop_payload_keys_by_pattern(self):
        jobs = RerunJobs()
        jobs.rerun_payloads = [
            {"envs": {"PACKIT_1": "v1", "CI_VAR": "v2", "OTHER": "v3"}}
        ]

        jobs.drop_payload_keys_by_pattern("envs", "PACKIT_*")

        self.assertNotIn("PACKIT_1", jobs.rerun_payloads[0]["envs"])
        self.assertIn("CI_VAR", jobs.rerun_payloads[0]["envs"])
        self.assertIn("OTHER", jobs.rerun_payloads[0]["envs"])

    def test_overwrite_payload_values(self):
        jobs = RerunJobs()
        jobs.rerun_payloads = [{"a": {"b": 1}}]

        jobs.overwrite_payload_values({"a.b": 2, "c": 3})

        self.assertEqual(jobs.rerun_payloads[0]["a"]["b"], 2)
        self.assertEqual(jobs.rerun_payloads[0]["c"], 3)

    @patch("enge.rerun.__main__.http_get")
    def test_build_rerun_payloads(self, mock_get):
        jobs = RerunJobs()

        # Mock API response for task details
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "123",
            "state": "complete",
            "environments_requested": [{"os": {"compose": "Fedora"}}],
            "test": {"fmf": {"name": "old_plan"}},
        }
        mock_get.return_value = mock_response

        # Setup qualifying data from previous step
        jobs.processed_data = {"123": ("new_plan$", "Fedora", {"new_plan": ["test1$"]})}

        payloads = jobs.build_rerun_payloads(["123"])

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["test"]["fmf"]["name"], "new_plan$")
        self.assertEqual(payloads[0]["test"]["fmf"]["test_name"], "test1$")
        self.assertIn("environments", payloads[0])


if __name__ == "__main__":
    unittest.main()
