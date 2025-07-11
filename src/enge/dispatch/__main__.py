#!/usr/bin/env python3
import logging
import sys
from typing import List, Dict, Any, Optional

import requests
from requests.exceptions import RequestException, Timeout, ConnectionError

from enge.utils.globals import ARTIFACT_MAPPING
from enge.utils.opt_manager import parsed_opts
from enge.utils.source_target_parser import (
    parse_source_target_config,
    generate_upgrade_path_alias,
    parse_architectures,
    generate_tmt_context,
)
from .tf_send_request import SubmitTest

LOGGER = logging.getLogger(__name__)

tests_repo_base_url = parsed_opts.tests.get("git_url") or parsed_opts.project.get(
    "repo_url"
)

# Determine plans to execute based on CLI arguments vs config vs test sets
cli_plans = getattr(parsed_opts.cli_args, "plan", None)
cli_tiers = getattr(parsed_opts.cli_args, "tier", None)
cli_sets = getattr(parsed_opts.cli_args, "set", None)
config_plans = parsed_opts.plans if parsed_opts.plans else []

# Get effective tiers (from CLI, test sets, or None)
effective_tiers = getattr(parsed_opts, "effective_tiers", None)

if cli_plans:
    # CLI plans take precedence
    plans = cli_plans
    tiers = None
elif cli_tiers:
    # CLI tiers take precedence
    plans = config_plans if config_plans else []
    tiers = cli_tiers
elif effective_tiers:
    # Test set tiers (when no CLI tiers specified)
    plans = config_plans if config_plans else []
    tiers = effective_tiers
else:
    # Use config plans
    plans = config_plans
    tiers = None

# Initialize artifact type variables
artifact_type_alias: str
artifact_type: str
reference: List[str]

# Determine artifact type based on CLI arguments and test set configuration
copr_artifact = getattr(parsed_opts.cli_args, "copr", None)
brew_artifact = getattr(parsed_opts.cli_args, "brew", None)

# Check for artifacts from CLI or test sets
has_copr = copr_artifact or getattr(parsed_opts, "copr_references", [])
has_brew = brew_artifact or getattr(parsed_opts, "brew_references", [])

if has_copr:
    artifact_type_alias = "copr"
    artifact_type = ARTIFACT_MAPPING["copr"]
    reference = getattr(
        parsed_opts,
        "copr_references",
        [parsed_opts.copr_reference] if parsed_opts.copr_reference else [],
    )
    if not copr_artifact and reference:
        LOGGER.debug(f"Using COPR artifact(s) from configuration: {reference}")
elif has_brew:
    artifact_type_alias = "brew"
    artifact_type = ARTIFACT_MAPPING["brew"]
    reference = getattr(
        parsed_opts,
        "brew_references",
        [parsed_opts.brew_reference] if parsed_opts.brew_reference else [],
    )
    if not brew_artifact and reference:
        LOGGER.debug(f"Using brew artifact(s) from configuration: {reference}")
else:
    # Default to compose artifact type when no artifacts are specified
    artifact_type_alias = "compose"
    artifact_type = "compose"
    reference = []

copr_pkg_name = (
    parsed_opts.copr_api.get("package") or parsed_opts.project.get("name") or ""
)
copr_repo = parsed_opts.copr_api.get("repository") or copr_pkg_name or ""

brew_pkg_name = (
    parsed_opts.brew_api.get("package") or parsed_opts.project.get("name") or ""
)


def validate_git_repository(url: str) -> None:
    """Validate that the git repository URL is accessible."""
    try:
        LOGGER.debug(f"Validating git repository URL: {url}")
        git_response = requests.get(url, timeout=10)

        if git_response.status_code == 404:
            LOGGER.critical(f"Git repository not found: {url}")
            LOGGER.critical(f"Response status: {git_response.status_code}")
            sys.exit(99)
        elif git_response.status_code >= 400:
            LOGGER.warning(
                f"Git repository returned status {git_response.status_code}, but continuing..."
            )

    except Timeout:
        LOGGER.critical(f"Timeout while accessing git repository: {url}")
        sys.exit(99)
    except ConnectionError:
        LOGGER.critical(f"Connection error while accessing git repository: {url}")
        sys.exit(99)
    except RequestException as e:
        LOGGER.critical(f"Failed to validate git repository URL {url}: {e}")
        sys.exit(99)


