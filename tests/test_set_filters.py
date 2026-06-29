"""Tests for set-level plan_filter and test_filter config keys.

Covers the priority chains:
  CLI --plan-filter > set plan_filter > tier-generated > base filter
  CLI --test-filter > set test_filter > None
"""

import copy
import unittest
from unittest.mock import MagicMock, patch

from enge.dispatch.set_flow import (
    RequestSpec,
    _build_plan_filter,
    _configure_submit_test,
)
from enge.utils.opt_manager import ParsedOpts
from enge.utils.arg_parser import get_arguments
from enge.utils.source_target_parser import resolve_effective_values


MINIMAL_CONFIG = {
    "common": {
        "archive_tasks_latest": "/tmp/enge_latest_jobs",
        "archive_tasks_default": "~/.enge/jobs_archive/",
        "logs_directory": "/var/tmp/enge/logs/",
    },
    "testing_farm": {
        "api_key": "test-tf-key",
        "cloud_resources_tag": "test-biz-tag",
        "api_endpoint_url": "https://api.example.tf",
        "log_artifact_baseurl": "https://logs.example.tf",
        "composes_prod_url": "https://composes.example.tf",
    },
    "project": {
        "name": "test-project",
        "repo_url": "https://github.com/oamg/test",
    },
    "tests": {
        "git_url": "https://github.com/oamg/tests",
        "git_ref": "main",
        "tiers": ["tier3"],
        "tier": {
            "tier0": "tag:tier[0]",
            "tier3": "tag:tier[0123]",
        },
    },
    "copr_api": {"owner": "", "build_references": []},
    "brew_api": {"session_url": ""},
    "reportportal": {"url": "", "project": ""},
    "sources": {"ami": {}},
}


