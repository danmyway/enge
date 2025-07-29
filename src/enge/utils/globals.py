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

# ==================== DEFAULT CONFIG SEARCH PATHS ====================

DEFAULT_CONFIG_PATHS: Tuple[str, ...] = (
    "~/.config/enge.toml",
    "~/.enge.toml",
    "./enge.toml",
)
"""Default paths to search for configuration files, in order of preference."""

# ==================== REPORTPORTAL CONFIGURATION ====================

TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX: str = "TMT_PLUGIN_REPORT_REPORTPORTAL_"
"""Prefix for ReportPortal environment variables generated from config values."""
