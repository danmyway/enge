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


def _log_request(spec, idx, total):
    """Log the request header."""
    label_parts = [f"[{idx}/{total}]"]
    if spec.set_name:
        label_parts.append(spec.set_name)
    if spec.tier:
        label_parts.append(spec.tier)
    if spec.plan:
        label_parts.append(spec.plan)
    label_parts.append(spec.arch)
    LOGGER.info(" ".join(label_parts), extra={"style": "bold"})


def _build_rp_context(spec, per_set_event):
    """Build ReportPortal request context dict, or None if no event."""
    if not per_set_event:
        return None
    return {
        "event": per_set_event,
        "set_name": spec.set_name,
        "tier": spec.tier,
        "architecture": spec.arch,
        "source_release": f"{spec.source_spec['major']}.{spec.source_spec['minor']}",
        "target_release": f"{spec.target_spec['major']}.{spec.target_spec['minor']}",
        "source_compose": spec.source_spec.get("compose_name"),
    }


def _configure_submit_test(spec, resolved_opts, shared_archive_filename):
    """Create and configure a SubmitTest instance."""
    submit_test = SubmitTest(
        shared_archive_filename=shared_archive_filename,
        launch_uuid=None,
    )
    submit_test.api_key = resolved_opts.testing_farm.get("api_key")
    submit_test.tests_git_url = (
        getattr(resolved_opts.cli_args, "git_url", None)
        or spec.effective_values.get("git_url")
        or resolved_opts.tests.get("git_url")
        or resolved_opts.project.get("repo_url")
    )
    submit_test.tests_git_ref = (
        getattr(resolved_opts.cli_args, "git_ref", None)
        or spec.effective_values.get("git_ref")
        or resolved_opts.tests.get("git_ref")
    )
    submit_test.testfilter = getattr(resolved_opts.cli_args, "testfilter", None)
    submit_test.test_name = getattr(resolved_opts.cli_args, "test", None)
    submit_test.plan = spec.plan.rstrip("/") if spec.plan else None
    submit_test.business_unit_tag = resolved_opts.testing_farm.get(
        "cloud_resources_tag"
    )
    submit_test.parallel_limit = (
        spec.effective_values.get("parallel_limit")
        or getattr(resolved_opts, "parallel_limit", None)
        or resolved_opts.tests.get("parallel_limit")
    )

    submit_test.set_auto_tags(
        set_name=spec.set_name,
        architecture=spec.arch,
        tier=spec.tier,
        upgrade_path_tag=spec.upgrade_path_detailed,
    )
    return submit_test


def _build_plan_filter(spec, resolved_opts):
    """Build the plan filter string, or a failure dict on ValueError."""
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
        elif no_rhsm:
            additional_filters.append("tag:-rhsm")
            LOGGER.info("Excluding RHSM-tagged tests from execution")

        tier_plan_filter = None
        base_plan_filter = None
        if spec.tier:
            tier_config = resolved_opts.tests.get("tier", {})
            tier_plan_filter = generate_tier_plan_filter(
                [spec.tier],
                tier_config,
                spec.upgrade_path,
                additional_filters if additional_filters else None,
            )
            LOGGER.log(
                VERBOSE,
                f"Generated plan filter for tier '{spec.tier}': {tier_plan_filter}",
            )
        else:
            base_plan_filter = generate_tier_plan_filter(
                [],
                None,
                spec.upgrade_path,
                additional_filters if additional_filters else None,
            )
            LOGGER.log(
                VERBOSE, f"Generated base non-tier plan filter: {base_plan_filter}"
            )
        cli_planfilter = getattr(resolved_opts.cli_args, "planfilter", None)
        planfilter = cli_planfilter or tier_plan_filter or base_plan_filter
        if spec.plan:
            LOGGER.log(VERBOSE, f"Using specific plan: {spec.plan}")
        return planfilter
    except ValueError as e:
        LOGGER.error(f"Failed to generate plan filter for tier '{spec.tier}': {e}")
        return {
            "status": "failed",
            "set_name": spec.set_name,
            "tier": spec.tier,
            "arch": spec.arch,
            "error": str(e),
        }


