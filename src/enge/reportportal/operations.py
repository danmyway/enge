"""ReportPortal pipeline operations.

Architecture: two resolvers produce normalized launch dicts, three pipeline
operations consume them source-agnostically, two standalone operations are
dispatched directly.

Normalized derived keys on launch dicts:
  _finish_status, _end_time, _artifacts_url, _description,
  _finish_attributes, _xml_content, _task_uuid, _enriched,
  _launch_uuid, _launch_id, _items
"""

from __future__ import annotations

import logging
import re
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from enge.utils import parse_date_arg
from enge.utils.http_client import http_get
from enge.report.concurrent_parser import ConcurrentRequestParser
from enge.report.__main__ import parse_tasks

from enge.reportportal.utils import (
    parse_size_string,
    extract_latest_timestamp_from_xml,
    extract_artifacts_url_from_items,
    discover_artifacts_from_xml,
    map_artifacts_to_items,
    derive_status_from_items,
    has_in_progress_items,
    latest_end_time_from_items,
    is_launch_enriched,
    build_enriched_attributes,
    extract_existing_log_headers,
    filter_already_enriched,
    filter_to_failed_items,
    show_dryrun_finish_data,
    show_dryrun_enrichment,
    show_dryrun_delete_logs,
    show_dryrun_delete_stale,
)

if TYPE_CHECKING:
    from enge.reportportal.__main__ import ReportPortalLaunch

LOGGER = logging.getLogger(__name__)

# ===================================================================
# TF task completion predicate
# ===================================================================

_INCOMPLETE_TF_STATES = frozenset({"NEW", "QUEUED", "RUNNING", "CANCELED", "CANCELLED"})


def _is_tf_task_incomplete(tf_state: str) -> bool:
    """TF task completion gates all operations -- incomplete runs yield
    incomplete data."""
    return tf_state.upper() in _INCOMPLETE_TF_STATES


# ===================================================================
# Internal helpers (unchanged)
# ===================================================================


def _launch_start_time_ms(start_time: Any) -> Optional[int]:
    """Normalize a launch ``startTime`` to epoch milliseconds."""
    if isinstance(start_time, (int, float)):
        return int(start_time)
    if isinstance(start_time, str):
        if start_time.isdigit():
            return int(start_time)
        normalized = start_time.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    return None


def _apply_date_filters(
    launches: List[Dict[str, Any]],
    ctx,
) -> List[Dict[str, Any]]:
    """Narrow launches using ``--since`` / ``--until`` CLI dates."""
    since_str = getattr(ctx.cli_args, "since", None)
    until_str = getattr(ctx.cli_args, "until", None)
    if not since_str and not until_str:
        return launches

    since_ms: Optional[int] = None
    until_ms: Optional[int] = None
    if since_str:
        since_ms = int(parse_date_arg(since_str).timestamp() * 1000)
    if until_str:
        until_dt = parse_date_arg(until_str).replace(hour=23, minute=59, second=59)
        until_ms = int(until_dt.timestamp() * 1000)

    filtered: List[Dict[str, Any]] = []
    for launch in launches:
        start_time_ms = _launch_start_time_ms(launch.get("startTime"))
        if start_time_ms is None:
            continue
        if since_ms and start_time_ms < since_ms:
            continue
        if until_ms and start_time_ms > until_ms:
            continue
        filtered.append(launch)

    if len(filtered) != len(launches):
        LOGGER.info(
            f"Date filter narrowed {len(launches)} launch(es) " f"to {len(filtered)}"
        )
    return filtered


def _extract_tf_uuid(artifacts_url: str) -> Optional[str]:
    """Extract a Testing Farm task UUID from an artifacts URL."""
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        artifacts_url,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _get_tf_task_info(task_uuid: str, ctx) -> Optional[Dict[str, Any]]:
    """Query the TF API for a task's state and result."""
    tf_cfg = ctx.testing_farm
    api_key = tf_cfg.get("api_key", "")
    api_url = tf_cfg.get("api_endpoint_url", "")
    if not api_key or not api_url:
        return None
    try:
        resp = http_get(
            f"{api_url.rstrip('/')}/{task_uuid}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result") or {}
            return {
                "state": data.get("state"),
                "overall": (
                    result.get("overall") if isinstance(result, dict) else None
                ),
            }
    except Exception as exc:
        LOGGER.debug(f"Could not query TF task info for {task_uuid}: {exc}")
    return None


