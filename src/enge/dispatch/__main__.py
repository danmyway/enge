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
import json
import logging
import sys
from typing import List, Dict, Any

from enge.utils.globals import ARTIFACT_MAPPING
from enge.utils.console import console
from enge.utils.errors import ConfigurationError, ValidationError
from enge.utils.tf_artifact import CoprRef, BrewRef
from .tf_send_request import SubmitTest
from .set_flow import expand_set_requests, process_request_spec
from .artifacts import ArtifactResolver
from enge.utils.validators import (
    validate_git_repository as validate_git_repo_util,
    validate_plan_filters as validate_plan_filters_util,
)
from .plan_flow import build_tier_plan_specs

LOGGER = logging.getLogger(__name__)


def _compute_tiers_and_plans(ctx):
    cli_plans = getattr(ctx.cli_args, "plan", None)
    cli_tiers = getattr(ctx.cli_args, "tier", None)
    cli_sets = getattr(ctx.cli_args, "set", None)
    config_plans = ctx.plans if ctx.plans else []
    effective_tiers = getattr(ctx, "effective_tiers", None)

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


def _determine_artifact_type(ctx) -> str:
    copr_artifact = getattr(ctx.cli_args, "copr", None)
    brew_artifact = getattr(ctx.cli_args, "brew", None)
    has_copr = copr_artifact or getattr(ctx, "copr_references", [])
    has_brew = brew_artifact or getattr(ctx, "brew_references", [])
    if has_copr:
        return ARTIFACT_MAPPING["copr"]
    if has_brew:
        return ARTIFACT_MAPPING["brew"]
    return "compose"


def validate_git_repository(url: str) -> None:
    validate_git_repo_util(url)


def validate_plan_filters(plans_list: List[str], ctx) -> None:
    cli_planfilter = getattr(ctx.cli_args, "planfilter", None)
    generated_planfilter = getattr(ctx, "plan_filter", None)
    cli_testfilter = getattr(ctx.cli_args, "testfilter", None)
    cli_test_name = getattr(ctx.cli_args, "test", None)
    validate_plan_filters_util(
        plans_list, cli_planfilter, generated_planfilter, cli_testfilter, cli_test_name
    )


def setup_submit_test(ctx) -> SubmitTest:
    """Initialize and configure the SubmitTest instance."""
    try:
        submit_test = SubmitTest(ctx)

        submit_test.api_key = ctx.testing_farm.get("api_key")
        submit_test.tests_git_url = (
            getattr(ctx.cli_args, "git_url", None)
            or ctx.tests.get("git_url")
            or ctx.project.get("repo_url")
        )
        submit_test.tests_git_ref = getattr(
            ctx.cli_args, "git_ref", None
        ) or ctx.tests.get("git_ref")
        # Use CLI planfilter if provided, otherwise use generated plan_filter
        cli_planfilter = getattr(ctx.cli_args, "planfilter", None)
        submit_test.planfilter = cli_planfilter or getattr(ctx, "plan_filter", None)
        submit_test.testfilter = getattr(ctx.cli_args, "testfilter", None)
        submit_test.test_name = getattr(ctx.cli_args, "test", None)

        # Note: Architecture handling is done in build_payload() method with full list support
        submit_test.business_unit_tag = ctx.testing_farm.get("cloud_resources_tag")

        submit_test.parallel_limit = getattr(ctx, "parallel_limit", None)

        # Validate essential fields
        if not submit_test.api_key:
            raise ValueError("Testing Farm API key is required")

        return submit_test

    except Exception as e:
        LOGGER.critical(f"Failed to initialize SubmitTest: {e}")
        raise ConfigurationError("Failed to initialize SubmitTest") from e


