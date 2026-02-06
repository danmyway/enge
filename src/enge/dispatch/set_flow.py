import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from enge.utils.source_target_parser import (
    parse_source_target_config,
    generate_upgrade_path_alias,
    generate_detailed_upgrade_path_alias,
    parse_architectures,
    generate_tier_plan_filter,
    generate_environment_variables,
    parse_environment_variables,
    merge_set_environment_variables,
    merge_tmt_context,
)
from enge.dispatch.tf_send_request import SubmitTest
from enge.dispatch.artifacts import ArtifactResolver
from enge.utils.reportportal_helper import (
    create_launch as rp_create_launch,
    filter_rp_launch_env_vars,
    DRYRUN_PLACEHOLDER,
    DRYRUN_UUID,
)
from enge.utils.globals import RP_COMPATIBLE_EVENT


_resolved_opts_placeholder = None  # set via tests when needed


class _DispatchParsedOptsContext:
    """Context manager to temporarily override dispatch module parsed_opts.

    This avoids ad-hoc global swapping sprinkled in the code and centralizes
    the readability of the temporary override for artifact resolution.
    """

    def __init__(self, temp_opts):
        self.temp_opts = temp_opts
        self._original = None

    def __enter__(self):
        import enge.dispatch.__main__ as dispatch_main

        self._original = dispatch_main.parsed_opts
        dispatch_main.parsed_opts = self.temp_opts
        return self

    def __exit__(self, exc_type, exc, tb):
        import enge.dispatch.__main__ as dispatch_main

        dispatch_main.parsed_opts = self._original


LOGGER = logging.getLogger(__name__)

_TEMPOPTS_ATTRS = [
    "source_spec",
    "target_spec",
    "upgrade_path_alias",
    "tmt_context",
    "project",
    "copr_references",
    "brew_references",
    "cli_args",
    "copr_api",
    "brew_api",
    "config",
]


class TempOpts:
    """Lightweight options snapshot used during per-request artifact resolution."""

    def __init__(self):
        self.source_spec = None
        self.target_spec = None
        self.upgrade_path_alias = None
        self.tmt_context = {}
        self.project = None
        self.copr_references = []
        self.brew_references = []
        self.cli_args = None
        self.architectures = []
        self.copr_api = {}
        self.brew_api = {}


@dataclass
class RequestSpec:
    set_name: Optional[str]
    tier: Optional[str]
    plan: Optional[str]
    arch: str
    source_spec: Dict[str, Any]
    target_spec: Dict[str, Any]
    upgrade_path: str
    effective_values: Dict[str, Any]
    upgrade_path_detailed: Optional[str] = None


def _get_parsed_opts():
    global _resolved_opts_placeholder
    if _resolved_opts_placeholder is not None:
        return _resolved_opts_placeholder
    from enge.utils import opt_manager  # local import to avoid import-time side effects

    return opt_manager.parsed_opts


def expand_set_requests() -> List[RequestSpec]:
    """Build list of RequestSpec from --set configuration."""
    specs: List[RequestSpec] = []
    resolved_opts = _get_parsed_opts()
    for test_set in getattr(resolved_opts, "individual_test_sets", []) or []:
        set_name = test_set["name"]
        effective_values = test_set["effective_values"]

        # Parse source/target
        source_value = effective_values.get("source")
        target_value = effective_values.get("target")
        if not source_value:
            LOGGER.error(f"No source specified for test set '{set_name}'")
            continue
        try:
            LOGGER.debug(f"Parsing source/target from test set '{set_name}' config")
            source_spec, target_spec = parse_source_target_config(
                source_value, target_value, resolved_opts.config
            )
            upgrade_path = generate_upgrade_path_alias(source_spec, target_spec)
            detailed_upgrade_path = generate_detailed_upgrade_path_alias(
                source_spec, target_spec
            )
        except ValueError as e:
            LOGGER.error(
                f"Failed to parse configuration for test set '{set_name}': {e}"
            )
            continue

        # Architectures
        arch_input = effective_values.get("architectures")
        if not arch_input:
            LOGGER.error(f"No architectures specified for test set '{set_name}'")
            continue
        try:
            architectures = parse_architectures(arch_input)
        except ValueError as e:
            LOGGER.error(f"Invalid architectures for set '{set_name}': {e}")
            continue

        # Tiers and plans
        set_tiers = effective_values.get("tiers", [])
        if not set_tiers:
            LOGGER.error(f"No tiers specified for test set '{set_name}'")
            continue
        all_specific_plans = effective_values.get("plans", [])

        for tier in set_tiers:
            for arch in architectures:
                if all_specific_plans:
                    for plan in all_specific_plans:
                        specs.append(
                            RequestSpec(
                                set_name=set_name,
                                tier=tier,
                                plan=plan,
                                arch=arch,
                                source_spec=source_spec,
                                target_spec=target_spec,
                                upgrade_path=upgrade_path,
                                upgrade_path_detailed=detailed_upgrade_path,
                                effective_values=effective_values,
                            )
                        )
                else:
                    specs.append(
                        RequestSpec(
                            set_name=set_name,
                            tier=tier,
                            plan=None,
                            arch=arch,
                            source_spec=source_spec,
                            target_spec=target_spec,
                            upgrade_path=upgrade_path,
                            upgrade_path_detailed=detailed_upgrade_path,
                            effective_values=effective_values,
                        )
                    )

    return specs