def _stamp_logs_attached(
    rp: ReportPortalLaunch,
    launch_id: Optional[int],
) -> None:
    """Stamp ``logs_attached=true`` on a launch, preserving existing attrs."""
    if launch_id is None:
        LOGGER.warning("Cannot stamp logs_attached: launch_id is None")
        return
    launch_data = rp.get_launch_by_id(launch_id)
    if launch_data is None:
        LOGGER.warning(
            f"Cannot fetch launch {launch_id} to read "
            f"existing attributes — stamping with empty base"
        )
        launch_data = {"attributes": []}
    success = rp.update_launch(
        launch_id,
        attributes=build_enriched_attributes(launch_data),
    )
    if success:
        LOGGER.info(f"Stamped logs_attached=true on launch {launch_id}")
    else:
        LOGGER.warning(f"Failed to stamp logs_attached on launch {launch_id}")


def extract_tmt_context_from_task(
    task_result,
    ctx,
) -> Optional[Dict[str, Any]]:
    """Extract TMT context from ``environments_requested[0].tmt.context``."""
    try:
        task_url = task_result.url
        LOGGER.debug(f"Fetching full task data from: {task_url}")
        response = http_get(
            task_url,
            headers={"Authorization": f"Bearer {ctx.testing_farm.get('api_key')}"},
            timeout=30,
        )
        if response.status_code != 200:
            LOGGER.warning(f"Could not fetch task data: HTTP {response.status_code}")
            LOGGER.debug(f"Response text: {response.text}")
            return None

        task_data = response.json()
        LOGGER.debug(f"Task data keys: {list(task_data.keys())}")
        environments = task_data.get("environments_requested", [])
        LOGGER.debug(f"Found {len(environments)} environments in task data")

        if environments and len(environments) > 0:
            env = environments[0]
            LOGGER.debug(f"Environment keys: {list(env.keys())}")
            tmt_config = env.get("tmt", {})
            LOGGER.debug(
                f"TMT config keys: "
                f"{list(tmt_config.keys()) if tmt_config else 'No TMT config'}"
            )
            context = tmt_config.get("context", {})
            LOGGER.debug(f"TMT context: {context}")
            if context:
                LOGGER.info(f"Found TMT context with {len(context)} attributes")
                return context
            else:
                LOGGER.warning("TMT context is empty")
        else:
            LOGGER.warning("No environments found in task data")

        LOGGER.warning("No TMT context found in task data")
        LOGGER.debug("Searching for context in alternative locations...")
        for key in ["test", "environments", "environments_requested"]:
            if key in task_data:
                LOGGER.debug(f"Found '{key}' section in task data")
        return None
    except Exception as e:
        LOGGER.error(f"Error extracting TMT context: {e}")
        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return None


# ===================================================================
# Status derivation helpers
# ===================================================================


def _derive_status_from_task(task_result) -> str:
    """Derive RP finish status from TF task state + overall result."""
    mapping = {"COMPLETE": "PASSED", "ERROR": "FAILED", "FAILED": "FAILED"}
    status = mapping.get(task_result.request_state.upper(), "STOPPED")
    if task_result.request_result_overall:
        result_map = {"passed": "PASSED", "failed": "FAILED", "error": "FAILED"}
        status = result_map.get(task_result.request_result_overall.lower(), status)
    return status


def _derive_status_from_tf_or_items(
    tf_info: Optional[Dict[str, Any]],
    items: List[Dict[str, Any]],
) -> str:
    """Derive RP finish status from TF task info or RP test items."""
    tf_state = ((tf_info or {}).get("state") or "").upper()
    if tf_state in ("ERROR", "FAILED"):
        return "FAILED"
    if tf_state in ("CANCELED", "CANCELLED"):
        return "STOPPED"
    if tf_info and tf_info.get("overall"):
        overall = tf_info["overall"].lower()
        return {"passed": "PASSED", "failed": "FAILED"}.get(
            overall, derive_status_from_items(items)
        )
    return derive_status_from_items(items)


# ===================================================================
# Resolvers
# ===================================================================


