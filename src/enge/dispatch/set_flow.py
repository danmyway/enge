import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from enge.utils.source_target_parser import (
    parse_source_target_config,
    generate_upgrade_path_alias,
    parse_architectures,
    generate_tier_plan_filter,
    generate_environment_variables,
    parse_environment_variables,
    merge_set_environment_variables,
    merge_tmt_context,
)
from enge.dispatch.tf_send_request import SubmitTest
from enge.dispatch.artifacts import ArtifactResolver
from enge.utils.reportportal_helper import create_launch as rp_create_launch
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
                            effective_values=effective_values,
                        )
                    )

    return specs


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
    source_spec = spec.source_spec
    target_spec = spec.target_spec
    upgrade_path = spec.upgrade_path
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

    # RP context (global + per request)
    launch_uuid = None
    shortened_uuid = None
    rp_request_context = None
    per_set_event = effective_values.get("event") or getattr(
        resolved_opts.cli_args, "event", None
    )
    if per_set_event:
        rp_request_context = {
            "event": per_set_event,
            "set_name": set_name,
            "tier": tier,
            "architecture": arch,
            "source_release": f"{source_spec['major']}.{source_spec['minor']}",
            "target_release": f"{target_spec['major']}.{target_spec['minor']}",
            "source_compose": source_spec.get("compose_name"),
        }

    # Prepare SubmitTest for this spec
    submit_test = SubmitTest(
        shared_archive_filename=shared_archive_filename,
        launch_uuid=launch_uuid,
    )
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
    # Parallel limit precedence for both set and non-set flows:
    # 1) per-request effective (CLI > set > config when available)
    # 2) globally resolved parsed_opts.parallel_limit (handles non-set flow)
    # 3) fallback to top-level [tests].parallel_limit
    submit_test.parallel_limit = (
        effective_values.get("parallel_limit")
        or getattr(resolved_opts, "parallel_limit", None)
        or resolved_opts.tests.get("parallel_limit")
    )
    submit_test.print_header = idx == 1

    # Auto tags if enabled
    submit_test.set_auto_tags(set_name=set_name, architecture=arch, tier=tier)

    # Plan filter
    try:
        # Build additional filters based on RHSM flags
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

        tier_plan_filter = None
        base_plan_filter = None
        if tier:
            tier_config = resolved_opts.tests.get("tier", {})
            tier_plan_filter = generate_tier_plan_filter(
                [tier],
                tier_config,
                upgrade_path,
                additional_filters if additional_filters else None,
            )
            LOGGER.debug(f"Generated plan filter for tier '{tier}': {tier_plan_filter}")
        else:
            base_plan_filter = generate_tier_plan_filter(
                [],
                None,
                upgrade_path,
                additional_filters if additional_filters else None,
            )
            LOGGER.debug(f"Generated base non-tier plan filter: {base_plan_filter}")
        cli_planfilter = getattr(resolved_opts.cli_args, "planfilter", None)
        submit_test.planfilter = cli_planfilter or tier_plan_filter or base_plan_filter
        if specific_plan:
            LOGGER.debug(f"Using specific plan: {specific_plan}")
    except ValueError as e:
        LOGGER.error(f"Failed to generate plan filter for tier '{tier}': {e}")
        return False

    # Prepare TMT context and environment variables
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
        "config",
    ]:
        if hasattr(resolved_opts, attr):
            setattr(temp_opts, attr, getattr(resolved_opts, attr))
    temp_opts.source_spec = source_spec
    temp_opts.target_spec = target_spec
    temp_opts.upgrade_path_alias = upgrade_path
    temp_opts.architectures = [arch]

    auto_env_vars = generate_environment_variables(
        source_spec,
        target_spec,
        has_copr=bool(effective_values.get("copr_api", {}).get("build_references")),
        has_brew=bool(effective_values.get("brew_api", {}).get("build_references")),
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
        set_name,
        arch,
        tier,
        f"{source_spec['major']}.{source_spec['minor']}",
        f"{target_spec['major']}.{target_spec['minor']}",
        source_spec["compose_name"],
        target_spec["compose_name"],
        event=per_set_event,
    )
    # Enrich TMT context with target compose if URL provided
    if "TARGET_COMPOSE_URL" in merged_env_vars:
        from enge.utils.source_target_parser import parse_target_compose_from_url

        target_compose = parse_target_compose_from_url(
            merged_env_vars["TARGET_COMPOSE_URL"]
        )
        if target_compose:
            temp_opts.tmt_context["target_compose"] = target_compose

    # Merge set-specific context first, then CLI --context to allow CLI to override per-set
    set_context = effective_values.get("context", {}) or {}
    # Ensure event is present in TMT context when provided (so it appears in launch attributes and tmt.context)
    if per_set_event:
        temp_opts.tmt_context = merge_tmt_context(
            temp_opts.tmt_context, {"event": per_set_event}
        )
    if set_context:
        temp_opts.tmt_context = merge_tmt_context(temp_opts.tmt_context, set_context)

    # Apply CLI --context overrides last for set mode
    try:
        cli_context_args = getattr(resolved_opts.cli_args, "context", None)
        from enge.utils.source_target_parser import parse_tmt_context

        cli_context = parse_tmt_context(cli_context_args)
        if cli_context:
            temp_opts.tmt_context = merge_tmt_context(
                temp_opts.tmt_context, cli_context
            )
    except ValueError as e:
        LOGGER.error(f"Failed to parse --context: {e}")

    # Apply RHSM-specific settings if flags are set
    only_rhsm_stage_cdn = getattr(resolved_opts.cli_args, "only_rhsm_stage_cdn", False)
    if only_rhsm_stage_cdn:
        merged_env_vars["RHSM_MODE"] = "stage"
        temp_opts.tmt_context["product_phase"] = "rc"
        LOGGER.debug("Applied RHSM stage settings: RHSM_MODE=stage, product_phase=rc")

    submit_test.set_specific_data([arch], merged_env_vars, temp_opts.tmt_context)

    # Artifacts
    # Temporarily override parsed_opts for artifact resolution using a context manager
    with _DispatchParsedOptsContext(temp_opts):
        resolver = artifact_resolver or ArtifactResolver()
        info = resolver.resolve_builds(source_spec["compose_name"])  # source compose
        if not info:
            LOGGER.warning(
                f"No artifact information found for {set_name} tier: {tier} arch: {arch}"
            )
            return False
        # Populate artifacts
        first_build = info[0]
        submit_test.compose = first_build["compose"]
        submit_test.tmt_distro = first_build["distro"]
        submit_test.artifacts.clear()
        for build in info:
            if build.get("build_id") is not None:
                submit_test.add_artifact(
                    artifact_id=str(build["build_id"]),
                    artifact_type=artifact_type,
                    package=build.get("package", temp_opts.project.get("name", "")),
                    nvr=build.get("nvr"),
                )

    # Maybe create RP launch (complete TMT context)
    if rp_request_context and per_set_event in RP_COMPATIBLE_EVENT:
        try:
            complete_tmt_context = submit_test.get_complete_tmt_context()
            complete_tmt_context.update(temp_opts.tmt_context)
            # Ensure architecture is part of launch attributes
            if arch:
                complete_tmt_context["arch"] = arch

            launch_uuid = rp_create_launch(
                context=rp_request_context,
                tmt_context=complete_tmt_context,
                config=resolved_opts.config,
                cli_args=resolved_opts.cli_args,
                dryrun=getattr(resolved_opts.cli_args, "dryrun", False),
            )
            if launch_uuid or getattr(resolved_opts.cli_args, "dryrun", False):
                # For dryrun, add deterministic uniq_id so payload shows expected attributes
                if getattr(resolved_opts.cli_args, "dryrun", False):
                    placeholder_uuid = "00000000-0000-0000-0000-000000000000"
                    complete_tmt_context["uniq_id"] = "-".join(
                        placeholder_uuid.split("-")[:2]
                    )
                else:
                    complete_tmt_context["uniq_id"] = "-".join(
                        launch_uuid.split("-")[:2]
                    )

                launch_uuid_effective = launch_uuid or placeholder_uuid
                submit_test.set_launch_uuid(launch_uuid_effective)
                # Ensure RP env uses UPLOAD_TO_LAUNCH and omits LAUNCH variables
                from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX

                rp_env = {
                    k: v
                    for (k, v) in merged_env_vars.items()
                    if not (
                        k.startswith(TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX)
                        and (k.endswith("LAUNCH") or k.endswith("LAUNCH_DESCRIPTION"))
                    )
                }
                upload_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}UPLOAD_TO_LAUNCH"
                rp_env[upload_key] = launch_uuid_effective

                submit_test.set_specific_data([arch], rp_env, complete_tmt_context)
        except Exception as e:
            LOGGER.error(f"Failed to create ReportPortal launch for request {idx}: {e}")
            return False

    # Send request
    req_header, req_payload = submit_test.build_payload()
    submit_test.send_request(req_payload, req_header)
    return True
