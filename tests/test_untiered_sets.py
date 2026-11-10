"""Untiered test sets: ``tiers = []`` (L12b).

Rulings: Q-T9 (an explicit ``tiers = []`` in a set, or in its preset when
the set leaves ``tiers`` undefined, means untiered; an absent key keeps
falling back to ``[tests].tiers``), Q-T8' (an untiered set must narrow plan
selection itself: plans from the CLI, the set or ``[tests]``, or a plan
filter from the CLI or the set; otherwise ConfigurationError, exit 99),
Q-T10 (no special casing downstream: ``tier`` is null), Q-T11 (one VERBOSE
line per untiered set, never a WARNING) and 2026-08-31 L12b (one
``tier=None`` iteration per architecture/plan; the RP module is untouched).

The payload tests drive the real chain: ``get_arguments`` ->
``build_test_attributes`` -> ``AppContext`` -> ``expand_set_requests`` ->
``process_request_spec`` (dry run). Only the build lookup is stubbed.
"""

import copy
import logging
import unittest
from unittest.mock import MagicMock

from enge.dispatch.set_flow import expand_set_requests, process_request_spec
from enge.utils.app_context import AppContext
from enge.utils.arg_parser import get_arguments
from enge.utils.errors import ConfigurationError
from enge.utils.globals import VERBOSE
from enge.utils.source_target_parser import resolve_effective_values
from enge.utils.test_attribute_builder import _resolve_preset, build_test_attributes

from tests._helpers import captured_logs, matching

SET_FLOW_LOGGER = "enge.dispatch.set_flow"
VERIFICATION = "/plans/verification($|/)"
UNTIERED_LINE = "is untiered (tiers = [])"

CONFIG = {
    "testing_farm": {
        "api_key": "test-tf-key",
        "cloud_resources_tag": "test-biz-tag",
        "api_endpoint_url": "https://api.example.tf",
        "log_artifact_baseurl": "https://logs.example.tf",
        # Empty: target derivation stays offline (RHEL-<x.y>.0-Nightly).
        "composes_prod_url": "",
    },
    "project": {"name": "leapp", "repo_url": "https://github.com/oamg/leapp"},
    "common": {},
    "composes": {"target_map": {"8.10": "9.4"}},
    "tests": {
        "git_url": "https://github.com/oamg/leapp",
        "git_ref": "main",
        # The bundled default: an absent `tiers` falls back to this.
        "tiers": ["tier3"],
        "tier": {"tier0": "tag:tier[0]", "tier3": "tag:tier[0123]"},
        "preset": {
            "tiered": {"tiers": ["tier0"], "architectures": ["x86_64"]},
            "untiered": {"tiers": [], "plans": [VERIFICATION]},
        },
        "set": {
            "verify": {
                "source": "8.10",
                "tiers": [],
                "plans": [VERIFICATION],
                "architectures": ["x86_64"],
            },
            "verify-arches": {
                "source": "8.10",
                "tiers": [],
                "plans": [VERIFICATION],
                "architectures": ["x86_64", "aarch64"],
            },
            "verify-two-plans": {
                "source": "8.10",
                "tiers": [],
                "plans": ["/plans/verification/a", "/plans/verification/b"],
                "architectures": ["x86_64"],
            },
            "verify-filter": {
                "source": "8.10",
                "tiers": [],
                "plan_filter": "tag:nondestructive & enabled:true",
                "architectures": ["x86_64"],
            },
            "bare": {
                "source": "8.10",
                "tiers": [],
                "architectures": ["x86_64"],
            },
            "absent": {
                "source": "8.10",
                "architectures": ["x86_64"],
            },
            "tier0": {
                "source": "8.10",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
            },
            "child-overrides": {
                "extends": "tiered",
                "source": "8.10",
                "tiers": [],
                "plans": [VERIFICATION],
            },
            "child-inherits": {
                "extends": "untiered",
                "source": "8.10",
                "architectures": ["x86_64"],
            },
        },
    },
    "copr_api": {"package": "", "build_references": []},
    "brew_api": {"session_url": "", "taskid_url": ""},
    "reportportal": {},
}


def _cli(argv=()):
    return get_arguments(args=["test", "--dry-run", *argv])


def _resolve(set_name, argv=(), cfg=None):
    """Preset-aware resolution of one set, as build_test_attributes does it."""
    cfg = cfg or copy.deepcopy(CONFIG)
    set_config = _resolve_preset(cfg["tests"]["set"][set_name], cfg, set_name=set_name)
    return resolve_effective_values(_cli(argv), set_config, cfg)


def _set_tiers(argv, cfg=None):
    """The effective tiers build_test_attributes records for the first set."""
    attrs = build_test_attributes(_cli(argv), cfg or copy.deepcopy(CONFIG))
    return attrs["individual_test_sets"][0]["effective_values"]["tiers"]