def _build_plan_filter(
    spec: RequestSpec,
    resolved_opts,
) -> Optional[str]:
    """Build the plan filter for a request spec, including RHSM adjustments.

    Returns the resolved plan filter string, or None on failure.
    """
    tier = spec.tier
    upgrade_path = spec.upgrade_path

    try:
        additional_filters = []
        only_rhsm_mock_cdn = getattr(
            resolved_opts.cli_args, "only_rhsm_mock_cdn", False
        )
        no_rhsm = getattr(resolved_opts.cli_args, "no_rhsm", False)
        only_rhsm_stage_cdn = getattr(
            resolved_opts.cli_args, "only_rhsm_stage_cdn", False
        )
        if only_rhsm_mock_cdn or only_rhsm_stage_cdn:
            additional_filters.append("tag:rhsm")
            LOGGER.debug("Adding tag:rhsm to plan filter")
        elif no_rhsm:
            additional_filters.append("tag:-rhsm")
            LOGGER.info("Excluding RHSM-tagged tests from execution")
            LOGGER.debug("Adding tag:-rhsm to plan filter")

        tier_config = resolved_opts.tests.get("tier", {})
        tier_plan_filter = generate_tier_plan_filter(
            [tier],
            tier_config,
            upgrade_path,
            additional_filters if additional_filters else None,
        )
        LOGGER.debug(f"Generated plan filter for tier '{tier}': {tier_plan_filter}")

        cli_planfilter = getattr(resolved_opts.cli_args, "planfilter", None)
        return cli_planfilter or tier_plan_filter
    except ValueError as e:
        LOGGER.error(f"Failed to generate plan filter for tier '{tier}': {e}")
        return None


