#!/usr/bin/env python3
"""
Characterization tests for enge.utils.opt_manager — validation & set handling.

Covers: set-regex expansion, option-dependency validation, test-set structure
validation, and _initialize_test_attributes.

These tests PIN the current behavior before refactoring.  Surprising behaviors
are marked # CHARACTERIZATION.

Additional surprising behaviors pinned here:
  8.  --set-regex with no configured sets raises ValidationError("No test sets
      configured"), not "pattern matched nothing".
  9.  _validate_test_set_structure returns True for unknown keys (only warns),
      so a typo like "archiectures" passes silently.
  10. -S my-set bypasses the "no plans specified" check even if the set has no
      plans defined (the check only tests cli_sets being truthy).
  11. self.plans is [] for set-based ParsedOpts even when sets define plans;
      per-set plans live in individual_test_sets[i]["effective_values"]["plans"].
"""

import copy
import unittest
from unittest.mock import patch

from enge.utils.app_context import AppContext
from enge.utils.errors import ConfigurationError, ValidationError
from enge.utils.opt_manager import ParsedOpts
from enge.utils.arg_parser import get_arguments


# ---------------------------------------------------------------------------
# Shared fixture
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
            "tier3": "tag:tier[0123]",
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

# Config with two test sets that use CentOS-Stream-9 as source.
# CentOS-Stream-N is parsed without any pin_compose network call.
#
# [tests].tiers = ['tier3'] mirrors the default config fallback added in
# fix/config-resolution-hardening.  Including it here avoids the need to
# patch the default-config loader in every full-constructor test.
SETS_CONFIG = {
    **copy.deepcopy(MINIMAL_CONFIG),
    "tests": {
        "git_url": MINIMAL_CONFIG["tests"]["git_url"],
        "git_ref": MINIMAL_CONFIG["tests"]["git_ref"],
        "tier": MINIMAL_CONFIG["tests"]["tier"],
        "tiers": ["tier3"],
        "set": {
            "alpha-set": {
                "source": "CentOS-Stream-9",
                "architectures": ["x86_64"],
                "plans": ["/plans/smoke"],
                "git_ref": "main",
            },
            "beta-set": {
                "source": "CentOS-Stream-9",
                "architectures": ["aarch64"],
                "plans": ["/plans/regression"],
                "git_ref": "main",
            },
        },
    },
}


def _make_partial_opts(config=None, cli_args=None):
    po = object.__new__(ParsedOpts)
    po._validation_hooks = {}
    po.config = (
        copy.deepcopy(config) if config is not None else copy.deepcopy(MINIMAL_CONFIG)
    )
    po.cli_args = cli_args if cli_args is not None else get_arguments(args=["report"])
    po.options = po._get_config_options()
    return po


# ---------------------------------------------------------------------------
# 1. _expand_set_regex_arguments — regex expansion
# ---------------------------------------------------------------------------


