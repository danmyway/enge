import logging
from dataclasses import dataclass
from types import SimpleNamespace
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
    apply_centos_context_overrides,
    generate_tmt_context,
    parse_target_compose_from_url,
    parse_tmt_context,
)
from enge.utils.globals import (
    VERBOSE,
    TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX,
    RP_COMPATIBLE_EVENT,
)
from enge.dispatch.tf_send_request import SubmitTest
from enge.dispatch.artifacts import ArtifactResolver
from enge.utils.reportportal_helper import create_launch as rp_create_launch


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


def expand_set_requests(ctx) -> List[RequestSpec]:
    """Build list of RequestSpec from --set configuration."""
    specs: List[RequestSpec] = []
    for test_set in getattr(ctx, "individual_test_sets", []) or []:
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


def _configure_submit_test(spec, ctx):
    """Create and configure a SubmitTest instance."""
    submit_test = SubmitTest(ctx)
    submit_test.api_key = ctx.testing_farm.get("api_key")
    submit_test.tests_git_url = (
        getattr(ctx.cli_args, "git_url", None)
        or spec.effective_values.get("git_url")
        or ctx.tests.get("git_url")
        or ctx.project.get("repo_url")
    )
    submit_test.tests_git_ref = (
        getattr(ctx.cli_args, "git_ref", None)
        or spec.effective_values.get("git_ref")
        or ctx.tests.get("git_ref")
    )
    submit_test.testfilter = getattr(
        ctx.cli_args, "testfilter", None
    ) or spec.effective_values.get("test_filter")
    submit_test.test_name = getattr(ctx.cli_args, "test", None)
    submit_test.plan = spec.plan.rstrip("/") if spec.plan else None
    submit_test.business_unit_tag = ctx.testing_farm.get("cloud_resources_tag")
    submit_test.parallel_limit = (
        spec.effective_values.get("parallel_limit")
        or getattr(ctx, "parallel_limit", None)
        or ctx.tests.get("parallel_limit")
    )

    if spec.source_spec.get("compose_name", "").endswith("-rhui"):
        submit_test.skip_guest_setup = True
        LOGGER.info("RHUI source detected — setting skip_guest_setup=true")

    return submit_test


def _build_plan_filter(spec, ctx):
    """Build the plan filter string, or a failure dict on ValueError."""
    try:
        additional_filters = []
        only_rhsm_mock_cdn = getattr(ctx.cli_args, "only_rhsm_mock_cdn", False)
        no_rhsm = getattr(ctx.cli_args, "no_rhsm", False)
        only_rhsm_stage_cdn = getattr(ctx.cli_args, "only_rhsm_stage_cdn", False)
        if only_rhsm_mock_cdn or only_rhsm_stage_cdn:
            additional_filters.append("tag:rhsm")
        elif no_rhsm:
            additional_filters.append("tag:-rhsm")
            LOGGER.info("Excluding RHSM-tagged tests from execution")

        tier_plan_filter = None
        base_plan_filter = None
        if spec.tier:
            tier_config = ctx.tests.get("tier", {})
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
        cli_planfilter = getattr(ctx.cli_args, "planfilter", None)
        set_planfilter = spec.effective_values.get("plan_filter")
        planfilter = (
            cli_planfilter or set_planfilter or tier_plan_filter or base_plan_filter
        )
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


def _build_tmt_context_and_env(spec, per_set_event, ctx, req_ctx):
    """Build TMT context dict and merged environment variables."""
    tmt_context = generate_tmt_context(
        spec.source_spec,
        spec.target_spec,
        event=per_set_event,
        tier=spec.tier,
    )

    merged_env_vars = merge_set_environment_variables(req_ctx)

    tmt_context = apply_centos_context_overrides(
        tmt_context, spec.source_spec, spec.target_spec, merged_env_vars
    )
    if "TARGET_COMPOSE_URL" in merged_env_vars:
        target_compose = parse_target_compose_from_url(
            merged_env_vars["TARGET_COMPOSE_URL"]
        )
        if target_compose:
            tmt_context["target_compose"] = target_compose

    set_context = spec.effective_values.get("context", {}) or {}
    if per_set_event:
        tmt_context = merge_tmt_context(tmt_context, {"event": per_set_event})
    if set_context:
        tmt_context = merge_tmt_context(tmt_context, set_context)

    try:
        cli_context_args = getattr(ctx.cli_args, "context", None)
        cli_context = parse_tmt_context(cli_context_args)
        if cli_context:
            tmt_context = merge_tmt_context(tmt_context, cli_context)
    except ValueError as e:
        LOGGER.error(f"Failed to parse --context: {e}")

    only_rhsm_stage_cdn = getattr(ctx.cli_args, "only_rhsm_stage_cdn", False)
    if only_rhsm_stage_cdn:
        merged_env_vars["RHSM_MODE"] = "stage"
        tmt_context["product_phase"] = "rc"
        LOGGER.debug("Applied RHSM stage settings: RHSM_MODE=stage, product_phase=rc")

    return tmt_context, merged_env_vars