def validate_plan_filters(plans_list: List[str]) -> None:
    """Validate plan and filter combinations."""
    if not plans_list:
        LOGGER.critical("No plans provided for testing!")
        sys.exit(99)

    # Exit if multiple plans requested with additional filter(s)
    cli_planfilter = getattr(parsed_opts.cli_args, "planfilter", None)
    generated_planfilter = getattr(parsed_opts, "plan_filter", None)
    cli_testfilter = getattr(parsed_opts.cli_args, "testfilter", None)

    if len(plans_list) > 1 and (
        cli_planfilter or generated_planfilter or cli_testfilter
    ):
        LOGGER.critical(
            "It is not advised to use testfilter or planfilter with multiple requested plans."
            " Please specify one plan with additional filters per request."
        )
        sys.exit(2)


def setup_submit_test(shared_archive_filename: Optional[str] = None) -> SubmitTest:
    """Initialize and configure the SubmitTest instance."""
    try:
        submit_test = SubmitTest(shared_archive_filename=shared_archive_filename)

        submit_test.api_key = parsed_opts.testing_farm.get("api_key")
        submit_test.tests_git_url = tests_repo_base_url
        submit_test.tests_git_branch = parsed_opts.tests.get("git_branch")
        # Use CLI planfilter if provided, otherwise use generated plan_filter
        cli_planfilter = getattr(parsed_opts.cli_args, "planfilter", None)
        submit_test.planfilter = cli_planfilter or getattr(
            parsed_opts, "plan_filter", None
        )
        submit_test.testfilter = getattr(parsed_opts.cli_args, "testfilter", None)
        # Get configuration values (validated by centralized validation)
        boot_method = (
            "uefi"
            if getattr(parsed_opts.cli_args, "uefi", False)
            else parsed_opts.common.get("boot_method")
        )

        # Boot method is guaranteed to be valid by centralized validation
        assert (
            isinstance(boot_method, str) and boot_method
        ), "Boot method validated by centralized validation"

        # Note: Architecture handling is done in build_payload() method with full list support
        submit_test.business_unit_tag = parsed_opts.testing_farm.get(
            "cloud_resources_tag"
        )
        submit_test.boot_method = boot_method

        submit_test.parallel_limit = getattr(parsed_opts, "parallel_limit", None)
        submit_test.print_header = True

        # Validate essential fields
        if not submit_test.api_key:
            raise ValueError("Testing Farm API key is required")

        return submit_test

    except Exception as e:
        LOGGER.critical(f"Failed to initialize SubmitTest: {e}")
        sys.exit(99)


