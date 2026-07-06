"""Test that _create_rerun_launch_for_payload passes ctx to ReportPortalLaunch."""

import unittest
from unittest.mock import patch, MagicMock

from tests._helpers import make_app_context


def _rp_payload(event="preliminary", tier="tier0", arch="x86_64"):
    return {
        "environments": [
            {
                "arch": arch,
                "tmt": {"context": {"event": event, "tier": tier}},
            }
        ],
    }


class TestRerunRPLaunchCtx(unittest.TestCase):
    """ReportPortalLaunch must receive ctx — bare construction crashes."""

    def _make_ctx(self):
        return make_app_context(
            action="rerun",
            extra_config={
                "reportportal": {
                    "url": "https://rp.example.com",
                    "token": "fake-token",
                    "project": "test-project",
                },
            },
        )

    @patch("enge.reportportal.__main__.ReportPortalLaunch")
    def test_real_path_passes_ctx(self, mock_rp_cls):
        from enge.rerun.__main__ import _create_rerun_launch_for_payload

        mock_instance = MagicMock()
        mock_instance.create_launch.return_value = "fake-uuid"
        mock_rp_cls.return_value = mock_instance

        ctx = self._make_ctx()
        result = _create_rerun_launch_for_payload(_rp_payload(), False, ctx)

        mock_rp_cls.assert_called_once_with(ctx)
        self.assertEqual(result, "fake-uuid")

    @patch("enge.reportportal.__main__.ReportPortalLaunch")
    def test_dryrun_path_passes_ctx(self, mock_rp_cls):
        from enge.rerun.__main__ import _create_rerun_launch_for_payload

        mock_instance = MagicMock()
        mock_instance.generate_launch_payload.return_value = {"name": "test"}
        mock_rp_cls.return_value = mock_instance

        ctx = self._make_ctx()
        result = _create_rerun_launch_for_payload(_rp_payload(), True, ctx)

        mock_rp_cls.assert_called_once_with(ctx)
        self.assertEqual(result, "dryrun_placeholder")
