import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock parsed_opts BEFORE importing enge modules
with patch.dict(sys.modules, {"enge.utils.opt_manager": MagicMock()}):
    mock_opt_manager = sys.modules["enge.utils.opt_manager"]
    mock_opt_manager.parsed_opts = MagicMock()

    from enge.dispatch.set_flow import process_request_spec
    from enge.dispatch.plan_flow import RequestSpec


class TestSetFlow(unittest.TestCase):

    @patch("enge.dispatch.set_flow.SubmitTest")
    def test_process_request_spec_basic(self, mock_submit_cls):
        """Test processing a simple request specification"""
        # Mock SubmitTest instance
        mock_submit = MagicMock()
        mock_submit_cls.return_value = mock_submit

        # Patch parsed_opts inside set_flow
        with patch("enge.dispatch.set_flow.parsed_opts") as mock_opts:
            mock_opts.config = {"testing_farm": {}}
            mock_opts.cli_args.copr = None
            mock_opts.cli_args.brew = None
            mock_opts.cli_args.environment = []

            # Create a test spec
            spec = RequestSpec(
                plan="test-plan",
                arch="x86_64",
                tier="tier1",
                source="RHEL-9.0",
                target="RHEL-9.1",
            )

            # Run process_request_spec
            result = process_request_spec(
                idx=0,
                total_expected_requests=1,
                spec=spec,
                shared_archive_filename="test_archive",
                artifact_type="none",
                artifact_resolver=None,
            )

            # Verifications
            self.assertTrue(result)
            mock_submit.build_payload.assert_called_once()
            mock_submit.send_request.assert_called_once()

            # Check if source/target parsing happened correctly
            call_args = mock_submit.set_specific_data.call_args
            self.assertIsNotNone(call_args)

            # Check architectures
            self.assertEqual(call_args[0][0], ["x86_64"])

            # Check TMT context
            tmt_context = call_args[0][2]
            self.assertIn("distro", tmt_context)
            self.assertEqual(tmt_context["tier"], "tier1")

    @patch("enge.dispatch.set_flow.SubmitTest")
    def test_process_request_spec_with_artifacts(self, mock_submit_cls):
        """Test request processing with artifacts"""
        mock_submit = MagicMock()
        mock_submit_cls.return_value = mock_submit

        with patch("enge.dispatch.set_flow.parsed_opts") as mock_opts:
            mock_opts.config = {"testing_farm": {}}
            mock_opts.cli_args.copr = None
            mock_opts.cli_args.brew = None
            mock_opts.cli_args.environment = []

            spec = RequestSpec(
                plan="test-plan",
                arch="x86_64",
                tier="tier1",
                source="RHEL-9.0",
                target="RHEL-9.1",
            )

            # Mock artifact resolver
            mock_resolver = MagicMock()
            mock_resolver.resolve_artifacts.return_value = [
                {"id": "123", "type": "copr", "packages": ["pkg"]}
            ]

            process_request_spec(
                idx=0,
                total_expected_requests=1,
                spec=spec,
                shared_archive_filename="test_archive",
                artifact_type="fedora-copr-build",
                artifact_resolver=mock_resolver,
            )

            # Check if artifact was added
            mock_submit.add_artifact.assert_called_once_with(
                "123", "copr", ["pkg"], nvr=None
            )


if __name__ == "__main__":
    unittest.main()