def get_artifact_info(compose_name: str) -> List[Dict[str, Any]]:
    """Get artifact information based on the artifact type."""
    try:
        copr_artifacts = getattr(parsed_opts.cli_args, "copr", None)
        brew_artifacts = getattr(parsed_opts.cli_args, "brew", None)

        all_builds = []

        if copr_artifacts:
            LOGGER.debug("Getting COPR artifact information from CLI")
            # Handle multiple COPR artifacts from CLI
            if not isinstance(copr_artifacts, list):
                copr_artifacts = [copr_artifacts]

            for copr_artifact in copr_artifacts:
                # Extract reference from the artifact object itself
                artifact_reference = getattr(copr_artifact, "ref", None)
                if artifact_reference is None:
                    artifact_reference = (
                        [parsed_opts.copr_reference]
                        if parsed_opts.copr_reference
                        else []
                    )
                elif not isinstance(artifact_reference, list):
                    artifact_reference = [artifact_reference]

                builds = copr_artifact.get_info(
                    packages=copr_pkg_name,
                    repo=copr_repo,
                    reference=artifact_reference,
                    composes=[compose_name],
                    options=parsed_opts,
                )
                if builds:
                    all_builds.extend(builds)

        elif brew_artifacts:
            LOGGER.debug("Getting brew artifact information from CLI")
            # Handle multiple Brew artifacts from CLI
            if not isinstance(brew_artifacts, list):
                brew_artifacts = [brew_artifacts]

            for brew_artifact in brew_artifacts:
                # Extract reference from the artifact object itself
                artifact_reference = getattr(brew_artifact, "ref", None)
                if artifact_reference is None:
                    artifact_reference = (
                        [parsed_opts.brew_reference]
                        if parsed_opts.brew_reference
                        else []
                    )
                elif not isinstance(artifact_reference, list):
                    artifact_reference = [artifact_reference]
                builds = brew_artifact.get_info(
                    packages=brew_pkg_name,  # Fallback package name (will be overridden by NVR parsing)
                    reference=artifact_reference,
                    composes=[compose_name],
                    options=parsed_opts,
                )
                if builds:
                    all_builds.extend(builds)
        elif getattr(parsed_opts, "copr_references", []):
            LOGGER.debug("Getting COPR artifact information from configuration")
            # Handle COPR references from test set or config (no CLI artifacts)
            from enge.utils.tf_artifact import CoprRef

            for copr_ref in parsed_opts.copr_references:
                copr_artifact = CoprRef([copr_ref])
                builds = copr_artifact.get_info(
                    packages=copr_pkg_name,
                    repo=copr_repo,
                    reference=[copr_ref],
                    composes=[compose_name],
                    options=parsed_opts,
                )
                if builds:
                    all_builds.extend(builds)
        elif getattr(parsed_opts, "brew_references", []):
            LOGGER.debug("Getting brew artifact information from configuration")
            # Handle Brew references from test set or config (no CLI artifacts)
            from enge.utils.tf_artifact import BrewRef

            for brew_ref in parsed_opts.brew_references:
                brew_artifact = BrewRef([brew_ref])
                builds = brew_artifact.get_info(
                    packages=brew_pkg_name,  # Fallback package name (will be overridden by NVR parsing)
                    reference=[brew_ref],
                    composes=[compose_name],
                    options=parsed_opts,
                )
                if builds:
                    all_builds.extend(builds)
        elif parsed_opts.copr_reference:
            LOGGER.debug(
                "Getting COPR artifact information from configuration (legacy)"
            )
            # Backward compatibility for single reference
            from enge.utils.tf_artifact import CoprRef

            copr_artifact = CoprRef([parsed_opts.copr_reference])
            builds = copr_artifact.get_info(
                packages=copr_pkg_name,
                repo=copr_repo,
                reference=[parsed_opts.copr_reference],
                composes=[compose_name],
                options=parsed_opts,
            )
            if builds:
                all_builds.extend(builds)
        elif parsed_opts.brew_reference:
            LOGGER.debug(
                "Getting brew artifact information from configuration (legacy)"
            )
            # Backward compatibility for single reference
            from enge.utils.tf_artifact import BrewRef

            brew_artifact = BrewRef([parsed_opts.brew_reference])
            builds = brew_artifact.get_info(
                packages=brew_pkg_name,  # Fallback package name (will be overridden by NVR parsing)
                reference=[parsed_opts.brew_reference],
                composes=[compose_name],
                options=parsed_opts,
            )
            if builds:
                all_builds.extend(builds)
        else:
            # Handle compose artifact type - no build artifacts needed
            LOGGER.debug(
                "Using artifact installed from the source compose - no build artifacts in payload"
            )
            all_builds = [
                {
                    "compose": compose_name,
                    "build_id": None,  # No build_id for compose artifacts
                    "distro": parsed_opts.tmt_context.get(
                        "distro",
                        f"rhel-{parsed_opts.source_spec['major']}.{parsed_opts.source_spec['minor']}",
                    ),
                }
            ]

        return all_builds

    except Exception as e:
        LOGGER.critical(f"Failed to get artifact information: {e}")
        sys.exit(99)


def validate_compose_targets(compose_name: str) -> None:
    """Validate that requested compose target is valid."""
    # For now, we'll just log the compose name being used
    # Additional validation can be added here if needed
    LOGGER.debug(f"Using compose for upgrade: {compose_name}")