class TestSetRegexExpansion(unittest.TestCase):

    def _po_with_regex(self, patterns, config, extra_sets=None):
        """Helper: partial ParsedOpts for the test action with --set-regex."""
        args = ["test", "--arch", "x86_64"]
        for p in patterns:
            args += ["--set-regex", p]
        if extra_sets:
            for s in extra_sets:
                args += ["-S", s]
        return _make_partial_opts(
            config=config,
            cli_args=get_arguments(args=args),
        )

    def test_non_test_action_skips_expansion(self):
        po = _make_partial_opts(
            config=SETS_CONFIG,
            cli_args=get_arguments(args=["report"]),
        )
        po._expand_set_regex_arguments()  # must not raise, noop for non-test

    def test_no_set_regex_arg_is_noop(self):
        po = _make_partial_opts(
            config=SETS_CONFIG,
            cli_args=get_arguments(args=["test", "-s", "9.7", "--arch", "x86_64"]),
        )
        po._expand_set_regex_arguments()  # must not raise, noop

    def test_matching_pattern_expands_to_set_names(self):
        po = self._po_with_regex(["alpha.*"], SETS_CONFIG)
        po._expand_set_regex_arguments()
        self.assertEqual(po.cli_args.set, ["alpha-set"])

    def test_multiple_patterns_combined_and_deduplicated(self):
        po = self._po_with_regex(["alpha.*", "beta.*"], SETS_CONFIG)
        po._expand_set_regex_arguments()
        result = po.cli_args.set
        self.assertIn("alpha-set", result)
        self.assertIn("beta-set", result)

    def test_explicit_set_combined_with_regex_no_duplicate(self):
        po = self._po_with_regex(["alpha.*"], SETS_CONFIG, extra_sets=["beta-set"])
        po._expand_set_regex_arguments()
        result = po.cli_args.set
        self.assertIn("alpha-set", result)
        self.assertIn("beta-set", result)
        # beta-set already in explicit list; regex adds alpha-set; no duplicates
        self.assertEqual(len(result), len(set(result)))

    def test_invalid_regex_raises_validation_error(self):
        po = self._po_with_regex(["[invalid(regex"], SETS_CONFIG)
        with self.assertRaises(ValidationError) as ctx:
            po._expand_set_regex_arguments()
        self.assertIn("set-regex", str(ctx.exception).lower())

    def test_pattern_matching_nothing_raises_validation_error_with_available_sets(self):
        po = self._po_with_regex(["zzz-no-match.*"], SETS_CONFIG)
        with self.assertRaises(ValidationError) as ctx:
            po._expand_set_regex_arguments()
        self.assertIn("set-regex", str(ctx.exception).lower())

    def test_no_sets_configured_raises_with_no_test_sets_message(self):
        # CHARACTERIZATION: when [tests.set] is empty/missing the error says
        # "No test sets configured" rather than "pattern matched nothing".
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        # No tests.set key at all
        po = self._po_with_regex(["any.*"], cfg)
        with self.assertRaises(ValidationError) as ctx:
            po._expand_set_regex_arguments()
        self.assertIn("No test sets configured", str(ctx.exception))

    def test_explicit_set_preserved_even_if_pattern_matches_nothing(self):
        # Pattern "zzz.*" matches nothing — that's an error, but the test
        # verifies the error message includes info about available sets.
        po = self._po_with_regex(["zzz.*"], SETS_CONFIG)
        with self.assertRaises(ValidationError):
            po._expand_set_regex_arguments()
        # After the exception, cli_args.set should NOT have been mutated
        # (the replacement happens only on success).
        self.assertIsNone(po.cli_args.set)


# ---------------------------------------------------------------------------
# 2. _validate_option_dependencies — inter-option validation
# ---------------------------------------------------------------------------


