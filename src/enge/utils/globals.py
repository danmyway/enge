#!/usr/bin/env python3
"""
Global constants for enge.

This module defines immutable constants used throughout the application.
Configuration defaults are handled in the default config file, not here.
"""

from typing import Dict, Tuple


# ==================== ARTIFACT MAPPINGS ====================

ARTIFACT_MAPPING: Dict[str, str] = {
    "brew": "redhat-brew-build",
    "copr": "fedora-copr-build",
}
"""Mapping of artifact type aliases to Testing Farm artifact types."""

COPR_PACKAGE_ALIASES: Dict[str, str] = {
    "lp": "leapp",
    "lpr": "leapp-repository",
}
"""Mapping of package aliases to COPR package names for reference parsing."""

# ==================== COPR API CONFIGURATION ====================

COPR_BASE_URL: str = "https://copr.fedorainfracloud.org"
"""Base URL for COPR API endpoints."""

COPR_BUILT_PACKAGES_API_URL: str = f"{COPR_BASE_URL}/api_3/build/built-packages"
"""API endpoint for fetching built packages from a COPR build."""

# ==================== DEFAULT CONFIG SEARCH PATHS ====================

DEFAULT_USER_CONFIG_PATHS: Tuple[str, ...] = (
    "~/.config/enge_user_config.toml",
    "~/enge_user_config.toml",
    "/etc/enge/enge_user_config.toml",
)
"""Default paths to search for configuration files, in order of preference."""

# ==================== REPORTPORTAL CONFIGURATION ====================

TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX: str = "TMT_PLUGIN_REPORT_REPORTPORTAL_"
"""Prefix for ReportPortal environment variables generated from config values."""

# ==================== REPORTPORTAL EVENT COMPATIBILITY ====================

RP_COMPATIBLE_EVENT: Tuple[str, ...] = (
    "preliminary",
    "ctc1",
    "ctc2",
    "rc-baseline-q",
    "rc-compose-q",
    "rc-regression",
)
"""Events that enable ReportPortal launch creation when matched by 'event'."""

# ==================== EXIT CODES & DEFAULTS ====================

# Standardized exit codes for CLI entrypoints
EXIT_GENERAL_ERROR: int = 1
EXIT_ALL_PASS: int = 0
EXIT_PARTIAL_FAILURE: int = 2
EXIT_CONFIG_ERROR: int = 99
EXIT_INTERRUPT: int = 130

# Default network timeouts (seconds)
REQUEST_TIMEOUT_DEFAULT: int = 30
REQUEST_POLL_TIMEOUT: int = 10

# SubmitTest response watcher total wait time (seconds)
RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT: int = 20

# ==================== PARALLELISM DEFAULTS ====================

# Global hardcoded default for tests parallelism when nothing else is set
PARALLEL_LIMIT_DEFAULT: int = 20
