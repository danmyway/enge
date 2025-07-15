#!/usr/bin/env python3
"""
Source and target parser utilities for enge.

This module provides functions to parse source and target specifications
and derive all necessary values for Testing Farm payloads.
"""

import re
from typing import Dict, Tuple, Optional, Any, List, TYPE_CHECKING
from logging import getLogger

if TYPE_CHECKING:
    from enge.utils.opt_manager import ParsedOpts

LOGGER = getLogger(__name__)


def parse_compose_spec(spec: str) -> Dict[str, Any]:
    """
    Parse a compose specification into its components.

    Args:
        spec: Either a version string like "8.10" or full compose name like "RHEL-8.10.0-Nightly"

    Returns:
        Dictionary containing parsed components:
        - major: Major version number
        - minor: Minor version number
        - compose_name: Full compose name
        - is_version_only: True if input was just version, False if full compose name

    Raises:
        ValueError: If the specification format is invalid
    """
    # Try parsing as version number first (e.g., "8.10")
    version_match = re.match(r"^(\d+)\.(\d+)$", spec.strip())
    if version_match:
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
        compose_name = f"RHEL-{major}.{minor}.0-Nightly"
        return {
            "major": major,
            "minor": minor,
            "compose_name": compose_name,
            "is_version_only": True,
        }

    # Try parsing as full compose name (e.g., "RHEL-8.10.0-Nightly")
    compose_match = re.match(r"^RHEL-(\d+)\.(\d+)\.(\d+)-(.+)$", spec.strip())
    if compose_match:
        major = int(compose_match.group(1))
        minor = int(compose_match.group(2))
        # For compose names, we use the actual compose name as provided
        return {
            "major": major,
            "minor": minor,
            "compose_name": spec.strip(),
            "is_version_only": False,
        }

    raise ValueError(
        f"Invalid compose specification: {spec}. Expected format: '8.10' or 'RHEL-8.10.0-Nightly'"
    )