def get_artifact_info(compose_name: str, ctx) -> List[Dict[str, Any]]:
    """Get artifact information based on the artifact type."""
    try:
        copr_artifacts = getattr(ctx.cli_args, "copr", None)
        brew_artifacts = getattr(ctx.cli_args, "brew", None)

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
                        [ctx.copr_reference] if ctx.copr_reference else []
                    )
                elif not isinstance(artifact_reference, list):
                    artifact_reference = [artifact_reference]

                copr_pkg_name = (
                    ctx.copr_api.get("package") or ctx.project.get("name") or ""
                )
                copr_repo = ctx.copr_api.get("repository") or copr_pkg_name or ""
                builds = copr_artifact.get_info(
                    packages=copr_pkg_name,
                    repo=copr_repo,
                    reference=artifact_reference,
                    composes=[compose_name],
                    options=ctx,
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
                        [ctx.brew_reference] if ctx.brew_reference else []
                    )
                elif not isinstance(artifact_reference, list):
                    artifact_reference = [artifact_reference]
                brew_pkg_name = (
                    ctx.brew_api.get("package") or ctx.project.get("name") or ""
                )
                builds = brew_artifact.get_info(
                    packages=brew_pkg_name,
                    reference=artifact_reference,
                    composes=[compose_name],
                    options=ctx,
                )
                if builds:
                    all_builds.extend(builds)
        elif getattr(ctx, "copr_references", []):
            LOGGER.debug("Getting COPR artifact information from configuration")
            # Handle COPR references from test set or config (no CLI artifacts)
            for copr_ref in ctx.copr_references:
                copr_pkg_name = (
                    ctx.copr_api.get("package") or ctx.project.get("name") or ""
                )
                copr_repo = ctx.copr_api.get("repository") or copr_pkg_name or ""
                copr_artifact = CoprRef([copr_ref])
                builds = copr_artifact.get_info(
                    packages=copr_pkg_name,
                    repo=copr_repo,
                    reference=[copr_ref],
                    composes=[compose_name],
                    options=ctx,
                )
                if builds:
                    all_builds.extend(builds)
        elif getattr(ctx, "brew_references", []):
            LOGGER.debug("Getting brew artifact information from configuration")
            # Handle Brew references from test set or config (no CLI artifacts)
            for brew_ref in ctx.brew_references:
                brew_pkg_name = (
                    ctx.brew_api.get("package") or ctx.project.get("name") or ""
                )
                brew_artifact = BrewRef([brew_ref])
                builds = brew_artifact.get_info(
                    packages=brew_pkg_name,
                    reference=[brew_ref],
                    composes=[compose_name],
                    options=ctx,
                )
                if builds:
                    all_builds.extend(builds)
        elif ctx.copr_reference:
            LOGGER.debug(
                "Getting COPR artifact information from configuration (legacy)"
            )
            # Backward compatibility for single reference
            copr_pkg_name = ctx.copr_api.get("package") or ctx.project.get("name") or ""
            copr_repo = ctx.copr_api.get("repository") or copr_pkg_name or ""
            copr_artifact = CoprRef([ctx.copr_reference])
            builds = copr_artifact.get_info(
                packages=copr_pkg_name,
                repo=copr_repo,
                reference=[ctx.copr_reference],
                composes=[compose_name],
                options=ctx,
            )
            if builds:
                all_builds.extend(builds)
        elif ctx.brew_reference:
            LOGGER.debug(
                "Getting brew artifact information from configuration (legacy)"
            )
            # Backward compatibility for single reference
            brew_pkg_name = ctx.brew_api.get("package") or ctx.project.get("name") or ""
            brew_artifact = BrewRef([ctx.brew_reference])
            builds = brew_artifact.get_info(
                packages=brew_pkg_name,
                reference=[ctx.brew_reference],
                composes=[compose_name],
                options=ctx,
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
                    "distro": ctx.tmt_context.get(
                        "distro",
                        f"rhel-{ctx.source_spec['major']}.{ctx.source_spec['minor']}",
                    ),
                }
            ]

        return all_builds

    except Exception as e:
        LOGGER.critical(f"Failed to get artifact information: {e}")
        raise ValidationError("Failed to get artifact information") from e


def _print_dispatch_summaries(
    results: List[Dict[str, Any]], output_format: str
) -> None:
    """Print all collected request summaries in the requested format."""
    if output_format == "json":
        successful = sum(1 for r in results if r.get("status") == "submitted")
        json_output = {
            "requests": [
                {k: v for k, v in r.items() if k != "summary"} for r in results
            ],
            "total": len(results),
            "successful": successful,
            "failed": len(results) - successful,
        }
        print(json.dumps(json_output, indent=2))
    elif output_format == "gitlab":
        print("```")
        for r in results:
            summary = r.get("summary")
            if summary:
                print(summary)
        print("```")
        print()
        print("| Set | Tier | Arch | Results |")
        print("|---|---|---|---|")
        for r in results:
            set_name = r.get("set_name") or "-"
            tier = r.get("tier") or "-"
            arch = r.get("arch") or "-"
            if r.get("status") == "failed":
                cell = f"FAILED: {r.get('error', 'unknown')}"
            elif r.get("results_url"):
                cell = r["results_url"]
            else:
                cell = "dry run"
            print(f"| {set_name} | {tier} | {arch} | {cell} |")
    else:
        for r in results:
            if r.get("status") == "failed":
                console.print(
                    f"FAILED  {r.get('set_name', '?')}/{r.get('tier', '?')}/{r.get('arch', '?')}: {r.get('error', 'unknown')}",
                    style="error",
                )
            else:
                summary = r.get("summary")
                if summary:
                    print(summary)


