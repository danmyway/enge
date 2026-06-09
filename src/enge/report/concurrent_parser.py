import logging
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import lxml.etree  # type: ignore
import requests
import requests.adapters
from requests.exceptions import ConnectionError, RequestException

from enge.utils import FormatText
from enge.utils.opt_manager import parsed_opts

LOGGER = logging.getLogger(__name__)

# Return value constants
ALL_PASS = 0
FAIL_HERE = 2
ERROR_HERE = 3
NO_RESULT = 4

# Global return value tracking
RETURN_VALUE = None


def update_retval(new_value):
    """Update the global return value."""
    global RETURN_VALUE
    if RETURN_VALUE is None or new_value > RETURN_VALUE:
        RETURN_VALUE = new_value


@dataclass
class TaskResult:
    """Data class for task results to improve type safety and readability."""

    request_uuid: str
    request_source_compose: str
    request_target_release: str
    request_upgrade_path: str
    request_arch: str
    request_state: str
    request_datetime_created: str
    request_plan: str
    request_plan_filter: str
    request_summary: str
    request_result_overall: str
    results_xml_url: str
    url: str
    xunit_content: Optional[str] = None
    error_message: Optional[str] = None
    should_skip: bool = False
    potential_pipeline_error: bool = False
    skip_reason: Optional[str] = None  # "canceled", "queued", "running", etc.