def derive_target_from_source(source_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Derive target specification from source specification.

    Args:
        source_spec: Source specification dictionary from parse_compose_spec

    Returns:
        Dictionary containing target specification:
        - major: Target major version (source_major + 1)
        - minor: Target minor version (source_minor - 6)
        - compose_name: Target compose name
        - is_version_only: True if derived from version, False if from compose name
    """
    target_major = source_spec["major"] + 1
    target_minor = max(0, source_spec["minor"] - 6)  # Ensure non-negative

    # Always create compose name for target
    target_compose_name = f"RHEL-{target_major}.{target_minor}.0-Nightly"

    return {
        "major": target_major,
        "minor": target_minor,
        "compose_name": target_compose_name,
        "is_version_only": source_spec["is_version_only"],
    }


def generate_upgrade_path_alias(
    source_spec: Dict[str, Any], target_spec: Dict[str, Any]
) -> str:
    """
    Generate upgrade path alias from source and target specifications.

    Args:
        source_spec: Source specification dictionary
        target_spec: Target specification dictionary

    Returns:
        Upgrade path alias string (e.g., "8to9")
    """
    return f"{source_spec['major']}to{target_spec['major']}"


def generate_environment_variables(
    source_spec: Dict[str, Any],
    target_spec: Dict[str, Any],
    has_copr: bool = False,
    has_brew: bool = False,
) -> Dict[str, str]:
    """
    Generate environment variables for the Testing Farm payload.

    Args:
        source_spec: Source specification dictionary
        target_spec: Target specification dictionary
        has_copr: Whether --copr is specified
        has_brew: Whether --brew is specified

    Returns:
        Dictionary of environment variables
    """
    env_vars = {
        "SOURCE_RELEASE": f"{source_spec['major']}.{source_spec['minor']}",
        "TARGET_RELEASE": f"{target_spec['major']}.{target_spec['minor']}",
    }

    # Add INSTALL_LEAPP_FROM_COMPOSE=yes only if neither --copr nor --brew is specified
    if not has_copr and not has_brew:
        env_vars["INSTALL_LEAPP_FROM_COMPOSE"] = "yes"
    else:
        env_vars["INSTALL_LEAPP_FROM_COMPOSE"] = "no"

    return env_vars


def generate_tmt_context(
    source_spec: Dict[str, Any], target_spec: Dict[str, Any]
) -> Dict[str, str]:
    """
    Generate TMT context for the Testing Farm payload.

    Args:
        source_spec: Source specification dictionary
        target_spec: Target specification dictionary

    Returns:
        Dictionary of TMT context variables (arch will be set per environment)
    """
    return {
        "distro": f"rhel-{source_spec['major']}.{source_spec['minor']}",
        "target_distro": f"rhel-{target_spec['major']}.{target_spec['minor']}",
    }


def parse_environment_variables(env_args: Optional[list] = None) -> Dict[str, str]:
    """
    Parse environment variables from command line arguments.

    Args:
        env_args: List of environment variable strings in "VAR=VAL" format

    Returns:
        Dictionary of parsed environment variables

    Raises:
        ValueError: If any environment variable is not in correct format
    """
    env_vars = {}

    if not env_args:
        return env_vars

    for env_arg in env_args:
        if "=" not in env_arg:
            raise ValueError(
                f"Invalid environment variable format: {env_arg}. Expected VAR=VAL format."
            )

        var_name, var_value = env_arg.split(
            "=", 1
        )  # Split only on first '=' to handle values with '='
        var_name = var_name.strip()
        var_value = var_value.strip()

        if not var_name:
            raise ValueError(f"Empty variable name in: {env_arg}")

        env_vars[var_name] = var_value
        LOGGER.debug(f"Parsed environment variable: {var_name}={var_value}")

    return env_vars


def merge_environment_variables(
    auto_env_vars: Dict[str, str], cli_env_vars: Dict[str, str]
) -> Dict[str, str]:
    """
    Merge automatically generated environment variables with CLI-provided ones.
    CLI variables take precedence over automatic ones.

    Args:
        auto_env_vars: Automatically generated environment variables
        cli_env_vars: Environment variables from CLI --environment option

    Returns:
        Merged environment variables dictionary
    """
    merged_vars = auto_env_vars.copy()
    merged_vars.update(cli_env_vars)  # CLI vars override automatic ones

    return merged_vars


def parse_architectures(arch_input: List[str]) -> List[str]:
    """
    Parse architecture specification from command line or config.

    Args:
        arch_input: Architecture specification (must be a list of strings)

    Returns:
        List of architectures

    Raises:
        ValueError: If arch_input is empty or invalid

    Examples:
        >>> parse_architectures(["x86_64"])
        ['x86_64']
        >>> parse_architectures(["x86_64", "aarch64"])
        ['x86_64', 'aarch64']
    """
    if not arch_input:
        raise ValueError("Architecture specification cannot be empty")

    if not isinstance(arch_input, list):
        raise ValueError("Architecture specification must be a list of strings")

    architectures = [arch.strip() for arch in arch_input if arch and arch.strip()]

    if not architectures:
        raise ValueError("No valid architectures found in specification")

    LOGGER.debug(f"Parsed architectures: {architectures}")
    return architectures


def generate_tier_plan_filter(
    tiers: List[str], tier_config: Dict[str, str], upgrade_path: str
) -> str:
    """
    Generate plan_filter from tier specifications.

    Args:
        tiers: List of tier names from CLI
        tier_config: Tier configuration mapping from config
        upgrade_path: Upgrade path alias (e.g., "8to9")

    Returns:
        Combined plan_filter string

    Raises:
        ValueError: If tier is not found in configuration

    Examples:
        >>> generate_tier_plan_filter(["tier-smoke"], {"tier-smoke": "tag:smoke"}, "8to9")
        'tag:8to9 & tag:smoke'
    """
    if not tiers:
        return f"tag:{upgrade_path}"

    # Look up tier mappings
    tier_filters = []
    for tier in tiers:
        if tier not in tier_config:
            available_tiers = list(tier_config.keys())
            raise ValueError(
                f"Tier '{tier}' not found in configuration. Available tiers: {available_tiers}"
            )

        tier_filter = tier_config[tier]
        tier_filters.append(tier_filter)
        LOGGER.debug(f"Mapped tier '{tier}' to filter '{tier_filter}'")

    # Combine upgrade path with tier filters using & operator
    all_filters = [f"tag:{upgrade_path}"] + tier_filters + ["enabled:true"]
    combined_filter = " & ".join(all_filters)

    LOGGER.debug(f"Generated plan_filter: {combined_filter}")
    return combined_filter


def parse_source_target_config(
    source: str, target: Optional[str] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parse source and target configurations, deriving target if not provided.

    Args:
        source: Source specification string
        target: Optional target specification string

    Returns:
        Tuple of (source_spec, target_spec) dictionaries

    Raises:
        ValueError: If source or target specifications are invalid
    """
    LOGGER.debug(
        f"Parsing source: {source}, target: {target or 'the default will be derived'}"
    )

    try:
        source_spec = parse_compose_spec(source)
        LOGGER.debug(f"Parsed source spec: {source_spec}")

        if target:
            target_spec = parse_compose_spec(target)
            LOGGER.debug(f"Parsed target spec: {target_spec}")
        else:
            target_spec = derive_target_from_source(source_spec)
            LOGGER.debug(f"Derived target spec: {target_spec}")

        return source_spec, target_spec

    except ValueError as e:
        LOGGER.error(f"Failed to parse source/target configuration: {e}")
        raise


def parse_test_sets(set_names: List[str], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse and merge test set configurations.

    Args:
        set_names: List of test set names to load
        config: Configuration dictionary containing test sets

    Returns:
        Merged configuration dictionary from all specified sets

    Raises:
        ValueError: If any test set is not found in configuration
    """
    if not set_names:
        return {}

    # Get test sets from config
    test_sets = config.get("tests", {}).get("set", {})

    merged_config = {}

    for set_name in set_names:
        if set_name not in test_sets:
            available_sets = list(test_sets.keys())
            raise ValueError(
                f"Test set '{set_name}' not found in configuration. Available sets: {available_sets}"
            )

        set_config = test_sets[set_name]
        LOGGER.debug(f"Loading test set '{set_name}': {set_config}")

        # Merge this set's configuration
        merged_config = merge_test_set_config(merged_config, set_config)

    LOGGER.debug(f"Merged test set configuration: {merged_config}")
    return merged_config


def merge_test_set_config(
    base_config: Dict[str, Any], set_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge test set configuration with base configuration.

    Args:
        base_config: Base configuration dictionary
        set_config: Test set configuration to merge

    Returns:
        Merged configuration dictionary
    """
    merged = base_config.copy()

    for key, value in set_config.items():
        if key in ["copr_api", "brew_api", "environment"]:
            # These are nested dictionaries that should be merged
            if key not in merged:
                merged[key] = {}
            merged[key].update(value)
        elif key == "tiers":
            # Tiers should be combined (not replaced)
            if "tiers" not in merged:
                merged["tiers"] = []
            merged["tiers"].extend(value)
        else:
            # Other keys are replaced (last set wins)
            merged[key] = value

    return merged


def resolve_effective_values(
    cli_args: Any, set_config: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Resolve effective values from CLI args, test sets, and config.
    Priority: CLI > Test Set > Config

    Args:
        cli_args: CLI arguments object
        set_config: Test set configuration
        config: Main configuration

    Returns:
        Dictionary of resolved values
    """
    resolved = {}

    # Resolve source (CLI > Set > Config)
    resolved["source"] = (
        getattr(cli_args, "source", None)
        or set_config.get("source")
        or config.get("tests", {}).get("source")
    )

    # Resolve target (CLI > Set > Config)
    resolved["target"] = (
        getattr(cli_args, "target", None)
        or set_config.get("target")
        or config.get("tests", {}).get("target")
    )

    # Resolve architectures (CLI > Set > Config)
    resolved["architectures"] = (
        getattr(cli_args, "architectures", None)
        or set_config.get("architectures")
        or config.get("tests", {}).get("architectures")
    )

    # Resolve git branch (CLI > Set > Config)
    resolved["git_branch"] = (
        getattr(cli_args, "git_branch", None)
        or set_config.get("git_branch")
        or config.get("tests", {}).get("git_branch")
    )

    # Resolve git url (CLI > Set > Config)
    resolved["git_url"] = (
        getattr(cli_args, "git_url", None)
        or set_config.get("git_url")
        or config.get("tests", {}).get("git_url")
    )

    # Resolve parallel limit (Set > Config, no CLI option)
    resolved["parallel_limit"] = set_config.get("parallel_limit") or config.get(
        "tests", {}
    ).get("parallel_limit")

    # Resolve tiers (CLI > Set)
    resolved["tiers"] = getattr(cli_args, "tier", None) or set_config.get("tiers")

    # Resolve artifact configurations
    resolved["copr_api"] = set_config.get("copr_api", {})
    resolved["brew_api"] = set_config.get("brew_api", {})
    resolved["environment"] = set_config.get("environment", {})

    return resolved


def merge_set_environment_variables(
    auto_env_vars: Dict[str, str],
    set_env_vars: Dict[str, str],
    cli_env_vars: Dict[str, str],
) -> Dict[str, str]:
    """
    Merge environment variables from automatic generation, test sets, and CLI.
    Priority: CLI > Test Set > Automatic

    Args:
        auto_env_vars: Automatically generated environment variables
        set_env_vars: Environment variables from test sets
        cli_env_vars: Environment variables from CLI --environment option

    Returns:
        Merged environment variables dictionary
    """
    merged_vars = auto_env_vars.copy()
    merged_vars.update(set_env_vars)  # Set vars override automatic ones
    merged_vars.update(cli_env_vars)  # CLI vars override everything

    return merged_vars
