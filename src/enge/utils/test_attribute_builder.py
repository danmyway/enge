"""Standalone builder for test-dispatch attributes.

Extracted from ``ParsedOpts._initialize_test_attributes`` so that
``AppContext`` can delegate to a pure function instead of the singleton.
"""

import logging
import os

from enge.utils.errors import ConfigurationError, ValidationError
from enge.utils.globals import PARALLEL_LIMIT_DEFAULT, VERBOSE
from enge.utils.source_target_parser import (
    apply_centos_context_overrides,
    generate_environment_variables,
    generate_tmt_context,
    generate_upgrade_path_alias,
    merge_set_environment_variables,
    merge_tmt_context,
    parse_architectures,
    parse_environment_variables,
    parse_source_target_config,
    parse_target_compose_from_url,
    parse_tmt_context,
    resolve_effective_values,
    validate_ami_architectures,
)

logger = logging.getLogger(__name__)


def _resolve_preset(set_config, config, *, set_name="<unknown>"):
    """Merge a preset layer under *set_config* if it carries ``extends``.

    Returns a new dict with ``extends`` stripped.  Keys present in the set
    (and not ``""`` / ``None``) wholly replace the preset's value — nested
    tables are NOT recursively merged.
    """
    raw = dict(set_config)
    preset_name = raw.pop("extends", None)
    if not preset_name:
        return raw

    presets = config.get("tests", {}).get("preset", {})
    if not isinstance(presets, dict) or preset_name not in presets:
        available = sorted(presets.keys()) if isinstance(presets, dict) else []
        raise ConfigurationError(
            f"Test set extends unknown preset '{preset_name}'. "
            f"Available presets: {available}"
        )

    preset = presets[preset_name]
    if "extends" in preset:
        raise ConfigurationError(
            f"Preset '{preset_name}' itself carries 'extends' "
            f"(targets '{preset['extends']}'); chained presets are not allowed"
        )

    merged = {}
    for key, preset_val in preset.items():
        set_val = raw.get(key)
        if key in raw and set_val not in (None, ""):
            merged[key] = set_val
        elif key in raw and set_val in (None, ""):
            if preset_val not in (None, ""):
                logger.warning(
                    "set '%s': key '%s' is empty " "— inheriting preset '%s' value %r",
                    set_name,
                    key,
                    preset_name,
                    preset_val,
                )
                merged[key] = preset_val
            else:
                logger.warning(
                    "set '%s': preset '%s' key '%s' is empty "
                    "— resolution will fall to [tests]",
                    set_name,
                    preset_name,
                    key,
                )
        else:
            if preset_val in (None, ""):
                logger.warning(
                    "set '%s': preset '%s' key '%s' is empty "
                    "— resolution will fall to [tests]",
                    set_name,
                    preset_name,
                    key,
                )
            else:
                merged[key] = preset_val

    for key, val in raw.items():
        if key not in merged:
            merged[key] = val

    return merged


