#!/usr/bin/env python3
"""
Dispatch entrypoint for enge test submissions.

High-level flow:
1) Parse and validate options via utils.opt_manager (lazy singleton).
2) Two orchestration modes are supported:
   - Test sets mode: if CLI specifies --set, each set is expanded into
     individual RequestSpec items (via set_flow.expand_set_requests) and
     processed independently (process_request_spec). This path supports
     ReportPortal launch creation inside set_flow.
   - Legacy tiers/plans mode: when no --set is provided, tiers and/or plans
     are resolved, converted to RequestSpec items (plan_flow.build_tier_plan_specs),
     artifacts are resolved (ArtifactResolver), and requests are submitted via
     SubmitTest.

Key responsibilities delegated to helpers:
 - Artifact resolution: dispatch.artifacts.ArtifactResolver
 - Request assembly and submission: dispatch.tf_send_request.SubmitTest
 - RP launch creation: utils.reportportal_helper.create_launch (used by set_flow)

Errors are surfaced as exceptions and mapped to exit codes in the top-level CLI.
"""
import logging
import sys
from typing import List, Dict, Any, Optional

from enge.utils.globals import ARTIFACT_MAPPING
from enge.utils.opt_manager import parsed_opts
from .tf_send_request import SubmitTest
from enge.utils.reportportal_helper import create_launch as rp_create_launch
from enge.utils.globals import RP_COMPATIBLE_EVENT
from .set_flow import expand_set_requests, process_request_spec
from .artifacts import ArtifactResolver
from enge.utils.validators import (
    validate_git_repository as validate_git_repo_util,
    validate_plan_filters as validate_plan_filters_util,
)
from .plan_flow import build_tier_plan_specs

LOGGER = logging.getLogger(__name__)


def _compute_tiers_and_plans():
    cli_plans = getattr(parsed_opts.cli_args, "plan", None)
    cli_tiers = getattr(parsed_opts.cli_args, "tier", None)
    cli_sets = getattr(parsed_opts.cli_args, "set", None)
    config_plans = parsed_opts.plans if parsed_opts.plans else []
    effective_tiers = getattr(parsed_opts, "effective_tiers", None)

    if cli_tiers or effective_tiers:
        tiers = cli_tiers or effective_tiers
        plans = cli_plans if cli_plans else config_plans
    elif cli_sets:
        tiers = None
        plans = []
    else:
        tiers = None
        plans = cli_plans or config_plans
    return tiers, plans


def _determine_artifact_type() -> str:
    copr_artifact = getattr(parsed_opts.cli_args, "copr", None)
    brew_artifact = getattr(parsed_opts.cli_args, "brew", None)
    has_copr = copr_artifact or getattr(parsed_opts, "copr_references", [])
    has_brew = brew_artifact or getattr(parsed_opts, "brew_references", [])
    if has_copr:
        return ARTIFACT_MAPPING["copr"]
    if has_brew:
        return ARTIFACT_MAPPING["brew"]
    return "compose"


def validate_git_repository(url: str) -> None:
    validate_git_repo_util(url)


def validate_plan_filters(plans_list: List[str]) -> None:
    cli_planfilter = getattr(parsed_opts.cli_args, "planfilter", None)
    generated_planfilter = getattr(parsed_opts, "plan_filter", None)
    cli_testfilter = getattr(parsed_opts.cli_args, "testfilter", None)
    cli_test_name = getattr(parsed_opts.cli_args, "test", None)
    validate_plan_filters_util(
        plans_list, cli_planfilter, generated_planfilter, cli_testfilter, cli_test_name
    )