def _resolve_spec_artifact_refs(spec, ctx):
    """Derive per-spec artifact references with CLI > set-level > run-level precedence.

    Each artifact family (copr, brew) is resolved independently — a CLI
    override in one family must not suppress set-level resolution in the
    other.  Within a family, the effective api dict is a per-key merge of
    the run-level ctx dict (base) with the set-level dict layered over it;
    set-level keys win per-key, empty-string values are dropped (inherit
    the run-level value).  References precedence: CLI > merged dict's
    build_references > run-level references.
    """
    cli_copr = getattr(ctx.cli_args, "copr", None)
    cli_brew = getattr(ctx.cli_args, "brew", None)

    set_copr_api = spec.effective_values.get("copr_api", {})
    set_brew_api = spec.effective_values.get("brew_api", {})

    if cli_copr:
        copr_refs = ctx.copr_references
        copr_api = ctx.copr_api
    else:
        copr_api = {
            **ctx.copr_api,
            **{k: v for k, v in set_copr_api.items() if v != ""},
        }
        merged_copr_refs = copr_api.get("build_references")
        if merged_copr_refs:
            copr_refs = (
                list(merged_copr_refs)
                if isinstance(merged_copr_refs, list)
                else [merged_copr_refs]
            )
        else:
            copr_refs = ctx.copr_references

    if cli_brew:
        brew_refs = ctx.brew_references
        brew_api = ctx.brew_api
    else:
        brew_api = {
            **ctx.brew_api,
            **{k: v for k, v in set_brew_api.items() if v != ""},
        }
        merged_brew_refs = brew_api.get("build_references")
        if merged_brew_refs:
            brew_refs = (
                list(merged_brew_refs)
                if isinstance(merged_brew_refs, list)
                else [merged_brew_refs]
            )
        else:
            brew_refs = ctx.brew_references

    return {
        "copr_references": copr_refs,
        "copr_reference": copr_refs[0] if copr_refs else None,
        "copr_api": copr_api,
        "brew_references": brew_refs,
        "brew_reference": brew_refs[0] if brew_refs else None,
        "brew_api": brew_api,
    }


def _resolve_artifacts(
    spec, submit_test, tmt_context, ctx, artifact_type, artifact_resolver
):
    """Resolve build artifacts; returns a failure dict or None on success."""
    artifact_refs = _resolve_spec_artifact_refs(spec, ctx)
    spec_ctx = SimpleNamespace(
        cli_args=ctx.cli_args,
        copr_reference=artifact_refs["copr_reference"],
        copr_references=artifact_refs["copr_references"],
        copr_api=artifact_refs["copr_api"],
        brew_reference=artifact_refs["brew_reference"],
        brew_references=artifact_refs["brew_references"],
        brew_api=artifact_refs["brew_api"],
        project=ctx.project,
        tmt_context=tmt_context,
        source_spec=spec.source_spec,
    )
    resolver = artifact_resolver or ArtifactResolver()
    info = resolver.resolve_builds(spec.source_spec["compose_name"], ctx=spec_ctx)
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


