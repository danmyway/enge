#!/usr/bin/env python3
"""
Shared ReportPortal helper functions.

Provides a small façade over ReportPortalLaunch to reduce duplication across
modules (dispatch, rerun).
"""

from typing import Optional, Dict, Any, List
import json
import logging

from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX
from enge.utils.source_target_parser import (
    generate_reportportal_environment_variables,
)


LOGGER = logging.getLogger(__name__)

# Sentinel returned on dry-run so callers can distinguish "no launch" from
# "launch would have been created".
DRYRUN_PLACEHOLDER = "dryrun_placeholder"
DRYRUN_UUID = "00000000-0000-0000-0000-000000000000"


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


def _pretty_print_payload(payload: dict) -> None:
    """Print a JSON payload with optional syntax highlighting."""
    try:
        from pygments import highlight, lexers, formatters

        colorful = highlight(
            json.dumps(payload, indent=4),
            lexers.JsonLexer(),
            formatters.TerminalFormatter(),
        )
        LOGGER.info("DRY RUN | ReportPortal launch payload that would be sent:")
        print(colorful)
    except Exception:
        LOGGER.info("DRY RUN | ReportPortal launch payload that would be sent:")
        print(json.dumps(payload, indent=4))


def create_launch(
    *,
    context: Optional[Dict[str, Any]] = None,
    tmt_context: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    cli_args: Optional[object] = None,
    dryrun: bool = False,
    launch_name: Optional[str] = None,
    extra_tags: Optional[List[str]] = None,
) -> Optional[str]:
    """Create (or dry-run) a ReportPortal launch.

    Parameters
    ----------
    launch_name:
        Explicit launch name override.  When ``None`` the name is derived
        from *config* / *cli_args* / *context* via ``_resolve_launch_name``.
    extra_tags:
        Additional tags to inject into the launch payload (e.g. ``["rerun"]``).

    Returns
    -------
    - A launch UUID string when the launch is created successfully.
    - ``DRYRUN_PLACEHOLDER`` when *dryrun* is ``True``.
    - ``None`` on failure.
    """
    if launch_name is None:
        launch_name = _resolve_launch_name(context, config or {}, cli_args)

    from enge.reportportal.__main__ import ReportPortalLaunch

    if dryrun:
        try:
            rp_launch = ReportPortalLaunch()
            payload = rp_launch.generate_launch_payload(
                name=launch_name, context=context, tmt_context=tmt_context
            )
            _inject_extra_tags(payload, extra_tags)
            _pretty_print_payload(payload)
        except Exception as e:
            LOGGER.warning(f"DRY RUN | Could not generate ReportPortal payload: {e}")
        return DRYRUN_PLACEHOLDER

    try:
        if extra_tags:
            # Subclass to inject extra tags via the standard pipeline
            class _TaggedLaunch(ReportPortalLaunch):
                def generate_launch_payload(
                    self_, name=None, description=None, context=None, tmt_context=None
                ):
                    data = super().generate_launch_payload(
                        name, description, context, tmt_context
                    )
                    _inject_extra_tags(data, extra_tags)
                    return data

            rp_launch = _TaggedLaunch()
        else:
            rp_launch = ReportPortalLaunch()

        launch_uuid = rp_launch.create_launch(
            name=launch_name, context=context, tmt_context=tmt_context
        )
        return launch_uuid
    except Exception as e:
        LOGGER.error(f"Failed to create ReportPortal launch: {e}")
        return None


def _inject_extra_tags(
    payload: Dict[str, Any], extra_tags: Optional[List[str]]
) -> None:
    """Add *extra_tags* to *payload* in place (idempotent)."""
    if not extra_tags:
        return
    tags = payload.setdefault("tags", [])
    for tag in extra_tags:
        if tag not in tags:
            tags.append(tag)


# -------------------------------------------------------------------
# Environment-variable helpers
# -------------------------------------------------------------------


def filter_rp_launch_env_vars(
    env_vars: Dict[str, str],
    launch_uuid: str,
) -> Dict[str, str]:
    """Return a copy of *env_vars* suitable for a RP-enabled TF request.

    * Strips ``…_LAUNCH`` and ``…_LAUNCH_DESCRIPTION`` keys.
    * Adds ``…_UPLOAD_TO_LAUNCH`` pointing at *launch_uuid*.
    """
    filtered = {
        k: v
        for k, v in env_vars.items()
        if not (k.endswith("LAUNCH") or k.endswith("LAUNCH_DESCRIPTION"))
    }
    upload_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}UPLOAD_TO_LAUNCH"
    filtered[upload_key] = launch_uuid
    return filtered