class _TempOpts:
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


def _build_tmt_context_and_env(spec, per_set_event, resolved_opts, ctx):
    """Build TMT context, environment variables, and TempOpts."""
    temp_opts = _TempOpts()
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
    temp_opts.source_spec = spec.source_spec
    temp_opts.target_spec = spec.target_spec
    temp_opts.upgrade_path_alias = spec.upgrade_path
    temp_opts.architectures = [spec.arch]

    from enge.utils.source_target_parser import (
        apply_centos_context_overrides,
        generate_tmt_context,
    )

    temp_opts.tmt_context = generate_tmt_context(
        spec.source_spec,
        spec.target_spec,
        event=per_set_event,
        tier=spec.tier,
    )

    merged_env_vars = merge_set_environment_variables(ctx)

    temp_opts.tmt_context = apply_centos_context_overrides(
        temp_opts.tmt_context, spec.source_spec, spec.target_spec, merged_env_vars
    )
    if "TARGET_COMPOSE_URL" in merged_env_vars:
        from enge.utils.source_target_parser import parse_target_compose_from_url

        target_compose = parse_target_compose_from_url(
            merged_env_vars["TARGET_COMPOSE_URL"]
        )
        if target_compose:
            temp_opts.tmt_context["target_compose"] = target_compose

    set_context = spec.effective_values.get("context", {}) or {}
    if per_set_event:
        temp_opts.tmt_context = merge_tmt_context(
            temp_opts.tmt_context, {"event": per_set_event}
        )
    if set_context:
        temp_opts.tmt_context = merge_tmt_context(temp_opts.tmt_context, set_context)

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

    only_rhsm_stage_cdn = getattr(resolved_opts.cli_args, "only_rhsm_stage_cdn", False)
    if only_rhsm_stage_cdn:
        merged_env_vars["RHSM_MODE"] = "stage"
        temp_opts.tmt_context["product_phase"] = "rc"
        LOGGER.debug("Applied RHSM stage settings: RHSM_MODE=stage, product_phase=rc")

    return temp_opts, merged_env_vars


def _resolve_artifacts(spec, submit_test, temp_opts, artifact_type, artifact_resolver):
    """Resolve build artifacts; returns a failure dict or None on success."""
    with _DispatchParsedOptsContext(temp_opts):
        resolver = artifact_resolver or ArtifactResolver()
        info = resolver.resolve_builds(spec.source_spec["compose_name"])
        if not info:
            LOGGER.warning(
                f"No artifact information found for {spec.set_name}"
                f" tier: {spec.tier} arch: {spec.arch}"
            )
            return {
                "status": "failed",
                "set_name": spec.set_name,
                "tier": spec.tier,
                "arch": spec.arch,
                "error": "no artifact information found",
            }
        first_build = info[0]
        if spec.source_spec.get("is_ami_source", False):
            submit_test.compose = format_ami_compose_name(spec.source_spec, spec.arch)
        elif spec.source_spec.get("is_centos_stream", False):
            submit_test.compose = spec.source_spec["compose_name"]
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
    return None


def _create_launch(spec, submit_test, rp_context, merged_env_vars, resolved_opts):
    """Create RP launch if applicable; returns a failure dict or None on success."""
    per_set_event = rp_context["event"] if rp_context else None
    if not (rp_context and per_set_event in RP_COMPATIBLE_EVENT):
        return None
    try:
        complete_tmt_context = submit_test.get_complete_tmt_context()
        complete_tmt_context.update(submit_test.set_tmt_context or {})
        if spec.arch:
            complete_tmt_context["arch"] = spec.arch

        launch_uuid = rp_create_launch(
            context=rp_context,
            tmt_context=complete_tmt_context,
            config=resolved_opts.config,
            cli_args=resolved_opts.cli_args,
            dryrun=getattr(resolved_opts.cli_args, "dryrun", False),
        )
        if launch_uuid or getattr(resolved_opts.cli_args, "dryrun", False):
            if getattr(resolved_opts.cli_args, "dryrun", False):
                placeholder_uuid = "00000000-0000-0000-0000-000000000000"
                complete_tmt_context["uniq_id"] = placeholder_uuid
            else:
                complete_tmt_context["uniq_id"] = launch_uuid

            launch_uuid_effective = launch_uuid or placeholder_uuid
            submit_test.set_launch_uuid(launch_uuid_effective)
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

            pool = spec.effective_values.get("pool")
            submit_test.set_specific_data(
                [spec.arch], rp_env, complete_tmt_context, pool=pool
            )
    except Exception as e:
        LOGGER.error(f"Failed to create ReportPortal launch: {e}")
        return {
            "status": "failed",
            "set_name": spec.set_name,
            "tier": spec.tier,
            "arch": spec.arch,
            "error": str(e),
        }
    return None


