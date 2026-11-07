"""Set isolation for build references, config/CLI to payload (L81).

Rulings: Q-ART-3 (a set is an isolated, fully configured object; only its
preset fills in-set-undefined values; a set's option group replaces the
preset's), Q-ART-4 (CLI references apply to every scheduled set, with a
warning), Q-ART-6 (run-level ``[copr_api]``/``[brew_api]``
``build_references`` do not apply to sets, with a warning), Q-ART-7 (the
warnings' exact conditions) and XR-artifacts-2 (``INSTALL_LEAPP_FROM_COMPOSE``
follows the references actually sent).

The payload tests drive the real chain: ``get_arguments`` ->
``build_test_attributes`` -> ``AppContext`` -> ``expand_set_requests`` ->
``process_request_spec`` (dry run). Only the build lookup is stubbed: the
resolver turns every reference it is handed into one build of its family,
so a payload's artifacts name exactly the references its request resolved.
"""

import copy
import logging
import unittest
from unittest.mock import MagicMock, patch

from enge.dispatch.set_flow import (
    RequestSpec,
    _build_request_context,
    _resolve_spec_artifact_refs,
    expand_set_requests,
    process_request_spec,
)
from enge.utils.app_context import AppContext
from enge.utils.arg_parser import get_arguments
from enge.utils.globals import ARTIFACT_MAPPING
from enge.utils.test_attribute_builder import build_test_attributes

SET_FLOW_LOGGER = "enge.dispatch.set_flow"

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
        "tier": {"tier0": "tag:tier[0]"},
        "preset": {
            "copr-pr7": {"copr_api": {"build_references": ["lp:pr7"]}},
        },
        "set": {
            "alma": {
                "source": "8.10",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
                "copr_api": {"build_references": ["lp:main"]},
            },
            "rhel": {
                "source": "8.10",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
                "brew_api": {"build_references": ["leapp-repository-0.23.0-1.el8"]},
            },
            "plain": {
                "source": "8.10",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
            },
            # Skipped by expand_set_requests (no architectures): never scheduled.
            "broken": {
                "source": "8.10",
                "tiers": ["tier0"],
                "copr_api": {"build_references": ["lp:old"]},
            },
            "child": {
                "extends": "copr-pr7",
                "source": "8.10",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
            },
            "override": {
                "extends": "copr-pr7",
                "source": "8.10",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
                "copr_api": {"build_references": ["lp:pr8"]},
            },
        },
    },
    "copr_api": {"package": "", "build_references": []},
    "brew_api": {"session_url": "", "taskid_url": ""},
    "reportportal": {},
}

COPR_TYPE = ARTIFACT_MAPPING["copr"]
BREW_TYPE = ARTIFACT_MAPPING["brew"]


def _config(copr_run_refs=None, brew_run_refs=None):
    cfg = copy.deepcopy(CONFIG)
    if copr_run_refs is not None:
        cfg["copr_api"]["build_references"] = copr_run_refs
    if brew_run_refs is not None:
        cfg["brew_api"]["build_references"] = brew_run_refs
    return cfg


def _ctx(argv, cfg):
    cli_args = get_arguments(args=["test", "--dry-run", *argv])
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


class EchoResolver:
    """One build per reference handed in; COPR first, then Brew."""

    def resolve_builds(self, compose_name, ctx):
        builds = [{"compose": compose_name, "distro": "rhel-8.10", "build_id": None}]
        for family, refs in (
            (COPR_TYPE, ctx.copr_references),
            (BREW_TYPE, ctx.brew_references),
        ):
            for ref in refs or []:
                builds.append(
                    {
                        "compose": compose_name,
                        "distro": "rhel-8.10",
                        "build_id": str(ref),
                        "type": family,
                        "packages": [str(ref)],
                        "nvr": None,
                    }
                )
        return builds


