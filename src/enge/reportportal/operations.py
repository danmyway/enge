#!/usr/bin/env python3
"""
ReportPortal workflow orchestration.

Contains high-level operations (finish, enrich, delete-logs, all-launches)
that combine ReportPortalLaunch API calls with Testing Farm data retrieval
and dry-run display.  Each function receives an ``rp`` instance rather than
living as a method on the class, keeping the class focused on API concerns.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Any, List, Optional

from enge.utils.http_client import http_get
from enge.utils.opt_manager import parsed_opts

from enge.reportportal.utils import (
    parse_size_string,
    extract_latest_timestamp_from_xml,
    extract_artifacts_url,
    extract_artifacts_url_from_items,
    discover_artifacts_from_xml,
    map_artifacts_to_items,
    derive_status_from_items,
    has_in_progress_items,
    latest_end_time_from_items,
    ENRICHED_ATTRIBUTE_KEY,
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
# Internal helpers
# ===================================================================


def _apply_date_filters(
    launches: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Narrow a list of launches using ``--since`` / ``--until`` CLI dates.

    Compares each launch's ``startTime`` (epoch milliseconds) against
    the user-supplied boundaries.  Returns the original list unchanged
    when neither flag is set.
    """
    from enge.utils import parse_date_arg

    since_str = getattr(parsed_opts.cli_args, "since", None)
    until_str = getattr(parsed_opts.cli_args, "until", None)

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
        start_time = launch.get("startTime")
        if start_time is None:
            continue
        if isinstance(start_time, str):
            start_time = int(start_time)
        if since_ms and start_time < since_ms:
            continue
        if until_ms and start_time > until_ms:
            continue
        filtered.append(launch)

    if len(filtered) != len(launches):
        LOGGER.info(
            f"Date filter narrowed {len(launches)} launch(es) " f"to {len(filtered)}"
        )
    return filtered


def _extract_tf_uuid(artifacts_url: str) -> Optional[str]:
    """Extract a Testing Farm task UUID from an artifacts URL.

    Expects the UUID as the last meaningful path segment, e.g.
    ``https://artifacts.dev.testing-farm.io/<uuid>`` or
    ``https://…/artifacts/<uuid>/``.
    """
    import re

    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        artifacts_url,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _get_tf_task_info(task_uuid: str) -> Optional[Dict[str, Any]]:
    """Query the Testing Farm API for a task's state and result.

    Returns ``{"state": …, "overall": …}`` or ``None`` when TF
    credentials are unavailable or the query fails.
    """
    tf_cfg = parsed_opts.config.get("testing_farm", {})
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
                "overall": result.get("overall") if isinstance(result, dict) else None,
            }
    except Exception as exc:
        LOGGER.debug(f"Could not query TF task info for {task_uuid}: {exc}")
    return None


def _stamp_logs_attached(
    rp: ReportPortalLaunch,
    launch_id: Optional[int],
) -> None:
    """
    Stamp the ``logs_attached=true`` attribute on a launch.

    Fetches the launch's current attributes first so existing ones are
    preserved.  Skips if *launch_id* is ``None``.
    """
    if launch_id is None:
        LOGGER.warning("Cannot stamp logs_attached: launch_id is None")
        return
    # Fetch current launch data to preserve existing attributes
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


# ===================================================================
# Helper: extract TMT context from a Testing Farm task
# ===================================================================