def _build_manifest_writer(ctx):
    from enge.utils.manifest import ManifestWriter
    from enge.utils.ulid import generate_ulid

    first_set = None
    if hasattr(ctx, "individual_test_sets") and ctx.individual_test_sets:
        first_set = ctx.individual_test_sets[0]
    eff = first_set["effective_values"] if first_set else {}

    context = {}
    set_names = getattr(ctx.cli_args, "set", None)
    if set_names:
        context["set"] = set_names[0] if len(set_names) == 1 else set_names[0]
    context["event"] = eff.get("event") or getattr(ctx.cli_args, "event", None)
    if hasattr(ctx, "source_spec") and ctx.source_spec:
        src = ctx.source_spec
        context["source"] = f"{src.get('major', '')}.{src.get('minor', '')}"
    if hasattr(ctx, "target_spec") and ctx.target_spec:
        tgt = ctx.target_spec
        context["target"] = f"{tgt.get('major', '')}.{tgt.get('minor', '')}"
    tiers = getattr(ctx.cli_args, "tier", None) or (
        [t for t in (getattr(ctx, "effective_tiers", None) or [])]
    )
    if tiers:
        context["tiers"] = tiers
    if hasattr(ctx, "architectures") and ctx.architectures:
        context["architectures"] = ctx.architectures

    return ManifestWriter(
        run_id=generate_ulid(),
        command="test",
        argv=sys.argv,
        tags=getattr(ctx.cli_args, "set_tag", None) or [],
        context=context,
    )


def main(ctx) -> int:
    global artifact_type
    try:
        output_format = getattr(ctx.cli_args, "output_format", "terminal")

        if getattr(ctx.cli_args, "copr", None):
            repo_url = ctx.tests.get("git_url") or ctx.project.get("repo_url")
            if repo_url:
                validate_git_repository(repo_url)

        total_requests = 0
        successful_requests = 0

        event_name = getattr(ctx.cli_args, "event", None)
        if (
            not event_name
            and hasattr(ctx, "individual_test_sets")
            and ctx.individual_test_sets
        ):
            first_set = ctx.individual_test_sets[0]
            event_name = first_set["effective_values"].get("event")

        if getattr(ctx.cli_args, "auto_tag", False):
            LOGGER.warning(
                "--auto-tag is deprecated; context is now always recorded "
                "in the manifest. This flag is a no-op."
            )

        tiers, plans = _compute_tiers_and_plans(ctx)
        artifact_type = _determine_artifact_type(ctx)

        manifest_writer = _build_manifest_writer(ctx)

        dispatch_results: List[Dict[str, Any]] = []

        if hasattr(ctx, "individual_test_sets") and ctx.individual_test_sets:
            all_set_requests = expand_set_requests(ctx=ctx)
            total_expected_requests = len(all_set_requests)
            LOGGER.info(f"Dispatching {total_expected_requests} request(s)")
            resolver = ArtifactResolver()
            for idx, spec in enumerate(all_set_requests, 1):
                result = process_request_spec(
                    idx,
                    total_expected_requests,
                    spec,
                    None,
                    artifact_type,
                    resolver,
                    ctx=ctx,
                    manifest_writer=manifest_writer,
                )
                if result:
                    dispatch_results.append({**result, "idx": idx})
                    if result.get("status") == "submitted":
                        successful_requests += 1
                total_requests += 1

        else:
            setup_submit_test(ctx)

            resolver = ArtifactResolver()
            validate_plan_filters(plans, ctx)
            specs = build_tier_plan_specs(tiers, plans, ctx)
            total_expected_requests = len(specs)
            if total_expected_requests == 0:
                LOGGER.warning(
                    "No requests to process: no tiers/plans resolved into concrete requests."
                )
                return 1
            LOGGER.info(f"Dispatching {total_expected_requests} request(s)")
            for i, spec in enumerate(specs, 1):
                result = process_request_spec(
                    i,
                    total_expected_requests,
                    spec,
                    None,
                    artifact_type,
                    resolver,
                    ctx=ctx,
                    manifest_writer=manifest_writer,
                )
                if result:
                    dispatch_results.append({**result, "idx": i})
                    if result.get("status") == "submitted":
                        successful_requests += 1
                total_requests += 1

        if output_format != "json":
            done_style = (
                "bold green" if successful_requests == total_requests else "bold yellow"
            )
            LOGGER.info(
                f"Done: {successful_requests}/{total_requests} submitted",
                extra={"style": done_style},
            )

        if dispatch_results:
            _print_dispatch_summaries(dispatch_results, output_format)

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