def _build_request_context(
    spec, per_set_event, resolved_opts, shared_archive_filename, artifact_type
):
    """Construct the RequestContext for one dispatch request."""
    copr_artifact = getattr(resolved_opts.cli_args, "copr", None)
    brew_artifact = getattr(resolved_opts.cli_args, "brew", None)
    auto_env_vars = generate_environment_variables(
        spec.source_spec,
        spec.target_spec,
        has_copr=bool(
            copr_artifact
            or spec.effective_values.get("copr_api", {}).get("build_references")
        ),
        has_brew=bool(
            brew_artifact
            or spec.effective_values.get("brew_api", {}).get("build_references")
        ),
    )
    cli_env_args = getattr(resolved_opts.cli_args, "environment", None)
    cli_env_vars = parse_environment_variables(cli_env_args)
    set_env_vars = spec.effective_values.get("environment", {})
    from enge.dispatch.context import RequestContext

    return RequestContext(
        spec=spec,
        config=resolved_opts.config,
        cli_args=resolved_opts.cli_args,
        api_key=resolved_opts.testing_farm.get("api_key"),
        event=per_set_event,
        auto_env_vars=auto_env_vars,
        set_env_vars=set_env_vars,
        cli_env_vars=cli_env_vars,
        set_reportportal_config=spec.effective_values.get("reportportal", {}),
        shared_archive_filename=shared_archive_filename,
        artifact_type=artifact_type,
    )


def _send_and_collect(submit_test, resolved_opts, spec):
    """Send the TF request and assemble the result dict."""
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
        "set_name": spec.set_name,
        "tier": spec.tier,
        "plan": spec.plan,
        "arch": spec.arch,
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


def process_request_spec(
    idx: int,
    total_expected_requests: int,
    spec: RequestSpec,
    shared_archive_filename: str,
    artifact_type: str,
    artifact_resolver: Optional[ArtifactResolver] = None,
) -> Optional[Dict[str, Any]]:
    """Prepare SubmitTest, optionally create RP launch, and send the request."""
    resolved_opts = _get_parsed_opts()
    per_set_event = spec.effective_values.get("event") or getattr(
        resolved_opts.cli_args, "event", None
    )

    _log_request(spec, idx, total_expected_requests)

    submit_test = _configure_submit_test(spec, resolved_opts, shared_archive_filename)

    plan_filter_result = _build_plan_filter(spec, resolved_opts)
    if isinstance(plan_filter_result, dict):
        return plan_filter_result
    submit_test.planfilter = plan_filter_result

    ctx = _build_request_context(
        spec, per_set_event, resolved_opts, shared_archive_filename, artifact_type
    )
    temp_opts, merged_env_vars = _build_tmt_context_and_env(
        spec, per_set_event, resolved_opts, ctx
    )

    pool = spec.effective_values.get("pool")
    submit_test.set_specific_data(
        [spec.arch], merged_env_vars, temp_opts.tmt_context, pool=pool
    )

    failure = _resolve_artifacts(
        spec, submit_test, temp_opts, artifact_type, artifact_resolver
    )
    if failure:
        return failure

    rp_context = _build_rp_context(spec, per_set_event)
    launch_failure = _create_launch(
        spec, submit_test, rp_context, merged_env_vars, resolved_opts
    )
    if launch_failure:
        return launch_failure

    return _send_and_collect(submit_test, resolved_opts, spec)