def _ctx(argv, cfg=None):
    cfg = cfg or copy.deepcopy(CONFIG)
    cli_args = _cli(argv)
    endpoint = MagicMock(
        api_endpoint_url=cfg["testing_farm"]["api_endpoint_url"],
        log_artifact_baseurl=cfg["testing_farm"]["log_artifact_baseurl"],
    )
    return AppContext(
        cli_args=cli_args,
        config=cfg,
        testing_farm_endpoint=endpoint,
        **build_test_attributes(cli_args, cfg),
    )


class ComposeOnlyResolver:
    """No artifacts: the compose alone, as a set without references gets."""

    def resolve_builds(self, compose_name, ctx):
        return [{"compose": compose_name, "distro": "rhel-8.10", "build_id": None}]


def _dispatch(argv, cfg=None):
    """[(spec, dry-run result)] for every scheduled request."""
    ctx = _ctx(argv, cfg)
    specs = expand_set_requests(ctx=ctx)
    resolver = ComposeOnlyResolver()
    results = []
    for i, spec in enumerate(specs, 1):
        result = process_request_spec(i, len(specs), spec, resolver, ctx=ctx)
        results.append((spec, result))
    return results


class TestTierResolution(unittest.TestCase):
    """resolve_effective_values: which tiers a set ends up with (Q-T9)."""

    def test_explicit_empty_tiers_resolve_to_no_tiers(self):
        self.assertEqual(_resolve("verify")["tiers"], [])

    def test_empty_tiers_override_the_presets_tiers(self):
        self.assertEqual(_resolve("child-overrides")["tiers"], [])

    def test_empty_tiers_inherited_from_the_preset_resolve_to_no_tiers(self):
        self.assertEqual(_resolve("child-inherits")["tiers"], [])

    def test_absent_tiers_still_fall_back_to_the_tests_default(self):
        self.assertEqual(_resolve("absent")["tiers"], ["tier3"])

    def test_listed_tiers_are_unchanged(self):
        self.assertEqual(_resolve("tier0")["tiers"], ["tier0"])

    def test_cli_tier_still_wins_over_empty_tiers(self):
        self.assertEqual(_resolve("bare", ["-T", "tier0"])["tiers"], ["tier0"])

    def test_non_set_resolution_still_falls_back_to_the_tests_default(self):
        cfg = copy.deepcopy(CONFIG)
        self.assertEqual(resolve_effective_values(_cli(), {}, cfg)["tiers"], ["tier3"])


class TestUntieredSetMustNarrowPlans(unittest.TestCase):
    """Q-T8': plans or a plan filter, from anywhere, or exit 99."""

    def test_untiered_set_without_plans_or_plan_filter_is_a_config_error(self):
        with self.assertRaises(ConfigurationError) as caught:
            _set_tiers(["-S", "bare"])
        message = str(caught.exception)
        self.assertIn("'bare'", message)
        self.assertIn("tiers = []", message)

    def test_set_plans_are_enough(self):
        self.assertEqual(_set_tiers(["-S", "verify"]), [])

    def test_set_plan_filter_alone_is_enough(self):
        self.assertEqual(_set_tiers(["-S", "verify-filter"]), [])

    def test_cli_plan_filter_alone_is_enough(self):
        argv = ["-S", "bare", "--plan-filter", "tag:nondestructive"]
        self.assertEqual(_set_tiers(argv), [])

    def test_cli_plan_alone_is_enough(self):
        self.assertEqual(_set_tiers(["-S", "bare", "--plan", VERIFICATION]), [])

    def test_tests_section_plans_are_enough(self):
        cfg = copy.deepcopy(CONFIG)
        cfg["tests"]["plans"] = [VERIFICATION]
        self.assertEqual(_set_tiers(["-S", "bare"], cfg), [])

    def test_preset_plans_are_enough(self):
        self.assertEqual(_set_tiers(["-S", "child-inherits"]), [])

    def test_cli_tier_needs_no_plans(self):
        self.assertEqual(_set_tiers(["-S", "bare", "-T", "tier0"]), ["tier0"])

    def test_a_tiered_set_needs_no_plans(self):
        self.assertEqual(_set_tiers(["-S", "tier0"]), ["tier0"])

    def test_every_selected_set_is_checked_not_only_the_first(self):
        with self.assertRaises(ConfigurationError) as caught:
            _set_tiers(["-S", "verify", "-S", "bare"])
        self.assertIn("'bare'", str(caught.exception))


def _with_plans(set_name, plans, **extra):
    cfg = copy.deepcopy(CONFIG)
    cfg["tests"]["set"][set_name]["plans"] = plans
    cfg["tests"]["set"][set_name].update(extra)
    return cfg