def main() -> int:
    global artifact_type
    try:
        # tests_repo_base_url is validated by centralized validation in opt_manager.py
        if getattr(parsed_opts.cli_args, "copr", None):
            # Fallback to ensure it's not None (should be validated by centralized validation)
            repo_url = (
                tests_repo_base_url
                or parsed_opts.tests.get("git_url")
                or parsed_opts.project.get("repo_url")
            )
            if repo_url:
                validate_git_repository(repo_url)

        total_requests = 0
        successful_requests = 0

        # Generate a single shared archive filename for all requests from this command
        from enge.utils import get_datetime

        shared_archive_filename = f"enge_jobs_archive_{get_datetime()}"

        # Check if we have individual test sets (new approach)
        if (
            hasattr(parsed_opts, "individual_test_sets")
            and parsed_opts.individual_test_sets
        ):
            # Process each test set independently
            all_set_requests = []

            for test_set in parsed_opts.individual_test_sets:
                set_name = test_set["name"]
                set_config = test_set["config"]
                effective_values = test_set["effective_values"]

                LOGGER.info(f"Processing test set: {set_name}")

                # Parse source/target for this set
                source_value = effective_values.get("source")
                target_value = effective_values.get("target")

                if not source_value:
                    LOGGER.error(f"No source specified for test set '{set_name}'")
                    continue

                try:
                    source_spec, target_spec = parse_source_target_config(
                        source_value, target_value
                    )
                    upgrade_path = generate_upgrade_path_alias(source_spec, target_spec)

                    # Parse architectures for this set
                    arch_input = effective_values.get("architectures")
                    if not arch_input:
                        LOGGER.error(
                            f"No architectures specified for test set '{set_name}'"
                        )
                        continue

                    architectures = parse_architectures(arch_input)

                    # Get tiers for this set
                    set_tiers = effective_values.get("tiers", [])
                    if not set_tiers:
                        LOGGER.error(f"No tiers specified for test set '{set_name}'")
                        continue

                    # Process each (tier, architecture) pair in this set
                    for tier in set_tiers:
                        for arch in architectures:
                            set_request = {
                                "set_name": set_name,
                                "tier": tier,
                                "source_spec": source_spec,
                                "target_spec": target_spec,
                                "upgrade_path": upgrade_path,
                                "architecture": arch,
                                "effective_values": effective_values,
                            }
                            all_set_requests.append(set_request)

                except ValueError as e:
                    LOGGER.error(
                        f"Failed to parse configuration for test set '{set_name}': {e}"
                    )
                    continue

            # Process all set requests
            total_expected_requests = len(all_set_requests)
            LOGGER.info(
                f"Preparing to process {total_expected_requests} request(s) from test sets"
            )

            # Import tier generation function
            from enge.utils.source_target_parser import generate_tier_plan_filter

            for idx, set_request in enumerate(all_set_requests, 1):
                set_name = set_request["set_name"]
                tier = set_request["tier"]
                source_spec = set_request["source_spec"]
                target_spec = set_request["target_spec"]
                upgrade_path = set_request["upgrade_path"]
                arch = set_request["architecture"]
                effective_values = set_request["effective_values"]

                LOGGER.info(
                    f"Processing request {idx}/{total_expected_requests}: {set_name} tier '{tier}' [{arch}]"
                )

                # Create a new SubmitTest instance for this request with shared archive filename
                submit_test = SubmitTest(
                    shared_archive_filename=shared_archive_filename
                )
                submit_test.api_key = parsed_opts.testing_farm.get("api_key")
                submit_test.tests_git_url = tests_repo_base_url
                submit_test.tests_git_branch = effective_values.get(
                    "git_branch"
                ) or parsed_opts.tests.get("git_branch")
                submit_test.testfilter = getattr(
                    parsed_opts.cli_args, "testfilter", None
                )
                submit_test.business_unit_tag = parsed_opts.testing_farm.get(
                    "cloud_resources_tag"
                )
                submit_test.parallel_limit = effective_values.get(
                    "parallel_limit"
                ) or parsed_opts.tests.get("parallel_limit")
                submit_test.print_header = idx == 1

                # Set boot method (validated to be present by centralized validation)
                boot_method = (
                    "uefi"
                    if getattr(parsed_opts.cli_args, "uefi", False)
                    else parsed_opts.common.get("boot_method")
                )
                # Boot method is guaranteed to be valid by centralized validation
                assert (
                    isinstance(boot_method, str) and boot_method
                ), "Boot method validated by centralized validation"
                submit_test.boot_method = boot_method

                # Generate plan filter for this tier
                try:
                    tier_config = parsed_opts.tests.get("tier", {})
                    tier_plan_filter = generate_tier_plan_filter(
                        [tier], tier_config, upgrade_path
                    )
                    LOGGER.debug(
                        f"Generated plan filter for tier '{tier}': {tier_plan_filter}"
                    )

                    # Use CLI planfilter if provided, otherwise use generated tier filter
                    cli_planfilter = getattr(parsed_opts.cli_args, "planfilter", None)
                    submit_test.planfilter = cli_planfilter or tier_plan_filter

                except ValueError as e:
                    LOGGER.error(
                        f"Failed to generate plan filter for tier '{tier}': {e}"
                    )
                    continue

                # Use source compose name for the request
                compose_name = source_spec["compose_name"]

                validate_compose_targets(compose_name)

                # Get artifact info - we need to temporarily update parsed_opts for get_artifact_info
                class TempOpts:
                    def __init__(self):
                        self.source_spec = None
                        self.target_spec = None
                        self.upgrade_path_alias = None
                        self.tmt_context = {}
                        self.project = None
                        self.copr_references = []
                        self.brew_references = []
                        self.copr_reference = None
                        self.brew_reference = None
                        self.cli_args = None
                        self.architectures = []
                        self.copr_api = {}
                        self.brew_api = {}

                temp_opts = TempOpts()

                # Copy necessary attributes
                for attr in [
                    "source_spec",
                    "target_spec",
                    "upgrade_path_alias",
                    "tmt_context",
                    "project",
                    "copr_references",
                    "brew_references",
                    "copr_reference",
                    "brew_reference",
                    "cli_args",
                    "copr_api",
                    "brew_api",
                ]:
                    if hasattr(parsed_opts, attr):
                        setattr(temp_opts, attr, getattr(parsed_opts, attr))

                # Update with set-specific values
                temp_opts.source_spec = source_spec
                temp_opts.target_spec = target_spec
                temp_opts.upgrade_path_alias = upgrade_path
                temp_opts.architectures = [arch]  # Only the current architecture

                # Generate TMT context for this set
                temp_opts.tmt_context = generate_tmt_context(
                    source_spec, target_spec, boot_method=boot_method
                )

                # Handle artifacts from set config
                set_copr_api = effective_values.get("copr_api", {})
                set_brew_api = effective_values.get("brew_api", {})

                # Generate environment variables for this set
                from enge.utils.source_target_parser import (
                    generate_environment_variables,
                    parse_environment_variables,
                    merge_set_environment_variables,
                )

                # Generate automatic environment variables for this set
                auto_env_vars = generate_environment_variables(
                    source_spec,
                    target_spec,
                    has_copr=bool(set_copr_api.get("build_references")),
                    has_brew=bool(set_brew_api.get("build_references")),
                )

                # Parse CLI environment variables
                cli_env_args = getattr(parsed_opts.cli_args, "environment", None)
                cli_env_vars = parse_environment_variables(cli_env_args)

                # Get environment variables from this test set
                set_env_vars = effective_values.get("environment", {})

                # Merge environment variables (CLI > Test Set > Automatic)
                merged_env_vars = merge_set_environment_variables(
                    auto_env_vars, set_env_vars, cli_env_vars
                )

                # Set the set-specific data for this SubmitTest instance
                submit_test.set_specific_data(
                    [arch], merged_env_vars, temp_opts.tmt_context
                )

                # Initialize artifact references as empty for this set (don't inherit from global)
                temp_opts.copr_references = []
                temp_opts.brew_references = []
                temp_opts.copr_reference = None
                temp_opts.brew_reference = None

                # Override artifact API configs if set has them
                if set_copr_api:
                    # Merge set copr_api with global copr_api (set values take precedence)
                    merged_copr_api = parsed_opts.copr_api.copy()
                    merged_copr_api.update(set_copr_api)
                    temp_opts.copr_api = merged_copr_api

                    # Only set artifact references if the set actually defines them
                    if set_copr_api.get("build_references"):
                        copr_ref = set_copr_api["build_references"]
                        temp_opts.copr_references = (
                            copr_ref if isinstance(copr_ref, list) else [copr_ref]
                        )
                        temp_opts.copr_reference = temp_opts.copr_references[0]

                if set_brew_api:
                    # Merge set brew_api with global brew_api (set values take precedence)
                    merged_brew_api = parsed_opts.brew_api.copy()
                    merged_brew_api.update(set_brew_api)
                    temp_opts.brew_api = merged_brew_api

                    # Only set artifact references if the set actually defines them
                    if set_brew_api.get("build_references"):
                        brew_ref = set_brew_api["build_references"]
                        temp_opts.brew_references = (
                            brew_ref if isinstance(brew_ref, list) else [brew_ref]
                        )
                        temp_opts.brew_reference = temp_opts.brew_references[0]

                # Temporarily replace parsed_opts for get_artifact_info
                original_parsed_opts = globals()["parsed_opts"]
                globals()["parsed_opts"] = temp_opts

                try:
                    info = get_artifact_info(compose_name)
                    if not info:
                        LOGGER.warning(
                            f"No artifact information found for {set_name} tier: {tier} arch: {arch}"
                        )
                        continue

                    total_requests += 1
                    LOGGER.debug(
                        f"Processing {len(info)} builds for {set_name} tier: {tier} arch: {arch}"
                    )

                    # Use the first build's compose info for the overall request
                    first_build = info[0]
                    submit_test.compose = first_build["compose"]
                    submit_test.tmt_distro = first_build["distro"]

                    # Clear any previous artifacts
                    submit_test.artifacts.clear()

                    # Add all builds as artifacts
                    for build in info:
                        LOGGER.debug(
                            f"Adding build: {build.get('build_id', 'unknown')}"
                        )

                        if build.get("build_id") is not None:
                            submit_test.add_artifact(
                                artifact_id=str(build["build_id"]),
                                artifact_type=artifact_type,
                                package=build.get(
                                    "package", parsed_opts.project.get("name", "")
                                ),
                                nvr=build.get("nvr"),
                            )

                    # Send single request with all artifacts
                    req_header, req_payload = submit_test.build_payload()
                    submit_test.send_request(req_payload, req_header)
                    successful_requests += 1
                    LOGGER.info(
                        f"✓ Completed request {idx}/{total_expected_requests} successfully"
                    )

                finally:
                    # Restore original parsed_opts
                    globals()["parsed_opts"] = original_parsed_opts

        else:
            # Fall back to original logic for non-test-set requests
            if not tiers:
                validate_plan_filters(plans)

            submit_test = setup_submit_test(
                shared_archive_filename=shared_archive_filename
            )

            # Import tier generation function if needed
            if tiers:
                from enge.utils.source_target_parser import generate_tier_plan_filter

                tier_config = parsed_opts.tests.get("tier", {})
                upgrade_path = parsed_opts.upgrade_path_alias

            # Calculate total requests to show progress
            if tiers:
                total_expected_requests = len(tiers)
                LOGGER.info(
                    f"Preparing to process {total_expected_requests} tier-based request(s)"
                )
            else:
                total_expected_requests = len(plans)
                LOGGER.info(
                    f"Preparing to process {total_expected_requests} plan-based request(s)"
                )

            # Process tiers or plans
            if tiers:
                # Process each tier separately
                for tier_idx, tier in enumerate(tiers, 1):
                    LOGGER.info(
                        f"Processing request {tier_idx}/{total_expected_requests}: tier '{tier}'"
                    )

                    # Generate plan filter for this specific tier
                    try:
                        tier_plan_filter = generate_tier_plan_filter(
                            [tier], tier_config, upgrade_path
                        )
                        LOGGER.debug(
                            f"Generated plan filter for tier '{tier}': {tier_plan_filter}"
                        )
                    except ValueError as e:
                        LOGGER.error(
                            f"Failed to generate plan filter for tier '{tier}': {e}"
                        )
                        continue

                    # Override the planfilter for this tier (unless CLI planfilter is specified)
                    cli_planfilter = getattr(parsed_opts.cli_args, "planfilter", None)
                    if not cli_planfilter:
                        submit_test.planfilter = tier_plan_filter

                    # For tiers, we don't need specific plans - the plan_filter handles selection
                    submit_test.plan = None

                    # Use the source compose name for the request
                    compose_name = parsed_opts.source_spec["compose_name"]

                    validate_compose_targets(compose_name)
                    info = get_artifact_info(compose_name)
                    if not info:
                        LOGGER.warning(
                            f"No artifact information found for tier: {tier}"
                        )
                        continue

                    # Process the request
                    try:
                        total_requests += 1
                        LOGGER.debug(f"Processing {len(info)} builds for tier: {tier}")

                        # Use the first build's compose info for the overall request
                        first_build = info[0]
                        submit_test.compose = first_build["compose"]
                        submit_test.tmt_distro = first_build["distro"]

                        # Clear any previous artifacts
                        submit_test.artifacts.clear()

                        # Add all builds as artifacts
                        for build in info:
                            LOGGER.debug(
                                f"Adding build: {build.get('build_id', 'unknown')}"
                            )

                            if build.get("build_id") is not None:
                                submit_test.add_artifact(
                                    artifact_id=str(build["build_id"]),
                                    artifact_type=artifact_type,
                                    package=build.get(
                                        "package", parsed_opts.project.get("name", "")
                                    ),
                                    nvr=build.get("nvr"),
                                )

                        # Send single request with all artifacts
                        req_header, req_payload = submit_test.build_payload()
                        submit_test.send_request(req_payload, req_header)
                        successful_requests += 1
                        LOGGER.info(
                            f"✓ Completed request {tier_idx}/{total_expected_requests} successfully"
                        )

                        submit_test.print_header = False

                    except KeyError as e:
                        LOGGER.error(f"Missing required field in build info: {e}")
                        continue
                    except Exception as e:
                        LOGGER.error(f"Failed to process builds: {e}")
                        continue
            else:
                # Original plan processing logic (when no tiers specified)
                for plan_idx, plan in enumerate(plans, 1):
                    LOGGER.info(
                        f"Processing request {plan_idx}/{total_expected_requests}: plan '{plan}'"
                    )
                    submit_test.plan = plan.rstrip("/")

                    # Use the source compose name for the request
                    compose_name = parsed_opts.source_spec["compose_name"]

                    validate_compose_targets(compose_name)
                    info = get_artifact_info(compose_name)
                    if not info:
                        LOGGER.warning(
                            f"No artifact information found for plan: {plan}"
                        )
                        continue

                    # Process the request
                    try:
                        total_requests += 1
                        LOGGER.debug(f"Processing {len(info)} builds for plan: {plan}")

                        # Use the first build's compose info for the overall request
                        first_build = info[0]
                        submit_test.compose = first_build["compose"]
                        submit_test.tmt_distro = first_build["distro"]

                        # Clear any previous artifacts
                        submit_test.artifacts.clear()

                        # Add all builds as artifacts
                        for build in info:
                            LOGGER.debug(
                                f"Adding build: {build.get('build_id', 'unknown')}"
                            )

                            if build.get("build_id") is not None:
                                submit_test.add_artifact(
                                    artifact_id=str(build["build_id"]),
                                    artifact_type=artifact_type,
                                    package=build.get(
                                        "package", parsed_opts.project.get("name", "")
                                    ),
                                    nvr=build.get("nvr"),
                                )

                        # Send single request with all artifacts
                        req_header, req_payload = submit_test.build_payload()
                        submit_test.send_request(req_payload, req_header)
                        successful_requests += 1
                        LOGGER.info(
                            f"✓ Completed request {plan_idx}/{total_expected_requests} successfully"
                        )

                        submit_test.print_header = False

                    except KeyError as e:
                        LOGGER.error(f"Missing required field in build info: {e}")
                        continue
                    except Exception as e:
                        LOGGER.error(f"Failed to process builds: {e}")
                        continue

        LOGGER.info(
            f"Completed processing: {successful_requests}/{total_requests} requests successful"
        )

        if successful_requests == 0:
            LOGGER.critical("No requests were successfully submitted!")
            return 1
        elif successful_requests < total_requests:
            LOGGER.warning(f"{total_requests - successful_requests} requests failed")
            return 2

        return 0

    except KeyboardInterrupt:
        LOGGER.info("Operation cancelled by user")
        return 130
    except Exception as e:
        LOGGER.critical(f"Unexpected error in dispatch: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