def _payloads(ctx):
    """{set name: (artifacts [(id, type)], environment variables)}."""
    out = {}
    specs = expand_set_requests(ctx=ctx)
    for idx, spec in enumerate(specs, 1):
        result = process_request_spec(idx, len(specs), spec, EchoResolver(), ctx=ctx)
        env = result["payload"]["environments"][0]
        out[spec.set_name] = (
            [(a["id"], a["type"]) for a in env.get("artifacts", [])],
            env.get("variables", {}),
        )
    return out


class TestSetReferencePayloads(unittest.TestCase):
    """What each set's request actually carries."""

    def test_set_without_a_family_does_not_inherit_the_first_sets_references(self):
        payloads = _payloads(_ctx(["-S", "alma", "-S", "rhel"], _config()))
        self.assertEqual(payloads["alma"][0], [("lp:main", COPR_TYPE)])
        self.assertEqual(
            payloads["rhel"][0], [("leapp-repository-0.23.0-1.el8", BREW_TYPE)]
        )

    def test_set_without_references_sends_none_after_a_set_with_references(self):
        payloads = _payloads(_ctx(["-S", "alma", "-S", "plain"], _config()))
        self.assertEqual(payloads["alma"][0], [("lp:main", COPR_TYPE)])
        self.assertEqual(payloads["plain"][0], [])
        self.assertEqual(payloads["plain"][1]["INSTALL_LEAPP_FROM_COMPOSE"], "yes")

    def test_run_level_references_do_not_reach_sets(self):
        cfg = _config(copr_run_refs=["lp:run"], brew_run_refs=["leapp-9.9-1.el8"])
        payloads = _payloads(_ctx(["-S", "rhel", "-S", "plain"], cfg))
        self.assertEqual(
            payloads["rhel"][0], [("leapp-repository-0.23.0-1.el8", BREW_TYPE)]
        )
        self.assertEqual(payloads["plain"][0], [])
        self.assertEqual(payloads["plain"][1]["INSTALL_LEAPP_FROM_COMPOSE"], "yes")

    def test_preset_fills_a_family_the_set_does_not_define(self):
        payloads = _payloads(_ctx(["-S", "child"], _config()))
        self.assertEqual(payloads["child"][0], [("lp:pr7", COPR_TYPE)])

    def test_set_family_replaces_the_presets_family(self):
        payloads = _payloads(_ctx(["-S", "override"], _config()))
        self.assertEqual(payloads["override"][0], [("lp:pr8", COPR_TYPE)])

    def test_cli_family_reaches_every_set_overriding_or_adding(self):
        payloads = _payloads(
            _ctx(["--copr", "lp:pr9", "-S", "alma", "-S", "rhel"], _config())
        )
        self.assertEqual(payloads["alma"][0], [("lp:pr9", COPR_TYPE)])
        self.assertEqual(
            payloads["rhel"][0],
            [("lp:pr9", COPR_TYPE), ("leapp-repository-0.23.0-1.el8", BREW_TYPE)],
        )


def _non_set_spec():
    return RequestSpec(
        set_name=None,
        tier="tier0",
        plan=None,
        arch="x86_64",
        source_spec={"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0-Nightly"},
        target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0-Nightly"},
        upgrade_path="rhel-8.10-to-9.4",
        effective_values={},
    )


class TestNonSetReferences(unittest.TestCase):
    """Non-set dispatch keeps the run-level references (Q-ART-6)."""

    def test_non_set_dispatch_still_uses_run_level_references(self):
        ctx = _ctx(
            ["-s", "8.10", "-T", "tier0", "--arch", "x86_64"],
            _config(copr_run_refs=["lp:run"]),
        )
        refs = _resolve_spec_artifact_refs(_non_set_spec(), ctx)
        self.assertEqual(refs["copr_references"], ["lp:run"])

    def test_non_set_run_level_references_mark_leapp_as_not_from_compose(self):
        ctx = _ctx(
            ["-s", "8.10", "-T", "tier0", "--arch", "x86_64"],
            _config(copr_run_refs=["lp:run"]),
        )
        req_ctx = _build_request_context(_non_set_spec(), None, ctx)
        self.assertEqual(req_ctx.auto_env_vars["INSTALL_LEAPP_FROM_COMPOSE"], "no")


