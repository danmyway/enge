"""Golden test for multi-set dispatch with per-set artifact references.

Captures the exact payload structure produced when two test sets define
different brew_api.build_references, ensuring each payload carries its
own set's artifact configuration.

Stubs (same pattern as test_dispatch_golden.py):
  - _pin_compose_with_fallback: avoids HTTP to compose service
  - ArtifactResolver.resolve_builds: avoids Brew resolution; returns
    per-set build info keyed by the spec_ctx.brew_references it receives
  - SubmitTest receives ctx directly (no singleton needed)
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from enge.dispatch.set_flow import expand_set_requests, process_request_spec

GOLDEN_FILE = os.path.join(
    os.path.dirname(__file__), "fixtures", "dispatch_golden_multiset.json"
)

PINNED_COMPOSE = "RHEL-8.10.0-20240101.0"

CONFIG = {
    "testing_farm": {
        "api_key": "test-multiset-key",
        "api_endpoint_url": "https://api.tf.example/v0.1/requests",
        "log_artifact_baseurl": "https://artifacts.tf.example",
        "cloud_resources_tag": "multiset-unit",
        "composes_prod_url": "https://composes.example",
    },
    "tests": {
        "git_url": "https://github.com/oamg/leapp",
        "git_ref": "main",
        "tiers": ["tier0"],
        "tier": {
            "tier0": "tag:tier[0]",
        },
        "set": {
            "alpha": {
                "source": "8.10",
                "target": "9.4",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
                "git_ref": "main",
                "brew_api": {
                    "package": "leapp",
                    "build_references": ["pkg-alpha-1.0"],
                },
            },
            "beta": {
                "source": "8.10",
                "target": "9.4",
                "tiers": ["tier0"],
                "architectures": ["x86_64"],
                "git_ref": "main",
                "brew_api": {
                    "package": "leapp",
                    "build_references": ["pkg-beta-2.0"],
                },
            },
        },
    },
    "project": {
        "name": "leapp",
        "owner": "oamg",
        "repo_url": "https://github.com/oamg/leapp",
    },
    "common": {
        "archive_tasks_latest": "/tmp/enge_multiset_test_latest",
        "archive_tasks_default": "/tmp/enge_multiset_test_archive/",
    },
    "copr_api": {},
    "brew_api": {},
    "reportportal": {},
}


def _build_cli_args():
    return SimpleNamespace(
        action="test",
        set=["alpha", "beta"],
        dryrun=True,
        output_format="terminal",
        source=None,
        target=None,
        architectures=None,
        tier=None,
        event=None,
        environment=None,
        context=None,
        copr=None,
        brew=None,
        plan=None,
        planfilter=None,
        testfilter=None,
        test=None,
        git_url=None,
        git_ref=None,
        rp_launch=None,
        rp_description=None,
        parallel_limit=None,
        pool=None,
        auto_tag=False,
        set_tag=None,
        only_rhsm_mock_cdn=False,
        no_rhsm=False,
        only_rhsm_stage_cdn=False,
        wait=False,
        config=None,
    )


def _build_resolved_opts(cli_args, individual_test_sets):
    mock = MagicMock()
    mock.individual_test_sets = individual_test_sets
    mock.testing_farm = CONFIG["testing_farm"]
    mock.tests = CONFIG["tests"]
    mock.project = CONFIG["project"]
    mock.config = CONFIG
    mock.cli_args = cli_args
    mock.copr_references = []
    mock.brew_references = ["pkg-alpha-1.0"]
    mock.copr_reference = None
    mock.brew_reference = "pkg-alpha-1.0"
    mock.copr_api = {}
    mock.brew_api = CONFIG["tests"]["set"]["alpha"]["brew_api"]
    mock.parallel_limit = None
    mock.testing_farm_endpoint = MagicMock(
        log_artifact_baseurl="https://artifacts.tf.example",
        api_endpoint_url="https://api.tf.example/v0.1/requests",
    )
    mock.archive_tasks_latest = "/tmp/enge_multiset_test_latest"
    mock.archive_tasks_default = "/tmp/enge_multiset_test_archive/"
    mock.pool = None
    mock.architectures = []
    mock.environment_variables = {}
    mock.tmt_context = {}
    return mock


class TestDispatchGoldenMultiset(unittest.TestCase):
    """Golden test: two sets with different brew_api.build_references."""

    maxDiff = None

    def _build_individual_test_sets(self, cli_args):
        from enge.utils.source_target_parser import (
            resolve_effective_values,
            parse_source_target_config,
        )

        sets = []
        for set_name in ["alpha", "beta"]:
            set_config = CONFIG["tests"]["set"][set_name]
            effective_values = resolve_effective_values(cli_args, set_config, CONFIG)

            with patch(
                "enge.dispatch.pin_compose._pin_compose_with_fallback",
                return_value=PINNED_COMPOSE,
            ):
                source_spec, target_spec = parse_source_target_config(
                    effective_values["source"],
                    effective_values.get("target"),
                    CONFIG,
                )

            sets.append(
                {
                    "name": set_name,
                    "config": set_config,
                    "effective_values": effective_values,
                    "source_spec": source_spec,
                    "target_spec": target_spec,
                }
            )
        return sets

    def _make_resolver_stub(self):
        """Return a resolver that echoes brew_references from the spec_ctx it receives."""

        def resolve_builds(compose_name, ctx):
            brew_refs = getattr(ctx, "brew_references", [])
            ref_label = brew_refs[0] if brew_refs else "none"
            return [
                {
                    "compose": compose_name,
                    "distro": "rhel-8.10",
                    "build_id": 12345,
                    "packages": [ref_label],
                    "nvr": ref_label,
                }
            ]

        stub = MagicMock()
        stub.resolve_builds = resolve_builds
        return stub

    def test_golden_multiset_dispatch_payloads(self):
        cli_args = _build_cli_args()
        individual_test_sets = self._build_individual_test_sets(cli_args)
        resolved_opts = _build_resolved_opts(cli_args, individual_test_sets)

        specs = expand_set_requests(ctx=resolved_opts)

        self.assertEqual(
            len(specs),
            2,
            f"Expected 2 specs (2 sets x 1 tier x 1 arch), got {len(specs)}",
        )

        resolver = self._make_resolver_stub()
        payloads = []
        for idx, spec in enumerate(specs, start=1):
            result = process_request_spec(
                idx=idx,
                total_expected_requests=2,
                spec=spec,
                artifact_type="fedora-koji-build",
                artifact_resolver=resolver,
                ctx=resolved_opts,
            )
            self.assertIsNotNone(
                result, f"process_request_spec returned None for spec {idx}"
            )
            self.assertIn(
                "payload", result, f"No dryrun payload in result for spec {idx}"
            )
            payloads.append(result["payload"])

        # --- Per-set artifact assertions ---
        alpha_artifacts = payloads[0].get("environments", [{}])[0].get("artifacts", [])
        beta_artifacts = payloads[1].get("environments", [{}])[0].get("artifacts", [])

        alpha_packages = [pkg for a in alpha_artifacts for pkg in a.get("packages", [])]
        beta_packages = [pkg for a in beta_artifacts for pkg in a.get("packages", [])]

        self.assertIn(
            "pkg-alpha-1.0",
            alpha_packages,
            "alpha payload must carry alpha's artifact reference",
        )
        self.assertIn(
            "pkg-beta-2.0",
            beta_packages,
            "beta payload must carry beta's artifact reference",
        )
        self.assertNotIn(
            "pkg-alpha-1.0",
            beta_packages,
            "beta payload must NOT carry alpha's artifact reference",
        )

        # --- Golden file comparison ---
        actual_json = json.dumps(payloads, sort_keys=True, indent=2)

        if not os.path.exists(GOLDEN_FILE):
            os.makedirs(os.path.dirname(GOLDEN_FILE), exist_ok=True)
            with open(GOLDEN_FILE, "w") as f:
                f.write(actual_json + "\n")
            self.fail(
                f"Golden file generated at {GOLDEN_FILE}. "
                "Inspect it, then re-run to confirm baseline."
            )

        with open(GOLDEN_FILE) as f:
            expected_json = f.read().rstrip("\n")

        self.assertEqual(
            actual_json,
            expected_json,
            "Payload drift detected — multi-set dispatch output differs from golden baseline",
        )


if __name__ == "__main__":
    unittest.main()