class ConcurrentRequestParser:
    """Parser with concurrent HTTP requests and better error handling."""

    def __init__(self, max_workers: int = 10, timeout: int = 30, max_retries: int = 3):
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = None

    def __enter__(self):
        # Create a session for connection pooling
        self.session = requests.Session()
        # Configure connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=self.max_workers,
            pool_maxsize=self.max_workers * 2,
            max_retries=self.max_retries,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            self.session.close()

    @staticmethod
    def _get_short_uuid(uuid: str) -> str:
        """Get shortened UUID for debug logging."""
        return uuid.split("-")[0]

    def _fetch_task_info(
        self, url: str, process_state: bool = True
    ) -> Optional[TaskResult]:
        """Fetch task information with comprehensive state and error handling."""
        if not self.session:
            LOGGER.error("Session not initialized")
            return None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()

                task_data = response.json()
                if not task_data:
                    LOGGER.error(f"Empty JSON response from {url}")
                    return None

                # Check for required fields
                required_fields = [
                    "id",
                    "state",
                    "created",
                    "environments_requested",
                    "test",
                ]
                for field in required_fields:
                    if field not in task_data:
                        LOGGER.error(
                            f"Missing required field '{field}' in response from {url}"
                        )
                        return None

                # Validate environments_requested
                environments = task_data.get("environments_requested", [])
                if not isinstance(environments, list) or not environments:
                    LOGGER.error(
                        f"Invalid or missing environments_requested in response from {url}"
                    )
                    return None

                test_data = task_data.get("test", {})
                fmf_data = (
                    test_data.get("fmf", {}) if isinstance(test_data, dict) else {}
                )

                # Safely extract result data
                result_data = task_data.get("result") or {}
                request_summary = (
                    result_data.get("summary", "Undefined")
                    if isinstance(result_data, dict)
                    else "Undefined"
                )
                request_result_overall = (
                    result_data.get("overall", "Undefined")
                    if isinstance(result_data, dict)
                    else "Undefined"
                )
                results_xml_url = (
                    result_data.get("xunit_url")
                    if isinstance(result_data, dict)
                    else None
                )
                if not results_xml_url:
                    results_xml_url = os.path.join(
                        str(parsed_opts.testing_farm_endpoint.log_artifact_baseurl),
                        task_data["id"],
                        "results.xml",
                    )

                task_result = TaskResult(
                    request_uuid=task_data["id"],
                    request_source_compose=(
                        environments[0].get("os", {}).get("compose", "Unknown")
                        if isinstance(environments[0], dict)
                        else "Unknown"
                    ),
                    request_target_release=(
                        environments[0]
                        .get("variables", {})
                        .get("TARGET_RELEASE", "Unknown")
                        if isinstance(environments[0], dict)
                        else "Unknown"
                    ),
                    request_upgrade_path=(
                        (
                            environments[0]
                            .get("variables", {})
                            .get("SOURCE_RELEASE", "Unknown")
                            + " to "
                            + environments[0]
                            .get("variables", {})
                            .get("TARGET_RELEASE", "Unknown")
                        )
                    ),
                    request_arch=(
                        environments[0].get("arch", "Unknown")
                        if isinstance(environments[0], dict)
                        else "Unknown"
                    ),
                    request_state=task_data["state"].upper(),
                    request_datetime_created=task_data["created"],
                    request_plan=(
                        fmf_data.get("name", "") if isinstance(fmf_data, dict) else ""
                    ),
                    request_plan_filter=(
                        fmf_data.get("plan_filter", "")
                        if isinstance(fmf_data, dict)
                        else ""
                    ),
                    request_summary=request_summary,
                    request_result_overall=request_result_overall,
                    results_xml_url=results_xml_url,
                    url=url,
                )

                # Handle canceled tasks early
                if "canceled" in task_result.request_state.lower():
                    update_retval(NO_RESULT)
                    task_result.should_skip = True
                    task_result.skip_reason = "canceled"
                    # Don't return early - let _process_task_state handle display

                if process_state:
                    self._process_task_state(task_result)

                return task_result

            except RequestException as e:
                # Don't retry on 404s - these are permanent failures
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code == 404
                ):
                    LOGGER.error(f"Task not found (404): {url}")
                    return None

                if attempt == self.max_retries:
                    LOGGER.error(
                        f"Failed to fetch task info from {url} after {self.max_retries + 1} attempts: {e}"
                    )
                    return None
                else:
                    LOGGER.warning(
                        f"Attempt {attempt + 1} failed for {url}, retrying..."
                    )
                    time.sleep(2**attempt)  # Exponential backoff
            except (KeyError, IndexError, TypeError, AttributeError) as e:
                LOGGER.error(f"Data parsing error for {url}: {e}")
                LOGGER.debug(
                    f"Response data: {task_data if 'task_data' in locals() else 'No data'}"
                )
                return None

        return None

    def _process_task_state(self, task_result: TaskResult):
        """Process task state and handle various conditions."""
        uuid_short = self._get_short_uuid(task_result.request_uuid)

        # Handle colored state display
        background = None
        if task_result.request_state == "COMPLETE":
            background = FormatText.BG_GREEN
        elif task_result.request_state in ("NEW", "QUEUED"):
            background = FormatText.BG_BLUE
        elif task_result.request_state == "RUNNING":
            background = FormatText.BG_CYAN
        elif task_result.request_state == "ERROR":
            background = FormatText.BG_YELLOW
            update_retval(ERROR_HERE)
        elif task_result.request_state == "CANCELED":
            background = FormatText.BG_YELLOW

        colored_state = FormatText.format_text(
            task_result.request_state, background, FormatText.BLACK
        )

        # Display task information concisely
        LOGGER.info(f"[{task_result.request_uuid}] task status: {colored_state}")
        LOGGER.debug(f"[{uuid_short}]    URL: {task_result.url}")

        # Handle canceled tasks
        if task_result.should_skip and "canceled" in task_result.request_state.lower():
            LOGGER.debug(f"[{uuid_short}] Task was canceled and will be skipped")
            return

        # Handle waiting for in-progress tasks
        if task_result.request_state in ("NEW", "QUEUED", "RUNNING"):
            if parsed_opts.cli_args.action == "rerun" or getattr(
                parsed_opts.cli_args, "wait", False
            ):
                self._wait_for_completion(task_result)
            else:
                # Don't log individual warnings - will show general warning later
                LOGGER.debug(f"[{uuid_short}] Request is still running.")
                update_retval(NO_RESULT)
                task_result.should_skip = True
                # Set specific skip reason based on state
                if task_result.request_state in ("NEW", "QUEUED"):
                    task_result.skip_reason = "queued"
                else:  # RUNNING
                    task_result.skip_reason = "running"
                return
        else:
            # Check if task is still running
            if task_result.request_state not in ("COMPLETE", "ERROR"):
                # Don't log individual warnings - will show general warning later
                LOGGER.debug(f"[{uuid_short}] Request is still running")
                update_retval(NO_RESULT)
                task_result.should_skip = True
                # Set specific skip reason based on state
                if task_result.request_state == "QUEUED":
                    task_result.skip_reason = "queued"
                elif task_result.request_state == "RUNNING":
                    task_result.skip_reason = "running"
                else:
                    task_result.skip_reason = (
                        "running"  # fallback to running for unknown states
                    )
                return

        # Handle error states
        if "error" in (
            task_result.request_state.lower(),
            task_result.request_result_overall.lower(),
        ):
            LOGGER.debug(f"[{uuid_short}] Task ended up in ERROR state.")
            LOGGER.debug(
                f"[{uuid_short}] We'll try to fetch the XML results to get more information, if possible."
            )
            update_retval(ERROR_HERE)

    def _wait_for_completion(self, task_result: TaskResult):
        """Wait for a running task to complete."""
        if not self.session:
            return

        clear_line = "\x1b[2K"
        spacer = " " * 10
        loading_chars = ["/", "-", "\\", "|"]
        index = 0

        while True:
            try:
                response = self.session.get(task_result.url, timeout=self.timeout)
                response.raise_for_status()
                current_state = response.json()["state"]

                if current_state in ("complete", "error", "canceled"):
                    print(end=clear_line)  # Clear the loading animation
                    LOGGER.info(
                        f"[{task_result.request_uuid}] {FormatText.GREEN}Job finished!{FormatText.END}"
                    )
                    # Update task result with final state
                    task_data = response.json()
                    if task_data:
                        task_result.request_state = task_data["state"].upper()
                        # Safely extract result data
                        result_data = task_data.get("result") or {}
                        if isinstance(result_data, dict):
                            task_result.request_summary = result_data.get(
                                "summary", "Undefined"
                            )
                            task_result.request_result_overall = result_data.get(
                                "overall", "Undefined"
                            )
                            xunit_url = result_data.get("xunit_url")
                            if xunit_url:
                                task_result.results_xml_url = xunit_url
                    break

                print(end=clear_line)
                print(
                    f"Waiting for the job to finish.{spacer}{loading_chars[index]}",
                    end="\r",
                    flush=True,
                )
                index = (index + 1) % len(loading_chars)
                time.sleep(30)

            except RequestException as e:
                LOGGER.error(
                    f"[{task_result.request_uuid}] Error while waiting for task completion: {e}"
                )
                break

    def _fetch_xml_results(self, task_result: TaskResult) -> TaskResult:
        """Fetch XML results for a task with fallback handling."""
        if not self.session:
            LOGGER.error("Session not initialized")
            task_result.error_message = "Session not initialized"
            return task_result

        # Skip if task should be skipped
        if task_result.should_skip:
            return task_result

        uuid_short = self._get_short_uuid(task_result.request_uuid)

        try:
            response = self.session.get(
                task_result.results_xml_url, timeout=self.timeout
            )
            if response.status_code == 200:
                task_result.xunit_content = response.text
                return task_result
            else:
                # Handle non-200 responses
                LOGGER.debug(
                    f"[{uuid_short}] XML fetch returned status {response.status_code}"
                )
                LOGGER.debug(f"[{uuid_short}] URL: {task_result.results_xml_url}")

        except ConnectionError as err:
            LOGGER.critical("Connection Error")
            LOGGER.critical(
                "   There was an issue while attempting to create an API connection."
            )
            LOGGER.critical("   Please verify, that you're connected to the VPN")
            LOGGER.debug(f"   Error details: {err}")
            from enge.utils.errors import NetworkError

            raise NetworkError(
                "Failed to fetch XML results due to connection error"
            ) from err
        except RequestException as e:
            LOGGER.warning(f"[{task_result.request_uuid}] Failed to fetch XML")
            LOGGER.debug(f"[{uuid_short}]    URL: {task_result.results_xml_url}")
            LOGGER.debug(f"[{uuid_short}]    Error: {e}")

        # Fallback handling when XML is not available
        LOGGER.debug(f"[{uuid_short}] Unable to find the xml to parse")
        LOGGER.debug(f"[{uuid_short}]    Trying to fall back to the request results")
        if (
            task_result.request_result_overall != "Undefined"
            and task_result.request_summary != "Undefined"
        ):
            LOGGER.debug(
                f"[{uuid_short}]    Result: {FormatText.BOLD}{task_result.request_result_overall}{FormatText.END}"
            )
            LOGGER.debug(
                f"[{uuid_short}]    Summary: {FormatText.BOLD}{task_result.request_summary}{FormatText.END}"
            )
        else:
            LOGGER.warning(
                f"[{task_result.request_uuid}] Couldn't find any valuable information"
            )
            LOGGER.warning(
                f"[{task_result.request_uuid}]    Please consult with {task_result.url}"
            )

        update_retval(ERROR_HERE)
        task_result.error_message = "XML not available, using fallback"
        return task_result

    def parse_tasks_concurrent(self, request_url_list: List[str]) -> List[TaskResult]:
        """Parse tasks using concurrent HTTP requests."""
        if not request_url_list:
            return []

        LOGGER.info(
            f"{FormatText.BLUE}Reporting for the requested tasks{FormatText.END}"
        )
        LOGGER.info(f"{FormatText.DIM}{'─' * 60}{FormatText.END}")

        # Phase 1: Fetch task information concurrently
        task_results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all task info requests
            future_to_url = {
                executor.submit(self._fetch_task_info, url): url
                for url in request_url_list
            }

            # Collect results as they complete
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    if result:
                        # Add ALL results to task_results (including canceled/skipped)
                        task_results.append(result)
                        uuid_short = self._get_short_uuid(result.request_uuid)
                        if not result.should_skip:
                            LOGGER.debug(f"[{uuid_short}] Fetched task info")
                    else:
                        LOGGER.warning(f"Failed to fetch task info for {url}")
                except Exception as e:
                    LOGGER.error(f"Exception fetching task info for {url}: {e}")

        # Filter out skipped tasks for XML processing
        tasks_for_xml = [task for task in task_results if not task.should_skip]

        if not tasks_for_xml:
            return task_results  # Return all tasks (including skipped) for summary

        LOGGER.info(
            f"{FormatText.BLUE}Fetching XML results for {len(tasks_for_xml)} tasks{FormatText.END}"
        )
        LOGGER.info(f"{FormatText.DIM}{'─' * 60}{FormatText.END}")

        # Check for running/queued tasks and show general warning
        running_or_queued_tasks = [
            task
            for task in task_results
            if task.should_skip and task.skip_reason in ("running", "queued")
        ]
        if running_or_queued_tasks:
            LOGGER.warning("One or more requests are still running.")
            LOGGER.warning("Please try later or use --wait to wait for them to finish")

        # Phase 2: Fetch XML results concurrently (only for non-skipped tasks)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all XML requests
            future_to_task = {
                executor.submit(self._fetch_xml_results, task): task
                for task in tasks_for_xml
            }

            # Update results as they complete
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                    # Find and update the corresponding task in task_results
                    for i, original_task in enumerate(task_results):
                        if original_task.request_uuid == task.request_uuid:
                            task_results[i] = result
                            break

                    # Only log "Processed XML" if XML was actually fetched successfully
                    if result.xunit_content:
                        uuid_short = self._get_short_uuid(task.request_uuid)
                        LOGGER.debug(
                            f"[{uuid_short}] {FormatText.GREEN}Processed XML{FormatText.END}"
                        )
                except Exception as e:
                    uuid_short = self._get_short_uuid(task.request_uuid)
                    LOGGER.error(f"[{task.request_uuid}] Exception fetching XML: {e}")
                    task.error_message = f"Exception: {e}"
                    # Find and update the corresponding task in task_results
                    for i, original_task in enumerate(task_results):
                        if original_task.request_uuid == task.request_uuid:
                            task_results[i] = task
                            break

        return task_results