def extract_tmt_context_from_task(
    task_result,
) -> Optional[Dict[str, Any]]:
    """
    Extract TMT context from a Testing Farm task result.

    Fetches full task data from the TF API and looks for TMT context
    in ``environments_requested[0].tmt.context``.
    """
    try:
        task_url = task_result.url
        LOGGER.debug(f"Fetching full task data from: {task_url}")

        response = http_get(
            task_url,
            headers={
                "Authorization": (f"Bearer {parsed_opts.testing_farm.get('api_key')}")
            },
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
        import traceback

        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return None


# ===================================================================
# Task-based operations
# ===================================================================


def finish_launch_from_task(rp: ReportPortalLaunch) -> int:
    """
    Finish RP launches based on Testing Farm task results.

    Returns:
        Exit code (0 success, non-zero error)
    """
    try:
        from enge.report.concurrent_parser import ConcurrentRequestParser
        from enge.report.__main__ import parse_tasks

        is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

        request_url_list, _tasks_source = parse_tasks()
        if not request_url_list:
            LOGGER.error("No task URLs found to process")
            return 1

        processed_count = 0

        for task_url in request_url_list:
            LOGGER.info(f"Processing task: {task_url}")

            with ConcurrentRequestParser() as parser:
                task_result = parser._fetch_task_info(task_url, process_state=False)
                if not task_result:
                    LOGGER.warning(f"Could not fetch task info for {task_url}")
                    continue

                task_uuid = task_result.request_uuid

                if task_result.request_state.upper() in (
                    "NEW",
                    "QUEUED",
                    "RUNNING",
                    "CANCELED",
                ):
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

                tmt_context = extract_tmt_context_from_task(task_result)
                if not tmt_context:
                    LOGGER.warning(f"No TMT context found for task {task_uuid}")
                    continue

                uniq_id = tmt_context.get("uniq_id")
                if not uniq_id:
                    LOGGER.warning(
                        f"No uniq_id found in TMT context for task {task_uuid}"
                    )
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
                        task_result.xunit_content
                    )
                if not end_time:
                    LOGGER.warning(
                        "No timestamp found in XML, using current time as fallback"
                    )
                    end_time = (
                        datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    )

                # Artifacts URL for launch description
                artifacts_url = (
                    task_result.results_xml_url.rsplit("/results.xml", 1)[0]
                    if task_result.results_xml_url
                    else None
                )
                description = f"\n{artifacts_url}" if artifacts_url else None

                attributes = []
                for key, value in tmt_context.items():
                    if value is not None:
                        attributes.append({"key": key, "value": str(value)})

                # Derive RP status from TF state + overall result
                status_mapping = {
                    "COMPLETE": "PASSED",
                    "ERROR": "FAILED",
                    "FAILED": "FAILED",
                }
                status = status_mapping.get(
                    task_result.request_state.upper(), "STOPPED"
                )
                if task_result.request_result_overall:
                    result_mapping = {
                        "passed": "PASSED",
                        "failed": "FAILED",
                        "error": "FAILED",
                    }
                    status = result_mapping.get(
                        task_result.request_result_overall.lower(), status
                    )

                if is_dryrun:
                    show_dryrun_finish_data(
                        api_base=rp.api_base,
                        token=rp.token,
                        task_uuid=task_uuid,
                        launch_uuid=launch_uuid,
                        end_time=end_time,
                        status=status,
                        description=description,
                        attributes=attributes,
                    )
                    processed_count += 1
                else:
                    success = rp.finish_launch(
                        launch_uuid=launch_uuid,
                        end_time=end_time,
                        status=status,
                        description=description,
                        attributes=attributes,
                    )
                    if success:
                        LOGGER.info(
                            f"Successfully finished launch for task {task_uuid}"
                        )
                        processed_count += 1
                    else:
                        LOGGER.error(f"Failed to finish launch for task {task_uuid}")

        if processed_count > 0:
            action = "shown" if is_dryrun else "finished"
            LOGGER.info(f"Successfully {action} {processed_count} launch(es)")
            return 0
        else:
            LOGGER.warning("No launches were finished")
            return 1

    except Exception as e:
        LOGGER.error(f"Error in finish launch logic: {e}")
        import traceback

        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return 1