def build_test_attributes(cli_args, config):  # noqa: C901
    """Build all test-dispatch attributes from *cli_args* and *config*.

    Returns a dict whose keys correspond 1-to-1 with the properties that
    ``AppContext`` exposes for the dispatch/set_flow pipeline.
    """
    tests = config.get("tests", {})
    copr_api_cfg = config.get("copr_api", {})
    brew_api_cfg = config.get("brew_api", {})

    # ------------------------------------------------------------------
    # Handle test sets first
    # ------------------------------------------------------------------
    cli_sets = getattr(cli_args, "set", None)
    if cli_sets:
        try:
            individual_test_sets = []
            for set_name in cli_sets:
                set_config = _resolve_preset(
                    config["tests"]["set"][set_name], config, set_name=set_name
                )
                logger.log(VERBOSE, f"Processing test set '{set_name}': {set_config}")

                effective_values = resolve_effective_values(
                    cli_args, set_config, config
                )

                if not effective_values.get("git_ref"):
                    raise ConfigurationError(
                        f"Missing effective git_ref (CLI/Set/Config) for set '{set_name}'"
                    )

                cli_arch = getattr(cli_args, "architectures", None)
                set_arch = set_config.get("architectures")
                if cli_arch and set_arch:
                    try:
                        cli_arch_set = set([str(a).strip() for a in cli_arch if a])
                        set_arch_set = set([str(a).strip() for a in set_arch if a])
                        if (
                            cli_arch_set
                            and set_arch_set
                            and cli_arch_set != set_arch_set
                        ):
                            logger.warning(
                                "CLI --architectures overrides [tests.set.%s].architectures (values differ: %s != %s)",
                                set_name,
                                sorted(list(set_arch_set)),
                                sorted(list(cli_arch_set)),
                            )
                    except Exception:  # noqa: BLE001
                        pass

                set_source = effective_values.get("source")
                set_target = effective_values.get("target")
                if set_source:
                    set_source_spec, set_target_spec = parse_source_target_config(
                        set_source, set_target, config
                    )
                    set_archs = effective_values.get("architectures", [])
                    if set_archs and set_source_spec:
                        validate_ami_architectures(set_source_spec, set_archs)
                else:
                    set_source_spec, set_target_spec = None, None

                individual_test_sets.append(
                    {
                        "name": set_name,
                        "config": set_config,
                        "effective_values": effective_values,
                        "source_spec": set_source_spec,
                        "target_spec": set_target_spec,
                    }
                )

            logger.info(f"Loaded test sets: {', '.join(cli_sets)}")
        except ValueError as e:
            logger.critical(f"Failed to load test sets: {e}")
            raise ConfigurationError("Failed to load test sets") from e

        first_set_values = individual_test_sets[0]["effective_values"]
        effective_values = first_set_values

    else:
        individual_test_sets = []
        effective_values = resolve_effective_values(cli_args, {}, config)

        if not effective_values.get("git_ref"):
            raise ConfigurationError("Missing effective git_ref (CLI/Config)")

    # ------------------------------------------------------------------
    # Warn if CLI architectures override [tests].architectures (non-set)
    # ------------------------------------------------------------------
    cli_arch = getattr(cli_args, "architectures", None)
    if not cli_sets and cli_arch is not None:
        cfg_arch = tests.get("architectures")
        if cfg_arch:
            try:
                cli_arch_set = set([str(a).strip() for a in cli_arch if a])
                cfg_arch_set = set([str(a).strip() for a in cfg_arch if a])
                if cli_arch_set and cfg_arch_set and cli_arch_set != cfg_arch_set:
                    logger.warning(
                        "CLI --architectures overrides [tests].architectures (values differ: %s != %s)",
                        sorted(list(cfg_arch_set)),
                        sorted(list(cli_arch_set)),
                    )
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Parallel limit
    # ------------------------------------------------------------------
    parallel_limit = (
        effective_values.get("parallel_limit")
        or tests.get("parallel_limit")
        or PARALLEL_LIMIT_DEFAULT
    )

    # ------------------------------------------------------------------
    # Artifact references
    # ------------------------------------------------------------------
    copr_artifact = getattr(cli_args, "copr", None)
    brew_artifact = getattr(cli_args, "brew", None)
    set_copr_api = effective_values.get("copr_api", {})
    set_brew_api = effective_values.get("brew_api", {})

    copr_references = []
    if copr_artifact:
        if isinstance(copr_artifact, list):
            for artifact in copr_artifact:
                if hasattr(artifact, "ref") and artifact.ref:
                    copr_references.extend(
                        artifact.ref
                        if isinstance(artifact.ref, list)
                        else [artifact.ref]
                    )
        else:
            if hasattr(copr_artifact, "ref") and copr_artifact.ref:
                copr_references.extend(
                    copr_artifact.ref
                    if isinstance(copr_artifact.ref, list)
                    else [copr_artifact.ref]
                )
    else:
        config_ref = set_copr_api.get("build_references") or copr_api_cfg.get(
            "build_references"
        )
        if config_ref:
            if isinstance(config_ref, list):
                copr_references.extend(config_ref)
            else:
                copr_references.append(config_ref)

    brew_references = []
    if brew_artifact:
        if isinstance(brew_artifact, list):
            for artifact in brew_artifact:
                if hasattr(artifact, "ref") and artifact.ref:
                    brew_references.extend(
                        artifact.ref
                        if isinstance(artifact.ref, list)
                        else [artifact.ref]
                    )
        else:
            if hasattr(brew_artifact, "ref") and brew_artifact.ref:
                brew_references.extend(
                    brew_artifact.ref
                    if isinstance(brew_artifact.ref, list)
                    else [brew_artifact.ref]
                )
    else:
        config_ref = set_brew_api.get("build_references") or brew_api_cfg.get(
            "build_references"
        )
        if config_ref:
            if isinstance(config_ref, list):
                brew_references.extend(config_ref)
            else:
                brew_references.append(config_ref)

    copr_reference = copr_references[0] if copr_references else None
    brew_reference = brew_references[0] if brew_references else None

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------
    cli_plans = getattr(cli_args, "plan", None)
    config_plans = tests.get("plans", [])
    plans = cli_plans or config_plans or []

    # ------------------------------------------------------------------
    # Source / target
    # ------------------------------------------------------------------
    source_value = effective_values.get("source")
    target_value = effective_values.get("target")

    if not source_value:
        logger.critical("Source compose specification is required!")
        raise ValidationError("Source compose specification is required")

    try:
        if cli_sets and individual_test_sets:
            source_spec = individual_test_sets[0]["source_spec"]
            target_spec = individual_test_sets[0]["target_spec"]
        else:
            source_spec, target_spec = parse_source_target_config(
                source_value, target_value, config
            )

        upgrade_path_alias = generate_upgrade_path_alias(source_spec, target_spec)

        copr_artifact = getattr(cli_args, "copr", None)
        brew_artifact = getattr(cli_args, "brew", None)
        auto_env_vars = generate_environment_variables(
            source_spec,
            target_spec,
            has_copr=bool(copr_artifact or copr_references),
            has_brew=bool(brew_artifact or brew_references),
        )

        cli_env_args = getattr(cli_args, "environment", None)
        cli_env_vars = parse_environment_variables(cli_env_args)
        set_env_vars = effective_values.get("environment", {})

        environment_variables = merge_set_environment_variables(
            auto_env_vars,
            set_env_vars,
            cli_env_vars,
            config,
            cli_args,
            effective_values.get("reportportal", {}),
            None,
            None,
            None,
            auto_env_vars.get("SOURCE_RELEASE"),
            auto_env_vars.get("TARGET_RELEASE"),
            source_spec["compose_name"],
            target_spec["compose_name"],
            event=effective_values.get("event"),
        )

        arch_input = effective_values.get("architectures")
        if not arch_input:
            logger.critical("No architectures specified in CLI or config!")
            raise ValidationError("No architectures specified in CLI or config")
        architectures = parse_architectures(arch_input)
        validate_ami_architectures(source_spec, architectures)

        pool = effective_values.get("pool")
        effective_tiers = effective_values.get("tiers")

        if effective_tiers and not getattr(cli_args, "tier", None):
            tier_config = tests.get("tier", {})
            if tier_config:
                for tier in effective_tiers:
                    if tier not in tier_config:
                        available_tiers = list(tier_config.keys())
                        logger.error(
                            f"Tier '{tier}' from config not found in tier configuration. Available tiers: {available_tiers}"
                        )
                        raise ValidationError(
                            f"Tier '{tier}' from config not found in tier configuration"
                        )

        first_tier = None
        if effective_tiers and len(effective_tiers) > 0:
            first_tier = effective_tiers[0]
        tmt_context = generate_tmt_context(
            source_spec,
            target_spec,
            event=effective_values.get("event"),
            tier=first_tier,
        )

        tmt_context = apply_centos_context_overrides(
            tmt_context,
            source_spec,
            target_spec,
            environment_variables,
        )

        if not cli_sets:
            config_context = effective_values.get("context", {}) or {}
            if config_context:
                tmt_context = merge_tmt_context(tmt_context, config_context)
            try:
                cli_context_args = getattr(cli_args, "context", None)
                cli_context = parse_tmt_context(cli_context_args)
                if cli_context:
                    tmt_context = merge_tmt_context(tmt_context, cli_context)
            except ValueError as e:
                logger.critical(f"Failed to parse --context: {e}")
                raise ValidationError("Invalid --context format") from e

        only_rhsm_stage_cdn = getattr(cli_args, "only_rhsm_stage_cdn", False)
        if only_rhsm_stage_cdn:
            environment_variables["RHSM_MODE"] = "stage"
            tmt_context["product_phase"] = "rc"
            logger.info(
                "Applied --only-rhsm-stage-cdn (RHSM_MODE=stage, product_phase=rc)"
            )

        cli_planfilter = getattr(cli_args, "planfilter", None)
        if cli_planfilter:
            plan_filter = cli_planfilter
            logger.info(f"Using CLI plan filter: {plan_filter}")
        else:
            plan_filter = None

        if not cli_sets:
            logger.info(f"Source: {source_spec['compose_name']}")
            logger.info(f"Upgrade path: {upgrade_path_alias}")
            if len(architectures) == 1:
                logger.info(f"Architecture: {architectures[0]}")
            else:
                logger.info(f"Architectures: {', '.join(architectures)}")
            if effective_tiers:
                logger.info(f"Tiers: {', '.join(effective_tiers)}")

        if "TARGET_COMPOSE_URL" in cli_env_vars:
            logger.debug("Target compose URL specified.")
            target_compose_url = cli_env_vars["TARGET_COMPOSE_URL"]
            target_compose = parse_target_compose_from_url(target_compose_url)
            if target_compose:
                logger.info(f"Target compose: {target_compose}")
            else:
                logger.info(
                    f"Target compose: {os.path.basename(target_compose_url.strip('/'))}"
                )

        for var_name, cli_value in cli_env_vars.items():
            if var_name in auto_env_vars and auto_env_vars[var_name] != cli_value:
                logger.log(
                    VERBOSE,
                    f"Environment variable {var_name} overridden: {auto_env_vars[var_name]} -> {cli_value}",
                )

        if set_env_vars:
            logger.debug(f"Test set environment variables: {set_env_vars}")
            for var_name, set_value in set_env_vars.items():
                if var_name in auto_env_vars and auto_env_vars[var_name] != set_value:
                    logger.log(
                        VERBOSE,
                        f"Environment variable {var_name} overridden by test set: {auto_env_vars[var_name]} -> {set_value}",
                    )

    except ValueError as e:
        logger.critical(f"Failed to parse source/target configuration: {e}")
        raise ValidationError("Failed to parse source/target configuration") from e

    return {
        "individual_test_sets": individual_test_sets,
        "parallel_limit": parallel_limit,
        "copr_references": copr_references,
        "brew_references": brew_references,
        "copr_reference": copr_reference,
        "brew_reference": brew_reference,
        "plans": plans,
        "source_spec": source_spec,
        "target_spec": target_spec,
        "upgrade_path_alias": upgrade_path_alias,
        "environment_variables": environment_variables,
        "architectures": architectures,
        "pool": pool,
        "effective_tiers": effective_tiers,
        "tmt_context": tmt_context,
        "plan_filter": plan_filter,
        "copr_api": copr_api_cfg,
        "brew_api": brew_api_cfg,
    }
