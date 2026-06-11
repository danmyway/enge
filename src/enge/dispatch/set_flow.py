import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from enge.utils.source_target_parser import (
    generate_upgrade_path_alias,
    generate_detailed_upgrade_path_alias,
    parse_architectures,
    generate_tier_plan_filter,
    generate_environment_variables,
    parse_environment_variables,
    merge_set_environment_variables,
    merge_tmt_context,
    format_ami_compose_name,
)
from enge.utils.globals import VERBOSE
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

        # Use cached source/target specs from opt_manager initialization
        source_spec = test_set.get("source_spec")
        target_spec = test_set.get("target_spec")
        if not source_spec or not target_spec:
            LOGGER.error(f"No parsed source/target for test set '{set_name}'")
            continue
        upgrade_path = generate_upgrade_path_alias(source_spec, target_spec)
        detailed_upgrade_path = generate_detailed_upgrade_path_alias(
            source_spec, target_spec
        )

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


def process_request_spec(
    idx: int,
    total_expected_requests: int,
    spec: RequestSpec,
    shared_archive_filename: str,
    artifact_type: str,
    artifact_resolver: Optional[ArtifactResolver] = None,
) -> Optional[Dict[str, Any]]:
    """Prepare SubmitTest, optionally create RP launch, and send the request.

    Returns a summary dict on success (for aggregated dispatch table), or None on failure.
    """
    resolved_opts = _get_parsed_opts()
    set_name = spec.set_name
    tier = spec.tier
    specific_plan = spec.plan
    source_spec = spec.source_spec
    target_spec = spec.target_spec
    upgrade_path = spec.upgrade_path
    upgrade_path_detailed = spec.upgrade_path_detailed
    arch = spec.arch
    effective_values = spec.effective_values

    label_parts = [f"[{idx}/{total_expected_requests}]"]
    if set_name:
        label_parts.append(set_name)
    if tier:
        label_parts.append(tier)
    if specific_plan:
        label_parts.append(specific_plan)
    label_parts.append(arch)
    LOGGER.info(" ".join(label_parts), extra={"style": "bold"})

    # RP context (global + per request)
    launch_uuid = None
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

    # Auto tags if enabled
    submit_test.set_auto_tags(
        set_name=set_name,
        architecture=arch,
        tier=tier,
        upgrade_path_tag=upgrade_path_detailed,
    )

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
        elif no_rhsm:
            additional_filters.append("tag:-rhsm")
            LOGGER.info("Excluding RHSM-tagged tests from execution")

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
            LOGGER.log(
                VERBOSE, f"Generated plan filter for tier '{tier}': {tier_plan_filter}"
            )
        else:
            base_plan_filter = generate_tier_plan_filter(
                [],
                None,
                upgrade_path,
                additional_filters if additional_filters else None,
            )
            LOGGER.log(
                VERBOSE, f"Generated base non-tier plan filter: {base_plan_filter}"
            )
        cli_planfilter = getattr(resolved_opts.cli_args, "planfilter", None)
        submit_test.planfilter = cli_planfilter or tier_plan_filter or base_plan_filter
        if specific_plan:
            LOGGER.log(VERBOSE, f"Using specific plan: {specific_plan}")
    except ValueError as e:
        LOGGER.error(f"Failed to generate plan filter for tier '{tier}': {e}")
        return {
            "status": "failed",
            "set_name": set_name,
            "tier": tier,
            "arch": arch,
            "error": str(e),
        }

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

    # Regenerate TMT context with per-set source/target specs
    from enge.utils.source_target_parser import (
        apply_centos_context_overrides,
        generate_tmt_context,
    )

    temp_opts.tmt_context = generate_tmt_context(
        source_spec,
        target_spec,
        event=per_set_event,
        tier=tier,
    )

    # Check CLI args directly, not just test set config
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
        set_name,
        arch,
        tier,
        auto_env_vars.get("SOURCE_RELEASE"),
        auto_env_vars.get("TARGET_RELEASE"),
        source_spec["compose_name"],
        target_spec["compose_name"],
        event=per_set_event,
    )

    temp_opts.tmt_context = apply_centos_context_overrides(
        temp_opts.tmt_context, source_spec, target_spec, merged_env_vars
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

    pool = effective_values.get("pool")
    submit_test.set_specific_data(
        [arch], merged_env_vars, temp_opts.tmt_context, pool=pool
    )

    # Artifacts
    # Temporarily override parsed_opts for artifact resolution using a context manager
    with _DispatchParsedOptsContext(temp_opts):
        resolver = artifact_resolver or ArtifactResolver()
        info = resolver.resolve_builds(source_spec["compose_name"])  # source compose
        if not info:
            LOGGER.warning(
                f"No artifact information found for {set_name} tier: {tier} arch: {arch}"
            )
            return {
                "status": "failed",
                "set_name": set_name,
                "tier": tier,
                "arch": arch,
                "error": "no artifact information found",
            }
        # Populate artifacts
        first_build = info[0]
        # AMI sources: construct compose name with architecture suffix
        # CentOS Stream: use symbolic compose name directly
        # RHEL: use compose from artifact resolution (pinned/repinned)
        if source_spec.get("is_ami_source", False):
            submit_test.compose = format_ami_compose_name(source_spec, arch)
        elif source_spec.get("is_centos_stream", False):
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
                    complete_tmt_context["uniq_id"] = placeholder_uuid
                else:
                    complete_tmt_context["uniq_id"] = launch_uuid

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

                submit_test.set_specific_data(
                    [arch], rp_env, complete_tmt_context, pool=pool
                )
        except Exception as e:
            LOGGER.error(f"Failed to create ReportPortal launch for request {idx}: {e}")
            return {
                "status": "failed",
                "set_name": set_name,
                "tier": tier,
                "arch": arch,
                "error": str(e),
            }

    # Send request
    output_format = getattr(resolved_opts.cli_args, "output_format", "terminal")
    submit_test.compact_output = output_format != "json"
    submit_test.silent_output = output_format == "json"
    req_header, req_payload = submit_test.build_payload()
    submit_test.send_request(req_payload, req_header)

    task_id = None
    if submit_test.log_artifact_url:
        task_id = submit_test.log_artifact_url.rsplit("/", 1)[-1]

    result = {
        "status": "submitted",
        "summary": submit_test.dispatch_summary,
        "set_name": set_name,
        "tier": tier,
        "plan": specific_plan,
        "arch": arch,
        "compose": submit_test.compose,
        "artifacts": [
            {
                "type": a.get("type"),
                "id": a.get("id"),
                "nvr": a.get("nvr"),
                "packages": a.get("packages", []),
            }
            for a in submit_test.artifacts
        ],
        "results_url": submit_test.log_artifact_url,
        "task_id": task_id,
    }
    if getattr(submit_test, "dryrun_payload", None) is not None:
        result["payload"] = submit_test.dryrun_payload
    return result
