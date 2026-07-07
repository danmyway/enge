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
                submit = _configure_submit_test(spec, po)
                self.assertEqual(
                    submit.skip_guest_setup,
                    expected,
                    f"{compose_name}: expected skip_guest_setup={expected}",
                )


class TestPerSetArtifactReferences(unittest.TestCase):
    """Regression: multi-set dispatch must resolve each set's own artifact refs."""

    def test_resolve_artifacts_uses_per_set_brew_references(self):
        """Two sets with different brew_api.build_references must each see their own."""
        from enge.dispatch.set_flow import _resolve_artifacts

        # --- ctx simulates run-level state collapsed from set 0 (alpha) ---
        ctx = MagicMock()
        ctx.cli_args = MagicMock()
        ctx.cli_args.copr = None
        ctx.cli_args.brew = None
        ctx.copr_reference = None
        ctx.copr_references = []
        ctx.copr_api = {}
        ctx.brew_reference = "pkg-alpha-1.0"
        ctx.brew_references = ["pkg-alpha-1.0"]
        ctx.brew_api = {"package": "leapp", "build_references": ["pkg-alpha-1.0"]}
        ctx.project = {"name": "leapp"}

        source_spec = {"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"}
        target_spec = {"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"}

        spec_alpha = RequestSpec(
            set_name="alpha",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec=source_spec,
            target_spec=target_spec,
            upgrade_path="rhel-8.10-to-9.4",
            effective_values={
                "brew_api": {"package": "leapp", "build_references": ["pkg-alpha-1.0"]},
                "copr_api": {},
            },
        )
        spec_beta = RequestSpec(
            set_name="beta",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec=source_spec,
            target_spec=target_spec,
            upgrade_path="rhel-8.10-to-9.4",
            effective_values={
                "brew_api": {"package": "leapp", "build_references": ["pkg-beta-2.0"]},
                "copr_api": {},
            },
        )

        captured_ctxs = []

        class SpyResolver:
            def resolve_builds(self, compose_name, ctx):
                captured_ctxs.append(ctx)
                return [
                    {"compose": compose_name, "distro": "rhel-8.10", "build_id": None}
                ]

        tmt_context = {"distro": "rhel-8.10"}
        submit_alpha = MagicMock()
        submit_alpha.artifacts = []
        submit_beta = MagicMock()
        submit_beta.artifacts = []
        resolver = SpyResolver()

        _resolve_artifacts(
            spec_alpha, submit_alpha, tmt_context, ctx, "compose", resolver
        )
        _resolve_artifacts(
            spec_beta, submit_beta, tmt_context, ctx, "compose", resolver
        )

        self.assertEqual(len(captured_ctxs), 2)
        alpha_ctx = captured_ctxs[0]
        beta_ctx = captured_ctxs[1]

        self.assertEqual(
            alpha_ctx.brew_references,
            ["pkg-alpha-1.0"],
            "alpha spec should see alpha's brew references",
        )
        self.assertEqual(
            beta_ctx.brew_references,
            ["pkg-beta-2.0"],
            "beta spec should see beta's brew references, not alpha's",
        )
        self.assertEqual(
            beta_ctx.brew_reference,
            "pkg-beta-2.0",
            "beta spec should see beta's singular brew reference",
        )

    def test_resolve_artifacts_uses_per_set_copr_references(self):
        """Same test for copr_api.build_references."""
        from enge.dispatch.set_flow import _resolve_artifacts

        ctx = MagicMock()
        ctx.cli_args = MagicMock()
        ctx.cli_args.copr = None
        ctx.cli_args.brew = None
        ctx.copr_reference = "pkg-alpha-copr"
        ctx.copr_references = ["pkg-alpha-copr"]
        ctx.copr_api = {
            "package": "leapp",
            "repository": "oamg/leapp",
            "build_references": ["pkg-alpha-copr"],
        }
        ctx.brew_reference = None
        ctx.brew_references = []
        ctx.brew_api = {}
        ctx.project = {"name": "leapp"}

        source_spec = {"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"}
        target_spec = {"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"}

        spec_beta = RequestSpec(
            set_name="beta",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec=source_spec,
            target_spec=target_spec,
            upgrade_path="rhel-8.10-to-9.4",
            effective_values={
                "copr_api": {
                    "package": "leapp",
                    "repository": "oamg/leapp",
                    "build_references": ["pkg-beta-copr"],
                },
                "brew_api": {},
            },
        )

        captured_ctxs = []

        class SpyResolver:
            def resolve_builds(self, compose_name, ctx):
                captured_ctxs.append(ctx)
                return [
                    {"compose": compose_name, "distro": "rhel-8.10", "build_id": None}
                ]

        tmt_context = {"distro": "rhel-8.10"}
        submit = MagicMock()
        submit.artifacts = []
        resolver = SpyResolver()

        _resolve_artifacts(spec_beta, submit, tmt_context, ctx, "compose", resolver)

        self.assertEqual(len(captured_ctxs), 1)
        beta_ctx = captured_ctxs[0]
        self.assertEqual(
            beta_ctx.copr_references,
            ["pkg-beta-copr"],
            "beta spec should see beta's copr references, not alpha's",
        )
        self.assertEqual(
            beta_ctx.copr_reference,
            "pkg-beta-copr",
            "beta spec should see beta's singular copr reference",
        )

    def test_cli_artifacts_override_per_set_references(self):
        """CLI --brew/--copr always wins over set-level build_references."""
        from enge.dispatch.set_flow import _resolve_artifacts

        ctx = MagicMock()
        ctx.cli_args = MagicMock()
        ctx.cli_args.copr = None
        ctx.cli_args.brew = "cli-brew-override"
        ctx.copr_reference = None
        ctx.copr_references = []
        ctx.copr_api = {}
        ctx.brew_reference = "cli-brew-override"
        ctx.brew_references = ["cli-brew-override"]
        ctx.brew_api = {"package": "leapp"}
        ctx.project = {"name": "leapp"}

        source_spec = {"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"}
        target_spec = {"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"}

        spec = RequestSpec(
            set_name="beta",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec=source_spec,
            target_spec=target_spec,
            upgrade_path="rhel-8.10-to-9.4",
            effective_values={
                "brew_api": {"package": "leapp", "build_references": ["pkg-beta-2.0"]},
                "copr_api": {},
            },
        )

        captured_ctxs = []

        class SpyResolver:
            def resolve_builds(self, compose_name, ctx):
                captured_ctxs.append(ctx)
                return [
                    {"compose": compose_name, "distro": "rhel-8.10", "build_id": None}
                ]

        tmt_context = {"distro": "rhel-8.10"}
        submit = MagicMock()
        submit.artifacts = []
        resolver = SpyResolver()

        _resolve_artifacts(spec, submit, tmt_context, ctx, "compose", resolver)

        self.assertEqual(len(captured_ctxs), 1)
        beta_ctx = captured_ctxs[0]
        self.assertEqual(
            beta_ctx.brew_references,
            ["cli-brew-override"],
            "CLI --brew must override set-level build_references",
        )

    def test_cli_brew_does_not_suppress_per_set_copr_references(self):
        """CLI --brew must win the brew family only; copr must still resolve per-set."""
        from enge.dispatch.set_flow import _resolve_artifacts

        ctx = MagicMock()
        ctx.cli_args = MagicMock()
        ctx.cli_args.copr = None
        ctx.cli_args.brew = "cli-brew-override"
        ctx.copr_reference = "pkg-alpha-copr"
        ctx.copr_references = ["pkg-alpha-copr"]
        ctx.copr_api = {"package": "leapp", "repository": "oamg/leapp"}
        ctx.brew_reference = "cli-brew-override"
        ctx.brew_references = ["cli-brew-override"]
        ctx.brew_api = {"package": "leapp"}
        ctx.project = {"name": "leapp"}

        source_spec = {"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"}
        target_spec = {"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"}

        spec = RequestSpec(
            set_name="beta",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec=source_spec,
            target_spec=target_spec,
            upgrade_path="rhel-8.10-to-9.4",
            effective_values={
                "copr_api": {
                    "package": "leapp",
                    "repository": "oamg/leapp",
                    "build_references": ["pkg-beta-copr"],
                },
                "brew_api": {},
            },
        )

        captured_ctxs = []

        class SpyResolver:
            def resolve_builds(self, compose_name, ctx):
                captured_ctxs.append(ctx)
                return [
                    {"compose": compose_name, "distro": "rhel-8.10", "build_id": None}
                ]

        tmt_context = {"distro": "rhel-8.10"}
        submit = MagicMock()
        submit.artifacts = []
        resolver = SpyResolver()

        _resolve_artifacts(spec, submit, tmt_context, ctx, "compose", resolver)

        self.assertEqual(len(captured_ctxs), 1)
        beta_ctx = captured_ctxs[0]
        self.assertEqual(
            beta_ctx.brew_references,
            ["cli-brew-override"],
            "CLI --brew must win the brew family",
        )
        self.assertEqual(
            beta_ctx.copr_references,
            ["pkg-beta-copr"],
            "copr must resolve from set-level, not be suppressed by CLI --brew",
        )
        self.assertEqual(
            beta_ctx.copr_api,
            {
                "package": "leapp",
                "repository": "oamg/leapp",
                "build_references": ["pkg-beta-copr"],
            },
            "set-level copr_api keys must merge over run-level dict",
        )


