#!/usr/bin/env python3
"""
Characterization tests for enge.utils.opt_manager — ParsedOpts and _LazyParsedOpts.

These tests PIN the current behavior before refactoring.  Where behavior
looks unintended, the test is marked:
    # CHARACTERIZATION: <explanation>

Surprising behaviors pinned (see final summary in commit message / PR):
  1. TestingFarmEndpoint always constructed even for non-test actions;
     raises ValueError (not ConfigurationError) when endpoint URLs are empty.
  2. Config api_key wins over TESTING_FARM_API_TOKEN env var — counterintuitive.
  3. resolve_effective_values uses `or` chains; empty string "" from CLI is
     falsy and falls through to the set/config value.
  4. validate_opts() catches SystemExit but NOT ConfigurationError/ValidationError,
     so those still propagate from validate_opts().
  5. self.plans on a set-based ParsedOpts is [] even when sets define plans;
     set plans live in individual_test_sets[i]["effective_values"]["plans"].
  6. Empty string in _validate_operational_defaults raises ConfigurationError;
     the check is (value is None or value == "") — same semantics as None.
  7. _validate_required_config uses `not value`, which treats "" and None
     identically — consistent with (6) but different implementation.
"""

import copy
import os
import unittest
from argparse import Namespace
from unittest.mock import patch

from enge.utils.config_parser import merge_configs
from enge.utils.errors import ConfigurationError, ValidationError
from enge.utils.opt_manager import ParsedOpts, _LazyParsedOpts, TestingFarmEndpoint
from enge.utils.arg_parser import get_arguments
from enge.utils.source_target_parser import resolve_effective_values

# Import the module (not the singleton instance) to avoid triggering lazy
# initialization during pytest collection (safe_getattr inspects module attrs).
import enge.utils.opt_manager as _opt_manager


# ---------------------------------------------------------------------------
# Shared fixture: minimal configuration that passes ALL static validations
# when using a non-test action (report / cancel / rerun / reportportal).
# ---------------------------------------------------------------------------
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
        "tier": {
            "tier0": "tag:tier[0]",
            "tier1": "tag:tier[01]",
        },
    },
    "copr_api": {
        "owner": "",
        "owner_is_group": True,
        "repository": "",
        "package": "",
        "build_references": [],
    },
    "brew_api": {"session_url": "", "taskid_url": ""},
    "reportportal": {"url": "", "project": ""},
    "sources": {"ami": {}},
}


def _make_partial_opts(config=None, cli_args=None):
    """
    Construct a ParsedOpts bypassing __init__, for testing individual methods.

    Sets only the attributes that validation methods require:
      _validation_hooks, config, cli_args, options.
    """
    po = object.__new__(ParsedOpts)
    po._validation_hooks = {}
    po.config = (
        copy.deepcopy(config) if config is not None else copy.deepcopy(MINIMAL_CONFIG)
    )
    po.cli_args = cli_args if cli_args is not None else get_arguments(args=["report"])
    po.options = po._get_config_options()
    return po


# ---------------------------------------------------------------------------
# 1. merge_configs (config_parser) — config merge & precedence
# ---------------------------------------------------------------------------


