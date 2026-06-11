#!/usr/bin/env python3
"""
Shared ReportPortal helper functions.

Provides a small façade over ReportPortalLaunch to reduce duplication across
modules (dispatch, rerun).
"""

from typing import Optional, Dict, Any
import json
import logging

from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX
from enge.utils.source_target_parser import (
    generate_reportportal_environment_variables,
)


LOGGER = logging.getLogger(__name__)


def _resolve_launch_name(context: Optional[Dict[str, Any]], config, cli_args) -> str:
    """Derive a ReportPortal launch name using config + context rules."""
    rp_env_vars = generate_reportportal_environment_variables(
        config=config,
        cli_args=cli_args,
        set_name=context.get("set_name") if context else None,
        architecture=context.get("architecture") if context else None,
        tier=context.get("tier") if context else None,
        source_release=context.get("source_release") if context else None,
        target_release=context.get("target_release") if context else None,
        source_compose=context.get("source_compose") if context else None,
        target_compose=context.get("target_compose") if context else None,
        event=context.get("event") if context else None,
    )
    launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
    return rp_env_vars.get(launch_key)


def create_launch(
    *,
    context: Optional[Dict[str, Any]] = None,
    tmt_context: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    cli_args: Optional[object] = None,
    dryrun: bool = False,
) -> Optional[str]:
    """
    Create (or dry-run) a ReportPortal launch.

    Returns a launch UUID when created, or None when dry-run or on failure.
    """
    # Resolve name using config + CLI overrides + context
    launch_name = _resolve_launch_name(context, config or {}, cli_args)

    from enge.reportportal.__main__ import ReportPortalLaunch

    if dryrun:
        try:
            rp_launch = ReportPortalLaunch()
            payload = rp_launch.generate_launch_payload(
                name=launch_name, context=context, tmt_context=tmt_context
            )

            from enge.utils import redact_sensitive

            LOGGER.info("DRY RUN | ReportPortal launch payload that would be sent:")
            print(json.dumps(redact_sensitive(payload), indent=4))
        except Exception as e:
            LOGGER.warning(f"DRY RUN | Could not generate ReportPortal payload: {e}")
        return None

    try:
        rp_launch = ReportPortalLaunch()
        launch_uuid = rp_launch.create_launch(
            name=launch_name, context=context, tmt_context=tmt_context
        )
        return launch_uuid
    except Exception as e:
        LOGGER.error(f"Failed to create ReportPortal launch: {e}")
        return None
