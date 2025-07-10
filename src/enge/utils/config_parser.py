#!/usr/bin/env python3
"""
Configuration file parser for enge.

This module handles loading and parsing TOML configuration files
with proper error handling, validation, and default value merging.
"""

import logging
import os
import sys
import tomllib
from typing import Dict, Any, List, Union, Optional
from pathlib import Path

from importlib.resources import files

LOGGER = logging.getLogger(__name__)


def load_default_config() -> Dict[str, Any]:
    """
    Load the built-in default configuration.

    Returns:
        Default configuration dictionary

    Raises:
        SystemExit: If default config cannot be loaded
    """
    try:
        # Try to load from package resources first (when installed)
        package_files = files("enge.utils")
        default_config_file = package_files / "default_config.toml"
        default_config_data = default_config_file.read_text(encoding="utf-8")
        return tomllib.loads(default_config_data)
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        # Fall back to relative path (during development)
        try:
            default_path = Path(__file__).parent / "default_config.toml"
            with open(default_path, "rb") as f:
                return tomllib.load(f)
        except (FileNotFoundError, tomllib.TOMLDecodeError) as e:
            LOGGER.critical(f"Cannot load default configuration: {e}")
            sys.exit(99)


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
        sys.exit(99)

    # Load default configuration
    default_config = load_default_config()

    expanded_paths = [Path(path).expanduser() for path in paths]

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
                sys.exit(99)
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


def validate_required_config(config: Dict[str, Any]) -> bool:
    """
    Validate that all required configuration values are present for actual usage.

    This validates user-specific values that are required for operations.
    Operational defaults are validated separately.

    Args:
        config: Configuration dictionary to validate

    Returns:
        True if all required values are present, False otherwise
    """
    required_sections = {
        "testing_farm": ["api_key", "cloud_resources_tag"],
    }

    errors = []

    for section_name, required_keys in required_sections.items():
        if section_name not in config:
            errors.append(f"Missing required section: [{section_name}]")
            continue

        section = config[section_name]
        if not isinstance(section, dict):
            errors.append(f"Section [{section_name}] must be a dictionary")
            continue

        for key in required_keys:
            value = section.get(key)
            if not value:  # Empty string, None, or empty list/dict
                errors.append(f"Missing required value: [{section_name}].{key}")

    if errors:
        LOGGER.critical("Configuration validation failed:")
        for error in errors:
            LOGGER.critical(f"  - {error}")
        return False

    return True


def validate_operational_defaults(config: Dict[str, Any]) -> bool:
    """
    Validate that all operational defaults are present in the configuration.

    These values should always be present because they come from the default config.
    If any are missing, it indicates a problem with the default configuration.

    Args:
        config: Configuration dictionary to validate

    Returns:
        True if all operational defaults are present, False otherwise
    """
    operational_defaults = {
        "tests": ["architectures", "git_branch", "parallel_limit"],
        "common": ["archive_tasks_latest", "archive_tasks_default", "logs_directory"],
    }

    errors = []

    for section_name, required_keys in operational_defaults.items():
        if section_name not in config:
            errors.append(f"Missing operational section: [{section_name}]")
            continue

        section = config[section_name]
        if not isinstance(section, dict):
            errors.append(f"Operational section [{section_name}] must be a dictionary")
            continue

        for key in required_keys:
            value = section.get(key)
            if value is None or value == "":  # Check for None or empty string
                errors.append(f"Missing operational default: [{section_name}].{key}")

    if errors:
        LOGGER.critical("Operational defaults validation failed:")
        for error in errors:
            LOGGER.critical(f"  - {error}")
        LOGGER.critical("This indicates a problem with the default configuration file.")
        return False

    return True


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


def validate_test_sets(config: Dict[str, Any], set_names: List[str]) -> bool:
    """
    Validate that specified test sets exist and are properly configured.

    Args:
        config: Configuration dictionary
        set_names: List of test set names to validate

    Returns:
        True if all test sets are valid, False otherwise
    """
    if not set_names:
        return True

    # Check if tests.set section exists
    if "tests" not in config:
        LOGGER.error("Missing [tests] section in configuration")
        return False

    tests_section = config["tests"]
    if not isinstance(tests_section, dict):
        LOGGER.error("[tests] section must be a dictionary")
        return False

    if "set" not in tests_section:
        LOGGER.error("Missing [tests.set] section in configuration")
        return False

    test_sets = tests_section["set"]
    if not isinstance(test_sets, dict):
        LOGGER.error("[tests.set] section must be a dictionary")
        return False

    # Validate each requested test set
    errors = []
    for set_name in set_names:
        if set_name not in test_sets:
            available_sets = list(test_sets.keys())
            errors.append(
                f"Test set '{set_name}' not found. Available sets: {available_sets}"
            )
            continue

        set_config = test_sets[set_name]
        if not isinstance(set_config, dict):
            errors.append(f"Test set '{set_name}' must be a dictionary")
            continue

        # Validate test set structure
        if not _validate_test_set_structure(set_name, set_config):
            errors.append(f"Test set '{set_name}' has invalid structure")

    if errors:
        LOGGER.error("Test set validation failed:")
        for error in errors:
            LOGGER.error(f"  - {error}")
        return False

    return True


def _validate_test_set_structure(set_name: str, set_config: Dict[str, Any]) -> bool:
    """
    Validate the structure of a single test set.

    Args:
        set_name: Name of the test set
        set_config: Test set configuration dictionary

    Returns:
        True if structure is valid, False otherwise
    """
    valid_keys = {
        "source",
        "target",
        "architectures",
        "git_branch",
        "parallel_limit",
        "tiers",
        "copr_api",
        "brew_api",
        "environment",
    }

    # Check for unknown keys
    unknown_keys = set(set_config.keys()) - valid_keys
    if unknown_keys:
        LOGGER.warning(f"Test set '{set_name}' has unknown keys: {unknown_keys}")

    # Validate tiers if present
    if "tiers" in set_config:
        tiers = set_config["tiers"]
        if not isinstance(tiers, list):
            LOGGER.error(f"Test set '{set_name}': 'tiers' must be a list")
            return False
        if not all(isinstance(tier, str) for tier in tiers):
            LOGGER.error(f"Test set '{set_name}': all tiers must be strings")
            return False

    # Validate nested dictionary structures
    for dict_key in ["copr_api", "brew_api", "environment"]:
        if dict_key in set_config:
            value = set_config[dict_key]
            if not isinstance(value, dict):
                LOGGER.error(
                    f"Test set '{set_name}': '{dict_key}' must be a dictionary"
                )
                return False

    # Validate architectures format if present
    if "architectures" in set_config:
        arch = set_config["architectures"]
        if not isinstance(arch, list):
            LOGGER.error(
                f"Test set '{set_name}': 'architectures' must be a list of strings"
            )
            return False
        if not all(isinstance(item, str) and item.strip() for item in arch):
            LOGGER.error(
                f"Test set '{set_name}': 'architectures' list must contain only non-empty strings"
            )
            return False

    # Validate parallel_limit if present
    if "parallel_limit" in set_config:
        parallel_limit = set_config["parallel_limit"]
        if not isinstance(parallel_limit, int) or parallel_limit <= 0:
            LOGGER.error(
                f"Test set '{set_name}': 'parallel_limit' must be a positive integer"
            )
            return False

    return True