class TestMergeConfigs(unittest.TestCase):

    def test_user_scalar_wins_over_default(self):
        merged = merge_configs({"a": 1, "b": 2}, {"b": 99})
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["b"], 99)

    def test_nested_dict_merged_recursively(self):
        default = {"outer": {"a": 1, "b": 2}}
        user = {"outer": {"b": 99, "c": 3}}
        merged = merge_configs(default, user)
        self.assertEqual(merged["outer"]["a"], 1)  # default key preserved
        self.assertEqual(merged["outer"]["b"], 99)  # user wins
        self.assertEqual(merged["outer"]["c"], 3)  # user-only key added

    def test_user_empty_string_replaces_default(self):
        # CHARACTERIZATION: empty string from user config overwrites a real
        # default.  The caller is responsible for treating "" as "missing."
        merged = merge_configs({"key": "default-value"}, {"key": ""})
        self.assertEqual(merged["key"], "")

    def test_user_none_replaces_default(self):
        merged = merge_configs({"key": "default-value"}, {"key": None})
        self.assertIsNone(merged["key"])

    def test_user_only_key_added_to_merged(self):
        merged = merge_configs({"a": 1}, {"b": 2})
        self.assertIn("b", merged)
        self.assertEqual(merged["b"], 2)

    def test_default_only_keys_preserved_when_user_adds_sibling(self):
        merged = merge_configs({"a": 1, "b": 2}, {"c": 3})
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["b"], 2)

    def test_deeply_nested_merge(self):
        default = {"a": {"b": {"c": 1, "d": 2}}}
        user = {"a": {"b": {"c": 99}}}
        merged = merge_configs(default, user)
        self.assertEqual(merged["a"]["b"]["c"], 99)
        self.assertEqual(merged["a"]["b"]["d"], 2)  # preserved


# ---------------------------------------------------------------------------
# 2. resolve_effective_values (source_target_parser) — CLI > Set > Config
# ---------------------------------------------------------------------------


class TestResolveEffectiveValues(unittest.TestCase):
    """Priority chain: CLI arg wins, then test-set config, then top-level config."""

    def _cli(self, **kwargs):
        defaults = dict(
            source=None,
            target=None,
            architectures=None,
            pool=None,
            git_ref=None,
            git_url=None,
            parallel_limit=None,
            tier=None,
            event=None,
            plan=None,
        )
        defaults.update(kwargs)
        return Namespace(**defaults)

    def test_cli_source_beats_set_and_config(self):
        result = resolve_effective_values(
            self._cli(source="cli-src"),
            {"source": "set-src"},
            {"tests": {"source": "cfg-src"}},
        )
        self.assertEqual(result["source"], "cli-src")

    def test_set_source_beats_config_when_cli_absent(self):
        result = resolve_effective_values(
            self._cli(source=None),
            {"source": "set-src"},
            {"tests": {"source": "cfg-src"}},
        )
        self.assertEqual(result["source"], "set-src")

    def test_config_source_used_as_fallback(self):
        result = resolve_effective_values(
            self._cli(source=None),
            {},
            {"tests": {"source": "cfg-src"}},
        )
        self.assertEqual(result["source"], "cfg-src")

    def test_empty_string_cli_falls_through_to_set(self):
        # CHARACTERIZATION: "" is falsy in the `or` chain, so the set value
        # wins.  There is no way to "clear" a value by passing --source "".
        result = resolve_effective_values(
            self._cli(source=""),
            {"source": "set-src"},
            {},
        )
        self.assertEqual(result["source"], "set-src")

    def test_cli_plans_override_set_and_config(self):
        result = resolve_effective_values(
            self._cli(plan=["/plans/cli"]),
            {"plans": ["/plans/set"]},
            {"tests": {"plans": ["/plans/cfg"]}},
        )
        self.assertEqual(result["plans"], ["/plans/cli"])

    def test_set_plans_beat_config_when_cli_absent(self):
        result = resolve_effective_values(
            self._cli(plan=None),
            {"plans": ["/plans/set"]},
            {"tests": {"plans": ["/plans/cfg"]}},
        )
        self.assertEqual(result["plans"], ["/plans/set"])

    def test_no_source_anywhere_returns_none(self):
        result = resolve_effective_values(self._cli(), {}, {})
        self.assertIsNone(result["source"])

    def test_git_ref_precedence(self):
        result = resolve_effective_values(
            self._cli(git_ref="cli-ref"),
            {"git_ref": "set-ref"},
            {"tests": {"git_ref": "cfg-ref"}},
        )
        self.assertEqual(result["git_ref"], "cli-ref")


# ---------------------------------------------------------------------------
# 3. _apply_env_var_fallbacks — env var behavior
# ---------------------------------------------------------------------------