def resolve_from_tasks(
    rp: ReportPortalLaunch,
    ctx,
) -> List[Dict[str, Any]]:
    """Resolve launches from TF task input.

    Parses task URLs, fetches task info, applies TF guard, extracts TMT
    context, finds matching RP launches, and derives normalized fields.
    """
    request_url_list, _tasks_source = parse_tasks(ctx)
    if not request_url_list:
        LOGGER.error("No task URLs found to process")
        return []

    launches: List[Dict[str, Any]] = []
    for task_url in request_url_list:
        LOGGER.info(f"Processing task: {task_url}")
        with ConcurrentRequestParser(ctx=ctx) as parser:
            task_result = parser._fetch_task_info(
                task_url,
                process_state=False,
            )
            if not task_result:
                LOGGER.warning(f"Could not fetch task info for {task_url}")
                continue

            task_uuid = task_result.request_uuid

            # TF task completion gates all operations
            if _is_tf_task_incomplete(task_result.request_state):
                LOGGER.info(
                    f"Task {task_uuid} is in state "
                    f"'{task_result.request_state}', skipping"
                )
                continue

            LOGGER.info(
                f"Task {task_uuid} is in state "
                f"'{task_result.request_state}', "
                f"proceeding to finish launch"
            )
            task_result = parser._fetch_xml_results(task_result)

            tmt_context = extract_tmt_context_from_task(task_result, ctx)
            if not tmt_context:
                LOGGER.warning(f"No TMT context found for task {task_uuid}")
                continue
            uniq_id = tmt_context.get("uniq_id")
            if not uniq_id:
                LOGGER.warning(f"No uniq_id found in TMT context for task {task_uuid}")
                continue
            LOGGER.info(f"Found uniq_id: {uniq_id}")

            launch_uuid = rp.find_launch_by_uniq_id(uniq_id, tmt_context)
            if not launch_uuid:
                LOGGER.error(f"No launch found matching uniq_id '{uniq_id}'")
                continue

            # End time from XML, fallback to now
            end_time = None
            if task_result.xunit_content and task_result.xunit_content.strip():
                end_time = extract_latest_timestamp_from_xml(
                    task_result.xunit_content,
                )
            if not end_time:
                LOGGER.warning(
                    "No timestamp found in XML, " "using current time as fallback"
                )
                end_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            artifacts_url = (
                task_result.results_xml_url.rsplit("/results.xml", 1)[0]
                if task_result.results_xml_url
                else None
            )
            attributes = [
                {"key": k, "value": str(v)}
                for k, v in tmt_context.items()
                if v is not None
            ]
            launches.append(
                {
                    "_launch_uuid": launch_uuid,
                    "_finish_status": _derive_status_from_task(task_result),
                    "_end_time": end_time,
                    "_artifacts_url": artifacts_url,
                    "_description": f"\n{artifacts_url}" if artifacts_url else None,
                    "_finish_attributes": attributes,
                    "_xml_content": task_result.xunit_content,
                    "_task_uuid": task_uuid,
                    "_enriched": False,
                    "_launch_id": None,
                    "_items": None,
                }
            )
    return launches


