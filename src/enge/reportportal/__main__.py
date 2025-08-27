#!/usr/bin/env python3
"""
ReportPortal launch management for enge.

This module handles ReportPortal API integration for creating and managing
test launches through the ReportPortal API.
"""

import logging
import sys
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

from enge.utils.http_client import http_get, http_post, http_put
from requests.exceptions import RequestException
import lxml.etree

from enge.utils.opt_manager import parsed_opts
from enge.utils.errors import ConfigurationError, NetworkError, EngeError

LOGGER = logging.getLogger(__name__)


class ReportPortalLaunch:
    """
    Handle ReportPortal launch creation and management via API.
    """

    def __init__(self):
        """Initialize ReportPortal launch manager."""
        self.config = parsed_opts.config.get("reportportal", {})
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

    def generate_launch_name(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate launch name in the format: (EVENT_NAME|SET_NAME)~datetime_stamp~tier~architecture

        Args:
            context: Optional context containing event, set_name, tier, architecture, etc.

        Returns:
            str: Generated launch name
        """
        # Get timestamp in YYYY-MM-DD format
        timestamp = datetime.now().strftime("%Y-%m-%d")

        if not context:
            return f"ENGE_Launch~{timestamp}~unknown"

        # Determine the event/set name component (event takes priority)
        name_component = context.get("event") or context.get("set_name") or "unknown"

        # Get tier and architecture
        tier = context.get("tier") or "unknown"
        architecture = context.get("architecture") or "unknown"

        # Generate the name in the format: (EVENT_NAME|SET_NAME)~datetime_stamp~tier~architecture
        return f"{name_component.upper()}~{timestamp}~{tier}~{architecture}"

    def generate_launch_payload(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        tmt_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate the launch payload that would be sent to ReportPortal API.

        This method is useful for dry run mode to show what would be sent without actually sending it.

        Args:
            name: Optional launch name. If not provided, will be auto-generated.
            description: Optional launch description.
            context: Optional context for launch name generation.
            tmt_context: Optional TMT context to include as launch attributes.

        Returns:
            Dict: The launch payload that would be sent to ReportPortal
        """
        if not name:
            name = self.generate_launch_name(context)

        launch_data = {
            "name": name,
            "description": description
            or f"Launch created by enge on {datetime.now().isoformat()}",
            "mode": "DEFAULT",
            "startTime": int(
                datetime.now().timestamp() * 1000
            ),  # ReportPortal expects milliseconds
            "tags": ["enge", "automated"],
        }

        # Add TMT context as ReportPortal launch attributes
        if tmt_context:
            attributes = []
            for key, value in tmt_context.items():
                if value is not None:  # Only include non-None values
                    # Convert value to string and escape special characters if needed
                    str_value = str(value)
                    # ReportPortal attributes format: key:value
                    attributes.append({"key": key, "value": str_value})

            if attributes:
                launch_data["attributes"] = attributes

        return launch_data

    def create_launch(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        tmt_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a new launch in ReportPortal.

        Args:
            name: Optional launch name. If not provided, will be auto-generated.
            description: Optional launch description.

        Returns:
            str: UUID of the created launch

        Raises:
            RequestException: If the API request fails
            ValueError: If the response is invalid
        """
        # Generate the launch payload
        launch_data = self.generate_launch_payload(
            name, description, context, tmt_context
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
                    f"Failed to create launch: {response.status_code} - {response.text}"
                )

            response_data = response.json()
            launch_uuid = response_data.get("id")

            if not launch_uuid:
                raise ValueError("Launch UUID not found in response")

            LOGGER.info(f"✓ ReportPortal launch created successfully: {launch_uuid}")
            LOGGER.info(f"  Launch name: {launch_data['name']}")
            LOGGER.info(
                f"  Launch URL: {self.url}/ui/#{self.project}/launches/all/{launch_uuid}"
            )

            return launch_uuid

        except RequestException as e:
            LOGGER.error(f"Failed to create ReportPortal launch.")
            raise NetworkError("Failed to create ReportPortal launch") from e
        except Exception as e:
            LOGGER.error(f"Unexpected error creating launch.")
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
            size: Number of launches to retrieve per page (default: 50)
            page: Page number to retrieve (default: 0)
            status: Launch status to filter by (default: "IN_PROGRESS")
            attribute_filters: Dict of attribute key-value pairs to filter by

        Returns:
            List[Dict]: List of launch data dictionaries

        Raises:
            RequestException: If the API request fails
        """
        try:
            # Build base parameters
            params = [
                ("page.size", size),
                ("page.page", page),
            ]

            # Add status filter if provided
            if status:
                params.append(("filter.eq.status", status))

            # Add attribute filters if provided
            if attribute_filters:
                for key, value in attribute_filters.items():
                    if key != "uniq_id":  # Skip uniq_id as it's used for UUID matching
                        params.append(("filter.has.attributeKey", key))
                        params.append(("filter.has.attributeValue", value))
                        LOGGER.debug(f"Adding attribute filter: {key}={value}")

            LOGGER.debug(f"Launch list params: {params}")

            response = http_get(
                f"{self.api_base}/launch",
                headers=self.headers,
                params=params,
                timeout=30,
            )

            if response.status_code != 200:
                raise RequestException(
                    f"Failed to list launches: {response.status_code} - {response.text}"
                )

            response_data = response.json()

            # Debug: Log the actual response structure
            LOGGER.debug(
                f"ReportPortal API response keys: {list(response_data.keys())}"
            )

            launches = response_data.get("content", [])

            # Debug: Log structure of first launch if available
            if launches and len(launches) > 0:
                LOGGER.debug(f"First launch keys: {list(launches[0].keys())}")
                LOGGER.debug(f"First launch sample: {launches[0]}")

            LOGGER.debug(f"Retrieved {len(launches)} launches from page {page}")
            return launches

        except RequestException as e:
            LOGGER.error(f"Failed to list ReportPortal launches")
            raise NetworkError("Failed to list ReportPortal launches") from e
        except Exception as e:
            LOGGER.error(f"Unexpected error listing launches.")
            raise EngeError("Unexpected error listing ReportPortal launches") from e

    def find_launch_by_uniq_id(
        self, uniq_id: str, tmt_context: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """
        Find a launch UUID by matching the uniq_id (first 12 chars of UUID).
        Uses TMT context for enhanced filtering when available.

        Args:
            uniq_id: First 12 characters of the launch UUID to match
            tmt_context: Optional TMT context for enhanced filtering

        Returns:
            str: Full launch UUID if found, None otherwise
        """
        try:
            LOGGER.info(f"Searching for launch with uniq_id: {uniq_id}")

            # Use TMT context for enhanced filtering if available
            attribute_filters = None
            if tmt_context:
                attribute_filters = {
                    k: str(v)
                    for k, v in tmt_context.items()
                    if k != "uniq_id" and v is not None
                }
                if attribute_filters:
                    LOGGER.info(
                        f"Using TMT context filters: {list(attribute_filters.keys())}"
                    )
                else:
                    LOGGER.debug(
                        "TMT context provided but no usable attributes for filtering"
                    )

            # First try with IN_PROGRESS status (most likely for active launches)
            total_checked = 0
            for status in [
                "IN_PROGRESS",
                None,
            ]:  # Try IN_PROGRESS first, then all statuses
                if status:
                    LOGGER.debug(f"Searching launches with status: {status}")
                else:
                    LOGGER.debug("Searching all launches (no status filter)")

                status_checked = 0
                for page in range(0, 10):  # Check fewer pages with better filtering
                    launches = self.list_launches(
                        size=50,
                        page=page,
                        status=status,
                        attribute_filters=attribute_filters,
                    )

                    if not launches:
                        LOGGER.debug(f"No more launches found at page {page}")
                        break

                    status_checked += len(launches)
                    total_checked += len(launches)
                    LOGGER.debug(f"Checking page {page} with {len(launches)} launches")

                    for launch in launches:
                        # Try both "uuid" and "id" fields as different RP versions may use different field names
                        launch_uuid = launch.get("uuid") or launch.get("id") or ""

                        if not launch_uuid:
                            LOGGER.debug(
                                f"Launch missing UUID field: {list(launch.keys())}"
                            )
                            continue

                        LOGGER.debug(
                            f"Checking launch UUID: {launch_uuid} against uniq_id: {uniq_id}"
                        )

                        if launch_uuid.startswith(uniq_id):
                            LOGGER.info(
                                f"✓ Found launch matching uniq_id '{uniq_id}': {launch_uuid}"
                            )
                            LOGGER.info(
                                f"  Launch name: {launch.get('name', 'Unknown')}"
                            )
                            LOGGER.info(
                                f"  Launch status: {launch.get('status', 'Unknown')}"
                            )

                            # Verify attributes match if we used filtering
                            if attribute_filters:
                                launch_attrs = {
                                    attr.get("key"): attr.get("value")
                                    for attr in launch.get("attributes", [])
                                    if attr.get("key") and attr.get("value")
                                }
                                LOGGER.debug(f"Launch attributes: {launch_attrs}")
                                matches = all(
                                    launch_attrs.get(k) == v
                                    for k, v in attribute_filters.items()
                                )
                                if matches:
                                    LOGGER.info(
                                        f"✓ Attributes verified for launch {launch_uuid}"
                                    )
                                else:
                                    LOGGER.warning(
                                        f"Attributes mismatch for launch {launch_uuid}, continuing search..."
                                    )
                                    continue

                            return launch_uuid

                LOGGER.debug(
                    f"Checked {status_checked} launches with status {status or 'any'}"
                )

                # If we found launches with filtering but no match, try next status
                if status_checked > 0:
                    continue

            LOGGER.warning(
                f"No launch found matching uniq_id '{uniq_id}' in {total_checked} launches checked"
            )
            return None

        except Exception as e:
            LOGGER.error(f"Error searching for launch with uniq_id '{uniq_id}': {e}")
            import traceback

            LOGGER.debug(f"Traceback: {traceback.format_exc()}")
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

        Args:
            launch_uuid: UUID of the launch to finish
            end_time: End time in ISO format (e.g., "2025-07-31T06:43:04.695Z")
            status: Launch status ("PASSED", "FAILED", "STOPPED", "SKIPPED", "INTERRUPTED")
            description: Optional launch description
            attributes: Optional list of launch attributes in [{"key": "key1", "value": "value1"}] format

        Returns:
            bool: True if successful, False otherwise

        Raises:
            RequestException: If the API request fails
        """
        finish_data = {
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

            if response.status_code not in [200, 201]:
                raise RequestException(
                    f"Failed to finish launch: {response.status_code} - {response.text}"
                )

            LOGGER.info(f"✓ ReportPortal launch finished successfully: {launch_uuid}")
            LOGGER.info(f"  Status: {status}")
            LOGGER.info(f"  End time: {end_time}")
            return True

        except RequestException as e:
            LOGGER.error(f"Failed to finish ReportPortal launch.")
            raise NetworkError("Failed to finish ReportPortal launch") from e
        except Exception as e:
            LOGGER.error(f"Unexpected error finishing launch.")
            raise EngeError("Unexpected error finishing ReportPortal launch") from e

    def extract_latest_timestamp_from_xml(self, xml_content: str) -> Optional[str]:
        """
        Extract the latest timestamp (end-time) from parsed XML content.

        Args:
            xml_content: Raw XML content from xunit results

        Returns:
            str: Latest timestamp in ISO format (e.g., "2025-07-31T06:43:04.695Z") or None if not found
        """
        if not xml_content or not xml_content.strip():
            LOGGER.warning("No XML content provided for timestamp extraction")
            return None

        try:
            # Parse XML
            LOGGER.debug(f"Parsing XML content (length: {len(xml_content)} chars)")
            xml = lxml.etree.fromstring(xml_content.encode())
            latest_timestamp = None
            latest_datetime = None

            # Look for timestamps in testsuites and testcases
            elements_to_check = xml.xpath("//testsuite | //testcase")
            LOGGER.debug(
                f"Found {len(elements_to_check)} XML elements to check for timestamps"
            )

            if not elements_to_check:
                LOGGER.warning("No testsuite or testcase elements found in XML")
                # Try to find any elements with time-related attributes
                all_elements = xml.xpath("//*")
                LOGGER.debug(f"Total XML elements found: {len(all_elements)}")
                elements_to_check = all_elements

            for elem in elements_to_check:
                # Check attributes directly (without @ prefix since we're checking .attrib)
                if "end-time" in elem.attrib:
                    timestamp_str = elem.attrib["end-time"]
                    LOGGER.debug(
                        f"Found timestamp in {elem.tag}.end-time: {timestamp_str}"
                    )

                    # Try to parse the timestamp
                    parsed_time = self._parse_timestamp(timestamp_str)
                    if parsed_time and (
                        latest_datetime is None or parsed_time > latest_datetime
                    ):
                        latest_datetime = parsed_time
                        latest_timestamp = timestamp_str
                        LOGGER.info(f"New latest timestamp: {timestamp_str}")
                        LOGGER.debug(f"New latest timestamp: {timestamp_str}")

            # If no timestamps found in attributes, look for <timestamp> elements
            if latest_timestamp is None:
                LOGGER.debug(
                    "No timestamps found in attributes, checking element text content"
                )
                timestamp_elements = xml.xpath("//timestamp | //time | //end-time")

                for elem in timestamp_elements:
                    if elem.text:
                        LOGGER.debug(f"Found timestamp element {elem.tag}: {elem.text}")
                        parsed_time = self._parse_timestamp(elem.text)
                        if parsed_time and (
                            latest_datetime is None or parsed_time > latest_datetime
                        ):
                            latest_datetime = parsed_time
                            latest_timestamp = elem.text

            if latest_timestamp:
                # Convert to ISO format with Z suffix for ReportPortal
                iso_timestamp = self._convert_to_iso_format(latest_timestamp)
                LOGGER.info(f"✓ Latest timestamp extracted from XML: {iso_timestamp}")
                return iso_timestamp
            else:
                LOGGER.warning("No timestamps found in XML content")
                LOGGER.debug("XML structure preview:")
                LOGGER.debug(
                    lxml.etree.tostring(xml, pretty_print=True, encoding="unicode")[
                        :500
                    ]
                    + "..."
                )
                return None

        except lxml.etree.XMLSyntaxError as e:
            LOGGER.error(f"XML parsing error: {e}")
            LOGGER.debug(f"XML content preview: {xml_content[:200]}...")
            return None
        except Exception as e:
            LOGGER.error(f"Error extracting timestamp from XML: {e}")
            return None

    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """
        Parse timestamp string into datetime object.

        Args:
            timestamp_str: Timestamp string in various formats

        Returns:
            datetime: Parsed datetime object or None if parsing fails
        """
        # Try the specific format first (based on your XML structure)
        primary_format = "%Y-%m-%dT%H:%M:%S.%f+00:00"

        try:
            return datetime.strptime(timestamp_str.strip(), primary_format)
        except ValueError:
            pass

        # Fallback to other common formats
        fallback_formats = [
            "%Y-%m-%dT%H:%M:%S.%fZ",  # ISO with microseconds and Z
            "%Y-%m-%dT%H:%M:%SZ",  # ISO without microseconds and Z
            "%Y-%m-%dT%H:%M:%S.%f",  # ISO with microseconds, no Z
            "%Y-%m-%dT%H:%M:%S",  # ISO without microseconds, no Z
            "%Y-%m-%d %H:%M:%S.%f",  # Space separated with microseconds
            "%Y-%m-%d %H:%M:%S",  # Space separated without microseconds
        ]

        for fmt in fallback_formats:
            try:
                return datetime.strptime(timestamp_str.strip(), fmt)
            except ValueError:
                continue

        # Try parsing as Unix timestamp (seconds)
        try:
            return datetime.fromtimestamp(float(timestamp_str))
        except (ValueError, OverflowError):
            pass

        LOGGER.debug(f"Could not parse timestamp: {timestamp_str}")
        return None

    def _convert_to_iso_format(self, timestamp_str: str) -> str:
        """
        Convert timestamp to ISO format required by ReportPortal.

        Args:
            timestamp_str: Original timestamp string

        Returns:
            str: ISO formatted timestamp with Z suffix
        """
        parsed_time = self._parse_timestamp(timestamp_str)
        if parsed_time:
            # Convert to ISO format with milliseconds and Z suffix
            return parsed_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        else:
            # Fallback to current time if parsing fails
            LOGGER.warning(
                f"Could not parse timestamp '{timestamp_str}', using current time"
            )
            return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def extract_artifacts_url(self, task_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract artifacts URL from request JSON run section.

        Args:
            task_data: Task data dictionary from the Testing Farm API

        Returns:
            str: Artifacts URL or None if not found
        """
        try:
            # Look for run.artifacts in the task data
            run_data = task_data.get("run", {})
            if not isinstance(run_data, dict):
                LOGGER.warning("No 'run' section found in task data")
                return None

            artifacts = run_data.get("artifacts")
            if not artifacts:
                LOGGER.warning("No 'artifacts' found in run section")
                return None

            # artifacts could be a string URL or a dict with URL inside
            if isinstance(artifacts, str):
                LOGGER.debug(f"Found artifacts URL: {artifacts}")
                return artifacts
            elif isinstance(artifacts, dict):
                # Look for common keys that might contain the URL
                url_keys = ["url", "link", "href", "artifacts_url"]
                for key in url_keys:
                    if key in artifacts and artifacts[key]:
                        LOGGER.debug(
                            f"Found artifacts URL in '{key}': {artifacts[key]}"
                        )
                        return artifacts[key]

                LOGGER.warning(f"No URL found in artifacts dict: {artifacts}")
                return None
            else:
                LOGGER.warning(f"Unexpected artifacts type: {type(artifacts)}")
                return None

        except Exception as e:
            LOGGER.error(f"Error extracting artifacts URL: {e}")
            return None

    def finish_launch_from_task(self) -> int:
        """
        Main logic for finishing a ReportPortal launch based on Testing Farm task results.

        This method:
        1. Uses the report module to get task data
        2. Checks if tasks are ready (not in new, queued, running, canceled states)
        3. Extracts uniq_id from TMT context
        4. Finds matching launch by uniq_id
        5. Extracts latest timestamp from XML results
        6. Extracts artifacts URL from task data
        7. Builds finish request with attributes from TMT context
        8. Finishes the launch

        Returns:
            int: Exit code (0 for success, non-zero for error)
        """
        try:
            # Import and use the report module to get task data
            from enge.report.concurrent_parser import parse_request_xunit_concurrent
            from enge.report.__main__ import parse_tasks

            LOGGER.info("Getting task data using report module...")

            # Get task URLs
            request_url_list, tasks_source = parse_tasks()
            if not request_url_list:
                LOGGER.error("No task URLs found to process")
                return 1

            # Parse task data with concurrent parser
            task_results_dict = parse_request_xunit_concurrent(
                request_url_list, tasks_source
            )

            if not task_results_dict:
                LOGGER.error("No task results found")
                return 1

            processed_count = 0
            for task_uuid, task_data in task_results_dict.items():
                LOGGER.info(f"Processing task: {task_uuid}")

                # Get the original task data with state information AND XML content
                from enge.report.concurrent_parser import ConcurrentRequestParser

                with ConcurrentRequestParser() as parser:
                    task_url = f"{parsed_opts.testing_farm_endpoint.api_endpoint_url}/{task_uuid}"
                    task_result = parser._fetch_task_info(task_url)

                    if not task_result:
                        LOGGER.warning(f"Could not fetch task info for {task_uuid}")
                        continue

                    # Check if task is ready to be finished (not in excluded states)
                    excluded_states = ["NEW", "QUEUED", "RUNNING", "CANCELED"]
                    if task_result.request_state.upper() in excluded_states:
                        LOGGER.info(
                            f"Task {task_uuid} is in state '{task_result.request_state}', skipping"
                        )
                        continue

                    LOGGER.info(
                        f"Task {task_uuid} is in state '{task_result.request_state}', proceeding to finish launch"
                    )

                    # NOW fetch the XML content - this is the missing step!
                    LOGGER.debug(f"Fetching XML content for task {task_uuid}")
                    task_result = parser._fetch_xml_results(task_result)

                    if task_result.xunit_content:
                        LOGGER.debug(
                            f"✓ Successfully fetched XML content (length: {len(task_result.xunit_content)})"
                        )
                    else:
                        LOGGER.warning(
                            f"No XML content available for task {task_uuid} (error: {task_result.error_message})"
                        )

                    # Get TMT context to extract uniq_id and build attributes
                    tmt_context = self._extract_tmt_context_from_task(task_result)
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

                    # Find the matching launch using enhanced filtering with TMT context
                    launch_uuid = self.find_launch_by_uniq_id(uniq_id, tmt_context)
                    if not launch_uuid:
                        LOGGER.error(f"No launch found matching uniq_id '{uniq_id}'")
                        continue

                    # Extract latest timestamp from XML
                    end_time = None
                    if task_result.xunit_content and task_result.xunit_content.strip():
                        LOGGER.info("Extracting timestamp from XML content...")
                        end_time = self.extract_latest_timestamp_from_xml(
                            task_result.xunit_content
                        )
                    else:
                        LOGGER.warning(f"No XML content available for task {task_uuid}")

                    if not end_time:
                        # Fallback to current time
                        LOGGER.warning(
                            "No timestamp found in XML, using current time as fallback"
                        )
                        end_time = (
                            datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                        )
                        LOGGER.info(f"Using fallback timestamp: {end_time}")

                    # Extract artifacts URL for description
                    # Need to fetch full task data for this

                    response = http_get(
                        task_url,
                        headers={
                            "Authorization": f"Bearer {parsed_opts.testing_farm.get('api_key')}"
                        },
                        timeout=30,
                    )
                    full_task_data = (
                        response.json() if response.status_code == 200 else {}
                    )

                    artifacts_url = self.extract_artifacts_url(full_task_data)
                    description = f"\n{artifacts_url}" if artifacts_url else None

                    # Build attributes from TMT context
                    attributes = []
                    for key, value in tmt_context.items():
                        if value is not None:
                            attributes.append({"key": key, "value": str(value)})

                    # Determine status from task state
                    status_mapping = {
                        "COMPLETE": "PASSED",
                        "ERROR": "FAILED",
                        "FAILED": "FAILED",
                    }
                    status = status_mapping.get(
                        task_result.request_state.upper(), "STOPPED"
                    )

                    # Also check overall result if available
                    if task_data.get("overall_result"):
                        result_status_mapping = {
                            "passed": "PASSED",
                            "failed": "FAILED",
                            "error": "FAILED",
                        }
                        status = result_status_mapping.get(
                            task_data["overall_result"].lower(), status
                        )

                    # Check if this is a dry run
                    is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

                    if is_dryrun:
                        # Show what would be sent without actually finishing the launch
                        self._show_dryrun_finish_data(
                            task_uuid=task_uuid,
                            launch_uuid=launch_uuid,
                            end_time=end_time,
                            status=status,
                            description=description,
                            attributes=attributes,
                        )
                        processed_count += 1
                    else:
                        # Actually finish the launch
                        success = self.finish_launch(
                            launch_uuid=launch_uuid,
                            end_time=end_time,
                            status=status,
                            description=description,
                            attributes=attributes,
                        )

                        if success:
                            LOGGER.info(
                                f"✓ Successfully finished launch for task {task_uuid}"
                            )
                            processed_count += 1
                        else:
                            LOGGER.error(
                                f"✗ Failed to finish launch for task {task_uuid}"
                            )

            is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)
            if processed_count > 0:
                if is_dryrun:
                    LOGGER.info(
                        f"✓ Dry run complete - showed {processed_count} launch finish request(s)"
                    )
                else:
                    LOGGER.info(f"✓ Successfully finished {processed_count} launch(es)")
                return 0
            else:
                if is_dryrun:
                    LOGGER.warning("No launches would be finished")
                else:
                    LOGGER.warning("No launches were finished")
                return 1

        except Exception as e:
            LOGGER.error(f"Error in finish launch logic: {e}")
            return 1

    def _show_dryrun_finish_data(
        self,
        task_uuid: str,
        launch_uuid: str,
        end_time: str,
        status: str,
        description: Optional[str] = None,
        attributes: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """
        Display what would be sent to ReportPortal in dry run mode.

        Args:
            task_uuid: Task UUID being processed
            launch_uuid: ReportPortal launch UUID
            end_time: End time in ISO format
            status: Launch status
            description: Optional launch description
            attributes: Optional list of launch attributes
        """
        from enge.utils import FormatText

        # Build the request data that would be sent
        finish_data = {
            "endTime": end_time,
            "status": status,
        }

        if description is not None:
            finish_data["description"] = description

        if attributes:
            finish_data["attributes"] = attributes

        # Display the dry run information
        print()
        print(f"{FormatText.BLUE}{'=' * 60}{FormatText.END}")
        print(
            f"{FormatText.BLUE}DRY RUN - ReportPortal Launch Finish Request{FormatText.END}"
        )
        print(f"{FormatText.BLUE}{'=' * 60}{FormatText.END}")
        print()
        print(f"{FormatText.BOLD}Task UUID:{FormatText.END} {task_uuid}")
        print(f"{FormatText.BOLD}Launch UUID:{FormatText.END} {launch_uuid}")
        print(
            f"{FormatText.BOLD}API Endpoint:{FormatText.END} PUT {self.api_base}/launch/{launch_uuid}/finish"
        )
        print()
        print(f"{FormatText.BOLD}Request Headers:{FormatText.END}")
        print(f"  Authorization: Bearer {self.token[:10]}...")
        print("  Content-Type: application/json")
        print()
        print(f"{FormatText.BOLD}Request Body:{FormatText.END}")
        print(json.dumps(finish_data, indent=2, ensure_ascii=False))
        print()

        # Show attribute details if present
        if attributes:
            print(f"{FormatText.BOLD}Attributes Details:{FormatText.END}")
            for attr in attributes:
                print(f"  • {attr['key']}: {attr['value']}")
            print()

        print(
            f"{FormatText.GREEN}✓ Would finish launch {launch_uuid} for task {task_uuid}{FormatText.END}"
        )
        print(f"{FormatText.BLUE}{'=' * 60}{FormatText.END}")
        print()

    def _extract_tmt_context_from_task(self, task_result) -> Optional[Dict[str, Any]]:
        """
        Extract TMT context from task result.

        Args:
            task_result: TaskResult object from concurrent parser

        Returns:
            Dict: TMT context or None if not found
        """
        # For now, we'll need to get the TMT context from the Testing Farm API
        # This requires getting the full task data
        try:

            task_url = task_result.url
            LOGGER.debug(f"Fetching full task data from: {task_url}")

            response = http_get(
                task_url,
                headers={
                    "Authorization": f"Bearer {parsed_opts.testing_farm.get('api_key')}"
                },
                timeout=30,
            )

            if response.status_code != 200:
                LOGGER.warning(
                    f"Could not fetch task data: HTTP {response.status_code}"
                )
                LOGGER.debug(f"Response text: {response.text}")
                return None

            task_data = response.json()
            LOGGER.debug(f"Task data keys: {list(task_data.keys())}")

            # Look for TMT context in environments
            environments = task_data.get("environments_requested", [])
            LOGGER.debug(f"Found {len(environments)} environments in task data")

            if environments and len(environments) > 0:
                env = environments[0]
                LOGGER.debug(f"Environment keys: {list(env.keys())}")

                tmt_config = env.get("tmt", {})
                LOGGER.debug(
                    f"TMT config keys: {list(tmt_config.keys()) if tmt_config else 'No TMT config'}"
                )

                context = tmt_config.get("context", {})
                LOGGER.debug(f"TMT context: {context}")

                if context:
                    LOGGER.info(f"✓ Found TMT context with {len(context)} attributes")
                    return context
                else:
                    LOGGER.warning("TMT context is empty")
            else:
                LOGGER.warning("No environments found in task data")

            LOGGER.warning("No TMT context found in task data")

            # Try alternative locations for context
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

    def test_connection_and_data(self) -> int:
        """
        Test ReportPortal connection and show sample data for debugging.

        Returns:
            int: Exit code (0 for success, non-zero for error)
        """
        try:
            LOGGER.info("Testing ReportPortal connection...")

            # Test 1: List launches to verify API connection
            LOGGER.info("Testing launch listing...")
            launches = self.list_launches(
                size=5, page=0, status=None
            )  # Get all statuses for testing

            if launches:
                LOGGER.info(f"✓ Successfully retrieved {len(launches)} launches")
                for i, launch in enumerate(launches[:3]):
                    LOGGER.info(
                        f"  Launch {i+1}: {launch.get('name', 'No name')} (UUID: {launch.get('uuid') or launch.get('id', 'No UUID')})"
                    )
            else:
                LOGGER.warning(
                    "No launches found - this might be expected for a new project"
                )

            # Test 2: Try report module integration
            LOGGER.info("Testing report module integration...")
            from enge.report.__main__ import parse_tasks

            request_url_list, tasks_source = parse_tasks()

            if request_url_list:
                LOGGER.info(
                    f"✓ Found {len(request_url_list)} task URLs from report module"
                )
                for i, url in enumerate(request_url_list[:3]):
                    LOGGER.info(f"  Task URL {i+1}: {url}")
            else:
                LOGGER.warning(
                    "No task URLs found - you may need to provide task IDs via -i, -f, or --get-tag"
                )

            return 0

        except Exception as e:
            LOGGER.error(f"Connection test failed: {e}")
            import traceback

            LOGGER.debug(f"Traceback: {traceback.format_exc()}")
            return 1

    def store_launch_uuid(self, launch_uuid: str) -> None:
        """
        Store the launch UUID for later use.

        For now, this just logs the UUID. In the future, this could be extended
        to store in a file, database, or other persistent storage.

        Args:
            launch_uuid: The UUID of the created launch
        """
        LOGGER.info(f"Launch UUID stored: {launch_uuid}")
        # TODO: Implement persistent storage if needed
        # For now, the UUID is available in the logs and returned from create_launch()


def main() -> int:
    """
    Main entry point for reportportal subcommand.

    Handles different ReportPortal operations based on CLI arguments.
    """
    try:
        rp_launch = ReportPortalLaunch()

        # Check if --finish flag is used
        if getattr(parsed_opts.cli_args, "finish", False):
            LOGGER.info("ReportPortal module - Finishing launches")
            return rp_launch.finish_launch_from_task()
        elif getattr(parsed_opts.cli_args, "test", False):
            LOGGER.info("ReportPortal module - Testing connection and data")
            return rp_launch.test_connection_and_data()
        else:
            # Default behavior: create a launch
            LOGGER.info("ReportPortal module - Launch creation")

            # Create a launch with auto-generated name
            launch_uuid = rp_launch.create_launch()

            # Store the UUID (for now just logs it)
            rp_launch.store_launch_uuid(launch_uuid)

            return 0

    except ConfigurationError as e:
        LOGGER.error(f"Configuration error: {e}")
        return 1
    except NetworkError as e:
        LOGGER.error(f"ReportPortal API/network error: {e}")
        return 1
    except EngeError as e:
        LOGGER.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