class TestReferenceWarnings(unittest.TestCase):
    """warn_on_reference_inheritance: once per invocation, WARNING only."""

    def _warnings(self, argv, cfg):
        from enge.dispatch.set_flow import warn_on_reference_inheritance

        ctx = _ctx(argv, cfg)
        with self.assertLogs(SET_FLOW_LOGGER, level="DEBUG") as logs:
            warn_on_reference_inheritance(ctx)
            logging.getLogger(SET_FLOW_LOGGER).debug("sentinel")
        records = [r for r in logs.records if r.getMessage() != "sentinel"]
        self.assertTrue(all(r.levelno == logging.WARNING for r in records))
        return [r.getMessage() for r in records]

    W1_COPR = (
        "All scheduled sets will inherit command-line defined COPR builds "
        "(--copr), in-set definitions might be overwritten and scheduled "
        "runs might be polluted"
    )

    def test_run_level_references_warn_under_set_dispatch(self):
        cfg = _config(copr_run_refs=["lp:run"], brew_run_refs=["leapp-9.9-1.el8"])
        self.assertEqual(
            self._warnings(["-S", "plain"], cfg),
            [
                "[copr_api].build_references is ignored for set dispatch; "
                "define references in the set or its preset, or pass --copr",
                "[brew_api].build_references is ignored for set dispatch; "
                "define references in the set or its preset, or pass --brew",
            ],
        )

    def test_cli_family_warns_and_names_sets_it_replaces(self):
        self.assertEqual(
            self._warnings(
                ["--copr", "lp:pr9", "-S", "alma", "-S", "child"], _config()
            ),
            [
                self.W1_COPR,
                "--copr replaces COPR references defined in set(s): alma, child",
            ],
        )

    def test_cli_family_over_other_family_warns_without_naming_sets(self):
        self.assertEqual(
            self._warnings(["--copr", "lp:pr9", "-S", "rhel"], _config()),
            [self.W1_COPR],
        )

    def test_cli_family_over_sets_without_references_is_silent(self):
        ctx = _ctx(["--copr", "lp:pr9", "-S", "plain"], _config())
        # Premise: the CLI family is set and a set is scheduled.
        self.assertTrue(ctx.cli_args.copr)
        self.assertEqual([s["name"] for s in ctx.individual_test_sets], ["plain"])
        from enge.dispatch.set_flow import warn_on_reference_inheritance

        with self.assertNoLogs(SET_FLOW_LOGGER, level="WARNING"):
            warn_on_reference_inheritance(ctx)

    def test_non_set_cli_replacing_run_level_references_warns(self):
        self.assertEqual(
            self._warnings(
                ["--copr", "lp:pr9", "-s", "8.10", "-T", "tier0", "--arch", "x86_64"],
                _config(copr_run_refs=["lp:run"]),
            ),
            ["--copr replaces [copr_api].build_references"],
        )

    def test_non_set_run_level_references_without_cli_are_silent(self):
        ctx = _ctx(
            ["-s", "8.10", "-T", "tier0", "--arch", "x86_64"],
            _config(copr_run_refs=["lp:run"]),
        )
        # Premise: run-level references exist and no set is scheduled.
        self.assertEqual(ctx.copr_api["build_references"], ["lp:run"])
        self.assertFalse(ctx.individual_test_sets)
        from enge.dispatch.set_flow import warn_on_reference_inheritance

        with self.assertNoLogs(SET_FLOW_LOGGER, level="WARNING"):
            warn_on_reference_inheritance(ctx)