def resolve_from_query(
    rp: ReportPortalLaunch,
    ctx,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Resolve launches from RP query (minimal — just query + date filter).

    Returns raw RP launch dicts with ``_launch_uuid`` added.  Per-operation
    logic (item fetch, TF check, XML fetch) stays in the operation wrappers.
    """
    raw = _apply_date_filters(
        rp.get_all_launches(status=status_filter),
        ctx,
    )
    result: List[Dict[str, Any]] = []
    for launch in raw:
        uuid = launch.get("uuid") or launch.get("id", "")
        if not uuid:
            LOGGER.warning(f"Launch missing UUID, skipping: {launch}")
            continue
        result.append({**launch, "_launch_uuid": str(uuid)})
    return result


# ===================================================================
# Pipeline operations — source-agnostic
# ===================================================================


def op_finish(
    rp: ReportPortalLaunch,
    launches: List[Dict[str, Any]],
    ctx,
    dryrun: bool,
) -> int:
    """Finish resolved launches.  Reads ``_finish_status``, ``_end_time``,
    ``_description``, ``_finish_attributes`` — no source branching."""
    processed = 0
    for launch in launches:
        uuid = launch["_launch_uuid"]
        task_id = launch.get("_task_uuid")
        if dryrun:
            show_dryrun_finish_data(
                api_base=rp.api_base,
                token=rp.token,
                task_uuid=task_id or "(all-launches)",
                launch_uuid=uuid,
                end_time=launch["_end_time"],
                status=launch["_finish_status"],
                description=launch.get("_description"),
                attributes=launch.get("_finish_attributes"),
            )
            processed += 1
        else:
            try:
                ok = rp.finish_launch(
                    launch_uuid=uuid,
                    end_time=launch["_end_time"],
                    status=launch["_finish_status"],
                    description=launch.get("_description"),
                    attributes=launch.get("_finish_attributes"),
                )
                if ok:
                    label = task_id or launch.get("name", uuid)
                    LOGGER.info(
                        f"Finished launch '{label}' ({uuid}) "
                        f"with status {launch['_finish_status']}"
                    )
                    processed += 1
            except Exception as e:
                LOGGER.error(f"Failed to finish launch {uuid}: {e}")

    action = "shown" if dryrun else "finished"
    if processed > 0:
        LOGGER.info(f"Successfully {action} {processed} launch(es)")
    else:
        LOGGER.info("No launches required finishing")
    return 0


def op_enrich(
    rp: ReportPortalLaunch,
    launches: List[Dict[str, Any]],
    ctx,
    dryrun: bool,
) -> int:
    """Enrich resolved launches with artifact logs.  Reads ``_xml_content``,
    ``_enriched``, ``_launch_uuid`` — no source branching."""
    max_file_size = parse_size_string(
        str(rp.config.get("enrich_max_file_size", "5 MB"))
    )
    enriched_count = 0

    for launch in launches:
        uuid = launch["_launch_uuid"]
        task_id = launch.get("_task_uuid")
        name = launch.get("name", task_id or uuid)

        if launch.get("_enriched"):
            LOGGER.debug(f"Launch '{name}' ({uuid}) already enriched, skipping")
            continue

        xml = launch.get("_xml_content")
        if not xml:
            if task_id:
                LOGGER.warning(f"No results.xml content for task {task_id}")
            continue

        artifacts = discover_artifacts_from_xml(xml)
        if not artifacts:
            LOGGER.info(f"No artifact logs found in results.xml for {task_id or name}")
            continue

        # Resolve launch_id and items if not already available
        lid = launch.get("_launch_id")
        items = launch.get("_items")
        if lid is None:
            lid = rp.get_launch_id_from_uuid(uuid)
        if items is None:
            if lid is not None:
                LOGGER.info(f"Resolved launch numeric ID: {lid}")
                items = rp.get_launch_test_items(lid)
            else:
                LOGGER.warning(
                    f"Could not resolve numeric launch ID for "
                    f"{uuid} -- all logs will go to launch level"
                )
                items = []

        mapped = filter_to_failed_items(
            map_artifacts_to_items(artifacts, items),
            items,
        )
        if not mapped:
            LOGGER.info(f"No failed items in launch {uuid} — skipping enrichment")
            continue

        # Layer 2: message-header dedup
        if lid is not None:
            existing_logs = rp.get_launch_logs(lid)
            LOGGER.info(
                f"Dedup check: {len(existing_logs)} existing log(s) in "
                f"launch {uuid if task_id else name}, "
                f"{len(mapped)} artifact(s) to consider"
            )
            mapped = filter_already_enriched(
                mapped,
                extract_existing_log_headers(existing_logs),
            )
            if not mapped:
                if task_id:
                    LOGGER.info(
                        f"All artifacts already uploaded for task "
                        f"{task_id} — skipping"
                    )
                    if not dryrun:
                        _stamp_logs_attached(rp, lid)
                else:
                    LOGGER.info(
                        f"All artifacts already uploaded for launch "
                        f"'{name}' — stamping attribute"
                    )
                    if not dryrun:
                        rp.update_launch(
                            lid,
                            attributes=build_enriched_attributes(launch),
                        )
                enriched_count += 1
                continue

        if dryrun:
            show_dryrun_enrichment(
                task_id or "(all-launches)",
                str(uuid),
                mapped,
            )
            enriched_count += 1
        else:
            count = rp.upload_logs_to_rp(
                str(uuid),
                mapped,
                max_file_size=max_file_size,
            )
            if count > 0:
                enriched_count += 1
                LOGGER.info(
                    f"Enriched launch {uuid if task_id else name} "
                    f"with {count} log(s)"
                    f"{f' from task {task_id}' if task_id else ''}"
                )
                if task_id:
                    _stamp_logs_attached(rp, lid)
                else:
                    rp.update_launch(
                        lid,
                        attributes=build_enriched_attributes(launch),
                    )
            else:
                LOGGER.warning(f"No logs uploaded for {task_id or name}")

    action = "shown" if dryrun else "enriched"
    if enriched_count > 0:
        LOGGER.info(f"Log enrichment complete: {enriched_count} launch(es) {action}")
    else:
        LOGGER.info("No launches required enrichment")
    return 0


def op_delete_logs(
    rp: ReportPortalLaunch,
    launches: List[Dict[str, Any]],
    ctx,
    dryrun: bool,
) -> int:
    """Delete logs from resolved launches."""
    processed = 0
    for launch in launches:
        uuid = launch["_launch_uuid"]
        task_id = launch.get("_task_uuid")
        name = launch.get("name", "Unknown")

        if dryrun:
            lid = launch.get("_launch_id")
            if lid is None:
                lid = rp.get_launch_id_from_uuid(uuid)
            logs = rp.get_launch_logs(lid) if lid else []
            show_dryrun_delete_logs(
                task_id or "(all-launches)",
                str(uuid),
                logs,
            )
            processed += 1
        else:
            total, deleted = rp.delete_launch_logs(str(uuid))
            if total > 0:
                LOGGER.info(
                    f"Deleted {deleted}/{total} log(s) from launch "
                    f"'{name}' ({uuid})"
                )
                processed += 1
            else:
                LOGGER.info(f"No logs to delete for launch '{name}' ({uuid})")

    if processed > 0:
        action = "shown" if dryrun else "processed"
        LOGGER.info(f"Log deletion complete: {processed} launch(es) {action}")
        return 0
    LOGGER.warning("No launches had logs deleted")
    return 1


# ===================================================================
# Standalone operations
# ===================================================================

_STALE_STATUSES = ("STOPPED", "INTERRUPTED")


def op_delete_stale(
    rp: ReportPortalLaunch,
    ctx,
    dryrun: bool,
) -> int:
    """Delete stale launches — stopped/interrupted with no test items."""
    launches: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for status in _STALE_STATUSES:
        for launch in rp.get_all_launches(status):
            lid = launch.get("id")
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
                launches.append(launch)
    launches = _apply_date_filters(launches, ctx)

    if not launches:
        LOGGER.info("No STOPPED/INTERRUPTED launches found")
        return 0

    stale = [
        lch
        for lch in launches
        if lch.get("id") is not None and not rp.get_launch_test_items(lch["id"])
    ]
    if not stale:
        LOGGER.info(
            f"No stale launches found among "
            f"{len(launches)} STOPPED/INTERRUPTED launch(es)"
        )
        return 0

    if dryrun:
        show_dryrun_delete_stale(stale)
        return 0

    deleted = 0
    for launch in stale:
        lid, name = launch.get("id"), launch.get("name", "Unknown")
        uuid = launch.get("uuid", "")
        if rp.delete_launch(lid):
            LOGGER.info(f"Deleted stale launch '{name}' ({uuid})")
            deleted += 1
        else:
            LOGGER.error(f"Failed to delete launch '{name}' ({uuid})")

    LOGGER.info(f"Deleted {deleted}/{len(stale)} stale launch(es)")
    return 0 if deleted > 0 else 1


def op_check(rp: ReportPortalLaunch, ctx) -> int:
    """Test RP connection and show sample data for debugging."""
    try:
        LOGGER.info("Testing ReportPortal connection...")
        LOGGER.info("Testing launch listing...")
        launches = rp.list_launches(size=5, page=0, status=None)
        if launches:
            LOGGER.info(f"Successfully retrieved {len(launches)} launches")
            for i, launch in enumerate(launches[:3]):
                LOGGER.info(
                    f"  Launch {i + 1}: {launch.get('name', 'No name')} "
                    f"(UUID: {launch.get('uuid') or launch.get('id', 'No UUID')})"
                )
        else:
            LOGGER.warning(
                "No launches found - " "this might be expected for a new project"
            )
        LOGGER.info("Testing report module integration...")
        request_url_list, tasks_source = parse_tasks(ctx)
        if request_url_list:
            LOGGER.info(f"Found {len(request_url_list)} task URLs from report module")
            for i, url in enumerate(request_url_list[:3]):
                LOGGER.info(f"  Task URL {i + 1}: {url}")
        else:
            LOGGER.warning(
                "No task URLs found - you may need to provide "
                "task IDs via -i, -f, or --get-tag"
            )
        return 0
    except Exception as e:
        LOGGER.error(f"Connection test failed: {e}")
        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return 1


# ===================================================================
# Legacy wrappers — called by parity tests and main()
# ===================================================================


def finish_launch_from_task(rp: ReportPortalLaunch) -> int:
    """Finish RP launches from TF tasks (resolve + op_finish)."""
    try:
        dryrun = getattr(rp.ctx.cli_args, "dryrun", False)
        launches = resolve_from_tasks(rp, rp.ctx)
        if not launches:
            LOGGER.warning("No launches were finished")
            return 1
        rc = op_finish(rp, launches, rp.ctx, dryrun)
        if dryrun:
            LOGGER.info(f"Successfully shown {len(launches)} launch(es)")
        return rc
    except Exception as e:
        LOGGER.error(f"Error in finish launch logic: {e}")
        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return 1


def enrich_logs_from_task(rp: ReportPortalLaunch) -> int:
    """Enrich RP launch logs from TF tasks (resolve + op_enrich)."""
    try:
        dryrun = getattr(rp.ctx.cli_args, "dryrun", False)
        LOGGER.info("Starting log enrichment from Testing Farm results.xml")
        launches = resolve_from_tasks(rp, rp.ctx)
        if not launches:
            LOGGER.warning("No launches were enriched")
            return 1
        return op_enrich(rp, launches, rp.ctx, dryrun)
    except Exception as e:
        LOGGER.error(f"Error during log enrichment: {e}")
        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return 1


def delete_logs_from_task(rp: ReportPortalLaunch) -> int:
    """Delete launch logs from TF tasks (resolve + op_delete_logs)."""
    try:
        dryrun = getattr(rp.ctx.cli_args, "dryrun", False)
        LOGGER.info("Starting log deletion for ReportPortal launches")
        launches = resolve_from_tasks(rp, rp.ctx)
        if not launches:
            LOGGER.warning("No launches had logs deleted")
            return 1
        return op_delete_logs(rp, launches, rp.ctx, dryrun)
    except Exception as e:
        LOGGER.error(f"Error during log deletion: {e}")
        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return 1


def finish_all_in_progress_launches(rp: ReportPortalLaunch) -> int:
    """Finish all IN_PROGRESS launches (query-resolve + op_finish)."""
    raw = resolve_from_query(rp, rp.ctx, status_filter="IN_PROGRESS")
    if not raw:
        LOGGER.info("No IN_PROGRESS launches found")
        return 0

    fallback_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    normalized: List[Dict[str, Any]] = []

    for launch in raw:
        uuid = launch["_launch_uuid"]
        name = launch.get("name", "Unknown")
        nid = launch.get("id")

        items = rp.get_launch_test_items(nid) if nid else []
        if not items:
            LOGGER.info(f"Launch '{name}' ({uuid}) has no test items yet, skipping")
            continue
        if has_in_progress_items(items):
            LOGGER.info(
                f"Launch '{name}' ({uuid}) still has IN_PROGRESS " f"items, skipping"
            )
            continue

        artifacts_url = extract_artifacts_url_from_items(items)
        tf_info: Optional[Dict[str, Any]] = None
        if artifacts_url:
            tf_uuid = _extract_tf_uuid(artifacts_url)
            if tf_uuid:
                tf_info = _get_tf_task_info(tf_uuid, rp.ctx)

        tf_state = (tf_info or {}).get("state", "")
        if tf_state and tf_state.upper() in ("NEW", "QUEUED", "RUNNING"):
            LOGGER.info(f"TF task for launch '{name}' is '{tf_state}', skipping")
            continue

        end_time = latest_end_time_from_items(items)
        if not end_time:
            LOGGER.debug(
                f"No endTime in test items for launch '{name}', " f"using current time"
            )
            end_time = fallback_time

        normalized.append(
            {
                **launch,
                "_finish_status": _derive_status_from_tf_or_items(tf_info, items),
                "_end_time": end_time,
                "_description": f"\n{artifacts_url}" if artifacts_url else None,
                "_finish_attributes": launch.get("attributes"),
                "_task_uuid": None,
            }
        )

    dryrun = getattr(rp.ctx.cli_args, "dryrun", False)
    return op_finish(rp, normalized, rp.ctx, dryrun)


def delete_logs_all_launches(rp: ReportPortalLaunch) -> int:
    """Delete logs from all IN_PROGRESS launches (query + op_delete_logs)."""
    raw = resolve_from_query(rp, rp.ctx, status_filter="IN_PROGRESS")
    if not raw:
        LOGGER.warning("No IN_PROGRESS launches found")
        return 1

    normalized: List[Dict[str, Any]] = []
    for launch in raw:
        uuid = launch["_launch_uuid"]
        nid = launch.get("id")
        if not uuid or nid is None:
            LOGGER.warning(f"Launch missing UUID/ID, skipping: {launch}")
            continue
        normalized.append({**launch, "_launch_id": nid, "_task_uuid": None})

    dryrun = getattr(rp.ctx.cli_args, "dryrun", False)
    return op_delete_logs(rp, normalized, rp.ctx, dryrun)


def enrich_all_launches(
    rp: ReportPortalLaunch,
    status_filter: Optional[str] = None,
) -> int:
    """Enrich logs for RP-queried launches (query-resolve + op_enrich)."""
    raw = resolve_from_query(rp, rp.ctx, status_filter=status_filter)
    if not raw:
        LOGGER.info("No launches found for enrichment")
        return 0

    dryrun = getattr(rp.ctx.cli_args, "dryrun", False)
    normalized: List[Dict[str, Any]] = []

    for launch in raw:
        uuid = launch["_launch_uuid"]
        name = launch.get("name", "Unknown")
        nid = launch.get("id")
        if not uuid or nid is None:
            continue

        if is_launch_enriched(launch):
            LOGGER.debug(f"Launch '{name}' ({uuid}) already enriched, skipping")
            continue

        items = rp.get_launch_test_items(nid)
        if not items:
            LOGGER.debug(f"No test items in launch '{name}', skipping")
            continue

        artifacts_url = extract_artifacts_url_from_items(items)
        if not artifacts_url:
            LOGGER.debug(f"No artifacts URL in launch '{name}', skipping")
            continue

        tf_uuid = _extract_tf_uuid(artifacts_url)
        if tf_uuid:
            tf_info = _get_tf_task_info(tf_uuid, rp.ctx)
            if tf_info:
                tf_state = (tf_info.get("state") or "").upper()
                if tf_state in ("NEW", "QUEUED", "RUNNING"):
                    LOGGER.info(
                        f"TF task {tf_uuid} for launch '{name}' "
                        f"is '{tf_state}', skipping enrichment"
                    )
                    continue
                if tf_state == "COMPLETE" and tf_info.get("overall") == "passed":
                    LOGGER.info(
                        f"TF task {tf_uuid} for launch '{name}' "
                        f"PASSED — nothing to enrich"
                    )
                    continue

        results_xml_url = artifacts_url.rstrip("/") + "/results.xml"
        LOGGER.info(
            f"Fetching results.xml for launch '{name}' " f"from {results_xml_url}"
        )
        try:
            resp = http_get(results_xml_url, timeout=30)
            if resp.status_code != 200:
                LOGGER.warning(
                    f"Could not fetch results.xml for launch "
                    f"'{name}': HTTP {resp.status_code}"
                )
                continue
            xml_content = resp.text
        except Exception as e:
            LOGGER.warning(f"Error fetching results.xml for launch '{name}': {e}")
            continue

        normalized.append(
            {
                **launch,
                "_launch_id": nid,
                "_xml_content": xml_content,
                "_task_uuid": None,
                "_enriched": False,
                "_items": items,
            }
        )

    if not normalized:
        LOGGER.info("No launches required enrichment")
        return 0
    return op_enrich(rp, normalized, rp.ctx, dryrun)


def delete_stale_launches(rp: ReportPortalLaunch) -> int:
    """Legacy wrapper for op_delete_stale."""
    return op_delete_stale(rp, rp.ctx, getattr(rp.ctx.cli_args, "dryrun", False))


def test_connection_and_data(rp: ReportPortalLaunch) -> int:
    """Legacy wrapper for op_check."""
    return op_check(rp, rp.ctx)