class XMLParser:
    """XML parser with streaming and memory efficiency."""

    @staticmethod
    def parse_xml_results(
        task_result: TaskResult, skip_pass: bool = False
    ) -> Dict[str, Any]:
        """Parse XML results with comprehensive business logic."""
        if not task_result.xunit_content:
            return {
                "request_uuid": task_result.request_uuid,
                "source_compose": task_result.request_source_compose,
                "target_release": task_result.request_target_release,
                "upgrade_path": task_result.request_upgrade_path,
                "plan": task_result.request_plan,
                "plan_filter": task_result.request_plan_filter,
                "created": task_result.request_datetime_created,
                "testsuites": [],
                "error": task_result.error_message or "No XML content",
            }

        try:
            # Parse XML efficiently
            xml = lxml.etree.fromstring(task_result.xunit_content.encode())

            job_result_overall = xml.xpath("/testsuites/@overall-result")[0]
            job_test_suites = xml.xpath("//testsuite")

            # Handle pipeline errors
            potential_pipeline_error = False
            if (
                len(job_test_suites) == 1
                and job_test_suites[0].xpath("./@name")[0] == "pipeline"
            ):
                potential_pipeline_error = True
                task_result.potential_pipeline_error = True

            # Update return values based on overall result
            if job_result_overall == "passed":
                update_retval(ALL_PASS)
            elif job_result_overall == "failed":
                update_retval(FAIL_HERE)
            elif job_result_overall == "error":
                update_retval(ERROR_HERE)
                # Bail out when potential pipeline error assessment returns True
                if potential_pipeline_error:
                    LOGGER.critical(
                        f"Potential pipeline ERROR, please verify the accuracy of the assessment at {task_result.url}"
                    )
                    LOGGER.critical(f"Result summary: {task_result.request_summary}")
                    return {
                        "request_uuid": task_result.request_uuid,
                        "source_compose": task_result.request_source_compose,
                        "target_release": task_result.request_target_release,
                        "upgrade_path": task_result.request_upgrade_path,
                        "plan": task_result.request_plan,
                        "plan_filter": task_result.request_plan_filter,
                        "created": task_result.request_datetime_created,
                        "testsuites": [],
                        "error": "Pipeline error detected",
                    }
            else:
                update_retval(99)

            # Skip if overall result is passed and skip_pass is enabled
            if skip_pass and job_result_overall.upper() == "PASSED":
                uuid_short = ConcurrentRequestParser._get_short_uuid(
                    task_result.request_uuid
                )
                LOGGER.debug(f"[{uuid_short}] Skipping as the overall result is pass")
                return {
                    "request_uuid": task_result.request_uuid,
                    "source_compose": task_result.request_source_compose,
                    "target_release": task_result.request_target_release,
                    "upgrade_path": task_result.request_upgrade_path,
                    "plan": task_result.request_plan,
                    "plan_filter": task_result.request_plan_filter,
                    "created": task_result.request_datetime_created,
                    "testsuites": [],
                    "skipped": "PASSED result skipped",
                }

            # Handle log downloads if requested
            if parsed_opts.cli_args.action != "rerun" and getattr(
                parsed_opts.cli_args, "download", False
            ):
                LOGGER.info(
                    f"{FormatText.BLUE}Requested download of the logs. This might take a minute.{FormatText.END}"
                )

            parsed_data = {
                "request_uuid": task_result.request_uuid,
                "source_compose": task_result.request_source_compose,
                "target_release": task_result.request_target_release,
                "upgrade_path": task_result.request_upgrade_path,
                "plan": task_result.request_plan,
                "plan_filter": task_result.request_plan_filter,
                "created": task_result.request_datetime_created,
                "testsuites": [],
                "overall_result": job_result_overall,
            }

            # Process test suites
            for suite_elem in job_test_suites:
                suite_data = XMLParser._parse_test_suite(
                    suite_elem, skip_pass, task_result
                )
                if suite_data:  # Only add if not filtered out
                    parsed_data["testsuites"].append(suite_data)

            return parsed_data

        except Exception as e:
            LOGGER.error(f"[{task_result.request_uuid}] Error parsing XML: {e}")
            return {
                "request_uuid": task_result.request_uuid,
                "source_compose": task_result.request_source_compose,
                "target_release": task_result.request_target_release,
                "upgrade_path": task_result.request_upgrade_path,
                "plan": task_result.request_plan,
                "plan_filter": task_result.request_plan_filter,
                "created": task_result.request_datetime_created,
                "testsuites": [],
                "error": f"XML parsing error: {e}",
            }

    @staticmethod
    def _parse_test_suite(
        suite_elem, skip_pass: bool, task_result: TaskResult
    ) -> Optional[Dict[str, Any]]:
        """Parse a single test suite element."""
        try:
            testsuite_name = suite_elem.xpath("./@name")[0].split(":")[-1]
            testsuite_result = suite_elem.xpath("./@result")[0].upper()

            # Early exit if skipping passed tests
            if skip_pass and testsuite_result == "PASSED":
                return None

            try:
                testsuite_arch = suite_elem.xpath(
                    "./testing-environment/property[@name='arch']/@value"
                )[0]
            except IndexError:
                testsuite_arch = suite_elem.xpath("./@name")[0].split(":")[-1]

            testsuite_data = {
                "testsuite_name": testsuite_name,
                "testsuite_arch": testsuite_arch,
                "testsuite_result": testsuite_result,
                "testcases": [],
            }

            # Parse test cases
            testcase_elements = suite_elem.xpath("./testcase")
            for testcase_elem in testcase_elements:
                testcase_data = XMLParser._parse_test_case(
                    testcase_elem, skip_pass, task_result, testsuite_name
                )
                if testcase_data:
                    testsuite_data["testcases"].append(testcase_data)

            return testsuite_data

        except Exception as e:
            LOGGER.error(f"Error parsing test suite: {e}")
            return None

    @staticmethod
    def _parse_test_case(
        testcase_elem, skip_pass: bool, task_result: TaskResult, testsuite_name: str
    ) -> Optional[Dict[str, Any]]:
        """Parse a single test case element."""
        try:
            testcase_name = testcase_elem.xpath("./@name")[0]
            testcase_result = testcase_elem.xpath("./@result")[0].upper()

            # Early exit if skipping passed tests
            if skip_pass and testcase_result == "PASSED":
                return None

            testcase_data = {
                "testcase_name": testcase_name,
                "testcase_result": testcase_result,
            }

            # Handle log downloads if requested
            if parsed_opts.cli_args.action != "rerun" and getattr(
                parsed_opts.cli_args, "download", False
            ):
                XMLParser._download_testcase_logs(
                    testcase_elem, task_result, testsuite_name, testcase_name
                )

            return testcase_data

        except Exception as e:
            LOGGER.error(f"Error parsing test case: {e}")
            return None

    @staticmethod
    def _download_testcase_logs(
        testcase_elem, task_result: TaskResult, testsuite_name: str, testcase_name: str
    ):
        """Download logs for a test case if available."""
        try:
            # Get logs directory (validated by operational defaults check)
            logs_directory = parsed_opts.common.get("logs_directory")
            if not logs_directory:
                LOGGER.warning("logs_directory not configured, skipping log download")
                return

            logs_base_directory = logs_directory.rstrip("/")

            testcase_log_url = testcase_elem.xpath(
                './logs/log[@name="testout.log"]/@href'
            )[0]
            # Sanitize log file name to avoid FS issues
            raw_name = (
                f"{task_result.request_source_compose}_{testcase_name.split('/')[-1]}"
            )
            safe_name = "".join(
                ch for ch in raw_name if ch.isalnum() or ch in ("-", "_", ".")
            )
            log_name = f"{safe_name}.log"
            uuid_short = ConcurrentRequestParser._get_short_uuid(
                task_result.request_uuid
            )
            LOGGER.debug(
                f"[{uuid_short}] Downloading the log files for testsuite {testsuite_name} testcase {testcase_name}"
            )

            # Create the log directory path for the request
            log_dir = f"{task_result.request_uuid}_logs"
            log_dir_path = os.path.join(logs_base_directory, log_dir)
            os.makedirs(log_dir_path, exist_ok=True)

            # Create the log directory path for the testsuite
            testsuite_log_dir = testsuite_name.split("/")[-1]
            testsuite_log_dir_path = os.path.join(log_dir_path, testsuite_log_dir)
            os.makedirs(testsuite_log_dir_path, exist_ok=True)

            response = urllib.request.urlopen(testcase_log_url, timeout=30)
            log_data = response.read().decode("utf-8")
            log_file_path = os.path.join(testsuite_log_dir_path, log_name)

            with open(log_file_path, "w") as logfile:
                logfile.write(log_data)

        except IndexError:
            LOGGER.warning(
                f"There is an issue with gathering logs for testsuite {testsuite_name} testcase {testcase_name}."
            )
        except Exception as e:
            LOGGER.error(f"Error downloading logs for testcase {testcase_name}: {e}")