class TestSubmitDispatchWarnsOnce(unittest.TestCase):
    def test_submit_dispatch_calls_the_warning_pass_once(self):
        from enge.dispatch import __main__ as dispatch_main

        ctx = _ctx(["-S", "plain"], _config())
        with (
            patch.object(dispatch_main, "warn_on_reference_inheritance") as warn,
            patch.object(dispatch_main, "expand_set_requests", return_value=[]),
        ):
            dispatch_main.submit_dispatch(ctx)
        warn.assert_called_once_with(ctx, set())

    def test_non_set_submit_dispatch_calls_the_warning_pass_once(self):
        from enge.dispatch import __main__ as dispatch_main

        ctx = _ctx(
            ["--copr", "lp:pr9", "-s", "8.10", "-T", "tier0", "--arch", "x86_64"],
            _config(copr_run_refs=["lp:run"]),
        )
        # Premise: no set is selected, so this is non-set dispatch.
        self.assertFalse(ctx.individual_test_sets)
        with (
            patch.object(dispatch_main, "warn_on_reference_inheritance") as warn,
            patch.object(dispatch_main, "setup_submit_test"),
            patch.object(dispatch_main, "validate_plan_filters"),
            patch.object(dispatch_main, "build_tier_plan_specs", return_value=[]),
        ):
            dispatch_main.submit_dispatch(ctx)
        warn.assert_called_once_with(ctx)


class TestWarningsFollowTheSchedule(unittest.TestCase):
    """XR-l81-1: W1/W2 name only sets that are actually scheduled; a set
    that expand_set_requests skips never counts, and skipping every set
    does not turn the run into non-set dispatch."""

    def _dispatch_warnings(self, argv, cfg):
        from enge.dispatch import __main__ as dispatch_main

        ctx = _ctx(argv, cfg)
        with (
            patch.object(dispatch_main, "ArtifactResolver", EchoResolver),
            patch.object(dispatch_main, "_build_manifest_writer", return_value=None),
            self.assertLogs(SET_FLOW_LOGGER, level="WARNING") as logs,
        ):
            logging.getLogger(SET_FLOW_LOGGER).warning("sentinel")
            dispatch_main.submit_dispatch(ctx)
        return [
            r.getMessage()
            for r in logs.records
            if r.levelno == logging.WARNING and r.getMessage() != "sentinel"
        ]

    def test_a_skipped_set_is_never_named(self):
        self.assertEqual(
            self._dispatch_warnings(
                ["--copr", "lp:pr9", "-S", "alma", "-S", "broken"], _config()
            ),
            [
                TestReferenceWarnings.W1_COPR,
                "--copr replaces COPR references defined in set(s): alma",
            ],
        )

    def test_only_skipped_sets_define_references(self):
        warnings = self._dispatch_warnings(
            ["--copr", "lp:pr9", "-S", "plain", "-S", "broken"], _config()
        )
        # Premise: the skipped set does define COPR references.
        self.assertEqual(
            CONFIG["tests"]["set"]["broken"]["copr_api"]["build_references"],
            ["lp:old"],
        )
        self.assertEqual(warnings, [])

    def test_every_set_skipped_is_still_set_dispatch(self):
        """The builder rejects a first set without architectures, so this
        is driven directly: an empty schedule must not fall back to the
        non-set W2."""
        from enge.dispatch.set_flow import warn_on_reference_inheritance

        ctx = _ctx(
            ["--copr", "lp:pr9", "-S", "plain"], _config(copr_run_refs=["lp:run"])
        )
        with self.assertLogs(SET_FLOW_LOGGER, level="WARNING") as logs:
            warn_on_reference_inheritance(ctx, set())
        self.assertEqual(
            [r.getMessage() for r in logs.records],
            [
                "[copr_api].build_references is ignored for set dispatch; "
                "define references in the set or its preset, or pass --copr",
            ],
        )


if __name__ == "__main__":
    unittest.main()
