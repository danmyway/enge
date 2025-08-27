import unittest
from unittest.mock import patch, MagicMock

from enge.dispatch.set_flow import (
    expand_set_requests,
    process_request_spec,
    RequestSpec,
)


class TestSetFlow(unittest.TestCase):
    @patch("enge.dispatch.set_flow._get_parsed_opts")
    def test_expand_set_requests_basic(self, mock_parsed_opts):
        po = MagicMock()
        po.config = {"foo": "bar"}
        po.individual_test_sets = [
            {
                "name": "setA",
                "effective_values": {
                    "source": "9.2",
                    "target": "9.4",
                    "architectures": ["x86_64"],
                    "tiers": ["sanity"],
                    "plans": ["/plans/p1"],
                },
            }
        ]
        mock_parsed_opts.return_value = po

        with patch(
            "enge.dispatch.set_flow.parse_source_target_config",
            return_value=(
                {"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
                {"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            ),
        ), patch(
            "enge.dispatch.set_flow.generate_upgrade_path_alias",
            return_value="rhel-9.2-to-9.4",
        ), patch(
            "enge.dispatch.set_flow.parse_architectures", return_value=["x86_64"]
        ):
            specs = expand_set_requests()

        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertIsInstance(spec, RequestSpec)
        self.assertEqual(spec.set_name, "setA")
        self.assertEqual(spec.tier, "sanity")
        self.assertEqual(spec.plan, "/plans/p1")
        self.assertEqual(spec.arch, "x86_64")

    @patch("enge.dispatch.set_flow._get_parsed_opts")
    def test_process_request_spec_dry_run(self, mock_parsed_opts):
        # Minimal parsed_opts needed by process_request_spec flow
        po = MagicMock()
        po.testing_farm = {"api_key": "token"}
        po.tests = {
            "git_url": "https://git.example/repo",
            "git_ref": "main",
            "parallel_limit": 5,
            "tier": {},
        }
        po.project = {"repo_url": "https://git.example/repo", "name": "pkg"}
        po.cli_args = MagicMock()
        po.cli_args.testfilter = None
        po.cli_args.test = None
        po.cli_args.planfilter = None
        po.cli_args.environment = None
        po.cli_args.rp = False
        po.cli_args.dryrun = True
        po.config = {}
        mock_parsed_opts.return_value = po

        spec = RequestSpec(
            set_name="setA",
            tier="sanity",
            plan="/plans/p1",
            arch="x86_64",
            source_spec={"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            upgrade_path="rhel-9.2-to-9.4",
            effective_values={},
        )

        with patch(
            "enge.dispatch.set_flow.generate_tier_plan_filter",
            return_value="name: /plans/.*",
        ), patch(
            "enge.dispatch.set_flow.generate_environment_variables", return_value={}
        ), patch(
            "enge.dispatch.set_flow.parse_environment_variables", return_value={}
        ), patch(
            "enge.dispatch.set_flow.merge_set_environment_variables", return_value={}
        ), patch(
            "enge.dispatch.set_flow.SubmitTest.send_request"
        ) as mock_send, patch(
            "enge.dispatch.set_flow.SubmitTest.get_complete_tmt_context",
            return_value={},
        ), patch(
            "enge.dispatch.set_flow.SubmitTest.build_payload", return_value=({}, {})
        ):
            with patch(
                "enge.dispatch.set_flow.ArtifactResolver.resolve_builds",
                return_value=[
                    {"compose": "RHEL-9.2.0", "distro": "rhel-9", "build_id": None}
                ],
            ), patch(
                "enge.dispatch.tf_send_request.parsed_opts",
                new=MagicMock(
                    testing_farm_endpoint=MagicMock(
                        log_artifact_baseurl="http://logs",
                        api_endpoint_url="http://api",
                    )
                ),
            ):
                ok = process_request_spec(
                    idx=1,
                    total_expected_requests=1,
                    spec=spec,
                    shared_archive_filename="shared",
                    artifact_type="compose",
                )

        self.assertTrue(ok)
        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