class TestEnvVarFallbacks(unittest.TestCase):

    def test_empty_api_key_filled_from_env(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        po = _make_partial_opts(config=cfg)
        with patch.dict(os.environ, {"TESTING_FARM_API_TOKEN": "env-key"}):
            po._apply_env_var_fallbacks()
        self.assertEqual(po.config["testing_farm"]["api_key"], "env-key")

    def test_non_empty_api_key_not_replaced_by_env(self):
        # CHARACTERIZATION: config api_key wins over TESTING_FARM_API_TOKEN.
        # This is counterintuitive (env vars usually override config files).
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = "config-key"
        po = _make_partial_opts(config=cfg)
        with patch.dict(os.environ, {"TESTING_FARM_API_TOKEN": "env-key"}):
            po._apply_env_var_fallbacks()
        self.assertEqual(po.config["testing_farm"]["api_key"], "config-key")

    def test_empty_rp_token_filled_from_env(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["reportportal"] = {"url": "", "project": "", "token": ""}
        po = _make_partial_opts(config=cfg)
        with patch.dict(os.environ, {"REPORTPORTAL_API_TOKEN": "env-rp-token"}):
            po._apply_env_var_fallbacks()
        self.assertEqual(po.config["reportportal"]["token"], "env-rp-token")

    def test_absent_api_key_filled_from_env(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"].pop("api_key", None)
        po = _make_partial_opts(config=cfg)
        with patch.dict(os.environ, {"TESTING_FARM_API_TOKEN": "env-key"}):
            po._apply_env_var_fallbacks()
        self.assertEqual(po.config["testing_farm"]["api_key"], "env-key")

    def test_missing_env_var_leaves_empty_key_as_empty(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        po = _make_partial_opts(config=cfg)
        clean_env = {
            k: v for k, v in os.environ.items() if k != "TESTING_FARM_API_TOKEN"
        }
        with patch.dict(os.environ, clean_env, clear=True):
            po._apply_env_var_fallbacks()
        self.assertEqual(po.config["testing_farm"]["api_key"], "")

    def test_non_dict_section_skipped_without_error(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"] = "not-a-dict"
        po = _make_partial_opts(config=cfg)
        with patch.dict(os.environ, {"TESTING_FARM_API_TOKEN": "env-key"}):
            po._apply_env_var_fallbacks()  # must not raise


# ---------------------------------------------------------------------------
# 4. _validate_operational_defaults — empty-string semantics
# ---------------------------------------------------------------------------


class TestOperationalDefaults(unittest.TestCase):

    def test_valid_config_passes_silently(self):
        po = _make_partial_opts()
        po._validate_operational_defaults()  # must not raise

    def test_missing_common_section_raises_configuration_error(self):
        # CHARACTERIZATION: the exception message is the summary ("Operational
        # defaults validation failed"); the per-section detail ("Missing
        # operational section: [common]") is only in the CRITICAL log output.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        del cfg["common"]
        po = _make_partial_opts(config=cfg)
        with self.assertLogs("enge.utils.opt_manager", level="CRITICAL") as log:
            with self.assertRaises(ConfigurationError):
                po._validate_operational_defaults()
        self.assertTrue(any("common" in m.lower() for m in log.output))

    def test_empty_string_key_raises_configuration_error(self):
        # CHARACTERIZATION: empty string is treated the same as None by
        # _validate_operational_defaults (check: value is None or value == "").
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["common"]["archive_tasks_latest"] = ""
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_operational_defaults()
        self.assertIn("Operational defaults", str(ctx.exception))

    def test_none_key_raises_configuration_error(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["common"]["logs_directory"] = None
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError):
            po._validate_operational_defaults()

    def test_non_dict_section_raises_configuration_error(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["common"] = "not-a-dict"
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError):
            po._validate_operational_defaults()


# ---------------------------------------------------------------------------
# 5. _validate_required_config — required fields for test / rerun actions
# ---------------------------------------------------------------------------


class TestRequiredConfig(unittest.TestCase):

    def test_report_action_skips_validation(self):
        # _validate_required_config only runs for test/rerun actions.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""  # would fail for test action
        po = _make_partial_opts(config=cfg, cli_args=get_arguments(args=["report"]))
        po._validate_required_config()  # must not raise

    def test_test_action_complete_config_passes(self):
        po = _make_partial_opts(
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            )
        )
        po._validate_required_config()  # must not raise

    def test_test_action_empty_api_key_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            ),
        )
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_required_config()
        self.assertIn("Required configuration", str(ctx.exception))

    def test_test_action_empty_cloud_tag_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["cloud_resources_tag"] = ""
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            ),
        )
        with self.assertRaises(ConfigurationError):
            po._validate_required_config()

    def test_rerun_action_requires_tf_config(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(args=["rerun"]),
        )
        with self.assertRaises(ConfigurationError):
            po._validate_required_config()

    def test_reportportal_action_requires_rp_token(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        # reportportal section has no "token" key
        po = _make_partial_opts(
            config=cfg, cli_args=get_arguments(args=["reportportal", "--finish"])
        )
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_required_config()
        self.assertIn("ReportPortal", str(ctx.exception))

    def test_reportportal_action_with_token_passes(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["reportportal"]["token"] = "rp-token"
        po = _make_partial_opts(
            config=cfg, cli_args=get_arguments(args=["reportportal", "--finish"])
        )
        po._validate_required_config()  # must not raise


# ---------------------------------------------------------------------------
# 6. _validate_static_configuration — static config rules
# ---------------------------------------------------------------------------


class TestStaticConfiguration(unittest.TestCase):

    def test_valid_config_passes(self):
        po = _make_partial_opts()
        po._validate_static_configuration()  # must not raise

    def test_empty_api_key_raises_with_hint(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_static_configuration()
        self.assertIn("Static configuration", str(ctx.exception))

    def test_empty_project_name_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["project"]["name"] = ""
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError):
            po._validate_static_configuration()

    def test_missing_both_git_url_and_repo_url_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["git_url"] = ""
        cfg["project"]["repo_url"] = ""
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError):
            po._validate_static_configuration()

    def test_project_repo_url_satisfies_git_url_requirement(self):
        # [project].repo_url can substitute for [tests].git_url.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["git_url"] = ""
        # project.repo_url remains set -> passes
        po = _make_partial_opts(config=cfg)
        po._validate_static_configuration()  # must not raise

    def test_test_action_without_cli_or_config_arch_raises(self):
        # CHARACTERIZATION: exception summary is "Static configuration invalid";
        # the per-field detail ("No architectures configured...") appears only
        # in the CRITICAL log output.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        # No architectures anywhere; no sets
        cli = get_arguments(args=["test", "-s", "9.7", "-T", "tier0"])  # no --arch
        po = _make_partial_opts(config=cfg, cli_args=cli)
        with self.assertLogs("enge.utils.opt_manager", level="CRITICAL") as log:
            with self.assertRaises(ConfigurationError):
                po._validate_static_configuration()
        self.assertTrue(any("architectures" in m.lower() for m in log.output))

    def test_test_action_with_sets_bypasses_arch_requirement(self):
        # When using --set, per-set architectures are expected; no top-level arch needed.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["set"] = {"my-set": {"source": "9.7", "architectures": ["x86_64"]}}
        cli = get_arguments(args=["test", "-S", "my-set"])
        po = _make_partial_opts(config=cfg, cli_args=cli)
        po._validate_static_configuration()  # must not raise

    def test_config_architectures_of_wrong_type_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["architectures"] = "x86_64"  # string, not list
        cli = get_arguments(args=["test", "-s", "9.7", "-T", "tier0"])
        po = _make_partial_opts(config=cfg, cli_args=cli)
        with self.assertRaises(ConfigurationError):
            po._validate_static_configuration()

    def test_arch_list_with_empty_string_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["architectures"] = ["x86_64", ""]  # contains empty string
        cli = get_arguments(args=["test", "-s", "9.7", "-T", "tier0"])
        po = _make_partial_opts(config=cfg, cli_args=cli)
        with self.assertRaises(ConfigurationError):
            po._validate_static_configuration()

    def test_tests_context_non_dict_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["context"] = "not-a-dict"  # should be a dict
        cli = get_arguments(
            args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
        )
        po = _make_partial_opts(config=cfg, cli_args=cli)
        with self.assertRaises(ConfigurationError):
            po._validate_static_configuration()

    def test_missing_testing_farm_section_raises_in_required_config(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        del cfg["testing_farm"]
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            ),
        )
        with self.assertRaises(ConfigurationError):
            po._validate_required_config()