def enrich_logs_from_task(rp: ReportPortalLaunch) -> int:
    """
    Enrich RP launch logs from Testing Farm artifact data.

    Returns:
        Exit code (0 success, non-zero error)
    """
    try:
        from enge.report.__main__ import parse_tasks
        from enge.report.concurrent_parser import ConcurrentRequestParser

        is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

        max_size_str = rp.config.get("enrich_max_file_size", "5 MB")
        max_file_size = parse_size_string(str(max_size_str))

        LOGGER.info("Starting log enrichment from Testing Farm results.xml")

        request_url_list, tasks_source = parse_tasks()
        if not request_url_list:
            LOGGER.error("No task URLs found to process")
            return 1

        enriched_count = 0

        for task_url in request_url_list:
            LOGGER.info(f"Processing task: {task_url}")

            with ConcurrentRequestParser() as parser:
                task_result = parser._fetch_task_info(task_url, process_state=False)
                if not task_result:
                    LOGGER.warning(f"Could not fetch task info for {task_url}")
                    continue

                if task_result.request_state.upper() in (
                    "NEW",
                    "QUEUED",
                    "RUNNING",
                ):
                    LOGGER.info(
                        f"Task {task_result.request_uuid} is in state "
                        f"'{task_result.request_state}', "
                        f"skipping enrichment"
                    )
                    continue

                task_uuid = task_result.request_uuid

                task_result = parser._fetch_xml_results(task_result)
                if not task_result.xunit_content:
                    LOGGER.warning(f"No results.xml content for task {task_uuid}")
                    continue

                artifacts = discover_artifacts_from_xml(task_result.xunit_content)
                if not artifacts:
                    LOGGER.info(
                        f"No artifact logs found in results.xml " f"for {task_uuid}"
                    )
                    continue

                tmt_context = extract_tmt_context_from_task(task_result)
                if not tmt_context:
                    LOGGER.warning(f"No TMT context found for task {task_uuid}")
                    continue

                uniq_id = tmt_context.get("uniq_id")
                if not uniq_id:
                    LOGGER.warning(
                        f"No uniq_id in TMT context " f"for task {task_uuid}"
                    )
                    continue

                launch_uuid = rp.find_launch_by_uniq_id(uniq_id, tmt_context)
                if not launch_uuid:
                    LOGGER.error(f"No RP launch found for uniq_id '{uniq_id}'")
                    continue

                LOGGER.info(f"Found RP launch {launch_uuid} " f"for task {task_uuid}")

                launch_id = rp.get_launch_id_from_uuid(launch_uuid)
                test_items: List[Dict[str, Any]] = []
                if launch_id is not None:
                    LOGGER.info(f"Resolved launch numeric ID: {launch_id}")
                    test_items = rp.get_launch_test_items(launch_id)
                else:
                    LOGGER.warning(
                        f"Could not resolve numeric launch ID for "
                        f"{launch_uuid} -- all logs will go to "
                        f"launch level"
                    )

                mapped = map_artifacts_to_items(artifacts, test_items)

                # Only attach logs to failed items
                mapped = filter_to_failed_items(mapped, test_items)
                if not mapped:
                    LOGGER.info(
                        f"No failed items in launch {launch_uuid} "
                        f"— skipping enrichment"
                    )
                    continue

                # Deduplicate: skip artifacts already in launch
                if launch_id is not None:
                    existing_logs = rp.get_launch_logs(launch_id)
                    LOGGER.info(
                        f"Dedup check: {len(existing_logs)} existing "
                        f"log(s) in launch {launch_uuid}, "
                        f"{len(mapped)} artifact(s) to consider"
                    )
                    existing_hdrs = extract_existing_log_headers(existing_logs)
                    mapped = filter_already_enriched(mapped, existing_hdrs)
                    if not mapped:
                        LOGGER.info(
                            f"All artifacts already uploaded "
                            f"for task {task_uuid} — skipping"
                        )
                        _stamp_logs_attached(rp, launch_id)
                        enriched_count += 1
                        continue

                if is_dryrun:
                    show_dryrun_enrichment(task_uuid, launch_uuid, mapped)
                    enriched_count += 1
                else:
                    count = rp.upload_logs_to_rp(
                        launch_uuid,
                        mapped,
                        max_file_size=max_file_size,
                    )
                    if count > 0:
                        enriched_count += 1
                        LOGGER.info(
                            f"Enriched launch {launch_uuid} "
                            f"with {count} log(s) "
                            f"from task {task_uuid}"
                        )
                        _stamp_logs_attached(rp, launch_id)
                    else:
                        LOGGER.warning(
                            f"No logs were uploaded " f"for task {task_uuid}"
                        )

        if enriched_count > 0:
            action = "shown" if is_dryrun else "enriched"
            LOGGER.info(
                f"Log enrichment complete: " f"{enriched_count} launch(es) {action}"
            )
            return 0
        else:
            LOGGER.warning("No launches were enriched")
            return 1

    except Exception as e:
        LOGGER.error(f"Error during log enrichment: {e}")
        import traceback as tb

        LOGGER.debug(f"Traceback: {tb.format_exc()}")
        return 1


