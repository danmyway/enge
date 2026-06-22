#!/usr/bin/env python3
"""
ReportPortal launch management for enge.

This module handles ReportPortal API integration for creating and managing
test launches through the ReportPortal API.

Standalone utilities (timestamp parsing, XML helpers, dry-run display)
live in :mod:`enge.reportportal.utils`.  High-level orchestration
(finish-from-task, enrich, delete, all-launches) lives in
:mod:`enge.reportportal.operations`.
"""

import logging
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from enge.utils.http_client import http_get, http_post, http_put, http_delete
from requests.exceptions import RequestException

from enge.utils.errors import ConfigurationError, NetworkError, EngeError

from enge.reportportal.utils import (
    DEFAULT_ENRICH_MAX_FILE_SIZE,
    ArtifactFile,
    derive_status_from_items,
    get_artifact_log_level,
    should_skip_artifact,
)
from enge.reportportal.operations import (
    # New unified API
    op_delete_stale,
    op_check,
    # Legacy wrappers (used by parity tests and combined flow)
    finish_launch_from_task,
    enrich_logs_from_task,
    enrich_all_launches,
    delete_logs_from_task,
    finish_all_in_progress_launches,
    delete_logs_all_launches,
)

LOGGER = logging.getLogger(__name__)


class ReportPortalLaunch:
    """
    Handle ReportPortal launch creation and management via API.
    """

    def __init__(self, ctx):
        """Initialize ReportPortal launch manager."""
        self.ctx = ctx
        self.config = ctx.reportportal
        self.url = self.config.get("url", "").rstrip("/")
        self.token = self.config.get("token", "")
        self.project = self.config.get("project", "")

        if not self.url:
            raise ConfigurationError("ReportPortal URL is required but not configured")
        if not self.token:
            raise ConfigurationError(
                "ReportPortal API token is required but not configured"
            )
        if not self.project:
            raise ConfigurationError(
                "ReportPortal project is required but not configured"
            )

        self.api_base = f"{self.url}/api/v1/{self.project}"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ===================================================================
    # Launch CRUD
    # ===================================================================

    def generate_launch_name(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate launch name in the format:
        ``(EVENT_NAME|SET_NAME)~datetime_stamp~tier~architecture``
        """
        timestamp = datetime.now().strftime("%Y-%m-%d")

        if not context:
            return f"ENGE_Launch~{timestamp}~unknown"

        name_component = context.get("event") or context.get("set_name") or "unknown"
        tier = context.get("tier") or "unknown"
        architecture = context.get("architecture") or "unknown"

        return f"{name_component.upper()}~{timestamp}~{tier}~{architecture}"

    def generate_launch_payload(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        tmt_context: Optional[Dict[str, Any]] = None,
        extra_tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate the launch payload for the ReportPortal API.

        Args:
            name: Launch name (auto-generated from *context* when absent).
            description: Launch description.
            context: Context for name generation (event, tier, arch, ...).
            tmt_context: TMT context dict — stored as launch attributes.
            extra_tags: Additional tags to append (duplicates silently skipped).
        """
        if not name:
            name = self.generate_launch_name(context)

        launch_data: Dict[str, Any] = {
            "name": name,
            "description": description
            or f"Launch created by enge on {datetime.now().isoformat()}",
            "mode": "DEFAULT",
            "startTime": int(datetime.now().timestamp() * 1000),
            "tags": ["enge", "automated"],
        }

        if extra_tags:
            seen = set(launch_data["tags"])
            for tag in extra_tags:
                if tag not in seen:
                    launch_data["tags"].append(tag)
                    seen.add(tag)

        if tmt_context:
            attributes = []
            for key, value in tmt_context.items():
                if value is not None:
                    attributes.append({"key": key, "value": str(value)})

            if (
                context
                and context.get("architecture")
                and not any(a.get("key") == "arch" for a in attributes)
            ):
                attributes.append({"key": "arch", "value": context["architecture"]})

            if attributes:
                launch_data["attributes"] = attributes

        return launch_data

    def create_launch(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        tmt_context: Optional[Dict[str, Any]] = None,
        extra_tags: Optional[List[str]] = None,
    ) -> str:
        """
        Create a new launch in ReportPortal.

        Returns:
            str: UUID of the created launch
        """
        launch_data = self.generate_launch_payload(
            name=name,
            description=description,
            context=context,
            tmt_context=tmt_context,
            extra_tags=extra_tags,
        )

        try:
            LOGGER.info(f"Creating ReportPortal launch: {launch_data['name']}")
            response = http_post(
                f"{self.api_base}/launch",
                headers=self.headers,
                json=launch_data,
                timeout=30,
            )

            if response.status_code not in [200, 201]:
                raise RequestException(
                    f"Failed to create launch: "
                    f"{response.status_code} - {response.text}"
                )

            response_data = response.json()
            launch_uuid = response_data.get("id")

            if not launch_uuid:
                raise ValueError("Launch UUID not found in response")

            LOGGER.info(f"ReportPortal launch created successfully: " f"{launch_uuid}")
            LOGGER.info(f"  Launch name: {launch_data['name']}")
            LOGGER.info(
                f"  Launch URL: {self.url}/ui/#{self.project}"
                f"/launches/all/{launch_uuid}"
            )

            return launch_uuid

        except RequestException as e:
            LOGGER.error("Failed to create ReportPortal launch.")
            raise NetworkError("Failed to create ReportPortal launch") from e
        except Exception as e:
            LOGGER.error("Unexpected error creating launch.")
            raise EngeError("Unexpected error creating ReportPortal launch") from e

    def list_launches(
        self,
        size: int = 50,
        page: int = 0,
        status: Optional[str] = "IN_PROGRESS",
        attribute_filters: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        List launches in the project.

        Args:
            size: Number of launches per page (default: 50)
            page: Page number (0-based, default: 0)
            status: Status filter (default: ``"IN_PROGRESS"``)
            attribute_filters: Attribute key/value pairs for server-side filtering
        """
        try:
            params: list = [
                ("page.size", size),
                ("page.page", page),
            ]

            if status:
                params.append(("filter.eq.status", status))

            if attribute_filters:
                for key, value in attribute_filters.items():
                    if key != "uniq_id":
                        params.append(("filter.has.attributeKey", key))
                        params.append(("filter.has.attributeValue", value))
                        LOGGER.debug(f"Adding attribute filter: {key}={value}")

            response = http_get(
                f"{self.api_base}/launch",
                headers=self.headers,
                params=params,
                timeout=30,
            )

            if response.status_code != 200:
                raise RequestException(
                    f"Failed to list launches: "
                    f"{response.status_code} - {response.text}"
                )

            response_data = response.json()
            launches = response_data.get("content", [])

            LOGGER.debug(f"Retrieved {len(launches)} launches from page {page}")
            return launches

        except RequestException as e:
            LOGGER.error("Failed to list ReportPortal launches")
            raise NetworkError("Failed to list ReportPortal launches") from e
        except Exception as e:
            LOGGER.error("Unexpected error listing launches.")
            raise EngeError("Unexpected error listing ReportPortal launches") from e

    def find_launch_by_uniq_id(
        self,
        uniq_id: str,
        tmt_context: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """
        Find a launch UUID by matching ``uniq_id`` (UUID prefix).

        Uses TMT context for server-side attribute filtering when
        available, then verifies client-side via UUID prefix matching.
        """
        try:
            LOGGER.info(f"Searching for launch with uniq_id: {uniq_id}")

            attribute_filters = None
            if tmt_context:
                attribute_filters = {
                    k: str(v)
                    for k, v in tmt_context.items()
                    if k != "uniq_id" and v is not None
                }
                if attribute_filters:
                    LOGGER.info(
                        f"Using TMT context filters: "
                        f"{list(attribute_filters.keys())}"
                    )

            total_checked = 0
            for status in ["IN_PROGRESS", None]:
                if status:
                    LOGGER.debug(f"Searching launches with status: {status}")
                else:
                    LOGGER.debug("Searching all launches (no status filter)")

                status_checked = 0
                for page in range(0, 10):
                    launches = self.list_launches(
                        size=50,
                        page=page,
                        status=status,
                        attribute_filters=attribute_filters,
                    )

                    if not launches:
                        break

                    status_checked += len(launches)
                    total_checked += len(launches)

                    for launch in launches:
                        launch_uuid = launch.get("uuid") or launch.get("id") or ""
                        if not launch_uuid:
                            continue

                        if launch_uuid.startswith(uniq_id):
                            LOGGER.info(
                                f"Found launch matching uniq_id "
                                f"'{uniq_id}': {launch_uuid}"
                            )

                            if attribute_filters:
                                launch_attrs = {
                                    attr.get("key"): attr.get("value")
                                    for attr in launch.get("attributes", [])
                                    if attr.get("key") and attr.get("value")
                                }
                                matches = all(
                                    launch_attrs.get(k) == v
                                    for k, v in attribute_filters.items()
                                )
                                if not matches:
                                    LOGGER.warning(
                                        f"Attributes mismatch for "
                                        f"launch {launch_uuid}, "
                                        f"continuing search..."
                                    )
                                    continue

                            return launch_uuid

                if status_checked > 0:
                    continue

            LOGGER.warning(
                f"No launch found matching uniq_id '{uniq_id}' "
                f"in {total_checked} launches checked"
            )
            return None

        except Exception as e:
            LOGGER.error(f"Error searching for launch by uniq_id: {e}")
            return None

    def finish_launch(
        self,
        launch_uuid: str,
        end_time: str,
        status: str,
        description: Optional[str] = None,
        attributes: Optional[List[Dict[str, str]]] = None,
    ) -> bool:
        """
        Finish a ReportPortal launch.
        """
        finish_data: Dict[str, Any] = {
            "endTime": end_time,
            "status": status,
        }

        if description is not None:
            finish_data["description"] = description
        if attributes:
            finish_data["attributes"] = attributes

        try:
            LOGGER.info(f"Finishing ReportPortal launch: {launch_uuid}")
            LOGGER.debug(f"Finish data: {finish_data}")

            response = http_put(
                f"{self.api_base}/launch/{launch_uuid}/finish",
                headers=self.headers,
                json=finish_data,
                timeout=30,
            )

            if response.status_code == 200:
                LOGGER.info(f"Launch {launch_uuid} finished with status '{status}'")
                return True
            else:
                LOGGER.error(f"Failed to finish launch: HTTP {response.status_code}")
                LOGGER.error(f"Response: {response.text}")
                return False
        except RequestException as e:
            LOGGER.error(f"Network error finishing launch: {e}")
            return False

    def store_launch_uuid(self, launch_uuid: str) -> None:
        """Store the launch UUID for later use (currently just logs it)."""
        LOGGER.info(f"Launch UUID stored: {launch_uuid}")

    def update_launch(
        self,
        launch_id: int,
        attributes: Optional[List[Dict[str, str]]] = None,
        description: Optional[str] = None,
    ) -> bool:
        """
        Update metadata on an existing launch via the RP update endpoint.

        Uses ``PUT /api/v1/{project}/launch/{launchId}/update``.
        Works on both IN_PROGRESS and FINISHED launches.
        """
        update_data: Dict[str, Any] = {}
        if attributes is not None:
            update_data["attributes"] = attributes
        if description is not None:
            update_data["description"] = description

        if not update_data:
            return True

        try:
            response = http_put(
                f"{self.api_base}/launch/{launch_id}/update",
                headers=self.headers,
                json=update_data,
                timeout=30,
            )
            if response.status_code == 200:
                LOGGER.debug(f"Updated launch {launch_id} attributes")
                return True
            else:
                LOGGER.warning(
                    f"Failed to update launch {launch_id}: "
                    f"HTTP {response.status_code}"
                )
                return False
        except RequestException as e:
            LOGGER.warning(f"Error updating launch {launch_id}: {e}")
            return False

    # ===================================================================
    # Test-item & launch-ID helpers
    # ===================================================================

    def get_launch_test_items(self, launch_id: int) -> List[Dict[str, Any]]:
        """Fetch test items belonging to a launch (paginated)."""
        items: List[Dict[str, Any]] = []
        page = 1
        try:
            while True:
                response = http_get(
                    f"{self.api_base}/item",
                    headers=self.headers,
                    params={
                        "filter.eq.launchId": launch_id,
                        "page.size": 300,
                        "page.page": page,
                    },
                    timeout=30,
                )
                if response.status_code != 200:
                    LOGGER.warning(
                        f"Failed to fetch test items: HTTP {response.status_code}"
                    )
                    break

                data = response.json()
                content = data.get("content", [])
                if not content:
                    break

                items.extend(content)
                total_pages = data.get("page", {}).get("totalPages", 1)
                if page >= total_pages:
                    break
                page += 1

            LOGGER.debug(f"Found {len(items)} test item(s) in launch {launch_id}")
            if getattr(self.ctx.cli_args, "delete_stale", False) and len(items) == 0:
                LOGGER.info(f"Found stale launch {launch_id} with no test items")
            if items:
                for item in items[:10]:
                    LOGGER.debug(
                        f"  RP item: name={item.get('name', '?')!r}  "
                        f"uuid={item.get('uuid', '?')[:12]}  "
                        f"type={item.get('type', '?')}"
                    )
                if len(items) > 10:
                    LOGGER.debug(f"  ... and {len(items) - 10} more")
        except Exception as e:
            LOGGER.error(f"Error fetching test items: {e}")

        return items

    def get_launch_id_from_uuid(self, launch_uuid: str) -> Optional[int]:
        """Resolve the numeric launch ID from a launch UUID."""
        try:
            response = http_get(
                f"{self.api_base}/launch",
                headers=self.headers,
                params={
                    "filter.eq.uuid": launch_uuid,
                    "page.size": 1,
                },
                timeout=30,
            )
            if response.status_code == 200:
                data = response.json()
                content = data.get("content", [])
                if content:
                    numeric_id = content[0].get("id")
                    LOGGER.debug(
                        f"Resolved launch UUID {launch_uuid} "
                        f"-> numeric ID {numeric_id}"
                    )
                    return numeric_id

            LOGGER.debug("UUID filter not supported, falling back to page scan")
            for status in ["IN_PROGRESS", None]:
                launches = self.list_launches(size=50, page=0, status=status)
                for launch in launches:
                    if str(launch.get("uuid", "")) == launch_uuid:
                        numeric_id = launch.get("id")
                        LOGGER.debug(
                            f"Found launch UUID {launch_uuid} "
                            f"-> numeric ID {numeric_id}"
                        )
                        return numeric_id
        except Exception as e:
            LOGGER.warning(f"Error resolving launch ID for UUID {launch_uuid}: {e}")
        LOGGER.warning(f"Could not resolve numeric launch ID for UUID {launch_uuid}")
        return None

    def get_launch_by_id(
        self,
        launch_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Fetch the full launch dict by its numeric ID."""
        try:
            response = http_get(
                f"{self.api_base}/launch/{launch_id}",
                headers=self.headers,
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            LOGGER.warning(f"Error fetching launch {launch_id}: {e}")
        return None

    # ===================================================================
    # Log upload
    # ===================================================================

    def upload_logs_to_rp(
        self,
        launch_uuid: str,
        mapped_artifacts: List[Tuple[ArtifactFile, Optional[str]]],
        max_file_size: int = DEFAULT_ENRICH_MAX_FILE_SIZE,
        max_workers: int = 5,
    ) -> int:
        """
        Download artifacts and upload them as log entries to ReportPortal.

        Callers are expected to handle deduplication before invoking this
        method (see :func:`~enge.reportportal.utils.filter_already_enriched`).

        Returns:
            Number of logs successfully uploaded
        """
        uploaded = 0
        batch: List[Dict[str, Any]] = []
        batch_size_limit = 20

        def _flush_batch():
            nonlocal uploaded
            if not batch:
                return
            try:
                multipart_headers = {
                    "Authorization": f"Bearer {self.token}",
                }
                files = [
                    (
                        "json_request_part",
                        (None, json.dumps(batch[:]), "application/json"),
                    )
                ]
                response = http_post(
                    f"{self.api_base}/log",
                    headers=multipart_headers,
                    files=files,
                    timeout=60,
                )
                if response.status_code in (200, 201):
                    uploaded += len(batch)
                    LOGGER.debug(f"Uploaded batch of {len(batch)} log entries")
                else:
                    LOGGER.warning(
                        f"Log batch upload returned "
                        f"{response.status_code}: "
                        f"{response.text[:200]}"
                    )
            except RequestException as e:
                LOGGER.warning(f"Failed to upload log batch: {e}")
            batch.clear()

        def _download_one(
            artifact: ArtifactFile,
        ) -> Optional[Tuple[ArtifactFile, str]]:
            try:
                resp = http_get(artifact.url, timeout=30)
                if resp.status_code != 200:
                    LOGGER.debug(
                        f"Artifact download returned "
                        f"{resp.status_code}: {artifact.url}"
                    )
                    return None

                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > max_file_size:
                    LOGGER.debug(
                        f"Skipping oversized artifact "
                        f"({content_length} bytes): "
                        f"{artifact.relative_path}"
                    )
                    return None

                content = resp.text
                if len(content.encode("utf-8", errors="replace")) > max_file_size:
                    LOGGER.debug(
                        f"Skipping oversized artifact content: "
                        f"{artifact.relative_path}"
                    )
                    return None

                return (artifact, content)
            except Exception as e:
                LOGGER.debug(f"Failed to download {artifact.url}: {e}")
                return None

        LOGGER.info(f"Downloading {len(mapped_artifacts)} artifact(s) for upload")
        download_results: List[Tuple[ArtifactFile, str, Optional[str]]] = []

        artifact_to_item = {id(a): item_uuid for a, item_uuid in mapped_artifacts}
        artifacts_only = [a for a, _ in mapped_artifacts]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_artifact = {
                executor.submit(_download_one, art): art for art in artifacts_only
            }
            for future in as_completed(future_to_artifact):
                result = future.result()
                if result:
                    art, content = result
                    item_uuid = artifact_to_item.get(id(art))
                    download_results.append((art, content, item_uuid))

        LOGGER.info(
            f"Downloaded {len(download_results)} artifact(s), " f"preparing log entries"
        )

        now_ms = str(int(datetime.now().timestamp() * 1000))

        skipped_names: List[str] = []
        for artifact, content, item_uuid in download_results:
            if should_skip_artifact(artifact.name):
                skipped_names.append(artifact.name)
                continue

            log_entry: Dict[str, Any] = {
                "launchUuid": launch_uuid,
                "time": now_ms,
                "level": get_artifact_log_level(artifact.name),
                "message": f"### `{artifact.name}`\n{content}",
            }
            if item_uuid:
                log_entry["itemUuid"] = item_uuid

            batch.append(log_entry)
            if len(batch) >= batch_size_limit:
                _flush_batch()

        if skipped_names:
            LOGGER.info(
                f"Skipped {len(skipped_names)} artifact(s) "
                f"per skip list: {', '.join(skipped_names[:10])}"
            )

        _flush_batch()

        LOGGER.info(
            f"Successfully uploaded {uploaded} log "
            f"entr{'y' if uploaded == 1 else 'ies'} to ReportPortal"
        )
        return uploaded

    # ===================================================================
    # Log deletion API methods
    # ===================================================================

    def get_launch_logs(
        self, launch_id: int, page_size: int = 300
    ) -> List[Dict[str, Any]]:
        """
        Fetch all log entries belonging to a launch.

        Collects logs at both the launch level (``filter.eq.launch``)
        and the item level (per test-item), since enrichment logs are
        attached to individual test items via ``itemUuid``.
        """
        seen_ids: set = set()
        logs: List[Dict[str, Any]] = []

        def _collect(params: dict, label: str) -> None:
            page = 1
            while True:
                paged_params = dict(params)
                paged_params["page.size"] = page_size
                paged_params["page.page"] = page
                try:
                    response = http_get(
                        f"{self.api_base}/log",
                        headers=self.headers,
                        params=paged_params,
                        timeout=30,
                    )
                except Exception as e:
                    LOGGER.warning(f"Error fetching logs ({label}): {e}")
                    break

                if response.status_code != 200:
                    LOGGER.warning(
                        f"Failed to fetch logs ({label}): "
                        f"HTTP {response.status_code}"
                    )
                    break

                data = response.json()
                content = data.get("content", [])
                if not content:
                    break

                for entry in content:
                    log_id = entry.get("id")
                    if log_id and log_id not in seen_ids:
                        seen_ids.add(log_id)
                        logs.append(entry)

                total_pages = data.get("page", {}).get("totalPages", 1)
                if page >= total_pages:
                    break
                page += 1

        try:
            # 1. Launch-level logs
            _collect(
                {"filter.eq.launch": launch_id},
                f"launch-level {launch_id}",
            )
            launch_level = len(logs)

            # 2. Item-level logs — fetch from each test item
            items = self.get_launch_test_items(launch_id)
            for item in items:
                item_id = item.get("id")
                if item_id:
                    _collect(
                        {"filter.eq.item": item_id},
                        f"item {item_id}",
                    )

            LOGGER.info(
                f"Found {len(logs)} log "
                f"entr{'y' if len(logs) == 1 else 'ies'} "
                f"in launch {launch_id} "
                f"({launch_level} launch-level, "
                f"{len(logs) - launch_level} item-level)"
            )
        except Exception as e:
            LOGGER.error(f"Error fetching launch logs: {e}")

        return logs

    def delete_logs(self, log_ids: List[int], batch_size: int = 20) -> int:
        """
        Delete log entries by their IDs (bulk with single-ID fallback).
        """
        if not log_ids:
            return 0

        deleted = 0

        for i in range(0, len(log_ids), batch_size):
            batch = log_ids[i : i + batch_size]
            ids_param = ",".join(str(lid) for lid in batch)

            try:
                response = http_delete(
                    f"{self.api_base}/log",
                    headers=self.headers,
                    params={"ids": ids_param},
                    timeout=30,
                )

                if response.status_code in (200, 204):
                    deleted += len(batch)
                    LOGGER.debug(f"Deleted batch of {len(batch)} log entries")
                else:
                    LOGGER.debug(
                        f"Bulk delete returned "
                        f"{response.status_code}, "
                        f"falling back to individual deletes"
                    )
                    for lid in batch:
                        try:
                            resp = http_delete(
                                f"{self.api_base}/log/{lid}",
                                headers=self.headers,
                                timeout=30,
                            )
                            if resp.status_code in (200, 204):
                                deleted += 1
                            else:
                                LOGGER.debug(
                                    f"Failed to delete log {lid}: "
                                    f"HTTP {resp.status_code}"
                                )
                        except RequestException as e:
                            LOGGER.debug(f"Failed to delete log {lid}: {e}")

            except RequestException as e:
                LOGGER.warning(f"Failed to delete log batch: {e}")

        return deleted

    def delete_launch_logs(self, launch_uuid: str) -> Tuple[int, int]:
        """
        Delete all log entries for a given launch.

        Returns:
            Tuple of (total_logs_found, successfully_deleted_count)
        """
        launch_id = self.get_launch_id_from_uuid(launch_uuid)
        if launch_id is None:
            LOGGER.error(f"Could not resolve numeric ID for launch " f"{launch_uuid}")
            return 0, 0

        logs = self.get_launch_logs(launch_id)
        if not logs:
            LOGGER.info(f"No logs found for launch {launch_uuid}")
            return 0, 0

        log_ids = [log["id"] for log in logs if log.get("id") is not None]
        if not log_ids:
            LOGGER.warning("Logs found but none had valid IDs")
            return len(logs), 0

        LOGGER.info(
            f"Deleting {len(log_ids)} log "
            f"entr{'y' if len(log_ids) == 1 else 'ies'} "
            f"from launch {launch_uuid}"
        )
        deleted = self.delete_logs(log_ids)
        return len(log_ids), deleted

    # ===================================================================
    # All-launches query helpers
    # ===================================================================

    def get_all_launches(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all launches in the project, optionally filtered by status."""
        all_launches: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for page in range(1, 51):
            launches = self.list_launches(size=50, page=page, status=status)
            if not launches:
                break
            for launch in launches:
                lid = launch.get("id")
                if lid and lid not in seen_ids:
                    seen_ids.add(lid)
                    all_launches.append(launch)
        status_label = status or "any"
        LOGGER.info(
            f"Found {len(all_launches)} launch(es) "
            f"(status={status_label}) in project '{self.project}'"
        )
        return all_launches

    def delete_launch(self, launch_id: int) -> bool:
        """Delete a launch by its numeric ID."""
        try:
            response = http_delete(
                f"{self.api_base}/launch/{launch_id}",
                headers=self.headers,
                timeout=30,
            )
            if response.status_code in (200, 204):
                LOGGER.info(f"Deleted launch {launch_id}")
                return True
            else:
                LOGGER.error(
                    f"Failed to delete launch {launch_id}: "
                    f"HTTP {response.status_code}"
                )
                return False
        except RequestException as e:
            LOGGER.error(f"Network error deleting launch {launch_id}: {e}")
            return False

    def derive_launch_status(self, launch_id: int) -> str:
        """Derive an overall status for a launch from its test items."""

        items = self.get_launch_test_items(launch_id)
        return derive_status_from_items(items)


# ===================================================================
# Entry point
# ===================================================================


def _resolve_subcommand(ctx):
    """Map CLI args to (subcommand, wants_enrich, wants_all, dryrun).

    Handles both new subcommand syntax and deprecated flag-verb aliases.
    """
    rp_sub = getattr(ctx.cli_args, "rp_subcommand", None)
    dryrun = getattr(ctx.cli_args, "dryrun", False)
    wants_all = getattr(ctx.cli_args, "all_launches", False)

    if rp_sub:
        wants_enrich = getattr(ctx.cli_args, "enrich", False)
        return rp_sub, wants_enrich, wants_all, dryrun

    # Deprecated flag-verb mapping
    wants_finish = getattr(ctx.cli_args, "finish", False)
    wants_enrich_logs = getattr(ctx.cli_args, "enrich_logs", False)
    wants_test = getattr(ctx.cli_args, "test", False)
    wants_delete_logs = getattr(ctx.cli_args, "delete_logs", False)
    wants_delete_stale = getattr(ctx.cli_args, "delete_stale", False)

    if wants_delete_stale:
        LOGGER.warning(
            "Deprecated: use 'enge reportportal delete-stale' instead of "
            "'--delete-stale'. "
            "Old spellings will be removed in a future release."
        )
        return "delete-stale", False, wants_all, dryrun

    if wants_test:
        LOGGER.warning(
            "Deprecated: use 'enge reportportal check' instead of '--test'. "
            "Old spellings will be removed in a future release."
        )
        return "check", False, wants_all, dryrun

    if wants_finish:
        LOGGER.warning(
            "Deprecated: use 'enge reportportal finish' instead of "
            "'--finish'. "
            "Old spellings will be removed in a future release."
        )
        return "finish", wants_enrich_logs, wants_all, dryrun

    if wants_enrich_logs:
        LOGGER.warning(
            "Deprecated: use 'enge reportportal enrich' instead of "
            "'--enrich-logs'. "
            "Old spellings will be removed in a future release."
        )
        return "enrich", False, wants_all, dryrun

    if wants_delete_logs:
        LOGGER.warning(
            "Deprecated: use 'enge reportportal delete-logs' instead of "
            "'--delete-logs'. "
            "Old spellings will be removed in a future release."
        )
        return "delete-logs", False, wants_all, dryrun

    if wants_all:
        LOGGER.warning(
            "Deprecated: use 'enge reportportal <subcommand> --all' "
            "instead of '--all-launches'. "
            "Old spellings will be removed in a future release."
        )

    return None, False, wants_all, dryrun


def _status_filter_for(subcommand: str) -> Optional[str]:
    """Map subcommand to the RP status filter for query-based resolution."""
    if subcommand in ("finish", "delete-logs"):
        return "IN_PROGRESS"
    return None  # enrich: all statuses


def main(ctx) -> int:
    """Entry point for the reportportal subcommand."""
    from enge.utils.globals import ExitCode

    try:
        rp = ReportPortalLaunch(ctx)
        subcommand, wants_enrich, wants_all, dryrun = _resolve_subcommand(ctx)

        if subcommand is None:
            LOGGER.error(
                "No reportportal subcommand specified. "
                "Run 'enge reportportal --help' for usage."
            )
            return ExitCode.EXCEPTION

        # Standalone operations
        if subcommand == "check":
            return op_check(rp, ctx)

        if subcommand == "delete-stale":
            return op_delete_stale(rp, ctx, dryrun)

        # Pipeline operations — resolve then apply
        if wants_all:
            if subcommand == "finish" and wants_enrich:
                enrich_rc = enrich_all_launches(rp, status_filter="IN_PROGRESS")
                finish_rc = finish_all_in_progress_launches(rp)
                return enrich_rc or finish_rc
            if subcommand == "finish":
                return finish_all_in_progress_launches(rp)
            if subcommand == "enrich":
                return enrich_all_launches(rp, status_filter=None)
            if subcommand == "delete-logs":
                return delete_logs_all_launches(rp)
        else:
            if subcommand == "finish" and wants_enrich:
                enrich_rc = enrich_logs_from_task(rp)
                finish_rc = finish_launch_from_task(rp)
                return enrich_rc or finish_rc
            if subcommand == "finish":
                return finish_launch_from_task(rp)
            if subcommand == "enrich":
                return enrich_logs_from_task(rp)
            if subcommand == "delete-logs":
                return delete_logs_from_task(rp)

        LOGGER.error(f"Unknown reportportal subcommand: {subcommand}")
        return ExitCode.EXCEPTION

    except ConfigurationError as e:
        LOGGER.error(f"Configuration error: {e}")
        return ExitCode.CONFIG_ERROR
    except NetworkError as e:
        LOGGER.error(f"ReportPortal API/network error: {e}")
        return ExitCode.EXCEPTION
    except EngeError as e:
        LOGGER.error(f"Unexpected error: {e}")
        return ExitCode.EXCEPTION


if __name__ == "__main__":
    sys.exit(main())