def _make_spec(**overrides):
    defaults = dict(
        set_name="test-set",
        tier="tier0",
        plan=None,
        arch="x86_64",
        source_spec={"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
        target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
        upgrade_path="9to9",
        effective_values={},
    )
    defaults.update(overrides)
    return RequestSpec(**defaults)


def _make_ctx(**cli_overrides):
    ctx = MagicMock()
    ctx.testing_farm = MINIMAL_CONFIG["testing_farm"]
    ctx.testing_farm_endpoint = MagicMock(
        log_artifact_baseurl="http://logs",
        api_endpoint_url="http://api",
    )
    ctx.tests = MINIMAL_CONFIG["tests"]
    ctx.project = MINIMAL_CONFIG["project"]
    ctx.config = MINIMAL_CONFIG
    ctx.archive_tasks_latest = "/tmp/enge_test_latest"
    ctx.archive_tasks_default = "/tmp/enge_test_archive/"

    cli = MagicMock()
    cli.planfilter = cli_overrides.get("planfilter", None)
    cli.testfilter = cli_overrides.get("testfilter", None)
    cli.test = None
    cli.git_url = None
    cli.git_ref = None
    cli.auto_tag = False
    cli.set_tag = None
    cli.only_rhsm_mock_cdn = False
    cli.no_rhsm = False
    cli.only_rhsm_stage_cdn = False
    ctx.cli_args = cli
    return ctx


class TestSetPlanFilter(unittest.TestCase):
    """Priority: CLI --plan-filter > set plan_filter > tier-generated."""

    @patch(
        "enge.dispatch.set_flow.generate_tier_plan_filter",
        return_value="tag:9to9 & tag:tier[0] & enabled:true",
    )
    def test_set_plan_filter_flows_to_submit(self, _mock_gen):
        spec = _make_spec(effective_values={"plan_filter": "tag:custom"})
        ctx = _make_ctx()
        result = _build_plan_filter(spec, ctx)
        self.assertEqual(result, "tag:custom")

    @patch(
        "enge.dispatch.set_flow.generate_tier_plan_filter",
        return_value="tag:9to9 & tag:tier[0] & enabled:true",
    )
    def test_cli_plan_filter_overrides_set(self, _mock_gen):
        spec = _make_spec(effective_values={"plan_filter": "tag:custom"})
        ctx = _make_ctx(planfilter="tag:cli-override")
        result = _build_plan_filter(spec, ctx)
        self.assertEqual(result, "tag:cli-override")

    @patch(
        "enge.dispatch.set_flow.generate_tier_plan_filter",
        return_value="tag:9to9 & tag:tier[0] & enabled:true",
    )
    def test_set_plan_filter_overrides_tier_generated(self, mock_gen):
        spec = _make_spec(effective_values={"plan_filter": "tag:explicit"})
        ctx = _make_ctx()
        result = _build_plan_filter(spec, ctx)
        self.assertEqual(result, "tag:explicit")
        mock_gen.assert_called_once()

    @patch(
        "enge.dispatch.set_flow.generate_tier_plan_filter",
        return_value="tag:9to9 & tag:tier[0] & enabled:true",
    )
    def test_no_set_filter_falls_through_to_tier_generated(self, _mock_gen):
        spec = _make_spec(effective_values={})
        ctx = _make_ctx()
        result = _build_plan_filter(spec, ctx)
        self.assertEqual(result, "tag:9to9 & tag:tier[0] & enabled:true")


class TestSetTestFilter(unittest.TestCase):
    """Priority: CLI --test-filter > set test_filter > None."""

    def test_set_test_filter_flows_to_submit(self):
        spec = _make_spec(effective_values={"test_filter": "tag:fast"})
        ctx = _make_ctx()
        submit = _configure_submit_test(spec, ctx, "shared")
        self.assertEqual(submit.testfilter, "tag:fast")

    def test_cli_test_filter_overrides_set(self):
        spec = _make_spec(effective_values={"test_filter": "tag:fast"})
        ctx = _make_ctx(testfilter="tag:cli-filter")
        submit = _configure_submit_test(spec, ctx, "shared")
        self.assertEqual(submit.testfilter, "tag:cli-filter")

    def test_no_test_filter_is_none(self):
        spec = _make_spec(effective_values={})
        ctx = _make_ctx()
        submit = _configure_submit_test(spec, ctx, "shared")
        self.assertIsNone(submit.testfilter)


class TestResolveEffectiveValues(unittest.TestCase):
    """resolve_effective_values picks up plan_filter/test_filter from set config."""

    def test_includes_filters_from_set(self):
        cli_args = MagicMock()
        cli_args.source = None
        cli_args.target = None
        cli_args.architectures = None
        cli_args.pool = None
        cli_args.git_ref = None
        cli_args.git_url = None
        cli_args.parallel_limit = None
        cli_args.tier = None
        cli_args.event = None
        cli_args.plan = None

        set_config = {
            "source": "CentOS-Stream-9",
            "git_ref": "main",
            "plan_filter": "tag:custom & enabled:true",
            "test_filter": "tag:fast",
        }

        result = resolve_effective_values(cli_args, set_config, MINIMAL_CONFIG)
        self.assertEqual(result["plan_filter"], "tag:custom & enabled:true")
        self.assertEqual(result["test_filter"], "tag:fast")

    def test_missing_filters_resolve_to_none(self):
        cli_args = MagicMock()
        cli_args.source = None
        cli_args.target = None
        cli_args.architectures = None
        cli_args.pool = None
        cli_args.git_ref = None
        cli_args.git_url = None
        cli_args.parallel_limit = None
        cli_args.tier = None
        cli_args.event = None
        cli_args.plan = None

        set_config = {"source": "CentOS-Stream-9", "git_ref": "main"}

        result = resolve_effective_values(cli_args, set_config, MINIMAL_CONFIG)
        self.assertIsNone(result["plan_filter"])
        self.assertIsNone(result["test_filter"])


class TestSetValidation(unittest.TestCase):
    """_validate_test_set_structure accepts plan_filter/test_filter without warning."""

    def test_filter_keys_are_valid(self):
        po = object.__new__(ParsedOpts)
        po._validation_hooks = {}
        po.config = copy.deepcopy(MINIMAL_CONFIG)
        po.cli_args = get_arguments(args=["report"])
        po.options = po._get_config_options()

        set_config = {
            "source": "CentOS-Stream-9",
            "architectures": ["x86_64"],
            "git_ref": "main",
            "plan_filter": "tag:custom",
            "test_filter": "tag:fast",
        }
        with patch("enge.utils.opt_manager.logger") as mock_logger:
            result = po._validate_test_set_structure("filter-set", set_config)
        self.assertTrue(result)
        for call in mock_logger.warning.call_args_list:
            self.assertNotIn("unknown keys", str(call))


if __name__ == "__main__":
    unittest.main()