def delete_logs_from_task(rp: ReportPortalLaunch) -> int:
    """
    Delete launch logs by resolving launches from TF tasks.

    Returns:
        Exit code (0 success, non-zero error)
    """
    try:
        from enge.report.__main__ import parse_tasks
        from enge.report.concurrent_parser import ConcurrentRequestParser

        is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)
        LOGGER.info("Starting log deletion for ReportPortal launches")

        request_url_list, tasks_source = parse_tasks()
        if not request_url_list:
            LOGGER.error("No task URLs found to process")
            return 1

        processed_count = 0
        for task_url in request_url_list:
            LOGGER.info(f"Processing task: {task_url}")
            with ConcurrentRequestParser() as parser:
                task_result = parser._fetch_task_info(task_url, process_state=False)
                if not task_result:
                    LOGGER.warning(f"Could not fetch task info for {task_url}")
                    continue

                task_uuid = task_result.request_uuid
                tmt_context = extract_tmt_context_from_task(task_result)
                if not tmt_context:
                    LOGGER.warning(f"No TMT context found for task {task_uuid}")
                    continue

                uniq_id = tmt_context.get("uniq_id")
                if not uniq_id:
                    LOGGER.warning(
                        f"No uniq_id in TMT context " f"for task {task_uuid}"
                    )
                    continue

                launch_uuid = rp.find_launch_by_uniq_id(uniq_id, tmt_context)
                if not launch_uuid:
                    LOGGER.error(f"No RP launch found for uniq_id '{uniq_id}'")
                    continue

                LOGGER.info(f"Found RP launch {launch_uuid} " f"for task {task_uuid}")

                if is_dryrun:
                    launch_id = rp.get_launch_id_from_uuid(launch_uuid)
                    logs = rp.get_launch_logs(launch_id) if launch_id else []
                    show_dryrun_delete_logs(task_uuid, launch_uuid, logs)
                    processed_count += 1
                else:
                    total, deleted = rp.delete_launch_logs(launch_uuid)
                    if total > 0:
                        LOGGER.info(
                            f"Deleted {deleted}/{total} log(s) from "
                            f"launch {launch_uuid} (task {task_uuid})"
                        )
                        processed_count += 1
                    else:
                        LOGGER.info(
                            f"No logs to delete for launch "
                            f"{launch_uuid} (task {task_uuid})"
                        )

        if processed_count > 0:
            action = "shown" if is_dryrun else "processed"
            LOGGER.info(
                f"Log deletion complete: " f"{processed_count} launch(es) {action}"
            )
            return 0
        else:
            LOGGER.warning("No launches had logs deleted")
            return 1

    except Exception as e:
        LOGGER.error(f"Error during log deletion: {e}")
        import traceback as tb

        LOGGER.debug(f"Traceback: {tb.format_exc()}")
        return 1


def test_connection_and_data(rp: ReportPortalLaunch) -> int:
    """Test RP connection and show sample data for debugging."""
    try:
        LOGGER.info("Testing ReportPortal connection...")

        LOGGER.info("Testing launch listing...")
        launches = rp.list_launches(size=5, page=0, status=None)

        if launches:
            LOGGER.info(f"Successfully retrieved {len(launches)} launches")
            for i, launch in enumerate(launches[:3]):
                LOGGER.info(
                    f"  Launch {i + 1}: "
                    f"{launch.get('name', 'No name')} "
                    f"(UUID: {launch.get('uuid') or launch.get('id', 'No UUID')})"
                )
        else:
            LOGGER.warning(
                "No launches found - " "this might be expected for a new project"
            )

        LOGGER.info("Testing report module integration...")
        from enge.report.__main__ import parse_tasks

        request_url_list, tasks_source = parse_tasks()

        if request_url_list:
            LOGGER.info(
                f"Found {len(request_url_list)} task URLs " f"from report module"
            )
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
        import traceback

        LOGGER.debug(f"Traceback: {traceback.format_exc()}")
        return 1


# ===================================================================
# --all-launches operations
# ===================================================================