def _create_launch(spec, submit_test, rp_context, merged_env_vars, ctx):
    """Create RP launch if applicable; returns the launch UUID, a failure dict, or None."""
    per_set_event = rp_context["event"] if rp_context else None
    if not (rp_context and per_set_event in RP_COMPATIBLE_EVENT):
        return None
    try:
        complete_tmt_context = submit_test.get_complete_tmt_context()
        complete_tmt_context.update(submit_test.set_tmt_context or {})
        if spec.arch:
            complete_tmt_context["arch"] = spec.arch

        launch_uuid = rp_create_launch(
            ctx=ctx,
            context=rp_context,
            tmt_context=complete_tmt_context,
            dryrun=getattr(ctx.cli_args, "dryrun", False),
        )
        if launch_uuid or getattr(ctx.cli_args, "dryrun", False):
            if getattr(ctx.cli_args, "dryrun", False):
                placeholder_uuid = "00000000-0000-0000-0000-000000000000"
                complete_tmt_context["uniq_id"] = placeholder_uuid
            else:
                complete_tmt_context["uniq_id"] = launch_uuid

            launch_uuid_effective = launch_uuid or placeholder_uuid
            submit_test.set_launch_uuid(launch_uuid_effective)

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
            return launch_uuid
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


def _build_request_context(spec, per_set_event, ctx, artifact_type):
    """Construct the RequestContext for one dispatch request."""
    copr_artifact = getattr(ctx.cli_args, "copr", None)
    brew_artifact = getattr(ctx.cli_args, "brew", None)
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
    cli_env_args = getattr(ctx.cli_args, "environment", None)
    cli_env_vars = parse_environment_variables(cli_env_args)
    set_env_vars = spec.effective_values.get("environment", {})
    from enge.dispatch.context import RequestContext

    return RequestContext(
        spec=spec,
        config=ctx.config,
        cli_args=ctx.cli_args,
        api_key=ctx.testing_farm.get("api_key"),
        event=per_set_event,
        auto_env_vars=auto_env_vars,
        set_env_vars=set_env_vars,
        cli_env_vars=cli_env_vars,
        set_reportportal_config=spec.effective_values.get("reportportal", {}),
        artifact_type=artifact_type,
    )


def _send_and_collect(submit_test, ctx, spec, manifest_writer=None, launch_uuid=None):
    """Send the TF request and assemble the result dict."""
    output_format = getattr(ctx.cli_args, "output_format", "terminal")
    submit_test.compact_output = output_format != "json"
    submit_test.silent_output = output_format == "json"
    req_header, req_payload = submit_test.build_payload()
    submit_test.send_request(req_payload, req_header)

    task_id = None
    if submit_test.log_artifact_url:
        task_id = submit_test.log_artifact_url.rsplit("/", 1)[-1]

    if task_id and manifest_writer and not getattr(ctx.cli_args, "dryrun", False):
        from pathlib import Path

        manifest_writer.add_request(
            task_id,
            set_name=spec.set_name,
            tier=spec.tier,
            arch=spec.arch,
            plan=spec.plan,
            source_compose=submit_test.compose,
            target_compose=submit_test.target_compose,
            artifacts_url=submit_test.log_artifact_url,
            launch_uuid=launch_uuid,
        )
        manifest_writer.flush(Path(ctx.manifest_runs_dir), Path(ctx.manifest_latest))

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
    artifact_type: str,
    artifact_resolver: Optional[ArtifactResolver] = None,
    *,
    ctx,
    manifest_writer=None,
) -> Optional[Dict[str, Any]]:
    """Prepare SubmitTest, optionally create RP launch, and send the request."""
    per_set_event = spec.effective_values.get("event") or getattr(
        ctx.cli_args, "event", None
    )

    _log_request(spec, idx, total_expected_requests)

    submit_test = _configure_submit_test(spec, ctx)

    plan_filter_result = _build_plan_filter(spec, ctx)
    if isinstance(plan_filter_result, dict):
        return plan_filter_result
    submit_test.planfilter = plan_filter_result

    req_ctx = _build_request_context(spec, per_set_event, ctx, artifact_type)
    tmt_context, merged_env_vars = _build_tmt_context_and_env(
        spec, per_set_event, ctx, req_ctx
    )

    pool = spec.effective_values.get("pool")
    submit_test.set_specific_data([spec.arch], merged_env_vars, tmt_context, pool=pool)

    failure = _resolve_artifacts(
        spec, submit_test, tmt_context, ctx, artifact_type, artifact_resolver
    )
    if failure:
        return failure

    rp_context = _build_rp_context(spec, per_set_event)
    launch_result = _create_launch(spec, submit_test, rp_context, merged_env_vars, ctx)
    if isinstance(launch_result, dict):
        return launch_result

    return _send_and_collect(
        submit_test,
        ctx,
        spec,
        manifest_writer=manifest_writer,
        launch_uuid=launch_result,
    )