class TestOptionDependencies(unittest.TestCase):

    def test_test_action_with_tier_passes(self):
        po = _make_partial_opts(
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
            )
        )
        po._validate_option_dependencies()  # must not raise

    def test_test_action_with_plan_passes(self):
        po = _make_partial_opts(
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-p", "/plans/smoke", "--arch", "x86_64"]
            )
        )
        po._validate_option_dependencies()  # must not raise

    def test_test_action_with_valid_set_passes(self):
        cfg = copy.deepcopy(SETS_CONFIG)
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(args=["test", "-S", "alpha-set"]),
        )
        po._validate_option_dependencies()  # must not raise

    def test_test_action_no_plan_no_tier_no_set_raises(self):
        po = _make_partial_opts(
            cli_args=get_arguments(args=["test", "-s", "9.7", "--arch", "x86_64"])
            # No -T, -p, or -S
        )
        with self.assertRaises(ValidationError) as ctx:
            po._validate_option_dependencies()
        self.assertIn("Option dependency", str(ctx.exception))

    def test_unknown_tier_raises_with_available_tiers_listed(self):
        # Pin #4 flipped: detail lines ("Tier 'tier99' not found. Available:
        # [...]") are now included in the exception message itself (joined),
        # in addition to the CRITICAL log output which is unchanged.
        po = _make_partial_opts(
            cli_args=get_arguments(
                args=["test", "-s", "9.7", "-T", "tier99", "--arch", "x86_64"]
            )
        )
        with self.assertLogs("enge.utils.opt_manager", level="CRITICAL") as log:
            with self.assertRaises(ValidationError) as ctx:
                po._validate_option_dependencies()
        self.assertTrue(any("tier99" in m for m in log.output))
        self.assertIn("tier99", str(ctx.exception))

    def test_nonexistent_set_raises_validation_error(self):
        po = _make_partial_opts(
            config=copy.deepcopy(MINIMAL_CONFIG),  # no sets defined
            cli_args=get_arguments(args=["test", "-S", "no-such-set"]),
        )
        with self.assertRaises(ValidationError):
            po._validate_option_dependencies()

    def test_set_without_plans_bypasses_plans_check(self):
        # CHARACTERIZATION: specifying -S bypasses the "no plans specified"
        # check entirely, even if the set defines no plans.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["set"] = {
            "empty-set": {"source": "9.7", "architectures": ["x86_64"]}
        }
        po = _make_partial_opts(
            config=cfg,
            cli_args=get_arguments(args=["test", "-S", "empty-set"]),
        )
        po._validate_option_dependencies()  # must not raise

    def test_non_test_action_skips_dependency_checks(self):
        po = _make_partial_opts(cli_args=get_arguments(args=["report"]))
        po._validate_option_dependencies()  # must not raise


# ---------------------------------------------------------------------------
# 3. _validate_test_set_structure — per-set structure validation
# ---------------------------------------------------------------------------


class TestTestSetStructure(unittest.TestCase):

    def _po(self):
        return _make_partial_opts()

    def test_valid_minimal_set_returns_true(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"source": "9.7", "architectures": ["x86_64"], "plans": ["/plans/smoke"]},
        )
        self.assertTrue(result)

    def test_unknown_key_returns_true_with_warning(self):
        # CHARACTERIZATION: unknown keys are only warnings, not errors.
        # A typo like "archiectures" passes validation silently.
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"archiectures": ["x86_64"], "plans": ["/plans/smoke"]},  # typo!
        )
        self.assertTrue(result)

    def test_non_list_architectures_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"architectures": "x86_64", "plans": ["/plans/smoke"]},  # string not list
        )
        self.assertFalse(result)

    def test_empty_string_in_architectures_list_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"architectures": ["x86_64", ""]},  # empty string in list
        )
        self.assertFalse(result)

    def test_non_list_tiers_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"tiers": "tier0"},  # string not list
        )
        self.assertFalse(result)

    def test_non_string_tier_item_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"tiers": [0]},  # integer in tiers list
        )
        self.assertFalse(result)

    def test_non_list_plans_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"plans": "/plans/smoke"},  # string not list
        )
        self.assertFalse(result)

    def test_non_dict_copr_api_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"copr_api": ["not", "a", "dict"]},
        )
        self.assertFalse(result)

    def test_non_dict_brew_api_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"brew_api": "string"},
        )
        self.assertFalse(result)

    def test_zero_parallel_limit_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"parallel_limit": 0},
        )
        self.assertFalse(result)

    def test_negative_parallel_limit_returns_false(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"parallel_limit": -1},
        )
        self.assertFalse(result)

    def test_positive_parallel_limit_returns_true(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {"parallel_limit": 5, "architectures": ["x86_64"]},
        )
        self.assertTrue(result)

    def test_all_valid_optional_dicts_accepted(self):
        po = self._po()
        result = po._validate_test_set_structure(
            "my-set",
            {
                "architectures": ["x86_64"],
                "tiers": ["tier0"],
                "plans": ["/plans/smoke"],
                "copr_api": {"build_references": []},
                "brew_api": {},
                "environment": {"KEY": "val"},
                "reportportal": {"url": "https://rp.example.com"},
                "context": {"distro": "rhel-9"},
                "parallel_limit": 10,
            },
        )
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# 4. _validate_test_sets_internal — set existence validation
# ---------------------------------------------------------------------------