def finish_all_in_progress_launches(rp: ReportPortalLaunch) -> int:
    """
    Finish all IN_PROGRESS launches in the project.

    For each launch, fetches test items once and derives both the
    overall status and the end time (latest ``endTime`` across items).
    Falls back to the current timestamp only when no item end times
    are available.
    """
    is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

    launches = _apply_date_filters(rp.get_all_launches("IN_PROGRESS"))
    if not launches:
        LOGGER.info("No IN_PROGRESS launches found")
        return 0

    fallback_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    processed = 0

    for launch in launches:
        launch_uuid = launch.get("uuid")
        launch_name = launch.get("name", "Unknown")
        numeric_id = launch.get("id")

        if not launch_uuid:
            LOGGER.warning(f"Launch missing UUID, skipping: {launch}")
            continue

        # Fetch test items once — derive status and end time from
        # item fields, artifacts URL from item descriptions
        items = rp.get_launch_test_items(numeric_id) if numeric_id else []

        if not items:
            LOGGER.info(
                f"Launch '{launch_name}' ({launch_uuid}) has no "
                f"test items yet, skipping"
            )
            continue

        # Skip launches that still have explicitly IN_PROGRESS items
        if has_in_progress_items(items):
            LOGGER.info(
                f"Launch '{launch_name}' ({launch_uuid}) still "
                f"has IN_PROGRESS items, skipping"
            )
            continue

        # Check TF task state — skip if still running, use state +
        # overall result to derive RP status when available.
        artifacts_url = extract_artifacts_url_from_items(items)
        tf_info: Optional[Dict[str, Any]] = None
        if artifacts_url:
            task_uuid_from_url = _extract_tf_uuid(artifacts_url)
            if task_uuid_from_url:
                tf_info = _get_tf_task_info(task_uuid_from_url)

        tf_state = (tf_info or {}).get("state", "")
        if tf_state and tf_state.upper() in ("NEW", "QUEUED", "RUNNING"):
            LOGGER.info(
                f"TF task for launch '{launch_name}' " f"is '{tf_state}', skipping"
            )
            continue

        # Derive RP status from TF info when available
        if tf_state and tf_state.upper() in ("ERROR", "FAILED"):
            status = "FAILED"
        elif tf_state and tf_state.upper() in ("CANCELED", "CANCELLED"):
            status = "STOPPED"
        elif tf_info and tf_info.get("overall"):
            overall = tf_info["overall"].lower()
            status = {"passed": "PASSED", "failed": "FAILED"}.get(
                overall, derive_status_from_items(items)
            )
        else:
            status = derive_status_from_items(items)

        end_time = latest_end_time_from_items(items)
        if not end_time:
            LOGGER.debug(
                f"No endTime in test items for launch "
                f"'{launch_name}', using current time"
            )
            end_time = fallback_time
        description = f"\n{artifacts_url}" if artifacts_url else None

        existing_attrs = launch.get("attributes")

        if is_dryrun:
            show_dryrun_finish_data(
                api_base=rp.api_base,
                token=rp.token,
                task_uuid="(all-launches)",
                launch_uuid=str(launch_uuid),
                end_time=end_time,
                status=status,
                description=description,
                attributes=existing_attrs,
            )
            processed += 1
        else:
            try:
                success = rp.finish_launch(
                    launch_uuid=str(launch_uuid),
                    end_time=end_time,
                    status=status,
                    description=description,
                    attributes=existing_attrs,
                )
                if success:
                    LOGGER.info(
                        f"Finished launch '{launch_name}' "
                        f"({launch_uuid}) with status {status}"
                    )
                    processed += 1
            except Exception as e:
                LOGGER.error(f"Failed to finish launch {launch_uuid}: {e}")

    action = "shown" if is_dryrun else "finished"
    if processed > 0:
        LOGGER.info(f"Successfully {action} {processed} launch(es)")
    else:
        LOGGER.info("No launches required finishing")
    return 0


def delete_logs_all_launches(rp: ReportPortalLaunch) -> int:
    """Delete logs from all IN_PROGRESS launches in the project."""
    is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

    launches = _apply_date_filters(rp.get_all_launches("IN_PROGRESS"))
    if not launches:
        LOGGER.warning("No IN_PROGRESS launches found")
        return 1

    processed = 0

    for launch in launches:
        launch_uuid = launch.get("uuid") or launch.get("id", "")
        launch_name = launch.get("name", "Unknown")
        numeric_id = launch.get("id")

        if not launch_uuid or numeric_id is None:
            LOGGER.warning(f"Launch missing UUID/ID, skipping: {launch}")
            continue

        if is_dryrun:
            logs = rp.get_launch_logs(numeric_id)
            show_dryrun_delete_logs(
                task_uuid="(all-launches)",
                launch_uuid=str(launch_uuid),
                logs=logs,
            )
            processed += 1
        else:
            total, deleted = rp.delete_launch_logs(str(launch_uuid))
            if total > 0:
                LOGGER.info(
                    f"Deleted {deleted}/{total} log(s) from launch "
                    f"'{launch_name}' ({launch_uuid})"
                )
            else:
                LOGGER.info(
                    f"No logs to delete for launch " f"'{launch_name}' ({launch_uuid})"
                )
            processed += 1

    action = "shown" if is_dryrun else "processed"
    if processed > 0:
        LOGGER.info(f"Log deletion complete: {processed} launch(es) {action}")
        return 0
    else:
        LOGGER.warning("No launches were processed")
        return 1