def setup_submit_test(shared_archive_filename: Optional[str] = None) -> SubmitTest:
    """Initialize and configure the SubmitTest instance."""
    try:
        submit_test = SubmitTest(shared_archive_filename=shared_archive_filename)

        submit_test.api_key = parsed_opts.testing_farm.get("api_key")
        submit_test.tests_git_url = (
            getattr(parsed_opts.cli_args, "git_url", None)
            or parsed_opts.tests.get("git_url")
            or parsed_opts.project.get("repo_url")
        )
        submit_test.tests_git_ref = getattr(
            parsed_opts.cli_args, "git_ref", None
        ) or parsed_opts.tests.get("git_ref")
        # Use CLI planfilter if provided, otherwise use generated plan_filter
        cli_planfilter = getattr(parsed_opts.cli_args, "planfilter", None)
        submit_test.planfilter = cli_planfilter or getattr(
            parsed_opts, "plan_filter", None
        )
        submit_test.testfilter = getattr(parsed_opts.cli_args, "testfilter", None)
        submit_test.test_name = getattr(parsed_opts.cli_args, "test", None)

        # Note: Architecture handling is done in build_payload() method with full list support
        submit_test.business_unit_tag = parsed_opts.testing_farm.get(
            "cloud_resources_tag"
        )

        submit_test.parallel_limit = getattr(parsed_opts, "parallel_limit", None)

        # Validate essential fields
        if not submit_test.api_key:
            raise ValueError("Testing Farm API key is required")

        return submit_test

    except Exception as e:
        LOGGER.critical(f"Failed to initialize SubmitTest: {e}")
        from enge.utils.errors import ConfigurationError

        raise ConfigurationError("Failed to initialize SubmitTest") from e


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

                copr_pkg_name = (
                    parsed_opts.copr_api.get("package")
                    or parsed_opts.project.get("name")
                    or ""
                )
                copr_repo = (
                    parsed_opts.copr_api.get("repository") or copr_pkg_name or ""
                )
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
                brew_pkg_name = (
                    parsed_opts.brew_api.get("package")
                    or parsed_opts.project.get("name")
                    or ""
                )
                builds = brew_artifact.get_info(
                    packages=brew_pkg_name,
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
                copr_pkg_name = (
                    parsed_opts.copr_api.get("package")
                    or parsed_opts.project.get("name")
                    or ""
                )
                copr_repo = (
                    parsed_opts.copr_api.get("repository") or copr_pkg_name or ""
                )
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
                brew_pkg_name = (
                    parsed_opts.brew_api.get("package")
                    or parsed_opts.project.get("name")
                    or ""
                )
                brew_artifact = BrewRef([brew_ref])
                builds = brew_artifact.get_info(
                    packages=brew_pkg_name,
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

            copr_pkg_name = (
                parsed_opts.copr_api.get("package")
                or parsed_opts.project.get("name")
                or ""
            )
            copr_repo = parsed_opts.copr_api.get("repository") or copr_pkg_name or ""
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

            brew_pkg_name = (
                parsed_opts.brew_api.get("package")
                or parsed_opts.project.get("name")
                or ""
            )
            brew_artifact = BrewRef([parsed_opts.brew_reference])
            builds = brew_artifact.get_info(
                packages=brew_pkg_name,
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
        from enge.utils.errors import ValidationError

        raise ValidationError("Failed to get artifact information") from e


def _maybe_create_rp_launch(
    *, context: Optional[Dict[str, Any]], tmt_context: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Create ReportPortal launch when event is compatible.

    Decision order (highest to lowest): CLI --event > first test set 'event' > None
    """
    event_name = getattr(parsed_opts.cli_args, "event", None)
    if not event_name:
        if (
            hasattr(parsed_opts, "individual_test_sets")
            and parsed_opts.individual_test_sets
        ):
            event_name = parsed_opts.individual_test_sets[0]["effective_values"].get(
                "event"
            )
    if not event_name or event_name not in RP_COMPATIBLE_EVENT:
        return None

    return rp_create_launch(
        context=context,
        tmt_context=tmt_context,
        config=parsed_opts.config,
        cli_args=parsed_opts.cli_args,
        dryrun=getattr(parsed_opts.cli_args, "dryrun", False),
    )


def main() -> int:
    global artifact_type
    try:
        # tests_repo_base_url is validated by centralized validation in opt_manager.py
        if getattr(parsed_opts.cli_args, "copr", None):
            # Resolve repo URL lazily
            repo_url = parsed_opts.tests.get("git_url") or parsed_opts.project.get(
                "repo_url"
            )
            if repo_url:
                validate_git_repository(repo_url)

        total_requests = 0
        successful_requests = 0

        # Handle ReportPortal launch creation based on event compatibility (global context)
        global_context = {
            "source_release": (
                f"{parsed_opts.source_spec['major']}.{parsed_opts.source_spec['minor']}"
                if hasattr(parsed_opts, "source_spec")
                else None
            ),
            "target_release": (
                f"{parsed_opts.target_spec['major']}.{parsed_opts.target_spec['minor']}"
                if hasattr(parsed_opts, "target_spec")
                else None
            ),
            "source_compose": (
                parsed_opts.source_spec.get("compose_name")
                if hasattr(parsed_opts, "source_spec")
                else None
            ),
        }

        # Add event/set name and architecture for launch naming (used only when creating RP launch)
        event_name = getattr(parsed_opts.cli_args, "event", None)
        set_name = None
        architecture = None
        if (
            not event_name
            and hasattr(parsed_opts, "individual_test_sets")
            and parsed_opts.individual_test_sets
        ):
            first_set = parsed_opts.individual_test_sets[0]
            event_name = first_set["effective_values"].get("event")
            set_name = first_set["name"]
        architectures = getattr(
            parsed_opts.cli_args, "architectures", None
        ) or parsed_opts.tests.get("architectures", [])
        if architectures:
            architecture = architectures[0]

        # Generate a single shared archive filename for all requests from this command
        from enge.utils import get_datetime

        shared_archive_filename = f"enge_jobs_archive_{get_datetime()}"

        # Determine tiers/plans and artifact type now (deferred to runtime)
        tiers, plans = _compute_tiers_and_plans()
        artifact_type = _determine_artifact_type()

        # Check if we have individual test sets (new approach)
        if (
            hasattr(parsed_opts, "individual_test_sets")
            and parsed_opts.individual_test_sets
        ):
            # Process each test set independently
            all_set_requests = expand_set_requests()

            # Process all set requests using helper
            total_expected_requests = len(all_set_requests)
            LOGGER.info(
                f"Preparing to process {total_expected_requests} request(s) from test sets"
            )
            resolver = ArtifactResolver()
            for idx, spec in enumerate(all_set_requests, 1):
                ok = process_request_spec(
                    idx,
                    total_expected_requests,
                    spec,
                    shared_archive_filename,
                    artifact_type,
                    resolver,
                )
                if ok:
                    successful_requests += 1
                    total_requests += 1

        else:
            # Fall back to original logic for non-test-set requests
            submit_test = setup_submit_test(
                shared_archive_filename=shared_archive_filename
            )

            # Import tier generation function if needed
            if tiers:
                tier_config = parsed_opts.tests.get("tier", {})
                upgrade_path = parsed_opts.upgrade_path_alias

            # Calculate total requests to show progress - now we combine tiers with plans
            if tiers:
                # When we have tiers, we process one request per tier (or tier+plan combination)
                if plans:
                    # Create one request per (tier, plan) combination
                    total_expected_requests = len(tiers) * len(plans)
                    LOGGER.info(
                        f"Preparing to process {total_expected_requests} request(s) ({len(tiers)} tier(s) × {len(plans)} plan(s))"
                    )
                else:
                    # Just tiers, no specific plans
                    total_expected_requests = len(tiers)
                    LOGGER.info(
                        f"Preparing to process {total_expected_requests} tier-based request(s)"
                    )
            else:
                # When we have only plans, we process one request per plan
                total_expected_requests = len(plans)
                LOGGER.info(
                    f"Preparing to process {total_expected_requests} plan-based request(s)"
                )

            resolver = ArtifactResolver()
            validate_plan_filters(plans)
            specs = build_tier_plan_specs(tiers, plans)
            total_expected_requests = len(specs)
            if total_expected_requests == 0:
                LOGGER.warning(
                    "No requests to process: no tiers/plans resolved into concrete requests."
                )
                return 1
            for i, spec in enumerate(specs, 1):
                # Reuse set flow processor for uniformity
                ok = process_request_spec(
                    i,
                    total_expected_requests,
                    spec,
                    shared_archive_filename,
                    artifact_type,
                    resolver,
                )
                if ok:
                    successful_requests += 1
                total_requests += 1

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
