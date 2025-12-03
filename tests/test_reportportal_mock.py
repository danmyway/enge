import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock parsed_opts BEFORE importing enge modules
with patch.dict(sys.modules, {"enge.utils.opt_manager": MagicMock()}):
    mock_opt_manager = sys.modules["enge.utils.opt_manager"]
    mock_opt_manager.parsed_opts = MagicMock()

    from enge.reportportal.__main__ import ReportPortalLaunch


class TestReportPortalLaunch(unittest.TestCase):

    @patch("enge.reportportal.__main__.ReportPortalService")
    def test_start_launch(self, mock_rp_service_cls):
        """Test starting a new launch"""
        # Setup RP service mock
        mock_service = MagicMock()
        mock_rp_service_cls.return_value = mock_service
        # Mock launch ID return
        mock_service.start_launch.return_value = "launch-uuid-123"

        # Init launch helper
        rp = ReportPortalLaunch(
            endpoint="http://rp.example.com", project="test-project", token="fake-token"
        )

        # Start launch
        launch_id = rp.start_launch(
            name="Test Launch",
            description="Unit test launch",
            attributes={"key": "value"},
        )

        # Verify
        self.assertEqual(launch_id, "launch-uuid-123")
        mock_service.start_launch.assert_called_once()
        call_kwargs = mock_service.start_launch.call_args.kwargs
        self.assertEqual(call_kwargs["name"], "Test Launch")
        self.assertEqual(call_kwargs["description"], "Unit test launch")

        # Attributes format check (RP client expects list of dicts)
        expected_attrs = [{"key": "key", "value": "value"}]
        self.assertEqual(call_kwargs["attributes"], expected_attrs)

    @patch("enge.reportportal.__main__.ReportPortalService")
    def test_finish_launch(self, mock_rp_service_cls):
        """Test finishing a launch"""
        mock_service = MagicMock()
        mock_rp_service_cls.return_value = mock_service

        rp = ReportPortalLaunch(endpoint="", project="", token="")
        rp.launch_id = "launch-uuid-123"

        rp.finish_launch()

        mock_service.finish_launch.assert_called_once_with(launch_id="launch-uuid-123")

    @patch("enge.reportportal.__main__.ReportPortalService")
    def test_process_results(self, mock_rp_service_cls):
        """Test processing results and sending items to RP"""
        mock_service = MagicMock()
        mock_rp_service_cls.return_value = mock_service
        mock_service.start_test_item.return_value = "item-id-1"

        rp = ReportPortalLaunch(endpoint="", project="", token="")
        rp.launch_id = "launch-uuid-123"

        # Mock TaskResult
        mock_task = MagicMock()
        mock_task.task_id = "task-1"
        mock_task.overall_result = "passed"
        mock_task.log_url = "http://logs/task-1"

        # Mock parsing of TMT context (usually done via log parsing)
        with patch.object(
            rp, "_extract_tmt_context_from_task", return_value={"distro": "rhel-9"}
        ):
            rp.process_results([mock_task])

        # Check calls
        # 1. Start item
        mock_service.start_test_item.assert_called_once()
        start_args = mock_service.start_test_item.call_args.kwargs
        self.assertEqual(start_args["name"], "task-1")
        self.assertEqual(start_args["item_type"], "TEST")
        self.assertEqual(start_args["launch_id"], "launch-uuid-123")

        # 2. Log URL
        mock_service.log.assert_called()

        # 3. Finish item
        mock_service.finish_test_item.assert_called_once()
        finish_args = mock_service.finish_test_item.call_args.kwargs
        self.assertEqual(finish_args["item_id"], "item-id-1")
        self.assertEqual(finish_args["status"], "PASSED")


if __name__ == "__main__":
    unittest.main()