class TestValidateTestSetsInternal(unittest.TestCase):

    def test_empty_list_returns_true(self):
        po = _make_partial_opts()
        self.assertTrue(po._validate_test_sets_internal([]))

    def test_existing_valid_set_returns_true(self):
        po = _make_partial_opts(config=SETS_CONFIG)
        self.assertTrue(po._validate_test_sets_internal(["alpha-set"]))

    def test_nonexistent_set_returns_false(self):
        po = _make_partial_opts(config=SETS_CONFIG)
        self.assertFalse(po._validate_test_sets_internal(["no-such-set"]))

    def test_missing_tests_section_returns_false(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        del cfg["tests"]
        po = _make_partial_opts(config=cfg)
        self.assertFalse(po._validate_test_sets_internal(["alpha-set"]))

    def test_missing_set_subsection_returns_false(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        # tests exists but has no "set" key
        po = _make_partial_opts(config=cfg)
        self.assertFalse(po._validate_test_sets_internal(["alpha-set"]))


# ---------------------------------------------------------------------------
# 5. Full 'test' action constructor — with sets, CentOS-Stream avoids network
# ---------------------------------------------------------------------------


class TestFullConstructorTestAction(unittest.TestCase):
    """Integration tests for ParsedOpts.__init__ with the 'test' action.

    CentOS-Stream-N sources are used throughout because they are parsed
    without any network call (no pin_compose look-up).
    """

    @patch("enge.utils.opt_manager.load_config")
    def test_test_action_with_set_constructs_individual_test_sets(self, mock_load):
        mock_load.return_value = copy.deepcopy(SETS_CONFIG)
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(len(ctx.individual_test_sets), 1)
        entry = ctx.individual_test_sets[0]
        self.assertEqual(entry["name"], "alpha-set")

    @patch("enge.utils.opt_manager.load_config")
    def test_individual_test_set_entry_keys(self, mock_load):
        mock_load.return_value = copy.deepcopy(SETS_CONFIG)
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        entry = ctx.individual_test_sets[0]
        for key in ("name", "config", "effective_values", "source_spec", "target_spec"):
            self.assertIn(
                key, entry, f"Missing key '{key}' in individual_test_sets entry"
            )

    @patch("enge.utils.opt_manager.load_config")
    def test_effective_values_contain_expected_fields(self, mock_load):
        mock_load.return_value = copy.deepcopy(SETS_CONFIG)
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        ev = ctx.individual_test_sets[0]["effective_values"]
        self.assertEqual(ev["source"], "CentOS-Stream-9")
        self.assertEqual(ev["architectures"], ["x86_64"])
        self.assertEqual(ev["plans"], ["/plans/smoke"])
        self.assertEqual(ev["git_ref"], "main")

    @patch("enge.utils.opt_manager.load_config")
    def test_source_spec_parsed_correctly_for_centos_stream(self, mock_load):
        mock_load.return_value = copy.deepcopy(SETS_CONFIG)
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        source_spec = ctx.individual_test_sets[0]["source_spec"]
        self.assertEqual(source_spec["major"], 9)
        self.assertTrue(source_spec["is_centos_stream"])
        self.assertEqual(source_spec["compose_name"], "CentOS-Stream-9")

    @patch("enge.utils.opt_manager.load_config")
    def test_self_plans_is_empty_when_using_sets(self, mock_load):
        # CHARACTERIZATION: ctx.plans is set from cli_plans or top-level
        # config plans only — set plans are NOT merged into ctx.plans.
        # Per-set plans live in individual_test_sets[i]["effective_values"]["plans"].
        mock_load.return_value = copy.deepcopy(SETS_CONFIG)
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(
            ctx.plans, []
        )  # ctx.plans is [] even though alpha-set has plans

    @patch("enge.utils.opt_manager.load_config")
    def test_multiple_sets_produce_multiple_entries(self, mock_load):
        mock_load.return_value = copy.deepcopy(SETS_CONFIG)
        cli = get_arguments(args=["test", "-S", "alpha-set", "-S", "beta-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(len(ctx.individual_test_sets), 2)
        names = [e["name"] for e in ctx.individual_test_sets]
        self.assertIn("alpha-set", names)
        self.assertIn("beta-set", names)

    @patch("enge.utils.opt_manager.load_config")
    def test_cli_arch_override_warns_when_different_from_set_arch(self, mock_load):
        # CHARACTERIZATION: when CLI --arch differs from the set's architectures,
        # a WARNING is logged with both values in sorted order.
        cfg = copy.deepcopy(SETS_CONFIG)
        # alpha-set has architectures=["x86_64"]; CLI provides aarch64
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "alpha-set", "--arch", "aarch64"])
        po = ParsedOpts(cli_args=cli)
        with self.assertLogs(
            "enge.utils.test_attribute_builder", level="WARNING"
        ) as log:
            AppContext.from_parsed_opts(po)
        warning_msgs = [
            m for m in log.output if "overrides" in m and "architectures" in m
        ]
        self.assertTrue(warning_msgs, "Expected arch override warning was not logged")

    @patch("enge.utils.opt_manager.load_config")
    def test_set_without_source_has_none_source_spec(self, mock_load):
        # A set with no source and no CLI source: source_spec = None.
        cfg = copy.deepcopy(SETS_CONFIG)
        # Add a set with no source
        cfg["tests"]["set"]["no-source-set"] = {
            "architectures": ["x86_64"],
            "plans": ["/plans/smoke"],
            "git_ref": "main",
        }
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "no-source-set"])
        # With no source, build_test_attributes raises ValidationError
        # "Source compose specification is required".
        po = ParsedOpts(cli_args=cli)
        with self.assertRaises(ValidationError):
            AppContext.from_parsed_opts(po)

    @patch("enge.utils.opt_manager.load_config")
    def test_test_action_no_sets_produces_empty_individual_test_sets(self, mock_load):
        # Without --set, individual_test_sets is set to [].
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["architectures"] = ["x86_64"]
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-s", "CentOS-Stream-9", "-T", "tier0"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(ctx.individual_test_sets, [])

    @patch("enge.utils.opt_manager.load_config")
    def test_parallel_limit_defaults_to_constant_when_not_configured(self, mock_load):
        from enge.utils.globals import PARALLEL_LIMIT_DEFAULT

        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["architectures"] = ["x86_64"]
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-s", "CentOS-Stream-9", "-T", "tier0"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(ctx.parallel_limit, PARALLEL_LIMIT_DEFAULT)