def _build_tmt_context_and_env_vars(
    spec: RequestSpec,
    resolved_opts,
    per_set_event: Optional[str],
) -> tuple:
    """Build TempOpts with TMT context and merged environment variables.

    Returns (temp_opts, merged_env_vars).
    """
    source_spec = spec.source_spec
    target_spec = spec.target_spec
    effective_values = spec.effective_values

    temp_opts = TempOpts()
    for attr in _TEMPOPTS_ATTRS:
        if hasattr(resolved_opts, attr):
            setattr(temp_opts, attr, getattr(resolved_opts, attr))
    temp_opts.source_spec = source_spec
    temp_opts.target_spec = target_spec
    temp_opts.upgrade_path_alias = spec.upgrade_path
    temp_opts.architectures = [spec.arch]

    # Regenerate TMT context with per-set source/target specs
    from enge.utils.source_target_parser import (
        apply_centos_context_overrides,
        generate_tmt_context,
        parse_tmt_context,
        parse_target_compose_from_url,
    )

    temp_opts.tmt_context = generate_tmt_context(
        source_spec,
        target_spec,
        event=per_set_event,
        tier=spec.tier,
    )

    # Environment variables
    copr_artifact = getattr(resolved_opts.cli_args, "copr", None)
    brew_artifact = getattr(resolved_opts.cli_args, "brew", None)
    auto_env_vars = generate_environment_variables(
        source_spec,
        target_spec,
        has_copr=bool(
            copr_artifact
            or effective_values.get("copr_api", {}).get("build_references")
        ),
        has_brew=bool(
            brew_artifact
            or effective_values.get("brew_api", {}).get("build_references")
        ),
    )
    cli_env_args = getattr(resolved_opts.cli_args, "environment", None)
    cli_env_vars = parse_environment_variables(cli_env_args)
    set_env_vars = effective_values.get("environment", {})
    merged_env_vars = merge_set_environment_variables(
        auto_env_vars,
        set_env_vars,
        cli_env_vars,
        resolved_opts.config,
        resolved_opts.cli_args,
        effective_values.get("reportportal", {}),
        spec.set_name,
        spec.arch,
        spec.tier,
        auto_env_vars.get("SOURCE_RELEASE"),
        auto_env_vars.get("TARGET_RELEASE"),
        source_spec["compose_name"],
        target_spec["compose_name"],
        event=per_set_event,
    )

    # CentOS overrides and target compose enrichment
    temp_opts.tmt_context = apply_centos_context_overrides(
        temp_opts.tmt_context, source_spec, target_spec, merged_env_vars
    )
    if "TARGET_COMPOSE_URL" in merged_env_vars:
        target_compose = parse_target_compose_from_url(
            merged_env_vars["TARGET_COMPOSE_URL"]
        )
        if target_compose:
            temp_opts.tmt_context["target_compose"] = target_compose

    # Merge set-specific context, then CLI --context to allow CLI to override per-set
    set_context = effective_values.get("context", {}) or {}
    if per_set_event:
        temp_opts.tmt_context = merge_tmt_context(
            temp_opts.tmt_context, {"event": per_set_event}
        )
    if set_context:
        temp_opts.tmt_context = merge_tmt_context(temp_opts.tmt_context, set_context)

    try:
        cli_context_args = getattr(resolved_opts.cli_args, "context", None)
        cli_context = parse_tmt_context(cli_context_args)
        if cli_context:
            temp_opts.tmt_context = merge_tmt_context(
                temp_opts.tmt_context, cli_context
            )
    except ValueError as e:
        LOGGER.error(f"Failed to parse --context: {e}")

    # RHSM-specific settings
    only_rhsm_stage_cdn = getattr(resolved_opts.cli_args, "only_rhsm_stage_cdn", False)
    if only_rhsm_stage_cdn:
        merged_env_vars["RHSM_MODE"] = "stage"
        temp_opts.tmt_context["product_phase"] = "rc"
        LOGGER.debug("Applied RHSM stage settings: RHSM_MODE=stage, product_phase=rc")

    return temp_opts, merged_env_vars


def _resolve_artifacts(
    submit_test: SubmitTest,
    spec: RequestSpec,
    temp_opts: TempOpts,
    artifact_type: str,
    artifact_resolver: Optional[ArtifactResolver],
) -> bool:
    """Resolve builds and populate artifacts on submit_test. Returns False on failure."""
    source_spec = spec.source_spec

    with _DispatchParsedOptsContext(temp_opts):
        resolver = artifact_resolver or ArtifactResolver()
        info = resolver.resolve_builds(source_spec["compose_name"])
        if not info:
            LOGGER.warning(
                f"No artifact information found for {spec.set_name} "
                f"tier: {spec.tier} arch: {spec.arch}"
            )
            return False

        first_build = info[0]
        if source_spec.get("is_centos_stream", False):
            submit_test.compose = source_spec["compose_name"]
        else:
            submit_test.compose = first_build["compose"]
        submit_test.tmt_distro = first_build["distro"]
        submit_test.artifacts.clear()
        for build in info:
            if build.get("build_id") is not None:
                submit_test.add_artifact(
                    artifact_id=str(build["build_id"]),
                    artifact_type=artifact_type,
                    packages=build["packages"],
                    nvr=build.get("nvr"),
                )

    return True


def _maybe_create_rp_launch_for_spec(
    idx: int,
    submit_test: SubmitTest,
    spec: RequestSpec,
    rp_request_context: Dict[str, Any],
    per_set_event: str,
    temp_opts: TempOpts,
    merged_env_vars: Dict[str, str],
    resolved_opts,
) -> bool:
    """Create a ReportPortal launch for this spec if applicable. Returns False on failure."""
    if not (rp_request_context and per_set_event in RP_COMPATIBLE_EVENT):
        return True

    try:
        complete_tmt_context = submit_test.get_complete_tmt_context()
        complete_tmt_context.update(temp_opts.tmt_context)
        complete_tmt_context["arch"] = spec.arch

        is_dryrun = getattr(resolved_opts.cli_args, "dryrun", False)
        launch_uuid = rp_create_launch(
            context=rp_request_context,
            tmt_context=complete_tmt_context,
            config=resolved_opts.config,
            cli_args=resolved_opts.cli_args,
            dryrun=is_dryrun,
        )

        if launch_uuid:
            effective_uuid = (
                DRYRUN_UUID if launch_uuid == DRYRUN_PLACEHOLDER else launch_uuid
            )
            complete_tmt_context["uniq_id"] = effective_uuid
            submit_test.set_launch_uuid(effective_uuid)

            rp_env = filter_rp_launch_env_vars(merged_env_vars, effective_uuid)
            submit_test.set_specific_data([spec.arch], rp_env, complete_tmt_context)
    except Exception as e:
        LOGGER.error(f"Failed to create ReportPortal launch for request {idx}: {e}")
        return False

    return True


