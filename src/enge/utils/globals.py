#!/usr/bin/env python3
"""
Global constants for enge.

This module defines immutable constants used throughout the application.
Configuration defaults are handled in the default config file, not here.
"""

from enum import IntEnum
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

# Custom log level between INFO (20) and DEBUG (10)
VERBOSE: int = 15


class ExitCode(IntEnum):
    SUCCESS = 0
    EXCEPTION = 1
    TEST_FAILURE = 2
    TEST_ERROR = 3
    MISSING_RESULTS = 4
    CONFIG_ERROR = 99
    INTERRUPT = 130


# Legacy aliases — other modules still import these bare ints during migration
EXIT_GENERAL_ERROR: int = ExitCode.EXCEPTION
EXIT_ALL_PASS: int = ExitCode.SUCCESS
EXIT_PARTIAL_FAILURE: int = ExitCode.TEST_FAILURE
EXIT_CONFIG_ERROR: int = ExitCode.CONFIG_ERROR
EXIT_INTERRUPT: int = ExitCode.INTERRUPT

# Severity rank for report exit-code aggregation.
# Numeric order (0<2<3<4) does NOT match severity: error(3) outranks
# missing(4) because missing results are rerun candidates and must
# never mask a real error.  Order: TEST_ERROR > TEST_FAILURE > MISSING > SUCCESS.
_SEVERITY_RANK: Dict[ExitCode, int] = {
    ExitCode.SUCCESS: 0,
    ExitCode.MISSING_RESULTS: 1,
    ExitCode.TEST_FAILURE: 2,
    ExitCode.TEST_ERROR: 3,
}


def worst_exit_code(a: "ExitCode | None", b: "ExitCode | None") -> "ExitCode | None":
    """Return whichever of *a* and *b* is more severe per the report contract."""
    if a is None:
        return b
    if b is None:
        return a
    ra = _SEVERITY_RANK.get(a, a.value)
    rb = _SEVERITY_RANK.get(b, b.value)
    return a if ra >= rb else b


# Default network timeouts (seconds)
REQUEST_TIMEOUT_DEFAULT: int = 30
REQUEST_POLL_TIMEOUT: int = 10

# SubmitTest response watcher total wait time (seconds)
RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT: int = 20

# ==================== PARALLELISM DEFAULTS ====================

# Global hardcoded default for tests parallelism when nothing else is set
PARALLEL_LIMIT_DEFAULT: int = 20
