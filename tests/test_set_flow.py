import unittest
from unittest.mock import patch, MagicMock

from enge.dispatch.set_flow import (
    expand_set_requests,
    process_request_spec,
    RequestSpec,
)


class TestSetFlow(unittest.TestCase):
    def test_expand_set_requests_basic(self):
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
                "source_spec": {"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
                "target_spec": {"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            }
        ]

        with patch(
            "enge.dispatch.set_flow.generate_upgrade_path_alias",
            return_value="rhel-9.2-to-9.4",
        ), patch("enge.dispatch.set_flow.parse_architectures", return_value=["x86_64"]):
            specs = expand_set_requests(ctx=po)

        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertIsInstance(spec, RequestSpec)
        self.assertEqual(spec.set_name, "setA")
        self.assertEqual(spec.tier, "sanity")
        self.assertEqual(spec.plan, "/plans/p1")
        self.assertEqual(spec.arch, "x86_64")

    def test_process_request_spec_dry_run(self):
        # Minimal ctx needed by process_request_spec flow
        po = MagicMock()
        po.testing_farm = {"api_key": "token"}
        po.testing_farm_endpoint = MagicMock(
            log_artifact_baseurl="http://logs",
            api_endpoint_url="http://api",
        )
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
        po.archive_tasks_latest = "/tmp/enge_test_latest"
        po.archive_tasks_default = "/tmp/enge_test_archive/"
        po.pool = None
        po.architectures = []
        po.environment_variables = {}
        po.tmt_context = {}

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
            ):
                ok = process_request_spec(
                    idx=1,
                    total_expected_requests=1,
                    spec=spec,
                    shared_archive_filename="shared",
                    artifact_type="compose",
                    ctx=po,
                )

        self.assertTrue(ok)
        mock_send.assert_called_once()

    def test_process_request_spec_failure_returns_dict(self):
        """Failure paths must return a dict with status='failed', not None."""
        po = MagicMock()
        po.testing_farm = {"api_key": "token"}
        po.testing_farm_endpoint = MagicMock(
            log_artifact_baseurl="http://logs",
            api_endpoint_url="http://api",
        )
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
        po.cli_args.dryrun = False
        po.config = {}
        po.archive_tasks_latest = "/tmp/enge_test_latest"
        po.archive_tasks_default = "/tmp/enge_test_archive/"
        po.pool = None
        po.architectures = []
        po.environment_variables = {}
        po.tmt_context = {}

        spec = RequestSpec(
            set_name="fail-set",
            tier="bad-tier",
            plan="/plans/p1",
            arch="x86_64",
            source_spec={"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            upgrade_path="rhel-9.2-to-9.4",
            effective_values={},
        )

        with patch(
            "enge.dispatch.set_flow.generate_tier_plan_filter",
            side_effect=ValueError("bad tier"),
        ), patch(
            "enge.dispatch.set_flow.generate_environment_variables", return_value={}
        ), patch(
            "enge.dispatch.set_flow.parse_environment_variables", return_value={}
        ), patch(
            "enge.dispatch.set_flow.merge_set_environment_variables", return_value={}
        ):
            result = process_request_spec(
                idx=1,
                total_expected_requests=1,
                spec=spec,
                shared_archive_filename="shared",
                artifact_type="compose",
                ctx=po,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["set_name"], "fail-set")
        self.assertIn("error", result)

    def test_configure_submit_test_sets_skip_guest_setup_for_rhui(self):
        po = MagicMock()
        po.testing_farm = {"api_key": "token"}
        po.testing_farm_endpoint = MagicMock(
            log_artifact_baseurl="http://logs",
            api_endpoint_url="http://api",
        )
        po.tests = {
            "git_url": "https://git.example/repo",
            "git_ref": "main",
            "parallel_limit": 5,
            "tier": {},
        }
        po.project = {"repo_url": "https://git.example/repo"}
        po.cli_args = MagicMock()
        po.cli_args.git_url = None
        po.cli_args.git_ref = None
        po.cli_args.testfilter = None
        po.cli_args.test = None
        po.cli_args.auto_tag = False
        po.cli_args.set_tag = None
        po.archive_tasks_latest = "/tmp/enge_test_latest"
        po.archive_tasks_default = "/tmp/enge_test_archive/"
        po.pool = None
        po.architectures = []
        po.environment_variables = {}
        po.tmt_context = {}

        from enge.dispatch.set_flow import _configure_submit_test

        rhui_cases = [
            ("RHEL-8-rhui", True),
            ("RHEL-8-sap-hana-rhui", True),
            ("RHEL-8-sap-netweaver-rhui", True),
            ("RHEL-9.2.0", False),
            ("CentOS-Stream-9", False),
        ]
        for compose_name, expected in rhui_cases:
            with self.subTest(compose_name=compose_name):
                spec = RequestSpec(
                    set_name="test",
                    tier="sanity",
                    plan="/plans/p1",
                    arch="x86_64",
                    source_spec={
                        "major": 8,
                        "minor": 0,
                        "compose_name": compose_name,
                    },
                    target_spec={
                        "major": 9,
                        "minor": 0,
                        "compose_name": "RHEL-9.0.0",
                    },
                    upgrade_path="rhel-8-to-9",
                    effective_values={},
                )
                submit = _configure_submit_test(spec, po, "shared")
                self.assertEqual(
                    submit.skip_guest_setup,
                    expected,
                    f"{compose_name}: expected skip_guest_setup={expected}",
                )


if __name__ == "__main__":
    unittest.main()
