#!/usr/bin/env python3
"""
Configuration file parser for enge.

This module handles loading and parsing TOML configuration files with
three-layer merging: bundled defaults < system/external < user.
"""

import logging
import tomllib
from typing import Dict, Any, List, Union, Optional
from pathlib import Path

from importlib.resources import files
from enge.utils.errors import ConfigurationError
from enge.utils.globals import SYSTEM_CONFIG_PATHS, USER_CONFIG_PATHS

LOGGER = logging.getLogger(__name__)


def _safe_load_toml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            with open(path, "rb") as f:
                return tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError) as e:
        LOGGER.warning(f"Skipping config candidate {path}: {e}")
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


def _load_bundled_config() -> Dict[str, Any]:
    """Load the package-bundled default configuration (always available)."""
    bundled_path: Optional[Path] = None
    try:
        package_files = files("enge.utils")
        bundled_path = Path(package_files / "enge_default_config.toml")
    except Exception:
        bundled_path = Path(__file__).parent / "enge_default_config.toml"

    if bundled_path:
        cfg = _safe_load_toml(bundled_path)
        if cfg:
            return cfg

    LOGGER.critical("Cannot load bundled default configuration")
    raise ConfigurationError("Cannot load bundled default configuration")


def load_default_config(
    user_override: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Load the default configuration with layering.

    When called without arguments, returns the bundled defaults merged with
    any system-level configs.  The *user_override* parameter replaces the
    system layer with a single file.

    Returns:
        Default configuration dictionary

    Raises:
        SystemExit: If default config cannot be loaded
    """
    bundled = _load_bundled_config()
    merged = bundled

    if user_override:
        override_path: Optional[Path] = None
        try:
            override_path = Path(user_override).expanduser()
        except TypeError:
            LOGGER.warning(
                "Ignoring default configuration override because it is not a valid path"
            )
        if override_path:
            override_cfg = _safe_load_toml(override_path)
            if override_cfg:
                LOGGER.info(
                    f"Using user-defined default configuration at {override_path}"
                )
                merged = merge_configs(merged, override_cfg)
                return merged
            else:
                LOGGER.warning(
                    f"User-defined default configuration {override_path} is "
                    "not readable; falling back"
                )

    for sys_path_str in SYSTEM_CONFIG_PATHS:
        sys_path = Path(sys_path_str)
        sys_cfg = _safe_load_toml(sys_path)
        if sys_cfg:
            ext_ver = _parse_version(sys_cfg.get("version"))
            bun_ver = _parse_version(bundled.get("version"))
            if (
                sys_path_str == "/etc/enge/enge_default_config.toml"
                and ext_ver
                and bun_ver
                and ext_ver < bun_ver
            ):
                LOGGER.warning(
                    "The external default config at /etc/enge/enge_default_config.toml "
                    "appears older than the bundled example. You may want to update it."
                )
            merged = merge_configs(merged, sys_cfg)

    return merged


def merge_configs(default: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge user configuration with default configuration.

    User values take precedence over defaults. Nested dictionaries are
    merged recursively.  Empty string ("") and None from the user config are
    treated as absent: the default value is inherited when a real default
    exists.  This lets config authors express "intentionally empty" only when
    the default is also empty.

    Args:
        default: Default configuration dictionary
        user: User configuration dictionary

    Returns:
        Merged configuration dictionary
    """
    merged = default.copy()

    for key, value in user.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_configs(merged[key], value)
        elif value in (None, ""):
            existing = merged.get(key)
            if existing in (None, ""):
                merged[key] = value  # no real default to inherit
        else:
            merged[key] = value

    return merged


def _warn_empty_user_values(
    user: Dict[str, Any], merged: Dict[str, Any], path: str, section: str = ""
) -> None:
    """Log one WARNING per key where the user set "" or None but a real default
    was inherited.  Called from load_config after the merge, where the file
    path is known."""
    for key, value in user.items():
        full_key = f"[{section}].{key}" if section else key
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            _warn_empty_user_values(value, merged[key], path, section=key)
        elif value in (None, ""):
            inherited = merged.get(key)
            if inherited not in (None, ""):
                LOGGER.warning(
                    "%s is empty in %s — inheriting default %r",
                    full_key,
                    path,
                    inherited,
                )


def load_config(paths: Union[List[str], List[Path]]) -> Dict[str, Any]:
    """
    Load a TOML configuration file with three-layer merging.

    Layer precedence (lowest to highest):
      bundled defaults  <  system/external config  <  user config

    The *paths* argument specifies where to search for the user-layer config.
    System-layer paths are loaded automatically from SYSTEM_CONFIG_PATHS.
    Bundled defaults are always present.

    Args:
        paths: List of user-config file paths to try (strings or Path objects).
               When ``--config`` is given, this is ``[config_path]``.
               When omitted, this is ``USER_CONFIG_PATHS``.

    Returns:
        Parsed configuration as a dictionary with all layers applied

    Raises:
        SystemExit: If no config file is found or parsing fails

    Examples:
        >>> config = load_config(['~/.config/enge.toml', '~/.enge.toml'])
        >>> api_key = config.get('testing_farm', {}).get('api_key')
    """
    if not paths:
        LOGGER.critical("No configuration file paths provided")
        raise ConfigurationError("No configuration file paths provided")

    expanded_paths = [Path(path).expanduser() for path in paths]
    for p in USER_CONFIG_PATHS:
        pp = Path(p).expanduser()
        if pp not in expanded_paths:
            expanded_paths.append(pp)

    user_config = None
    loaded_user_path: Optional[Path] = None
    for path in expanded_paths:
        if path.exists():
            LOGGER.debug(f"Loading configuration file from: {path}")
            try:
                with open(path, "rb") as f:
                    user_config = tomllib.load(f)

                LOGGER.info(f"Successfully loaded configuration from: {path}")
                loaded_user_path = path
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

    default_override_path: Optional[Union[str, Path]] = None
    override_source = "root"
    if user_config:
        override_candidate = user_config.get("default_config_path")
        if override_candidate is None:
            common_section = user_config.get("common")
            if isinstance(common_section, dict):
                override_candidate = common_section.get("default_config_path")
                if override_candidate is not None:
                    override_source = "[common]"
        if isinstance(override_candidate, (str, Path)):
            override_text = str(override_candidate).strip()
            if override_text:
                default_override_path = override_text
            else:
                LOGGER.warning(
                    f"Ignoring default_config_path override in {override_source} "
                    "because it is empty"
                )
        elif override_candidate is not None:
            LOGGER.warning(
                f"Ignoring default_config_path override in {override_source} "
                "because it must be a string path"
            )

    default_config = load_default_config(default_override_path)

    if user_config is None:
        LOGGER.warning("No user configuration file found, using defaults")
        LOGGER.info("Create a config file at one of these locations:")
        for path in expanded_paths:
            LOGGER.info(f"  - {path}")
        return default_config

    merged_config = merge_configs(default_config, user_config)

    _warn_empty_user_values(
        user_config, merged_config, str(loaded_user_path or "<unknown>")
    )

    LOGGER.debug("Configuration loaded and merged with defaults")
    return merged_config
