import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock parsed_opts BEFORE importing enge modules
with patch.dict(sys.modules, {"enge.utils.opt_manager": MagicMock()}):
    mock_opt_manager = sys.modules["enge.utils.opt_manager"]
    mock_opt_manager.parsed_opts = MagicMock()
    # Setup default mock values for parsed_opts
    mock_opt_manager.parsed_opts.testing_farm_endpoint.log_artifact_baseurl = (
        "http://logs"
    )
    mock_opt_manager.parsed_opts.cli_args.action = "report"
    mock_opt_manager.parsed_opts.cli_args.download = False

    from enge.report.concurrent_parser import ConcurrentRequestParser, TaskResult


class TestConcurrentParser(unittest.TestCase):

    def setUp(self):
        self.parser = ConcurrentRequestParser()
        # Manually set session since we might mock __enter__
        self.parser.session = MagicMock()

    @patch("enge.report.concurrent_parser.requests.Session")
    def test_fetch_task_info_success(self, mock_session_cls):
        """Test fetching task info successfully"""
        # Mock session and response
        mock_session = self.parser.session
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "task-123",
            "state": "complete",
            "created": "2023-01-01T12:00:00",
            "environments_requested": [
                {
                    "os": {"compose": "RHEL-9.0"},
                    "arch": "x86_64",
                    "variables": {"SOURCE_RELEASE": "9.0", "TARGET_RELEASE": "9.1"},
                }
            ],
            "test": {"fmf": {"name": "plan-A", "plan_filter": "filter"}},
            "result": {
                "summary": "2 passed",
                "overall": "passed",
                "xunit_url": "http://logs/task-123/results.xml",
            },
        }
        mock_session.get.return_value = mock_response

        result = self.parser._fetch_task_info("http://api/tasks/task-123")

        self.assertIsNotNone(result)
        self.assertEqual(result.request_uuid, "task-123")
        self.assertEqual(result.request_state, "COMPLETE")
        self.assertEqual(result.request_result_overall, "passed")
        self.assertEqual(result.results_xml_url, "http://logs/task-123/results.xml")

    def test_fetch_task_info_failure(self):
        """Test handling of request failure"""
        mock_session = self.parser.session
        # Simulate 404
        mock_response = MagicMock()
        mock_response.status_code = 404

        # Configure raise_for_status to raise exception with response
        error = Exception("404 Not Found")
        error.response = mock_response
        mock_session.get.side_effect = error

        result = self.parser._fetch_task_info("http://api/tasks/missing")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