# ===================================================================
# --all-launches enrichment
# ===================================================================


def enrich_all_launches(
    rp: ReportPortalLaunch,
    status_filter: Optional[str] = None,
) -> int:
    """
    Enrich logs for launches without TF task input.

    Reads the artifacts URL from RP test-item descriptions, fetches
    ``results.xml`` from that URL, and uploads discovered artifact logs.

    Two dedup layers prevent duplicate uploads:

    1. **Coarse** — launches with the ``enge_enriched`` attribute are
       skipped entirely (no API calls).
    2. **Fine** — the message-prefix check filters out individual
       artifacts already present in the launch logs.

    After a successful enrichment the ``enge_enriched`` attribute is
    stamped on the launch so subsequent runs short-circuit.

    Args:
        rp: ReportPortalLaunch instance
        status_filter: RP status to filter (``"IN_PROGRESS"`` or
            ``None`` for all launches).
    """
    is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

    max_size_str = rp.config.get("enrich_max_file_size", "5 MB")
    max_file_size = parse_size_string(str(max_size_str))

    launches = _apply_date_filters(rp.get_all_launches(status=status_filter))

    if not launches:
        LOGGER.info("No launches found for enrichment")
        return 0

    enriched_count = 0

    for launch in launches:
        launch_uuid = launch.get("uuid") or launch.get("id", "")
        launch_name = launch.get("name", "Unknown")
        numeric_id = launch.get("id")

        if not launch_uuid or numeric_id is None:
            continue

        # Layer 1: coarse dedup — skip already-enriched launches
        if is_launch_enriched(launch):
            LOGGER.debug(
                f"Launch '{launch_name}' ({launch_uuid}) already enriched, skipping"
            )
            continue

        # Get test items to extract artifacts URL
        items = rp.get_launch_test_items(numeric_id)
        if not items:
            LOGGER.debug(f"No test items in launch '{launch_name}', skipping")
            continue

        artifacts_url = extract_artifacts_url_from_items(items)
        if not artifacts_url:
            LOGGER.debug(f"No artifacts URL in launch '{launch_name}', skipping")
            continue

        # Check TF task state — skip if still running (no logs yet)
        # or if complete with all passed (nothing to enrich)
        task_uuid_from_url = _extract_tf_uuid(artifacts_url)
        if task_uuid_from_url:
            tf_info = _get_tf_task_info(task_uuid_from_url)
            if tf_info:
                tf_state = (tf_info.get("state") or "").upper()
                if tf_state in ("NEW", "QUEUED", "RUNNING"):
                    LOGGER.info(
                        f"TF task {task_uuid_from_url} for launch "
                        f"'{launch_name}' is '{tf_state}', "
                        f"skipping enrichment"
                    )
                    continue
                if tf_state == "COMPLETE" and tf_info.get("overall") == "passed":
                    LOGGER.info(
                        f"TF task {task_uuid_from_url} for launch "
                        f"'{launch_name}' PASSED — nothing to enrich"
                    )
                    continue

        # Fetch results.xml from the artifacts endpoint
        results_xml_url = artifacts_url.rstrip("/") + "/results.xml"
        LOGGER.info(
            f"Fetching results.xml for launch '{launch_name}' "
            f"from {results_xml_url}"
        )
        try:
            resp = http_get(results_xml_url, timeout=30)
            if resp.status_code != 200:
                LOGGER.warning(
                    f"Could not fetch results.xml for launch "
                    f"'{launch_name}': HTTP {resp.status_code}"
                )
                continue
            xml_content = resp.text
        except Exception as e:
            LOGGER.warning(
                f"Error fetching results.xml for launch " f"'{launch_name}': {e}"
            )
            continue

        # Discover artifacts from the XML
        artifacts = discover_artifacts_from_xml(xml_content)
        if not artifacts:
            LOGGER.info(
                f"No artifacts found in results.xml for launch " f"'{launch_name}'"
            )
            continue

        # Map artifacts to test items
        mapped = map_artifacts_to_items(artifacts, items)

        # Only attach logs to failed items
        mapped = filter_to_failed_items(mapped, items)
        if not mapped:
            LOGGER.info(
                f"No failed items in launch '{launch_name}' " f"— skipping enrichment"
            )
            continue

        # Layer 2: fine dedup — filter out already-uploaded artifacts
        existing_logs = rp.get_launch_logs(numeric_id)
        LOGGER.info(
            f"Dedup check: {len(existing_logs)} existing log(s) "
            f"in launch '{launch_name}', "
            f"{len(mapped)} artifact(s) to consider"
        )
        existing_hdrs = extract_existing_log_headers(existing_logs)
        mapped = filter_already_enriched(mapped, existing_hdrs)
        if not mapped:
            LOGGER.info(
                f"All artifacts already uploaded for launch "
                f"'{launch_name}' — stamping attribute"
            )
            if not is_dryrun:
                rp.update_launch(
                    numeric_id,
                    attributes=build_enriched_attributes(launch),
                )
            enriched_count += 1
            continue

        if is_dryrun:
            show_dryrun_enrichment(
                task_uuid="(all-launches)",
                launch_uuid=str(launch_uuid),
                mapped=mapped,
            )
            enriched_count += 1
        else:
            count = rp.upload_logs_to_rp(
                str(launch_uuid),
                mapped,
                max_file_size=max_file_size,
            )
            if count > 0:
                LOGGER.info(
                    f"Enriched launch '{launch_name}' "
                    f"({launch_uuid}) with {count} log(s)"
                )
                # Stamp enge_enriched attribute
                rp.update_launch(
                    numeric_id,
                    attributes=build_enriched_attributes(launch),
                )
                enriched_count += 1
            else:
                LOGGER.warning(f"No logs uploaded for launch '{launch_name}'")

    action = "shown" if is_dryrun else "enriched"
    if enriched_count > 0:
        LOGGER.info(f"Enrichment complete: {enriched_count} launch(es) {action}")
    else:
        LOGGER.info("No launches required enrichment")
    return 0


