import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

from enge.reportportal.__main__ import ReportPortalLaunch
from enge.utils.errors import ConfigurationError


class TestReportPortalLaunch(unittest.TestCase):
    def setUp(self):
        # Patch parsed_opts
        self.opts_patcher = patch("enge.reportportal.__main__.parsed_opts")
        self.mock_parsed_opts = self.opts_patcher.start()

        # Mock basic config
        self.mock_parsed_opts.config = {
            "reportportal": {
                "url": "http://rp.example.com",
                "token": "test_token",
                "project": "test_project",
            }
        }

    def tearDown(self):
        self.opts_patcher.stop()

    def test_init_missing_config(self):
        self.mock_parsed_opts.config = {"reportportal": {}}
        with self.assertRaises(ConfigurationError):
            ReportPortalLaunch()

    def test_generate_launch_name(self):
        rp = ReportPortalLaunch()

        context = {"set_name": "setA", "tier": "tier1", "architecture": "x86_64"}
        name = rp.generate_launch_name(context)
        timestamp = datetime.now().strftime("%Y-%m-%d")

        self.assertEqual(name, f"SETA~{timestamp}~tier1~x86_64")

    def test_generate_launch_payload(self):
        rp = ReportPortalLaunch()
        context = {"architecture": "x86_64"}
        tmt_context = {"distro": "fedora", "arch": "x86_64"}

        payload = rp.generate_launch_payload(
            name="TestLaunch",
            description="Desc",
            context=context,
            tmt_context=tmt_context,
        )

        self.assertEqual(payload["name"], "TestLaunch")
        self.assertEqual(payload["description"], "Desc")

        # Check attributes
        attrs = payload["attributes"]
        distro_attr = next(a for a in attrs if a["key"] == "distro")
        self.assertEqual(distro_attr["value"], "fedora")

    @patch("enge.reportportal.__main__.http_post")
    def test_create_launch_success(self, mock_post):
        rp = ReportPortalLaunch()

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "uuid-123"}
        mock_post.return_value = mock_response

        uuid = rp.create_launch(name="TestLaunch")
        self.assertEqual(uuid, "uuid-123")
        mock_post.assert_called_once()

    @patch("enge.reportportal.__main__.http_get")
    def test_find_launch_by_uniq_id(self, mock_get):
        rp = ReportPortalLaunch()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "uuid": "abcdef123456-rest-of-uuid",
                    "name": "Matching Launch",
                    "status": "IN_PROGRESS",
                }
            ]
        }
        mock_get.return_value = mock_response

        found_uuid = rp.find_launch_by_uniq_id("abcdef123456")
        self.assertEqual(found_uuid, "abcdef123456-rest-of-uuid")

    @patch("enge.reportportal.__main__.http_put")
    def test_finish_launch_success(self, mock_put):
        rp = ReportPortalLaunch()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_put.return_value = mock_response

        success = rp.finish_launch(
            launch_uuid="uuid-123", end_time="2024-01-01T12:00:00.000Z", status="PASSED"
        )
        self.assertTrue(success)
        mock_put.assert_called_once()


if __name__ == "__main__":
    unittest.main()
