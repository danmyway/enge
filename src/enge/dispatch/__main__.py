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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rich.markup import escape

from enge.utils.globals import ARTIFACT_MAPPING
from enge.utils.console import console
from enge.utils.errors import (
    ArtifactResolutionError,
    ConfigurationError,
    ValidationError,
)
from enge.utils.tf_artifact import CoprRef, BrewRef
from .tf_send_request import SubmitTest
from .set_flow import (
    expand_set_requests,
    process_request_spec,
    warn_on_reference_inheritance,
)
from .artifacts import ArtifactResolver
from enge.utils.validators import (
    validate_git_repository as validate_git_repo_util,
    validate_plan_filters as validate_plan_filters_util,
)
from .plan_flow import build_tier_plan_specs

LOGGER = logging.getLogger(__name__)

# Bound at import time on purpose: these are pure reference parsers, and the
# binding keeps them reachable when a test patches CoprRef/BrewRef with a
# double that only implements get_info().
_parse_copr_reference = CoprRef._parse_copr_reference
_parse_brew_nvr_package = BrewRef._parse_package_name_from_nvr


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


def _as_reference_list(value: Any) -> List[Any]:
    """Normalize a reference holder into a list, dropping an absent one."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _reference_label(reference: List[Any]) -> str:
    """How a reference is named in a user-facing error."""
    return ", ".join(str(ref) for ref in reference) or "<unset>"


def _copr_requested_package(reference: List[Any], ctx) -> Optional[str]:
    """The package a COPR reference asks for.

    The alias table answers it for ``alias:ref`` form; a bare build ID has
    no package in it, so the configured name stands in -- the same
    resolution the COPR query itself uses.
    """
    parsed = None
    if reference and reference[0] is not None:
        parsed, _ = _parse_copr_reference(str(reference[0]))
    return parsed or ctx.copr_api.get("package") or ctx.project.get("name") or None


def _brew_requested_package(build: Dict[str, Any]) -> Optional[str]:
    """The package a resolved Brew build was asked for, from its NVR."""
    nvr = build.get("nvr")
    parsed = _parse_brew_nvr_package(str(nvr)) if nvr else None
    if parsed:
        return parsed
    packages = build.get("packages") or []
    return packages[0] if packages else None


def _require_builds(builds: List[Dict[str, Any]], family: str, reference: str) -> None:
    """Every configured reference must resolve; half a dependency pair is
    worse than nothing, so an empty result fails the request."""
    if not builds:
        raise ArtifactResolutionError(
            f"{family} reference '{reference}' resolved to no builds"
        )


def _resolve_copr_builds(compose_name: str, ctx) -> List[Dict[str, Any]]:
    """Resolve every configured COPR reference into stamped build dicts.

    Within the family the precedence is unchanged: CLI ``--copr`` replaces
    configured ``copr_references``, which replace the legacy single
    ``copr_reference``.
    """
    copr_artifacts = _as_reference_list(getattr(ctx.cli_args, "copr", None))
    copr_references = list(getattr(ctx, "copr_references", None) or [])
    legacy_reference = getattr(ctx, "copr_reference", None)
    if not copr_artifacts and not copr_references and legacy_reference:
        LOGGER.debug("Getting COPR artifact information from configuration (legacy)")
        copr_references = [legacy_reference]
    elif copr_references:
        LOGGER.debug("Getting COPR artifact information from configuration")
    if not copr_artifacts and not copr_references:
        return []

    copr_pkg_name = ctx.copr_api.get("package") or ctx.project.get("name") or ""
    copr_repo = ctx.copr_api.get("repository") or copr_pkg_name or ""

    # (artifact, reference list) pairs, CLI objects first or config refs.
    if copr_artifacts:
        LOGGER.debug("Getting COPR artifact information from CLI")
        pending = []
        for copr_artifact in copr_artifacts:
            reference = getattr(copr_artifact, "ref", None)
            if reference is None:
                reference = [legacy_reference] if legacy_reference else []
            else:
                reference = _as_reference_list(reference)
            pending.append((copr_artifact, reference))
    else:
        pending = [(CoprRef([ref]), [ref]) for ref in copr_references]

    all_builds: List[Dict[str, Any]] = []
    for copr_artifact, reference in pending:
        builds = copr_artifact.get_info(
            packages=copr_pkg_name,
            repo=copr_repo,
            reference=reference,
            composes=[compose_name],
            options=ctx,
        )
        label = _reference_label(reference)
        _require_builds(builds, "COPR", label)
        requested_package = _copr_requested_package(reference, ctx)
        for build in builds:
            build["type"] = ARTIFACT_MAPPING["copr"]
            build["requested_package"] = requested_package
            build["requested_reference"] = label
        all_builds.extend(builds)
    return all_builds


def _resolve_brew_builds(compose_name: str, ctx) -> List[Dict[str, Any]]:
    """Resolve every configured Brew reference into stamped build dicts.

    Within the family the precedence is unchanged: CLI ``--brew`` replaces
    configured ``brew_references``, which replace the legacy single
    ``brew_reference``.
    """
    brew_artifacts = _as_reference_list(getattr(ctx.cli_args, "brew", None))
    brew_references = list(getattr(ctx, "brew_references", None) or [])
    legacy_reference = getattr(ctx, "brew_reference", None)
    if not brew_artifacts and not brew_references and legacy_reference:
        LOGGER.debug("Getting brew artifact information from configuration (legacy)")
        brew_references = [legacy_reference]
    elif brew_references:
        LOGGER.debug("Getting brew artifact information from configuration")
    if not brew_artifacts and not brew_references:
        return []

    brew_pkg_name = ctx.brew_api.get("package") or ctx.project.get("name") or ""

    if brew_artifacts:
        LOGGER.debug("Getting brew artifact information from CLI")
        pending = []
        for brew_artifact in brew_artifacts:
            reference = getattr(brew_artifact, "ref", None)
            if reference is None:
                reference = [legacy_reference] if legacy_reference else []
            else:
                reference = _as_reference_list(reference)
            pending.append((brew_artifact, reference))
    else:
        pending = [(BrewRef([ref]), [ref]) for ref in brew_references]

    all_builds: List[Dict[str, Any]] = []
    for brew_artifact, reference in pending:
        builds = brew_artifact.get_info(
            packages=brew_pkg_name,
            reference=reference,
            composes=[compose_name],
            options=ctx,
        )
        label = _reference_label(reference)
        _require_builds(builds, "Brew", label)
        for build in builds:
            build["type"] = ARTIFACT_MAPPING["brew"]
            build["requested_package"] = _brew_requested_package(build)
            build["requested_reference"] = label
        all_builds.extend(builds)
    return all_builds


def _assert_no_package_collision(
    copr_builds: List[Dict[str, Any]], brew_builds: List[Dict[str, Any]]
) -> None:
    """Refuse the same package from both families.

    The comparison is between what each reference *asked for*, never the
    resolved ``packages`` lists: COPR fills those with binary NVRAs and
    Brew with a bare source name, so intersecting them would be a silent
    no-op.
    """
    by_package: Dict[str, str] = {
        build["requested_package"]: build.get("requested_reference", "<unset>")
        for build in copr_builds
        if build.get("requested_package")
    }
    for build in brew_builds:
        package = build.get("requested_package")
        if not package or package not in by_package:
            continue
        raise ArtifactResolutionError(
            f"Package '{package}' is requested from both artifact families: "
            f"COPR reference '{by_package[package]}' and Brew reference "
            f"'{build.get('requested_reference', '<unset>')}'. Refusing to "
            f"install two versions of one package in a single request."
        )


def get_artifact_info(compose_name: str, ctx) -> List[Dict[str, Any]]:
    """Resolve every configured artifact reference, both families.

    Each returned build carries its own Testing Farm ``type`` and the
    ``requested_package`` its reference asked for. With no references
    configured at all, the single compose-only entry stands in and its
    ``type`` is None.
    """
    try:
        copr_builds = _resolve_copr_builds(compose_name, ctx)
        brew_builds = _resolve_brew_builds(compose_name, ctx)
        _assert_no_package_collision(copr_builds, brew_builds)

        if copr_builds or brew_builds:
            return copr_builds + brew_builds

        # No build artifacts anywhere - install from the source compose.
        LOGGER.debug(
            "Using artifact installed from the source compose - no build artifacts in payload"
        )
        return [
            {
                "compose": compose_name,
                "build_id": None,  # No build_id for compose artifacts
                "distro": ctx.tmt_context.get(
                    "distro",
                    f"rhel-{ctx.source_spec['major']}.{ctx.source_spec['minor']}",
                ),
                "type": None,
                "requested_package": None,
            }
        ]

    except ArtifactResolutionError:
        # A per-request failure the caller records and continues from; it
        # must not be flattened into a run-level ValidationError.
        raise
    except Exception as e:
        LOGGER.critical(f"Failed to get artifact information: {e}")
        raise ValidationError("Failed to get artifact information") from e


def _failed_label(result: Dict[str, Any]) -> str:
    """set/tier/arch of a failed request, skipping the parts it does not have."""
    parts = [result.get(k) for k in ("set_name", "tier", "arch")]
    return "/".join(str(p) for p in parts if p) or "?"


def _gitlab_cell(value: Any) -> str:
    """One GitLab markdown table cell: a single line, with '|' escaped."""
    return " ".join(str(value).splitlines()).replace("|", "\\|")


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
            print(
                f"| {_gitlab_cell(set_name)} | {_gitlab_cell(tier)} "
                f"| {_gitlab_cell(arch)} | {_gitlab_cell(cell)} |"
            )
    else:
        for r in results:
            if r.get("status") == "failed":
                console.print(
                    escape(f"FAILED  {_failed_label(r)}: {r.get('error', 'unknown')}"),
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
        # Per-request set fields are authoritative for multi-set runs.
        context["set"] = set_names[0]
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


@dataclass(frozen=True)
class DispatchSubmission:
    """What ``submit_dispatch`` did, for the caller to log and map to an exit code.

    run_id: the run's manifest run_id; None on a dry run, or when no request
        was submitted (nothing was written to the manifest).
    total: the number of requests attempted.
    submitted: the number Testing Farm accepted.
    results: one dict per request, in submission order (the input of
        ``_print_dispatch_summaries``).
    """

    run_id: Optional[str]
    total: int
    submitted: int
    results: Tuple[Dict[str, Any], ...]


def submit_dispatch(ctx) -> Optional[DispatchSubmission]:
    """Resolve and submit every request of an ``enge test`` invocation.

    The body of ``main`` up to and including submission: artifact-repo
    validation, request expansion (test sets, or legacy tiers/plans),
    ``process_request_spec`` per request and the manifest writes it makes.
    Returns None when the legacy tiers/plans path resolves no request (a
    WARNING has been logged). Does not log the ``Done:`` / ``Run ID:`` /
    ``Report with:`` lines, print the summaries or map failures to exit
    codes; ``main`` does all of that from the returned value. Exceptions
    propagate; ``main`` maps them.
    """
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

    warn_on_reference_inheritance(ctx)

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
            return None
        LOGGER.info(f"Dispatching {total_expected_requests} request(s)")
        for i, spec in enumerate(specs, 1):
            result = process_request_spec(
                i,
                total_expected_requests,
                spec,
                resolver,
                ctx=ctx,
                manifest_writer=manifest_writer,
            )
            if result:
                dispatch_results.append({**result, "idx": i})
                if result.get("status") == "submitted":
                    successful_requests += 1
            total_requests += 1

    return DispatchSubmission(
        run_id=(
            manifest_writer.run_id
            if not getattr(ctx.cli_args, "dryrun", False) and successful_requests > 0
            else None
        ),
        total=total_requests,
        submitted=successful_requests,
        results=tuple(dispatch_results),
    )


def main(ctx) -> int:
    try:
        output_format = getattr(ctx.cli_args, "output_format", "terminal")

        result = submit_dispatch(ctx)
        if result is None:
            return 1

        if output_format != "json":
            done_style = (
                "bold green" if result.submitted == result.total else "bold yellow"
            )
            LOGGER.info(
                f"Done: {result.submitted}/{result.total} submitted",
                extra={"style": done_style},
            )
            if result.run_id:
                LOGGER.info(f"Run ID: {result.run_id}")
                LOGGER.info(f"Report with: enge report --run {result.run_id}")

        if result.results:
            _print_dispatch_summaries(list(result.results), output_format)

        if result.submitted == 0:
            LOGGER.critical("No requests were successfully submitted!")
            return 1
        elif result.submitted < result.total:
            LOGGER.warning(f"{result.total - result.submitted} requests failed")
            return 2

        return 0

    except KeyboardInterrupt:
        LOGGER.info("Operation cancelled by user")
        return 130
    except Exception as e:
        LOGGER.critical(f"Unexpected error in dispatch: {e}")
        return 1