def parse_request_xunit_concurrent(
    request_url_list: Optional[List[str]] = None,
    tasks_source: Optional[str] = None,
    skip_pass: bool = False,
) -> Dict[str, Any]:
    """
    Parse request xunit with concurrent requests for better performance.

    Returns:
        Dictionary with parsed results organized by UUID
    """
    from enge.report.__main__ import parse_tasks  # Import from original module

    if request_url_list is None or tasks_source is None:
        parsed_result = parse_tasks()
        request_url_list = parsed_result[0] or []
        # Handle both string and list types for tasks_source
        raw_tasks_source = parsed_result[1]
        if isinstance(raw_tasks_source, list):
            tasks_source = str(raw_tasks_source[0]) if raw_tasks_source else ""
        else:
            tasks_source = raw_tasks_source or ""

    if not request_url_list or all(element == "" for element in request_url_list):
        LOGGER.critical("There are no tasks to report for!")
        return {}

    # Use concurrent parser with connection pooling
    with ConcurrentRequestParser(max_workers=10, timeout=30, max_retries=3) as parser:
        task_results = parser.parse_tasks_concurrent(request_url_list)

    # Parse XML results and track what happened to each task
    parsed_dict = {}
    xml_parser = XMLParser()
    skipped_due_to_pass = 0

    for task_result in task_results:
        parsed_data = xml_parser.parse_xml_results(task_result, skip_pass)

        # Check if this was skipped due to --skip-pass
        if (
            parsed_data
            and "skipped" in parsed_data
            and "PASSED result skipped" in str(parsed_data.get("skipped", ""))
        ):
            skipped_due_to_pass += 1
            continue

        # Only add tasks that have actual test suites to report
        # Skip tasks with errors, no XML, or that were filtered out
        if (
            parsed_data
            and parsed_data.get("testsuites")
            and len(parsed_data["testsuites"]) > 0
            and "error" not in parsed_data
            and "skipped" not in parsed_data
        ):
            parsed_dict[task_result.request_uuid] = parsed_data

    # Return parsed results

    # Add enhanced summary information
    input_count = len(request_url_list)
    total_processed = len(task_results)
    tasks_with_data = len(parsed_dict)

    # Count different types of skipped tasks
    canceled_tasks = sum(
        1 for tr in task_results if tr.should_skip and tr.skip_reason == "canceled"
    )
    queued_tasks = sum(
        1 for tr in task_results if tr.should_skip and tr.skip_reason == "queued"
    )
    running_tasks = sum(
        1 for tr in task_results if tr.should_skip and tr.skip_reason == "running"
    )

    total_skipped = canceled_tasks + queued_tasks + running_tasks + skipped_due_to_pass
    failed_tasks = total_processed - tasks_with_data - total_skipped

    LOGGER.info(f"{FormatText.DIM}{'─' * 60}{FormatText.END}")
    LOGGER.info(f"{FormatText.BLUE}Summary:{FormatText.END}")
    LOGGER.info(f"   {'Task IDs provided:':<28}{input_count:>3}")
    LOGGER.info(f"   {'Tasks processed:':<28}{total_processed:>3}")
    LOGGER.info(
        f"   {FormatText.GREEN}{'Tasks with reportable data:':<28}{tasks_with_data:>3}{FormatText.END}"
    )
    LOGGER.info(
        f"   {FormatText.YELLOW}{'Skipped:':<28}{total_skipped:>3}{FormatText.END}"
    )

    # Build the breakdown display for skipped tasks
    skip_categories = []
    if canceled_tasks > 0:
        skip_categories.append(("Canceled", canceled_tasks))
    if queued_tasks > 0:
        skip_categories.append(("Queued", queued_tasks))
    if running_tasks > 0:
        skip_categories.append(("Running", running_tasks))
    if skipped_due_to_pass > 0:
        skip_categories.append(("Passed (--skip-pass)", skipped_due_to_pass))

    # Display the breakdown
    if skip_categories:
        for i, (category, count) in enumerate(skip_categories):
            if i == len(skip_categories) - 1:
                # Last item
                LOGGER.info(
                    f"     {FormatText.DIM}{'└─ ' + category + ':':<26}{count:>3}{FormatText.END}"
                )
            else:
                # Not last item
                LOGGER.info(
                    f"     {FormatText.DIM}{'├─ ' + category + ':':<26}{count:>3}{FormatText.END}"
                )

    LOGGER.info(
        f"   {FormatText.RED}{'No reportable data:':<28}{failed_tasks:>3}{FormatText.END}"
    )
    LOGGER.info(f"{FormatText.DIM}{'─' * 60}{FormatText.END}")

    return parsed_dict


def get_return_value():
    """Get the current return value."""
    global RETURN_VALUE
    return RETURN_VALUE
