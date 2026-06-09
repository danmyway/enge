#!/usr/bin/env python3
"""
Source and target parser utilities for enge.

This module provides functions to parse source and target specifications
and derive all necessary values for Testing Farm payloads.
"""

from mimetypes import suffix_map
import re
from typing import Dict, Tuple, Optional, Any, List
from logging import getLogger

from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX
from enge.utils.globals import RP_COMPATIBLE_EVENT
from enge.utils.errors import ValidationError

LOGGER = getLogger(__name__)

# AMI source regex patterns (without architecture suffix) for sanity validation.
# These mirror the regexes used on the Testing Farm backend.
AMI_SOURCE_PATTERNS = {
    "alma": re.compile(r"^AlmaLinux OS (\d+)\.(\d+)\.\d+$"),
    "rocky": re.compile(r"^Rocky-\d+-[eE][cC]2(?:-Base|-LVM)?-(\d+)\.(\d+)-\d+\.\d+$"),
}

# Architecture suffix separators per AMI os_type
AMI_ARCH_SEPARATORS = {"alma": " ", "rocky": "."}

# Only these architectures are available for AMI sources on AWS EC2
VALID_AMI_ARCHITECTURES = {"x86_64", "aarch64"}

# Symbolic RHEL composes (pass-through to Testing Farm, no repinning)
SYMBOLIC_RHEL_COMPOSE_PATTERN = re.compile(
    r"^RHEL-(\d+)-(rhui|sap-rhui|sap-ha-rhui)$",
    re.IGNORECASE,
)


def _strip_ami_arch_suffix(spec: str) -> str:
    """Strip a trailing architecture suffix (space- or dot-separated) from an AMI name."""
    for sep in (" ", "."):
        for arch in VALID_AMI_ARCHITECTURES:
            suffix = f"{sep}{arch}"
            if spec.endswith(suffix):
                return spec[: -len(suffix)]
    return spec