# ===================================================================
# --delete-stale
# ===================================================================


_STALE_STATUSES = ("STOPPED", "INTERRUPTED")


def delete_stale_launches(rp: ReportPortalLaunch) -> int:
    """
    Delete stale launches — stopped/interrupted launches with no test items.

    Fetches all STOPPED and INTERRUPTED launches, checks each for test
    items, and deletes those that have none.
    """
    is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

    launches: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for status in _STALE_STATUSES:
        for launch in rp.get_all_launches(status):
            lid = launch.get("id")
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
                launches.append(launch)
    launches = _apply_date_filters(launches)

    if not launches:
        LOGGER.info("No STOPPED/INTERRUPTED launches found")
        return 0

    stale: List[Dict[str, Any]] = []
    for launch in launches:
        numeric_id = launch.get("id")
        if numeric_id is None:
            continue
        items = rp.get_launch_test_items(numeric_id)
        if not items:
            stale.append(launch)

    if not stale:
        LOGGER.info(
            f"No stale launches found among "
            f"{len(launches)} STOPPED/INTERRUPTED launch(es)"
        )
        return 0

    if is_dryrun:
        show_dryrun_delete_stale(stale)
        return 0

    deleted = 0
    for launch in stale:
        launch_id = launch.get("id")
        launch_name = launch.get("name", "Unknown")
        launch_uuid = launch.get("uuid", "")
        if rp.delete_launch(launch_id):
            LOGGER.info(f"Deleted stale launch '{launch_name}' ({launch_uuid})")
            deleted += 1
        else:
            LOGGER.error(f"Failed to delete launch '{launch_name}' ({launch_uuid})")

    LOGGER.info(f"Deleted {deleted}/{len(stale)} stale launch(es)")
    return 0 if deleted > 0 else 1
