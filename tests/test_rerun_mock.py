import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock parsed_opts BEFORE importing enge modules
with patch.dict(sys.modules, {"enge.utils.opt_manager": MagicMock()}):
    mock_opt_manager = sys.modules["enge.utils.opt_manager"]
    mock_opt_manager.parsed_opts = MagicMock()

    # Need to import SubmitTest here as well if used
    from enge.dispatch.tf_send_request import SubmitTest
    from enge.rerun.__main__ import _get_request_json


class TestRerun(unittest.TestCase):

    @patch("enge.rerun.__main__.http_get")
    # We patch parsed_opts here just in case it's accessed again or different instance
    @patch("enge.rerun.__main__.parsed_opts")
    def test_get_request_json_success(self, mock_opts, mock_http_get):
        """Test fetching historical request JSON"""
        # Setup mock values on the patched object
        mock_opts.testing_farm_endpoint.api_endpoint_url = "http://api"

        # Mock API response for /requests/{id}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "task-old",
            "environments": [{"os": {"compose": "RHEL-8.8"}}],
            "test": {"fmf": {"url": "git://test", "ref": "master"}},
        }
        mock_http_get.return_value = mock_response

        request_json = _get_request_json("task-old")

        self.assertEqual(request_json["id"], "task-old")
        self.assertEqual(request_json["environments"][0]["os"]["compose"], "RHEL-8.8")

    @patch("enge.rerun.__main__.parsed_opts")
    def test_rerun_population(self, mock_opts):
        """Test populating SubmitTest from historical data"""
        mock_opts.testing_farm_endpoint.log_artifact_baseurl = "http://logs"
        mock_opts.testing_farm_endpoint.api_endpoint_url = "http://api"

        # We need to instantiate SubmitTest which uses parsed_opts
        # Since we mocked opt_manager globally, SubmitTest should use the mock
        # But we need to ensure the mock has necessary attributes

        # Access the global mock we set up in the top level patch
        global_mock = sys.modules["enge.utils.opt_manager"].parsed_opts
        global_mock.testing_farm_endpoint.log_artifact_baseurl = "http://logs"
        global_mock.testing_farm_endpoint.api_endpoint_url = "http://api"
        global_mock.cli_args.auto_tag = False
        global_mock.cli_args.set_tag = None

        submit = SubmitTest()

        # Simulate extracted data from a previous run
        request_data = {
            "tests_git_url": "https://github.com/old/tests",
            "tests_git_ref": "feature-branch",
            "plan": "plan-A",
            "compose": "RHEL-9.0",
            "architectures": ["aarch64"],
            "environment_variables": {"KEY": "VALUE"},
            "tmt_context": {"distro": "rhel-9"},
        }

        submit.populate_from_request_data(request_data)

        self.assertEqual(submit.tests_git_url, "https://github.com/old/tests")
        self.assertEqual(submit.tests_git_ref, "feature-branch")
        self.assertEqual(submit.compose, "RHEL-9.0")

        # Verify set_specific_data was used (implied by checking values)
        self.assertEqual(submit.set_architectures, ["aarch64"])
        self.assertEqual(submit.set_environment_variables, {"KEY": "VALUE"})
        self.assertEqual(submit.set_tmt_context, {"distro": "rhel-9"})


if __name__ == "__main__":
    unittest.main()