def _parse_ami_source(spec: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Try to parse spec as an AMI source (Alma Linux or Rocky Linux).

    Resolution order:
    1. Alias lookup in config [sources.ami]
    2. Direct regex match against full or base AMI name

    Returns parsed spec dict or None if not an AMI source.
    """
    ami_aliases = config.get("sources", {}).get("ami", {})
    base_name = None

    # 1. Alias lookup (e.g., "alma97" -> "AlmaLinux OS 9.7.20251118")
    if spec in ami_aliases:
        base_name = str(ami_aliases[spec]).strip()
        LOGGER.debug(f"Resolved AMI alias '{spec}' to: {base_name}")

    if base_name is None:
        # 2. Direct AMI name: strip arch suffix if present, then try regex
        base_name = _strip_ami_arch_suffix(spec)

    # Validate base name against known AMI patterns
    for os_type, pattern in AMI_SOURCE_PATTERNS.items():
        match = pattern.match(base_name)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            LOGGER.debug(
                f"Parsed {os_type.title()} Linux AMI spec '{spec}' as: "
                f"{base_name} (major={major}, minor={minor})"
            )
            return {
                "major": major,
                "minor": minor,
                "compose_name": base_name,
                "is_version_only": False,
                "is_centos_stream": False,
                "is_ami_source": True,
                "is_major_only": False,
                "os_type": os_type,
            }

    # If the alias resolved but didn't match any AMI pattern, fail with a clear message
    if spec in ami_aliases:
        raise ValueError(
            f"AMI alias '{spec}' resolved to '{base_name}' which does not match "
            f"any known AMI name pattern (Alma Linux or Rocky Linux)"
        )

    return None


def format_ami_compose_name(source_spec: Dict[str, Any], arch: str) -> str:
    """
    Construct the full AMI compose name by appending the architecture suffix.

    Alma uses space separator: 'AlmaLinux OS 9.7.20251118 x86_64'
    Rocky uses dot separator: 'Rocky-9-EC2-Base-9.7-20251123.2.x86_64'
    """
    base = source_spec["compose_name"]
    sep = AMI_ARCH_SEPARATORS[source_spec["os_type"]]
    return f"{base}{sep}{arch}"


def validate_ami_architectures(
    source_spec: Dict[str, Any], architectures: List[str]
) -> None:
    """
    Validate that requested architectures are available for AMI sources.

    Only x86_64 and aarch64 are available for Alma/Rocky on AWS EC2.
    Raises ValidationError for any unsupported architecture.
    """
    if not source_spec.get("is_ami_source"):
        return
    invalid = set(architectures) - VALID_AMI_ARCHITECTURES
    if invalid:
        raise ValidationError(
            f"Architecture(s) {', '.join(sorted(invalid))} not available for "
            f"{source_spec['os_type'].title()} Linux AMI sources. "
            f"Supported: {', '.join(sorted(VALID_AMI_ARCHITECTURES))}"
        )


def parse_compose_spec(
    spec: str, config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Parse a compose specification into its components.

    Args:
        spec: Either a version string like "8.10", full compose name like "RHEL-8.10.0-Nightly",
              symbolic RHUI compose like "RHEL-8-rhui", CentOS Stream format like "CentOS-Stream-9",
              or an AMI source alias/name for Alma Linux or Rocky Linux
              (e.g., "alma97", "AlmaLinux OS 9.7.20251118 x86_64")
        config: Configuration dictionary (optional, will be loaded if not provided)

    Returns:
        Dictionary containing parsed components:
        - major: Major version number
        - minor: Minor version number (0 for CentOS Stream)
        - compose_name: Full compose name (translated via pin_compose if needed);
          for AMI sources this is the base AMI name without architecture suffix
        - is_version_only: True if input was just version, False if full compose name
        - is_centos_stream: True if source is CentOS Stream, False otherwise
        - is_ami_source: True if source is an AMI-based system (Alma/Rocky), False otherwise
        - is_major_only: True if only a major version was requested, False otherwise
        - os_type: OS type string ("rhel", "centos", "alma", "rocky") for context generation

    Raises:
        ValueError: If the specification format is invalid
    """
    # Load config if not provided
    if config is None:
        from enge.utils.config_parser import load_config
        from enge.utils.globals import DEFAULT_USER_CONFIG_PATHS

        config = load_config(paths=list(DEFAULT_USER_CONFIG_PATHS))

    # Try parsing as CentOS Stream format with aliases
    # Supported formats: CentOS-Stream-9, centos-stream-9, stream-9, cs-9, stream9, cs9
    spec_stripped = spec.strip()
    centos_stream_patterns = [
        r"^(?:CentOS-Stream|centos-stream|stream|cs)-(\d+)$",  # With hyphen: CentOS-Stream-9, stream-9, cs-9
        r"^(?:stream|cs)(\d+)$",  # Without hyphen: stream9, cs9
    ]

    for pattern in centos_stream_patterns:
        centos_stream_match = re.match(pattern, spec_stripped, re.IGNORECASE)
        if centos_stream_match:
            major = int(centos_stream_match.group(1))
            compose_name = f"CentOS-Stream-{major}"
            LOGGER.debug(
                f"Parsed CentOS Stream spec '{spec_stripped}' as: {compose_name}"
            )
            return {
                "major": major,
                "minor": 0,  # CentOS Stream doesn't use minor versions
                "compose_name": compose_name,
                "is_version_only": False,
                "is_centos_stream": True,
                "is_ami_source": False,
                "is_major_only": False,
                "os_type": "centos",
            }

    # Try parsing as AMI source (Alma Linux / Rocky Linux)
    ami_result = _parse_ami_source(spec_stripped, config)
    if ami_result is not None:
        return ami_result

    # Try parsing as version number (e.g., "8.10")
    version_match = re.match(r"^(\d+)\.(\d+)$", spec.strip())
    if version_match:
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
        try:
            suffix = version_match.group(3)
        except IndexError:
            suffix = "Nightly"
            LOGGER.debug(
                f"Provided compose name {spec.strip()} does not contain a suffix, falling back to 'Nightly'."
            )

        # Use pin_compose to translate the compose with fallback logic
        from enge.dispatch.pin_compose import _pin_compose_with_fallback

        composes_prod_url = config.get("testing_farm", {}).get("composes_prod_url", "")

        if composes_prod_url:
            translated_compose = _pin_compose_with_fallback(
                major, minor, suffix, composes_prod_url
            )
            LOGGER.debug(
                f"Translated compose for {major}.{minor} to {translated_compose}"
            )
            compose_name = translated_compose
        else:
            LOGGER.debug(
                "composes_prod_url not configured, using standard compose name"
            )
            compose_name = f"RHEL-{major}.{minor}.0-Nightly"

        return {
            "major": major,
            "minor": minor,
            "compose_name": compose_name,
            "is_version_only": True,
            "is_centos_stream": False,
            "is_ami_source": False,
            "is_major_only": False,
            "os_type": "rhel",
        }

    # Try parsing as full compose name (e.g., "RHEL-8.10.0-Nightly")
    compose_match = re.match(r"^RHEL-(\d+)\.(\d+)(?:\.(\d+))?-(.+)$", spec.strip())
    if compose_match:
        major = int(compose_match.group(1))
        minor = int(compose_match.group(2))

        from enge.dispatch.pin_compose import repin_compose

        composes_prod_url = config.get("testing_farm", {}).get("composes_prod_url", "")
        compose_name = repin_compose(spec.strip(), composes_prod_url)

        return {
            "major": major,
            "minor": minor,
            "compose_name": compose_name,
            "is_version_only": False,
            "is_centos_stream": False,
            "is_ami_source": False,
            "is_major_only": False,
            "os_type": "rhel",
        }

    # Symbolic RHUI composes (e.g., RHEL-8-rhui) — pass through without repinning
    rhui_match = SYMBOLIC_RHEL_COMPOSE_PATTERN.match(spec_stripped)
    if rhui_match:
        major = int(rhui_match.group(1))
        suffix = rhui_match.group(2).lower()
        compose_name = f"RHEL-{major}-{suffix}"
        LOGGER.debug(f"Parsed symbolic RHUI spec '{spec_stripped}' as: {compose_name}")
        return {
            "major": major,
            "minor": 0,
            "compose_name": compose_name,
            "is_version_only": False,
            "is_centos_stream": False,
            "is_ami_source": False,
            "is_major_only": True,
            "os_type": "rhel",
        }

    # If neither pattern matches, raise an error
    raise ValueError(f"Invalid compose specification: {spec}")


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
        "is_centos_stream": False,
        "is_major_only": False,
        "os_type": "rhel",
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


def generate_detailed_upgrade_path_alias(
    source_spec: Dict[str, Any], target_spec: Dict[str, Any]
) -> Optional[str]:
    """
    Generate detailed upgrade path alias including minor versions (e.g., "98to102").

    Args:
        source_spec: Source specification dictionary
        target_spec: Target specification dictionary

    Returns:
        Detailed upgrade path alias string or None if components are missing
    """

    def _format(spec: Dict[str, Any]) -> Optional[str]:
        major = spec.get("major")
        minor = spec.get("minor")
        if major is None or minor is None:
            return None
        try:
            return f"{int(major)}{int(minor)}"
        except (TypeError, ValueError):
            return None

    source_alias = _format(source_spec)
    target_alias = _format(target_spec)
    if not source_alias or not target_alias:
        return None

    return f"{source_alias}to{target_alias}"


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

    def _format_release(spec: Dict[str, Any]) -> str:
        """Format release version - major-only if CentOS Stream or explicitly major-only."""
        if spec.get("is_centos_stream", False) or spec.get("is_major_only", False):
            return str(spec["major"])
        return f"{spec['major']}.{spec['minor']}"

    env_vars = {
        "SOURCE_RELEASE": _format_release(source_spec),
        "TARGET_RELEASE": _format_release(target_spec),
    }

    # Auto-generate TARGET_OS for non-RHEL targets (e.g., centos, alma, rocky)
    target_os_type = target_spec.get("os_type", "rhel")
    if target_os_type != "rhel":
        env_vars["TARGET_OS"] = target_os_type

    # Set INSTALL_LEAPP_FROM_COMPOSE based on artifact type for transparency
    # yes = install from compose (no --copr or --brew)
    # no = use build artifacts (--copr or --brew specified)
    if not has_copr and not has_brew:
        env_vars["INSTALL_LEAPP_FROM_COMPOSE"] = "yes"
    else:
        env_vars["INSTALL_LEAPP_FROM_COMPOSE"] = "no"

    return env_vars


def generate_tmt_context(
    source_spec: Dict[str, Any],
    target_spec: Dict[str, Any],
    event: Optional[str] = None,
    tier: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate TMT context for the Testing Farm payload.

    Args:
        source_spec: Source specification dictionary
        target_spec: Target specification dictionary
        event: Event type (optional)
        tier: Test tier (optional)

    Returns:
        Dictionary of TMT context variables (arch will be set per environment).
        Note: Brew artifact NVRs are automatically added later during payload building
        in the format package_name: version-release (e.g., leapp: 0.16.0-1.el9).
    """

    def _format_distro(prefix: str, spec: Dict[str, Any]) -> str:
        """Format distro string - major-only if CentOS Stream or explicitly major-only."""
        if spec.get("is_centos_stream", False) or spec.get("is_major_only", False):
            return f"{prefix}-{spec['major']}"
        return f"{prefix}-{spec['major']}.{spec['minor']}"

    # Determine prefixes based on os_type (rhel, centos, alma, rocky, etc.)
    source_prefix = source_spec.get("os_type", "rhel")
    target_prefix = target_spec.get("os_type", "rhel")

    distro = _format_distro(source_prefix, source_spec)
    target_distro = _format_distro(target_prefix, target_spec)

    context = {
        "distro": distro,
        "target_distro": target_distro,
        "source_compose": source_spec.get("compose_name", ""),
        "upgrade_path": f"{source_spec['major']}to{target_spec['major']}",
    }

    # Add optional context fields if provided
    if event:
        context["event"] = event
    if tier:
        context["tier"] = tier

    return context


TMT_COMPOSE_CONTEXT_KEYS = ("source_compose", "target_compose")


def normalize_tmt_compose_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of TMT context with compose values safe for tmt -c CLI usage.

    Testing Farm passes context to tmt as shell arguments. Values containing
    spaces (e.g. Alma Linux AMI names) break unless quoted; dashes avoid that.
    Only source_compose and target_compose are normalized; provisioning compose
    names are unchanged elsewhere in the payload.
    """
    if not context:
        return {}
    normalized = dict(context)
    for key in TMT_COMPOSE_CONTEXT_KEYS:
        value = normalized.get(key)
        if isinstance(value, str) and " " in value:
            normalized[key] = value.replace(" ", "-")
    return normalized


def apply_centos_context_overrides(
    tmt_context: Dict[str, Any],
    source_spec: Dict[str, Any],
    target_spec: Dict[str, Any],
    env_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Ensure CentOS Stream scenarios use CentOS naming and honor TARGET_OS overrides.

    Args:
        tmt_context: Existing TMT context dictionary
        source_spec: Parsed source specification
        target_spec: Parsed target specification
        env_vars: Final merged environment variables (optional)

    Returns:
        Updated TMT context dictionary (copy)
    """
    if not source_spec.get("is_centos_stream", False):
        return tmt_context

    updated_context = dict(tmt_context or {})
    updated_context["distro"] = f"centos-{source_spec['major']}"

    # Determine target prefix: CentOS Stream target takes priority, then TARGET_OS env var, then default to rhel
    if target_spec.get("is_centos_stream", False):
        target_prefix = "centos"
    else:
        target_os = (env_vars or {}).get("TARGET_OS", "").strip().lower()
        target_prefix = "centos" if target_os == "centos" else "rhel"

    # Format target_distro based on target_spec's own properties (not source's)
    if target_spec.get("is_centos_stream", False) or target_spec.get(
        "is_major_only", False
    ):
        updated_context["target_distro"] = f"{target_prefix}-{target_spec['major']}"
    else:
        updated_context["target_distro"] = (
            f"{target_prefix}-{target_spec['major']}.{target_spec['minor']}"
        )

    return updated_context


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


def parse_tmt_context(context_args: Optional[list] = None) -> Dict[str, Any]:
    """
    Parse TMT context key-value pairs from command line arguments.

    Args:
        context_args: List of strings in "KEY=VAL" format

    Returns:
        Dictionary of parsed context values

    Raises:
        ValueError: If any item is not in correct KEY=VAL format
    """
    context: Dict[str, Any] = {}

    if not context_args:
        return context

    for item in context_args:
        if "=" not in item:
            raise ValueError(
                f"Invalid context format: {item}. Expected KEY=VAL format."
            )
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Empty context key in: {item}")
        # Keep values as strings to align with Testing Farm/TMT expectations
        if key in context and context[key] != value:
            LOGGER.warning(
                f"TMT context '{key}' overridden by CLI duplicate: {context[key]} -> {value}"
            )
        context[key] = value
        LOGGER.debug(f"Parsed TMT context: {key}={value}")

    return context


def merge_tmt_context(
    base_context: Dict[str, Any], cli_context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge CLI-provided TMT context into the base context with warnings.

    Args:
        base_context: Generated base context (e.g., from source/target specs)
        cli_context: Context provided via --context KEY=VAL flags

    Returns:
        Merged context dictionary (base modified copy)
    """
    merged = dict(base_context) if base_context else {}

    for key, value in (cli_context or {}).items():
        if key in merged and merged[key] != value:
            LOGGER.warning(
                f"TMT context '{key}' overridden by CLI: {merged[key]} -> {value}"
            )
        merged[key] = value

    return merged


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
    tiers: List[str],
    tier_config: Dict[str, str],
    upgrade_path: str,
    additional_filters: Optional[List[str]] = None,
) -> str:
    """
    Generate plan_filter from tier specifications.
    If no tiers are provided, return the upgrade path filter and enabled:true.

    Args:
        tiers: List of tier names from CLI
        tier_config: Tier configuration mapping from config
        upgrade_path: Upgrade path alias (e.g., "8to9")
        additional_filters: Optional list of additional filter strings to include (e.g., ["tag:rhsm"])

    Returns:
        Combined plan_filter string

    Raises:
        ValueError: If tier is not found in configuration

    Examples:
        >>> generate_tier_plan_filter(["tier-smoke"], {"tier-smoke": "tag:smoke"}, "8to9")
        'tag:8to9 & tag:smoke & enabled:true'
        >>> generate_tier_plan_filter([], None, "8to9")
        'tag:8to9 & enabled:true'
        >>> generate_tier_plan_filter([], None, "8to9", ["tag:rhsm"])
        'tag:8to9 & tag:rhsm & enabled:true'
    """
    if not tiers:
        base_filters = [f"tag:{upgrade_path}"]
        if additional_filters:
            base_filters.extend(additional_filters)
        base_filters.append("enabled:true")
        return " & ".join(base_filters)

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
    all_filters = [f"tag:{upgrade_path}"] + tier_filters
    if additional_filters:
        all_filters.extend(additional_filters)
    all_filters.append("enabled:true")
    combined_filter = " & ".join(all_filters)

    return combined_filter


def parse_source_target_config(
    source: str, target: Optional[str] = None, config: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parse source and target configurations, deriving target if not provided.

    Args:
        source: Source specification string
        target: Optional target specification string
        config: Configuration dictionary (optional, will be loaded if not provided)

    Returns:
        Tuple of (source_spec, target_spec) dictionaries

    Raises:
        ValueError: If source or target specifications are invalid
    """
    LOGGER.debug(
        f"Parsing source: {source}, target: {target or 'the default will be derived'}"
    )

    try:
        source_spec = parse_compose_spec(source, config)
        LOGGER.debug(f"Parsed source spec: {source_spec}")

        target_input = target
        target_major_only = False
        if target and source_spec.get("is_centos_stream"):
            target_str = str(target).strip()
            if re.fullmatch(r"\d+", target_str):
                target_input = f"{target_str}.0"
                target_major_only = True
                LOGGER.debug(
                    "Interpreting major-only target '%s' as '%s' because source is CentOS Stream",
                    target,
                    target_input,
                )

        if target_input:
            target_spec = parse_compose_spec(target_input, config)
            target_spec["is_major_only"] = (
                target_spec.get("is_major_only", False) or target_major_only
            )
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
        if key in ["copr_api", "brew_api", "environment", "reportportal"]:
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

    # Resolve pool (CLI > Set > Config)
    resolved["pool"] = (
        getattr(cli_args, "pool", None)
        or set_config.get("pool")
        or config.get("tests", {}).get("pool")
    )

    # Resolve git ref (CLI > Set > Config)
    resolved["git_ref"] = (
        getattr(cli_args, "git_ref", None)
        or set_config.get("git_ref")
        or config.get("tests", {}).get("git_ref")
    )

    # Resolve git url (CLI > Set > Config)
    resolved["git_url"] = (
        getattr(cli_args, "git_url", None)
        or set_config.get("git_url")
        or config.get("tests", {}).get("git_url")
    )

    # Resolve parallel limit (CLI > Set > Config)
    cli_parallel = getattr(cli_args, "parallel_limit", None)
    if cli_parallel is not None:
        resolved["parallel_limit"] = cli_parallel
    else:
        resolved["parallel_limit"] = set_config.get("parallel_limit") or config.get(
            "tests", {}
        ).get("parallel_limit")

    # Resolve tiers (CLI > Set > Config)
    # Handle both singular "tier" and plural "tiers" keys
    cli_tier = getattr(cli_args, "tier", None)
    set_tiers = set_config.get("tiers")
    config_tiers = config.get("tests", {}).get("tiers")
    config_tier = config.get("tests", {}).get("tier")

    # Normalize config_tier to list if it's a string
    if config_tier and isinstance(config_tier, str):
        config_tier = [config_tier]

    resolved["tiers"] = cli_tier or set_tiers or config_tiers or config_tier

    # Resolve event (CLI > Set)
    resolved["event"] = getattr(cli_args, "event", None) or set_config.get("event")

    # Resolve plans (CLI > Set > Config) - override, not combine
    cli_plans = getattr(cli_args, "plan", None)
    set_plans = set_config.get("plans", [])
    config_plans = config.get("tests", {}).get("plans", [])

    # Priority override: CLI plans override set plans, set plans override config plans
    if cli_plans:
        resolved["plans"] = cli_plans
    elif set_plans:
        resolved["plans"] = set_plans
    elif config_plans:
        resolved["plans"] = config_plans
    else:
        resolved["plans"] = []

    # Resolve artifact configurations
    resolved["copr_api"] = set_config.get("copr_api", {})
    resolved["brew_api"] = set_config.get("brew_api", {})
    resolved["environment"] = set_config.get("environment", {})
    resolved["reportportal"] = set_config.get("reportportal", {})

    # Resolve context (Config defaults > Set overrides)
    # CLI overrides are handled later via --context in opt_manager
    tests_section = config.get("tests", {}) if isinstance(config, dict) else {}
    global_context = tests_section.get("context", {}) or {}
    set_context = set_config.get("context", {}) or {}
    merged_context: Dict[str, Any] = {}
    if isinstance(global_context, dict):
        merged_context.update(global_context)
    if isinstance(set_context, dict):
        merged_context.update(set_context)
    resolved["context"] = merged_context

    return resolved


def merge_set_environment_variables(
    auto_env_vars: Dict[str, str],
    set_env_vars: Dict[str, str],
    cli_env_vars: Dict[str, str],
    config: Optional[Dict[str, Any]] = None,
    cli_args: Any = None,
    set_reportportal_config: Optional[Dict[str, Any]] = None,
    set_name: Optional[str] = None,
    architecture: Optional[str] = None,
    tier: Optional[str] = None,
    source_release: Optional[str] = None,
    target_release: Optional[str] = None,
    source_compose: Optional[str] = None,
    target_compose: Optional[str] = None,
    event: Optional[str] = None,
) -> Dict[str, str]:
    """
    Merge environment variables from automatic generation, test sets, CLI, and ReportPortal config.
    Priority: CLI > Test Set > Automatic > ReportPortal config

    Args:
        auto_env_vars: Automatically generated environment variables
        set_env_vars: Environment variables from test sets
        cli_env_vars: Environment variables from CLI --environment option
        config: Full configuration dictionary (for ReportPortal config)
        cli_args: CLI arguments object (for ReportPortal overrides)
        set_reportportal_config: ReportPortal config from test set (overrides main config)
        set_name: Name of the test set (for auto-generation)
        architecture: Target architecture (for auto-generation)
        tier: Test tier (for auto-generation)
        source_release: Source release version (for auto-generation)
        target_release: Target release version (for auto-generation)
        source_compose: Source compose name (for auto-generation)
        target_compose: Target compose name (for auto-generation)

    Returns:
        Merged environment variables dictionary
    """
    merged_vars = auto_env_vars.copy()

    # Add ReportPortal environment variables first (lowest priority)
    # Only when event is present and compatible
    effective_event = event or (getattr(cli_args, "event", None) if cli_args else None)
    rp_enabled = bool(effective_event) and effective_event in RP_COMPATIBLE_EVENT

    if rp_enabled and (config or set_reportportal_config):
        # Create a merged reportportal config with test set values taking precedence
        reportportal_config = {}
        base_rp_config = {}
        if config and config.get("reportportal"):
            reportportal_config.update(config["reportportal"])
            base_rp_config.update(config["reportportal"])
        if set_reportportal_config:
            # Validate: do not allow empty-string overrides for sensitive keys
            for key, value in set_reportportal_config.items():
                if isinstance(value, str) and value == "" and base_rp_config.get(key):
                    raise ValidationError(
                        f"Invalid empty override for reportportal.{key}"
                    )
                # Warn on non-empty override changing an existing value
                if (
                    base_rp_config.get(key) is not None
                    and value not in (None, "")
                    and base_rp_config.get(key) != value
                ):
                    LOGGER.warning(
                        f"ReportPortal '{key}' overridden by test set: {base_rp_config.get(key)} -> {value}"
                    )
            reportportal_config.update(set_reportportal_config)

        # Create a temporary config dict with the merged reportportal config
        temp_config = (
            {"reportportal": reportportal_config} if reportportal_config else {}
        )
        reportportal_vars = generate_reportportal_environment_variables(
            temp_config,
            cli_args,
            set_name,
            architecture,
            tier,
            source_release,
            target_release,
            source_compose,
            target_compose,
            event=effective_event,
        )
        # Merge with warnings and ignore empty overrides
        for k, v in reportportal_vars.items():
            if k in merged_vars and merged_vars[k] != v and v not in (None, ""):
                LOGGER.warning(
                    f"Environment variable {k} overridden by reportportal config: {merged_vars[k]} -> {v}"
                )
            if v not in (None, ""):
                merged_vars[k] = v

    # Apply test set environment overrides with warnings; ignore empty overrides
    for k, v in (set_env_vars or {}).items():
        if k in merged_vars and merged_vars[k] != v and v not in (None, ""):
            LOGGER.warning(
                f"Environment variable {k} overridden by test set: {merged_vars[k]} -> {v}"
            )
        if v not in (None, ""):
            merged_vars[k] = v

    # Apply CLI environment overrides with warnings; ignore empty overrides
    for k, v in (cli_env_vars or {}).items():
        if k in merged_vars and merged_vars[k] != v and v not in (None, ""):
            LOGGER.warning(
                f"Environment variable {k} overridden by CLI: {merged_vars[k]} -> {v}"
            )
        if v not in (None, ""):
            merged_vars[k] = v

    return merged_vars


def generate_reportportal_environment_variables(
    config: Dict[str, Any],
    cli_args: Any = None,
    set_name: Optional[str] = None,
    architecture: Optional[str] = None,
    tier: Optional[str] = None,
    source_release: Optional[str] = None,
    target_release: Optional[str] = None,
    source_compose: Optional[str] = None,
    target_compose: Optional[str] = None,
    event: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate ReportPortal environment variables from config and CLI overrides.

    Args:
        config: Full configuration dictionary
        cli_args: CLI arguments object (optional)
        set_name: Name of the test set (optional, for auto-generation)
        architecture: Target architecture (optional, for auto-generation)
        tier: Test tier (optional, for auto-generation)
        source_release: Source release version (optional, for auto-generation)
        target_release: Target release version (optional, for auto-generation)
        source_compose: Source compose name (optional, for auto-generation)
        target_compose: Target compose name (optional, for auto-generation)
        event: Event name (optional, for auto-generation)

    Returns:
        Dictionary of ReportPortal environment variables with TMT_PLUGIN_REPORT_REPORTPORTAL_ prefix
    """

    reportportal_env_vars = {}

    # Get reportportal section from config
    reportportal_config = config.get("reportportal", {})

    if not reportportal_config:
        # If no config but we have context for auto-generation, generate launch name
        launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
        auto_launch = _generate_auto_launch_name(
            set_name,
            architecture,
            tier,
            source_release,
            target_release,
            source_compose,
            event,
        )
        if auto_launch:
            reportportal_env_vars[launch_key] = auto_launch
        return reportportal_env_vars

    # Process all config values with the prefix
    for key, value in reportportal_config.items():
        if value:  # Only include non-empty values
            # Special-case mapping to align with TMT env var expectations
            normalized_key = str(key).strip().lower()
            if normalized_key == "description":
                env_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH_DESCRIPTION"
            elif normalized_key == "launch":
                env_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
            else:
                env_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}{key.upper()}"
            reportportal_env_vars[env_key] = str(value)

    # Handle CLI overrides for specific keys (warn on override and ignore empty)
    if cli_args:
        rp_launch = getattr(cli_args, "rp_launch", None)
        if rp_launch:
            launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
            if (
                launch_key in reportportal_env_vars
                and reportportal_env_vars[launch_key] != rp_launch
            ):
                LOGGER.warning(
                    f"ReportPortal 'launch' overridden by CLI: {reportportal_env_vars[launch_key]} -> {rp_launch}"
                )
            reportportal_env_vars[launch_key] = rp_launch

        rp_description = getattr(cli_args, "rp_description", None)
        if rp_description:
            desc_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH_DESCRIPTION"
            if (
                desc_key in reportportal_env_vars
                and reportportal_env_vars[desc_key] != rp_description
            ):
                LOGGER.warning(
                    f"ReportPortal 'description' overridden by CLI: {reportportal_env_vars[desc_key]} -> {rp_description}"
                )
            reportportal_env_vars[desc_key] = rp_description

    # Auto-generate launch name if not provided anywhere
    launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
    if launch_key not in reportportal_env_vars:
        auto_launch = _generate_auto_launch_name(
            set_name,
            architecture,
            tier,
            source_release,
            target_release,
            source_compose,
            event,
        )
        if auto_launch:
            reportportal_env_vars[launch_key] = auto_launch

    return reportportal_env_vars


def _generate_auto_launch_name(
    set_name: Optional[str] = None,
    architecture: Optional[str] = None,
    tier: Optional[str] = None,
    source_release: Optional[str] = None,
    target_release: Optional[str] = None,
    source_compose: Optional[str] = None,
    event: Optional[str] = None,
) -> Optional[str]:
    """
    Generate automatic launch name in format: (EVENT_NAME|SET_NAME)~datetime_stamp~tier~architecture

    Args:
        set_name: Name of the test set (optional)
        architecture: Target architecture (optional)
        tier: Test tier (optional)
        source_release: Source release version (optional)
        target_release: Target release version (optional)
        source_compose: Source compose name (optional)
        event: Event name (optional, takes priority over set_name)

    Returns:
        Generated launch name or None if no components available
    """
    from datetime import datetime

    # Get timestamp in YYYY-MM-DD format
    timestamp = datetime.now().strftime("%Y-%m-%d")

    # Determine the event/set name component (event takes priority)
    name_component = event or set_name
    if not name_component:
        return None

    # Use architecture or 'unknown' if not provided
    arch_component = architecture or "unknown"

    # Use tier or 'unknown' if not provided
    tier_component = tier or "unknown"

    # Generate the name in the format: (EVENT_NAME|SET_NAME)~datetime_stamp~tier~architecture
    return f"{name_component.upper()}~{timestamp}~{tier_component}~{arch_component}"


def parse_target_compose_from_url(target_compose_url: Optional[str]) -> Optional[str]:
    r"""
    Parse TARGET_COMPOSE_URL to extract RHEL compose name.

    Looks for pattern: RHEL-\d+\.\d+(\.\d+)?-\d{8}\.\d+

    Args:
        target_compose_url: URL containing compose information

    Returns:
        Extracted compose name or None if not found

    Examples:
        >>> parse_target_compose_from_url("http://example.com/RHEL-10.1-19700101.0/compose")
        "RHEL-10.1-19700101.0"
        >>> parse_target_compose_from_url("http://example.com/RHEL-9.7.0-19700101.1/compose")
        "RHEL-9.7.0-19700101.1"
        >>> parse_target_compose_from_url("http://example.com/invalid/path")
        None
    """
    if not target_compose_url:
        return None

    # Pattern to match RHEL-X.Y(.Z)?-YYYYMMDD.N
    rhel_compose_pattern = r"RHEL-\d+\.\d+(?:\.\d+)?-\d{8}\.\d+"

    match = re.search(rhel_compose_pattern, target_compose_url)
    if match:
        return match.group(0)

    return None