class TestUntieredSetRejectsEmptyPlans(unittest.TestCase):
    """XR-1: an empty plan entry sends no plan name, so it selects every plan."""

    def _assert_rejected(self, argv, cfg):
        with self.assertRaises(ConfigurationError) as caught:
            _set_tiers(argv, cfg)
        message = str(caught.exception)
        self.assertIn("'bare'", message)
        self.assertIn("empty plan", message)

    def test_an_empty_plan_is_a_config_error(self):
        self._assert_rejected(["-S", "bare"], _with_plans("bare", [""]))

    def test_an_empty_plan_next_to_a_real_one_is_a_config_error(self):
        self._assert_rejected(["-S", "bare"], _with_plans("bare", [VERIFICATION, ""]))

    def test_a_blank_plan_is_a_config_error(self):
        self._assert_rejected(["-S", "bare"], _with_plans("bare", ["  "]))

    def test_an_empty_cli_plan_is_a_config_error(self):
        self._assert_rejected(["-S", "bare", "--plan", ""], copy.deepcopy(CONFIG))

    def test_a_plan_filter_does_not_excuse_an_empty_plan(self):
        cfg = _with_plans("bare", [""], plan_filter="tag:nondestructive")
        self._assert_rejected(["-S", "bare"], cfg)

    def test_a_tiered_set_keeps_its_empty_plan_entry(self):
        # Out of scope (ruled 2026-11-10): tiered sets are unchanged.
        cfg = _with_plans("tier0", [""])
        specs = expand_set_requests(ctx=_ctx(["-S", "tier0"], cfg))
        self.assertEqual([(s.tier, s.plan) for s in specs], [("tier0", "")])


class TestUntieredExpansion(unittest.TestCase):
    """expand_set_requests: one tier=None iteration (2026-08-31, Q-T11)."""

    def test_one_request_per_arch_and_plan_with_no_tier(self):
        with captured_logs(SET_FLOW_LOGGER) as records:
            specs = expand_set_requests(ctx=_ctx(["-S", "verify-two-plans"]))
        self.assertEqual(
            [(s.set_name, s.tier, s.plan, s.arch) for s in specs],
            [
                ("verify-two-plans", None, "/plans/verification/a", "x86_64"),
                ("verify-two-plans", None, "/plans/verification/b", "x86_64"),
            ],
        )
        self.assertEqual(matching(records, "No tiers", level=None), [])

    def test_each_architecture_gets_a_request(self):
        specs = expand_set_requests(ctx=_ctx(["-S", "verify-arches"]))
        self.assertEqual(
            sorted((s.tier, s.plan, s.arch) for s in specs),
            [(None, VERIFICATION, "aarch64"), (None, VERIFICATION, "x86_64")],
        )

    def test_untiered_set_logs_one_verbose_line_naming_it(self):
        with captured_logs(SET_FLOW_LOGGER) as records:
            expand_set_requests(ctx=_ctx(["-S", "verify"]))
        lines = matching(records, UNTIERED_LINE, level=VERBOSE)
        self.assertEqual(len(lines), 1)
        self.assertIn("'verify'", lines[0])
        # Never louder than VERBOSE.
        self.assertEqual(matching(records, UNTIERED_LINE, level=None), lines)
        self.assertEqual([r for r in records if r.levelno >= logging.WARNING], [])

    def test_tiered_set_logs_no_untiered_line(self):
        with captured_logs(SET_FLOW_LOGGER) as records:
            specs = expand_set_requests(ctx=_ctx(["-S", "tier0"]))
        # Premise: the set was scheduled at all.
        self.assertEqual([s.tier for s in specs], ["tier0"])
        self.assertEqual(matching(records, UNTIERED_LINE, level=None), [])

    def test_untiered_and_tiered_sets_in_one_run(self):
        specs = expand_set_requests(ctx=_ctx(["-S", "tier0", "-S", "verify-filter"]))
        self.assertEqual(
            [(s.set_name, s.tier, s.plan) for s in specs],
            [("tier0", "tier0", None), ("verify-filter", None, None)],
        )


class TestUntieredPayload(unittest.TestCase):
    """What an untiered request sends (Q-T10: no tier anywhere)."""

    def test_payload_selects_by_plan_name_without_a_tier_filter(self):
        ((spec, result),) = _dispatch(["-S", "verify"])
        fmf = result["payload"]["test"]["fmf"]
        self.assertEqual(fmf["name"], VERIFICATION)
        self.assertEqual(fmf["plan_filter"], "tag:8to9 & enabled:true")
        self.assertNotIn("tier", fmf["plan_filter"])

    def test_payload_tmt_context_carries_no_tier(self):
        ((spec, result),) = _dispatch(["-S", "verify"])
        context = result["payload"]["environments"][0]["tmt"]["context"]
        # Premise: the context was built at all.
        self.assertEqual(context["distro"], "rhel-8.10")
        self.assertNotIn("tier", context)

    def test_result_records_a_null_tier(self):
        ((spec, result),) = _dispatch(["-S", "verify"])
        self.assertIsNone(spec.tier)
        self.assertIn("tier", result)
        self.assertIsNone(result["tier"])

    def test_set_plan_filter_is_sent_verbatim(self):
        ((spec, result),) = _dispatch(["-S", "verify-filter"])
        fmf = result["payload"]["test"]["fmf"]
        self.assertEqual(fmf["plan_filter"], "tag:nondestructive & enabled:true")
        self.assertIsNone(fmf["name"])
        self.assertNotIn("tier", result["payload"]["environments"][0]["tmt"]["context"])


if __name__ == "__main__":
    unittest.main()