# ---------------------------------------------------------------------------
# 6. Tier resolution hierarchy — set-with-no-tier, CLI override, [tests].tiers
# ---------------------------------------------------------------------------


class TestTierResolution(unittest.TestCase):
    """Tests for the tier selection hierarchy: CLI > set > [tests].tiers.

    The [tests].tier mapping (filter definitions) must NEVER be used as the
    selection source; [tests].tiers is the list-typed default.
    """

    @patch("enge.utils.opt_manager.load_config")
    def test_set_with_no_tier_resolves_to_tests_tiers_default(self, mock_load):
        # Pin #10 flipped: set-with-no-tier no longer crashes with KeyError: 0.
        # It resolves to [tests].tiers from the config (the default catch-all).
        cfg = copy.deepcopy(SETS_CONFIG)
        # alpha-set has no 'tiers' key; [tests].tiers = ['tier3'] provides the default.
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        ev = ctx.individual_test_sets[0]["effective_values"]
        self.assertEqual(ev["tiers"], ["tier3"])

    @patch("enge.utils.opt_manager.load_config")
    def test_cli_tier_overrides_tests_tiers_default(self, mock_load):
        cfg = copy.deepcopy(SETS_CONFIG)
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "alpha-set", "-T", "tier0"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        ev = ctx.individual_test_sets[0]["effective_values"]
        self.assertEqual(ev["tiers"], ["tier0"])

    @patch("enge.utils.opt_manager.load_config")
    def test_set_tiers_override_tests_tiers_default(self, mock_load):
        cfg = copy.deepcopy(SETS_CONFIG)
        cfg["tests"]["set"]["alpha-set"]["tiers"] = ["tier1"]
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        ctx = AppContext.from_parsed_opts(po)
        ev = ctx.individual_test_sets[0]["effective_values"]
        self.assertEqual(ev["tiers"], ["tier1"])

    @patch("enge.utils.opt_manager.load_config")
    def test_tests_tier_mapping_not_used_for_tier_selection(self, mock_load):
        # When [tests].tiers is absent and no CLI/set tier is given,
        # ConfigurationError is raised rather than silently falling back to
        # the [tests].tier mapping dict (which would cause KeyError: 0 on
        # subsequent index access).
        cfg = copy.deepcopy(SETS_CONFIG)
        cfg["tests"].pop("tiers", None)
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        with self.assertRaises(ConfigurationError):
            AppContext.from_parsed_opts(po)

    def test_no_tiers_anywhere_raises_configuration_error(self):
        # Direct unit test: resolve_effective_values raises ConfigurationError
        # when all three tier sources (CLI, set, [tests].tiers) are absent.
        from enge.utils.source_target_parser import resolve_effective_values

        cfg = copy.deepcopy(MINIMAL_CONFIG)  # has no [tests].tiers
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        with self.assertRaises(ConfigurationError) as ctx:
            resolve_effective_values(cli, {}, cfg)
        self.assertIn("tiers", str(ctx.exception).lower())

    @patch("enge.utils.opt_manager.load_config")
    def test_tier_fallback_log_emitted_exactly_once(self, mock_load):
        # The INFO fallback message for tiers-from-[tests].tiers must appear
        # exactly once through the full init path.  build_test_attributes
        # (called by AppContext.from_parsed_opts) delegates to
        # resolve_effective_values which emits the INFO line once.
        cfg = copy.deepcopy(SETS_CONFIG)
        mock_load.return_value = cfg
        cli = get_arguments(args=["test", "-S", "alpha-set"])
        po = ParsedOpts(cli_args=cli)
        with self.assertLogs("enge.utils.source_target_parser", level="INFO") as log:
            AppContext.from_parsed_opts(po)
        fallback_msgs = [m for m in log.output if "using default from [tests]" in m]
        self.assertEqual(len(fallback_msgs), 1)

    def test_tiers_shape_validation_wrong_type_raises(self):
        # [tests].tiers must be a list.  A string value must fail with
        # ConfigurationError naming both 'tiers' and 'tier'.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["tiers"] = "tier3"  # string instead of list
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_static_configuration()
        msg = str(ctx.exception)
        self.assertIn("tiers", msg)

    def test_tier_mapping_wrong_type_raises(self):
        # [tests].tier must be a table (dict).  A list value must fail with
        # ConfigurationError naming both keys.
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["tests"]["tier"] = ["tier0", "tier1"]  # list instead of dict
        po = _make_partial_opts(config=cfg)
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_static_configuration()
        msg = str(ctx.exception)
        self.assertIn("tier", msg)


# ---------------------------------------------------------------------------
# 7. Bundled default configuration — ships with correct defaults
# ---------------------------------------------------------------------------


class TestBundledDefaultConfig(unittest.TestCase):
    def test_bundled_default_config_has_tiers(self):
        """Pins the shipped default — the crash guard for tier resolution."""
        import tomllib
        from importlib.resources import files
        from pathlib import Path

        try:
            bundled = Path(files("enge.utils") / "enge_default_config.toml")
        except (TypeError, ValueError, ModuleNotFoundError):
            bundled = (
                Path(__file__).parent.parent
                / "src"
                / "enge"
                / "utils"
                / "enge_default_config.toml"
            )
        with open(bundled, "rb") as f:
            config = tomllib.load(f)
        self.assertEqual(config["tests"]["tiers"], ["tier3"])


if __name__ == "__main__":
    unittest.main()
