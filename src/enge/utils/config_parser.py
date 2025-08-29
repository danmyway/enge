#!/usr/bin/env python3
"""
Configuration file parser for enge.

This module handles loading and parsing TOML configuration files
with proper error handling, validation, and default value merging.
"""

import logging
import tomllib
from typing import Dict, Any, List, Union, Optional
from pathlib import Path

from importlib.resources import files
from enge.utils.errors import ConfigurationError
from enge.utils.globals import DEFAULT_USER_CONFIG_PATHS

LOGGER = logging.getLogger(__name__)


def _safe_load_toml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            with open(path, "rb") as f:
                return tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError) as e:
        LOGGER.warning(f"Skipping default config candidate {path}: {e}")
    return None


def _parse_version(version_val: Any) -> Optional[tuple[int, int, int]]:
    try:
        if isinstance(version_val, str):
            parts = version_val.strip().split(".")
            if len(parts) == 3:
                return int(parts[0]), int(parts[1]), int(parts[2])
    except Exception:
        pass
    return None


def load_default_config() -> Dict[str, Any]:
    """
    Load the built-in default configuration.

    Returns:
        Default configuration dictionary

    Raises:
        SystemExit: If default config cannot be loaded
    """
    # Preferred external default at /etc/enge/enge_default_config.toml
    external_path = Path("/etc/enge/enge_default_config.toml")
    # Fallbacks to package-bundled example and dev path
    bundled_path: Optional[Path] = None
    try:
        package_files = files("enge.utils")
        bundled_path = Path(package_files / "enge_default_config.toml")
    except Exception:
        bundled_path = Path(__file__).parent / "enge_default_config.toml"

    external_cfg = _safe_load_toml(external_path)
    bundled_cfg = _safe_load_toml(bundled_path) if bundled_path else None

    # Version comparison warning: warn only if external exists and is older than bundled
    if external_cfg and bundled_cfg:
        ext_ver = _parse_version(external_cfg.get("version"))
        bun_ver = _parse_version(bundled_cfg.get("version"))
        if ext_ver and bun_ver and ext_ver < bun_ver:
            LOGGER.warning(
                "The external default config at /etc/enge/enge_default_config.toml "
                "appears older than the bundled example. You may want to update it."
            )

    # Selection: prefer external when present
    if external_cfg:
        return external_cfg
    if bundled_cfg:
        return bundled_cfg

    LOGGER.critical(
        "Cannot load default configuration from external or bundled locations"
    )
    raise ConfigurationError("Cannot load default configuration")


def merge_configs(default: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge user configuration with default configuration.

    User values take precedence over defaults. Nested dictionaries are
    merged recursively.

    Args:
        default: Default configuration dictionary
        user: User configuration dictionary

    Returns:
        Merged configuration dictionary
    """
    merged = default.copy()

    for key, value in user.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            # Recursively merge nested dictionaries
            merged[key] = merge_configs(merged[key], value)
        else:
            # User value overwrites default
            merged[key] = value

    return merged


def load_config(paths: Union[List[str], List[Path]]) -> Dict[str, Any]:
    """
    Load a TOML configuration file from the given paths with defaults.

    Tries each path in order until a valid config file is found.
    The loaded config is merged with built-in defaults.

    Args:
        paths: List of file paths to try (strings or Path objects)

    Returns:
        Parsed configuration as a dictionary with defaults applied

    Raises:
        SystemExit: If no config file is found or parsing fails

    Examples:
        >>> config = load_config(['~/.config/enge.toml', '~/.enge.toml'])
        >>> api_key = config.get('testing_farm', {}).get('api_key')
    """
    if not paths:
        LOGGER.critical("No configuration file paths provided")
        raise ConfigurationError("No configuration file paths provided")

    # Load default configuration
    default_config = load_default_config()

    # Append system/user paths in priority order if not already present
    expanded_paths = [Path(path).expanduser() for path in paths]
    for p in DEFAULT_USER_CONFIG_PATHS:
        pp = Path(p).expanduser()
        if pp not in expanded_paths:
            expanded_paths.append(pp)

    # Try to find and load user configuration
    user_config = None
    for path in expanded_paths:
        if path.exists():
            LOGGER.debug(f"Loading configuration file from: {path}")
            try:
                with open(path, "rb") as f:
                    user_config = tomllib.load(f)

                LOGGER.info(f"Successfully loaded configuration from: {path}")
                break

            except tomllib.TOMLDecodeError as e:
                LOGGER.critical(f"Error parsing TOML file {path}: {e}")
                raise ConfigurationError(f"Error parsing TOML file {path}") from e
            except PermissionError:
                LOGGER.warning(f"Permission denied reading config file: {path}")
                continue
            except OSError as e:
                LOGGER.warning(f"Error reading config file {path}: {e}")
                continue

    if user_config is None:
        # No user config found, use defaults only
        LOGGER.warning("No user configuration file found, using defaults")
        LOGGER.info("Create a config file at one of these locations:")
        for path in expanded_paths:
            LOGGER.info(f"  - {path}")
        return default_config

    # Merge user config with defaults
    merged_config = merge_configs(default_config, user_config)

    LOGGER.debug("Configuration loaded and merged with defaults")
    return merged_config


# Validation functions have been moved to opt_manager.py for centralized validation


def validate_config_section(
    config: Dict[str, Any], section: str, required_keys: Optional[List[str]] = None
) -> bool:
    """
    Validate that a configuration section exists and has required keys.

    Args:
        config: Configuration dictionary
        section: Section name to validate
        required_keys: List of required keys in the section

    Returns:
        True if section is valid, False otherwise
    """
    if section not in config:
        LOGGER.error(f"Required configuration section '{section}' not found")
        return False

    section_data = config[section]
    if not isinstance(section_data, dict):
        LOGGER.error(f"Configuration section '{section}' must be a dictionary")
        return False

    if required_keys:
        missing_keys = []
        for key in required_keys:
            if key not in section_data or not section_data[key]:
                missing_keys.append(key)

        if missing_keys:
            LOGGER.error(
                f"Missing required keys in section '{section}': {missing_keys}"
            )
            return False

    return True


def get_config_value(
    config: Dict[str, Any], section: str, key: str, default: Any = None
) -> Any:
    """
    Safely get a configuration value with fallback to default.

    Args:
        config: Configuration dictionary
        section: Section name
        key: Key name within the section
        default: Default value if key is not found

    Returns:
        Configuration value or default
    """
    try:
        return config.get(section, {}).get(key, default)
    except (KeyError, AttributeError):
        return default