def process_request_spec(
    idx: int,
    total_expected_requests: int,
    spec: RequestSpec,
    shared_archive_filename: str,
    artifact_type: str,
    artifact_resolver: Optional[ArtifactResolver] = None,
) -> bool:
    """Prepare SubmitTest, optionally create RP launch, and send the request."""
    resolved_opts = _get_parsed_opts()
    set_name = spec.set_name
    tier = spec.tier
    specific_plan = spec.plan
    arch = spec.arch
    effective_values = spec.effective_values

    # Log
    if set_name and tier and specific_plan:
        LOGGER.info(
            f"Processing request {idx}/{total_expected_requests}: {set_name} tier '{tier}' plan '{specific_plan}' [{arch}]"
        )
    elif tier and specific_plan:
        LOGGER.info(
            f"Processing request {idx}/{total_expected_requests}: tier '{tier}' plan '{specific_plan}' [{arch}]"
        )
    elif specific_plan:
        LOGGER.info(
            f"Processing request {idx}/{total_expected_requests}: plan '{specific_plan}' [{arch}]"
        )
    else:
        LOGGER.info(
            f"Processing request {idx}/{total_expected_requests}: {set_name} tier '{tier}' [{arch}]"
        )

    # RP context
    per_set_event = effective_values.get("event") or getattr(
        resolved_opts.cli_args, "event", None
    )
    rp_request_context = None
    if per_set_event:
        rp_request_context = {
            "event": per_set_event,
            "set_name": set_name,
            "tier": tier,
            "architecture": arch,
            "source_release": f"{spec.source_spec['major']}.{spec.source_spec['minor']}",
            "target_release": f"{spec.target_spec['major']}.{spec.target_spec['minor']}",
            "source_compose": spec.source_spec.get("compose_name"),
        }

    # Prepare SubmitTest
    submit_test = SubmitTest(shared_archive_filename=shared_archive_filename)
    submit_test.api_key = resolved_opts.testing_farm.get("api_key")
    submit_test.tests_git_url = (
        getattr(resolved_opts.cli_args, "git_url", None)
        or effective_values.get("git_url")
        or resolved_opts.tests.get("git_url")
        or resolved_opts.project.get("repo_url")
    )
    submit_test.tests_git_ref = (
        getattr(resolved_opts.cli_args, "git_ref", None)
        or effective_values.get("git_ref")
        or resolved_opts.tests.get("git_ref")
    )
    submit_test.testfilter = getattr(resolved_opts.cli_args, "testfilter", None)
    submit_test.test_name = getattr(resolved_opts.cli_args, "test", None)
    submit_test.plan = specific_plan.rstrip("/") if specific_plan else None
    submit_test.business_unit_tag = resolved_opts.testing_farm.get(
        "cloud_resources_tag"
    )
    submit_test.parallel_limit = (
        effective_values.get("parallel_limit")
        or getattr(resolved_opts, "parallel_limit", None)
        or resolved_opts.tests.get("parallel_limit")
    )
    submit_test.set_auto_tags(
        set_name=set_name,
        architecture=arch,
        tier=tier,
        upgrade_path_tag=spec.upgrade_path_detailed,
    )

    # Plan filter
    planfilter = _build_plan_filter(spec, resolved_opts)
    if planfilter is None:
        return False
    submit_test.planfilter = planfilter
    if specific_plan:
        LOGGER.debug(f"Using specific plan: {specific_plan}")

    # TMT context and environment variables
    temp_opts, merged_env_vars = _build_tmt_context_and_env_vars(
        spec, resolved_opts, per_set_event
    )
    submit_test.set_specific_data([arch], merged_env_vars, temp_opts.tmt_context)

    # Artifacts
    if not _resolve_artifacts(
        submit_test, spec, temp_opts, artifact_type, artifact_resolver
    ):
        return False

    # ReportPortal launch
    if not _maybe_create_rp_launch_for_spec(
        idx,
        submit_test,
        spec,
        rp_request_context,
        per_set_event,
        temp_opts,
        merged_env_vars,
        resolved_opts,
    ):
        return False

    # Send request
    req_header, req_payload = submit_test.build_payload()
    submit_test.send_request(req_payload, req_header)
    return True
