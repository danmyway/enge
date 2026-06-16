"""Golden test for Testing Farm dispatch payloads.

Captures the exact payload structure produced by the dispatch pipeline
(resolve_effective_values -> expand_set_requests -> process_request_spec)
so that behavior-preserving refactors can be verified against a known
baseline.

Stubs (all documented inline):
  - _pin_compose_with_fallback: avoids HTTP to compose service
  - ArtifactResolver.resolve_builds: avoids COPR/Brew resolution
  - parsed_opts in tf_send_request: avoids singleton initialization
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

# Never import parsed_opts at module level in tests — see CLAUDE.md.
import enge.dispatch.set_flow as set_flow
from enge.dispatch.set_flow import expand_set_requests, process_request_spec

GOLDEN_FILE = os.path.join(
    os.path.dirname(__file__), "fixtures", "dispatch_golden.json"
)

# ---- Stub return values (documented) ------------------------------------

# _pin_compose_with_fallback → deterministic compose name (avoids HTTP)
PINNED_COMPOSE = "RHEL-8.10.0-20240101.0"

# ArtifactResolver.resolve_builds → minimal compose-only artifact info
# (avoids COPR/Brew network calls)
ARTIFACT_STUB = [
    {
        "compose": PINNED_COMPOSE,
        "distro": "rhel-8.10",
        "build_id": None,
    }
]

# ---- Config fixture (matches the spec in the task description) ----------

CONFIG = {
    "testing_farm": {
        "api_key": "test-golden-key",
        "api_endpoint_url": "https://api.tf.example/v0.1/requests",
        "log_artifact_baseurl": "https://artifacts.tf.example",
        "cloud_resources_tag": "golden-unit",
        "composes_prod_url": "https://composes.example",
    },
    "tests": {
        "git_url": "https://github.com/oamg/leapp",
        "git_ref": "main",
        "tiers": ["tier3"],  # bundled default for fallback
        "tier": {
            "tier0": "tag:tier[0]",
            "tier3": "tag:tier[0123]",
        },
        "set": {
            "golden-set": {
                "source": "8.10",
                "tiers": ["tier0", "tier3"],
                "architectures": ["x86_64", "aarch64"],
                "git_ref": "main",
            },
        },
    },
    "project": {
        "name": "leapp",
        "owner": "oamg",
        "repo_url": "https://github.com/oamg/leapp",
    },
    "common": {
        "archive_tasks_latest": "/tmp/enge_golden_test_latest",
        "archive_tasks_default": "/tmp/enge_golden_test_archive/",
    },
    "copr_api": {},
    "brew_api": {},
    "reportportal": {},
}


def _build_cli_args():
    """Build a mock cli_args with every attribute the dispatch pipeline reads."""
    return SimpleNamespace(
        action="test",
        set=["golden-set"],
        dryrun=True,
        output_format="terminal",
        # No CLI overrides — these must all be None:
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
        # Boolean flags:
        auto_tag=False,
        set_tag=None,
        only_rhsm_mock_cdn=False,
        no_rhsm=False,
        only_rhsm_stage_cdn=False,
        wait=False,
        config=None,
    )


def _build_resolved_opts(cli_args, individual_test_sets):
    """Build the mock object returned by set_flow._get_parsed_opts().

    Must carry every attribute that process_request_spec reads from
    resolved_opts (testing_farm, tests, project, config, cli_args, etc.).
    """
    mock = MagicMock()
    mock.individual_test_sets = individual_test_sets
    mock.testing_farm = CONFIG["testing_farm"]
    mock.tests = CONFIG["tests"]
    mock.project = CONFIG["project"]
    mock.config = CONFIG
    mock.cli_args = cli_args
    mock.copr_references = []
    mock.brew_references = []
    mock.copr_reference = None
    mock.brew_reference = None
    mock.copr_api = {}
    mock.brew_api = {}
    mock.parallel_limit = None
    return mock


class TestDispatchGolden(unittest.TestCase):
    """Golden test: capture dispatch payloads for a 2-tier x 2-arch test set."""

    maxDiff = None

    def _build_individual_test_sets(self, cli_args):
        """Run REAL resolve_effective_values + parse_compose_spec to build
        the individual_test_sets list as opt_manager._initialize_test_attributes
        would.  Only _pin_compose_with_fallback is stubbed (HTTP avoidance).
        """
        from enge.utils.source_target_parser import (
            resolve_effective_values,
            parse_source_target_config,
        )

        set_name = "golden-set"
        set_config = CONFIG["tests"]["set"][set_name]

        # REAL resolution: CLI > Set > Config chain
        effective_values = resolve_effective_values(cli_args, set_config, CONFIG)

        # Stub _pin_compose_with_fallback to avoid HTTP, but let
        # parse_compose_spec / derive_target_from_source run for real.
        # The function lives in enge.dispatch.pin_compose and is imported
        # locally inside parse_compose_spec.
        with patch(
            "enge.dispatch.pin_compose._pin_compose_with_fallback",
            return_value=PINNED_COMPOSE,
        ):
            source_spec, target_spec = parse_source_target_config(
                effective_values["source"],
                effective_values.get("target"),
                CONFIG,
            )

        return [
            {
                "name": set_name,
                "config": set_config,
                "effective_values": effective_values,
                "source_spec": source_spec,
                "target_spec": target_spec,
            }
        ]

    def test_golden_dispatch_payloads(self):
        """Expand set requests and process each spec; compare payloads to golden file."""
        cli_args = _build_cli_args()
        individual_test_sets = self._build_individual_test_sets(cli_args)
        resolved_opts = _build_resolved_opts(cli_args, individual_test_sets)

        # --- Step 1: expand_set_requests (uses real cartesian product logic) ---
        with patch.object(set_flow, "_resolved_opts_placeholder", resolved_opts):
            specs = expand_set_requests()

        self.assertEqual(
            len(specs),
            4,
            f"Expected 4 specs (2 tiers x 2 arches), got {len(specs)}",
        )

        # --- Step 2: process each spec with stubs for network-touching code ---
        payloads = []
        for idx, spec in enumerate(specs, start=1):
            # Mock parsed_opts in tf_send_request (accessed at SubmitTest.__init__
            # time and in assess_summary_message / build_payload / send_request).
            # Attributes that must NOT return auto-generated MagicMocks are set
            # explicitly to their real values.
            tf_parsed_opts = MagicMock()
            tf_parsed_opts.testing_farm_endpoint.log_artifact_baseurl = (
                "https://artifacts.tf.example"
            )
            tf_parsed_opts.testing_farm_endpoint.api_endpoint_url = (
                "https://api.tf.example/v0.1/requests"
            )
            tf_parsed_opts.cli_args = cli_args
            tf_parsed_opts.archive_tasks_latest = "/tmp/enge_golden_test_latest"
            tf_parsed_opts.archive_tasks_default = "/tmp/enge_golden_test_archive/"
            # Fallback attributes read by build_payload / assess_summary_message
            # when set_specific_data values are None:
            tf_parsed_opts.pool = None
            tf_parsed_opts.architectures = []
            tf_parsed_opts.environment_variables = {}
            tf_parsed_opts.tmt_context = {}

            with patch.object(
                set_flow, "_resolved_opts_placeholder", resolved_opts
            ), patch(
                "enge.dispatch.tf_send_request.parsed_opts", tf_parsed_opts
            ), patch(
                "enge.dispatch.set_flow.ArtifactResolver.resolve_builds",
                return_value=ARTIFACT_STUB,
            ):
                result = process_request_spec(
                    idx=idx,
                    total_expected_requests=4,
                    spec=spec,
                    shared_archive_filename="enge_golden_test_archive",
                    artifact_type="compose",
                )

            self.assertIsNotNone(
                result, f"process_request_spec returned None for spec {idx}"
            )
            self.assertIn(
                "payload", result, f"No dryrun payload in result for spec {idx}"
            )
            payloads.append(result["payload"])

        # --- Step 3: structural completeness checks ---
        for i, payload in enumerate(payloads):
            with self.subTest(spec_index=i):
                # Each payload must have a plan_filter containing 'tag:'
                plan_filter = payload["test"]["fmf"]["plan_filter"]
                self.assertIn(
                    "tag:",
                    plan_filter,
                    f"plan_filter missing 'tag:' prefix: {plan_filter}",
                )

                # Each must have environments[0].os.compose
                compose = payload["environments"][0]["os"]["compose"]
                self.assertTrue(compose, "Missing os.compose in environment")

                # Each must have SOURCE_RELEASE and TARGET_RELEASE
                env_vars = payload["environments"][0]["variables"]
                self.assertIn("SOURCE_RELEASE", env_vars)
                self.assertIn("TARGET_RELEASE", env_vars)

        # --- Step 4: golden file comparison ---
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
            "Payload drift detected — dispatch output differs from golden baseline",
        )


if __name__ == "__main__":
    unittest.main()