# ---------------------------------------------------------------------------
# 7. _validate_cli_arguments — CLI argument validation
# ---------------------------------------------------------------------------


class TestCliArgumentValidation(unittest.TestCase):

    def test_test_action_without_source_or_set_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        # No source in config, no --source CLI, no --set
        cli = get_arguments(args=["test", "-T", "tier0", "--arch", "x86_64"])
        po = _make_partial_opts(config=cfg, cli_args=cli)
        with self.assertRaises(ValidationError) as ctx:
            po._validate_cli_arguments()
        self.assertIn("CLI argument", str(ctx.exception))

    def test_test_action_source_in_config_satisfies_requirement(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["source"] = "9.7"
        cli = get_arguments(args=["test", "-T", "tier0", "--arch", "x86_64"])
        po = _make_partial_opts(config=cfg, cli_args=cli)
        po._validate_cli_arguments()  # must not raise

    def test_test_action_with_set_bypasses_source_requirement(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["set"] = {"my-set": {"source": "9.7"}}
        cli = get_arguments(args=["test", "-S", "my-set"])
        po = _make_partial_opts(config=cfg, cli_args=cli)
        po._validate_cli_arguments()  # must not raise

    def test_non_test_action_does_not_require_source(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        po = _make_partial_opts(config=cfg, cli_args=get_arguments(args=["report"]))
        po._validate_cli_arguments()  # must not raise


# ---------------------------------------------------------------------------
# 8. ParsedOpts.__getattr__ — dynamic attribute access
# ---------------------------------------------------------------------------


class TestDynamicGetattr(unittest.TestCase):

    def test_top_level_config_section_returned_as_dict(self):
        po = _make_partial_opts()
        result = po.testing_farm
        self.assertIsInstance(result, dict)
        self.assertIn("api_key", result)
        self.assertEqual(result["api_key"], "test-tf-key")

    def test_common_section_returned_correctly(self):
        po = _make_partial_opts()
        self.assertIn("archive_tasks_latest", po.common)

    def test_key_within_section_accessible_via_options_fallback(self):
        # api_key is NOT a top-level config key, so __getattr__ falls through
        # to the options scan and finds it inside [testing_farm].
        po = _make_partial_opts()
        self.assertEqual(po.api_key, "test-tf-key")

    def test_unknown_attribute_raises_attribute_error(self):
        po = _make_partial_opts()
        with self.assertRaises(AttributeError):
            _ = po.this_attribute_does_not_exist_xyz

    def test_top_level_section_priority_over_within_section_key(self):
        # If a section name matches a key inside another section,
        # the top-level section dict wins (returned first from config lookup).
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"][
            "common"
        ] = "shadow-value"  # a key inside testing_farm named "common"
        po = _make_partial_opts(config=cfg)
        result = po.common
        # Should be the top-level [common] section dict, not "shadow-value"
        self.assertIsInstance(result, dict)
        self.assertIn("archive_tasks_latest", result)


# ---------------------------------------------------------------------------
# 9. _LazyParsedOpts — singleton management (set / use)
# ---------------------------------------------------------------------------


class TestLazyParsedOpts(unittest.TestCase):
    """Uses fresh _LazyParsedOpts() instances to avoid touching the module-level
    'parsed_opts' singleton during pytest collection (safe_getattr would trigger
    eager initialization via __getattr__ → _ensure() → ParsedOpts() → sys.argv)."""

    def _new_lazy(self):
        return _LazyParsedOpts()

    def test_set_stores_instance(self):
        lazy = self._new_lazy()
        po = _make_partial_opts()
        lazy.set(po)
        self.assertIs(lazy._instance, po)

    def test_use_context_manager_temporarily_swaps_instance(self):
        lazy = self._new_lazy()
        po_before = _make_partial_opts()
        po_during = _make_partial_opts()
        lazy.set(po_before)
        with lazy.use(po_during):
            self.assertIs(lazy._instance, po_during)

    def test_use_restores_previous_on_normal_exit(self):
        lazy = self._new_lazy()
        po_before = _make_partial_opts()
        po_during = _make_partial_opts()
        lazy.set(po_before)
        with lazy.use(po_during):
            pass
        self.assertIs(lazy._instance, po_before)

    def test_use_restores_previous_on_exception(self):
        lazy = self._new_lazy()
        po_before = _make_partial_opts()
        po_during = _make_partial_opts()
        lazy.set(po_before)
        try:
            with lazy.use(po_during):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertIs(lazy._instance, po_before)

    def test_getattr_delegates_to_wrapped_instance(self):
        lazy = self._new_lazy()
        po = _make_partial_opts()
        lazy.set(po)
        self.assertEqual(lazy.testing_farm, po.config["testing_farm"])

    def test_use_with_none_previous_restores_to_none(self):
        lazy = self._new_lazy()
        po = _make_partial_opts()
        lazy._instance = None
        with lazy.use(po):
            self.assertIsNotNone(lazy._instance)
        self.assertIsNone(lazy._instance)

    def test_module_parsed_opts_use_context_manager(self):
        """The module-level singleton's .use() context manager correctly swaps
        and restores.  Accessed only from inside a test method, not at collection
        time, so __getattr__ is not triggered by safe_getattr."""
        module_lazy = _opt_manager.parsed_opts
        po = _make_partial_opts()
        saved = module_lazy._instance
        try:
            with module_lazy.use(po):
                self.assertIs(module_lazy._instance, po)
            self.assertIs(module_lazy._instance, saved)
        finally:
            module_lazy._instance = saved


# ---------------------------------------------------------------------------
# 10. Full constructor (integration) — 'report' action with patched load_config
# ---------------------------------------------------------------------------


class TestFullConstructorReport(unittest.TestCase):

    @patch("enge.utils.opt_manager.load_config")
    def test_report_action_constructs_successfully(self, mock_load):
        mock_load.return_value = copy.deepcopy(MINIMAL_CONFIG)
        po = ParsedOpts(cli_args=get_arguments(args=["report"]))
        self.assertIsInstance(po, ParsedOpts)

    @patch("enge.utils.opt_manager.load_config")
    def test_archive_tasks_latest_tilde_expanded(self, mock_load):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["common"]["archive_tasks_latest"] = "~/custom_latest"
        mock_load.return_value = cfg
        po = ParsedOpts(cli_args=get_arguments(args=["report"]))
        self.assertNotIn("~", po.archive_tasks_latest)
        self.assertTrue(po.archive_tasks_latest.startswith("/"))

    @patch("enge.utils.opt_manager.load_config")
    def test_testing_farm_endpoint_initialized(self, mock_load):
        mock_load.return_value = copy.deepcopy(MINIMAL_CONFIG)
        po = ParsedOpts(cli_args=get_arguments(args=["report"]))
        self.assertIsInstance(po.testing_farm_endpoint, TestingFarmEndpoint)
        self.assertEqual(
            po.testing_farm_endpoint.api_endpoint_url, "https://api.example.tf"
        )

    @patch("enge.utils.opt_manager.load_config")
    def test_empty_endpoint_url_raises_value_error_not_configuration_error(
        self, mock_load
    ):
        # CHARACTERIZATION: TestingFarmEndpoint is constructed unconditionally,
        # even for non-test actions.  When api_endpoint_url is empty it raises
        # ValueError (not ConfigurationError), bypassing the normal error path.
        # _validate_required_config does NOT check endpoint URLs for 'report'.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_endpoint_url"] = ""
        mock_load.return_value = cfg
        with self.assertRaises(ValueError):  # ValueError, not ConfigurationError
            ParsedOpts(cli_args=get_arguments(args=["report"]))

    @patch("enge.utils.opt_manager.load_config")
    def test_validate_opts_propagates_configuration_error(self, mock_load):
        # CHARACTERIZATION: validate_opts() catches SystemExit only.
        # ConfigurationError raised by the validation methods still propagates.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        mock_load.return_value = cfg
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError):
            po.validate_opts()


# ---------------------------------------------------------------------------
# 11. TestingFarmEndpoint — constructor invariants
# ---------------------------------------------------------------------------


class TestTestingFarmEndpoint(unittest.TestCase):

    def test_both_urls_required(self):
        with self.assertRaises(ValueError):
            TestingFarmEndpoint("", "https://logs.example.tf")
        with self.assertRaises(ValueError):
            TestingFarmEndpoint("https://api.example.tf", "")
        with self.assertRaises(ValueError):
            TestingFarmEndpoint("", "")

    def test_both_urls_provided_succeeds(self):
        ep = TestingFarmEndpoint("https://api.example.tf", "https://logs.example.tf")
        self.assertEqual(ep.api_endpoint_url, "https://api.example.tf")
        self.assertEqual(ep.log_artifact_baseurl, "https://logs.example.tf")


# ---------------------------------------------------------------------------
# 12. Auxiliary methods — check_dependencies, hooks, _collect_set_value,
#     _validate_effective_configuration
# ---------------------------------------------------------------------------


class TestAuxiliaryMethods(unittest.TestCase):
    """Cover smaller helpers not exercised by the validation-method tests above."""

    def test_check_dependencies_returns_empty_for_report_action(self):
        po = _make_partial_opts()
        errors = po.check_dependencies()
        self.assertIsInstance(errors, list)
        self.assertEqual(errors, [])

    def test_check_dependencies_test_action_missing_api_key_returns_error(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["testing_farm"]["api_key"] = ""
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            ),
        )
        errors = po.check_dependencies()
        self.assertTrue(
            any("api" in e.lower() or "testing farm" in e.lower() for e in errors)
        )

    def test_register_hook_and_run_it(self):
        po = _make_partial_opts()
        fired = []
        po.register_validation_hook("test_hook", lambda: fired.append(1) or True)
        result = po.run_validation_hook("test_hook")
        self.assertTrue(result)
        self.assertEqual(len(fired), 1)

    def test_run_unknown_hook_returns_true(self):
        po = _make_partial_opts()
        self.assertTrue(po.run_validation_hook("nonexistent_hook_xyz"))

    def test_collect_set_value_returns_first_match(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["set"] = {
            "s1": {"source": "CentOS-Stream-9"},
            "s2": {"source": "CentOS-Stream-8"},
        }
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(args=["test", "-S", "s1"]),
        )
        result = po._collect_set_value("source")
        self.assertEqual(result, "CentOS-Stream-9")

    def test_collect_set_value_returns_none_without_sets(self):
        po = _make_partial_opts()
        self.assertIsNone(po._collect_set_value("source"))

    def test_collect_set_value_returns_none_for_absent_key(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["set"] = {"s1": {"source": "CentOS-Stream-9"}}
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(args=["test", "-S", "s1"]),
        )
        self.assertIsNone(po._collect_set_value("no_such_key"))

    def test_validate_effective_configuration_missing_git_ref_raises(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["git_ref"] = ""  # falsy — triggers error
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            ),
        )
        with self.assertRaises(ConfigurationError):
            po._validate_effective_configuration()

    def test_validate_effective_configuration_valid_passes(self):
        po = _make_partial_opts()
        po._validate_effective_configuration()  # must not raise


if __name__ == "__main__":
    unittest.main()