class TestArtifactApiDictMerge(unittest.TestCase):
    """Per-key merge semantics for set-level api dicts over run-level base."""

    RUN_BREW_API = {
        "session_url": "https://brew.example/hub",
        "taskid_url": "https://brew.example/task",
        "build_references": ["run-level-ref"],
    }

    def _make_ctx(self, **overrides):
        ctx = MagicMock()
        ctx.cli_args = MagicMock()
        ctx.cli_args.copr = None
        ctx.cli_args.brew = None
        ctx.copr_reference = None
        ctx.copr_references = []
        ctx.copr_api = {}
        ctx.brew_reference = "run-level-ref"
        ctx.brew_references = ["run-level-ref"]
        ctx.brew_api = dict(self.RUN_BREW_API)
        ctx.project = {"name": "leapp"}
        for k, v in overrides.items():
            setattr(ctx, k, v)
        return ctx

    def _make_spec(self, set_brew_api, set_copr_api=None):
        return RequestSpec(
            set_name="merge-test",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec={"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            upgrade_path="rhel-8.10-to-9.4",
            effective_values={
                "brew_api": set_brew_api,
                "copr_api": set_copr_api or {},
            },
        )

    def _resolve(self, spec, ctx):
        from enge.dispatch.set_flow import _resolve_artifacts

        captured = []

        class SpyResolver:
            def resolve_builds(self, compose_name, ctx):
                captured.append(ctx)
                return [
                    {"compose": compose_name, "distro": "rhel-8.10", "build_id": None}
                ]

        submit = MagicMock()
        submit.artifacts = []
        _resolve_artifacts(spec, submit, {}, ctx, "compose", SpyResolver())
        return captured[0]

    def test_set_refs_inherit_run_level_endpoints(self):
        """Set defines only build_references; session_url/taskid_url must
        come from the run-level dict via per-key merge."""
        ctx = self._make_ctx()
        spec = self._make_spec({"build_references": ["pkg-beta-2.0"]})
        resolved = self._resolve(spec, ctx)

        self.assertEqual(resolved.brew_references, ["pkg-beta-2.0"])
        self.assertEqual(
            resolved.brew_api["session_url"],
            "https://brew.example/hub",
            "session_url must inherit from run-level when set omits it",
        )
        self.assertEqual(
            resolved.brew_api["taskid_url"],
            "https://brew.example/task",
            "taskid_url must inherit from run-level when set omits it",
        )
        self.assertEqual(
            resolved.brew_api["build_references"],
            ["pkg-beta-2.0"],
            "build_references must come from set-level",
        )

    def test_endpoint_only_override_preserves_run_refs(self):
        """Set overrides session_url but defines no build_references;
        references must resolve from run-level, endpoint from the set."""
        ctx = self._make_ctx()
        spec = self._make_spec({"session_url": "https://other.example/hub"})
        resolved = self._resolve(spec, ctx)

        self.assertEqual(
            resolved.brew_api["session_url"],
            "https://other.example/hub",
            "session_url must come from set-level override",
        )
        self.assertEqual(
            resolved.brew_api["taskid_url"],
            "https://brew.example/task",
            "taskid_url must inherit from run-level",
        )
        self.assertEqual(
            resolved.brew_references,
            ["run-level-ref"],
            "references must come from run-level when set has none",
        )

    def test_empty_string_unset_inherits_run_level(self):
        """Set-level session_url="" must inherit the run-level value,
        not mask it with an empty string."""
        ctx = self._make_ctx()
        spec = self._make_spec({"session_url": "", "build_references": ["pkg-x"]})
        resolved = self._resolve(spec, ctx)

        self.assertEqual(
            resolved.brew_api["session_url"],
            "https://brew.example/hub",
            "empty-string session_url must inherit run-level value",
        )
        self.assertEqual(resolved.brew_references, ["pkg-x"])


if __name__ == "__main__":
    unittest.main()
