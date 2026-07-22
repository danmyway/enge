import copy
import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, Iterable, List
from enge.utils.http_client import http_get
from rich.table import Table
from rich.markup import escape
from rich import box

from enge.dispatch.pin_compose import repin_compose
from enge.dispatch.tf_send_request import SubmitTest
from enge.report.__main__ import parse_request_xunit
from enge.utils.task_resolver import parse_tasks_with_map
from enge.utils.errors import ValidationError
from enge.utils.manifest import ManifestReader
from enge.utils.app_context import AppContext
from enge.utils.globals import REQUEST_TIMEOUT_DEFAULT, RP_COMPATIBLE_EVENT
from enge.utils.console import console

logger = logging.getLogger(__name__)

# Don't print unnecessary log messages from the report module
report_logger = logging.getLogger("enge.report")
report_logger.setLevel(logging.WARNING)


def _unique_preserve(values: Iterable[str]) -> List[str]:
    """Return values with duplicates removed while preserving their order."""
    seen = set()
    ordered: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _resolve_archive_sources(
    task_source: Optional[Any],
    archive_default_path: Optional[str],
    cli_args: Any,
) -> List[Path]:
    """
    Resolve archive file paths that were used as rerun inputs.

    Args:
        task_source: Metadata returned by parse_tasks (file list, filenames, or None)
        archive_default_path: Configured archive directory path

    Returns:
        List of Path objects pointing to archive files associated with the rerun input.
    """
    paths: List[Path] = []

    file_args = getattr(cli_args, "file", None) or []
    for entry in file_args:
        if entry:
            paths.append(Path(entry).expanduser())

    get_tag_args = getattr(cli_args, "get_tag", None)
    if get_tag_args and task_source and archive_default_path:
        if isinstance(task_source, str):
            filenames = [task_source]
        else:
            filenames = list(task_source)
        archive_root = Path(archive_default_path).expanduser()
        for filename in filenames:
            if filename:
                paths.append(archive_root / filename)

    return paths


def _extract_tags_from_filename(path: Path) -> List[str]:
    """Extract appended tags from an archive filename."""
    name = path.name
    if "." not in name:
        return []
    _, *tag_parts = name.split(".")
    return [part for part in tag_parts if part]


def _resolve_parent_lineage(
    task_source: Optional[Any], runs_dir: str
) -> "tuple[Optional[str], List[str]]":
    """Derive parent_run_id and inherited tags from the task resolution source.

    When tasks were resolved from a single manifest (default latest or --run),
    returns (parent_run_id, parent_tags).  Otherwise returns (None, []).
    """
    if not isinstance(task_source, str) or not task_source.startswith("manifest:"):
        return None, []

    source_id = task_source[len("manifest:") :]
    if source_id in ("filter", "latest"):
        return None, []

    try:
        manifest = ManifestReader.get_run(Path(runs_dir), source_id)
    except Exception:
        return None, []

    return manifest.get("run_id", source_id), manifest.get("tags", [])


def _build_parent_request_index(
    parent_run_id: Optional[str], runs_dir: str
) -> Dict[str, Dict[str, Any]]:
    """Index the parent manifest's requests by task_id, for inheriting
    set/tier/target_compose onto rerun request entries.

    Returns an empty index whenever inheritance cannot proceed (no parent,
    or the parent manifest cannot be loaded) so callers fall back to None
    for those fields, matching current behavior.
    """
    if not parent_run_id:
        return {}
    try:
        manifest = ManifestReader.get_run(Path(runs_dir), parent_run_id)
    except (ValidationError, OSError, json.JSONDecodeError) as e:
        logger.debug(
            "Could not load parent manifest %s for rerun field inheritance: %s",
            parent_run_id,
            e,
        )
        return {}
    return {
        req["task_id"]: req
        for req in manifest.get("requests", [])
        if req.get("task_id")
    }


class RerunJobs:
    """
    A class to handle the identification, filtering, and re-submission of tasks for re-run based on results
    obtained from the Testing Farm API.

    Attributes:
        rerun_payloads (list): A list to store payloads prepared for re-running tasks.
        parsed_dict (dict): Stores the parsed results from test plans, organized by their UUIDs.
        processed_data (dict): Stores filtered and processed task data for re-run.
        rerun_uuids (list): Stores the UUIDs of tasks that qualify for re-run.
        req_url_list (list): URLs of requested tasks for analysis.
        task_source (str): The source from which tasks were retrieved.
    """

    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.rerun_payloads = []
        self.parsed_dict = {}
        self.processed_data = {}
        self.rerun_uuids = []

        # Retrieve task URLs and their source from the report module
        self.req_url_list, self.task_source, self.uuid_source_map = (
            parse_tasks_with_map(ctx)
        )

    def qualify_results(self):
        """
        Parse the task results, filter them based on specified CLI arguments (e.g., 'ERROR' or 'FAILED'),
        and prepare the data for re-run.
        """
        logger.info(
            "Looking for tasks from the requested sources, this may take a while."
        )

        # Parse test results from the specified URLs
        for i in self.req_url_list:
            logger.debug(f"Parsing the payload from: {i}")
        self.parsed_dict = parse_request_xunit(
            self.req_url_list, self.task_source, False, ctx=self.ctx
        )

        for key, details in self.parsed_dict.items():
            # Determine the result filter based on CLI arguments
            result_filter = [
                "SKIPPED",
                "PASSED",
            ]  # We want to filter out skipped and passed plans

            if self.ctx.cli_args.error:
                # Keep only ERROR results (exclude FAILED)
                result_filter.append("FAILED")
            elif self.ctx.cli_args.fail:
                # Keep only FAILED results (exclude ERROR)
                result_filter.append("ERROR")

            # Filter test suites based on the result filter and collect failed tests per suite
            filtered_suites = []
            suite_test_mapping = {}  # Map suite name to list of failed test names
            undefined_suites = []  # Plans with UNDEFINED result and no failed tests

            for suite in details["testsuites"]:
                # Skip suites that don't match result filter
                if suite["testsuite_result"] in result_filter:
                    continue

                # Extract failed testcase names from this suite
                failed_test_names = []
                for testcase in suite.get("testcases", []):
                    # Collect testcase names that failed/errored (not skipped or passed)
                    if testcase["testcase_result"] not in ["SKIPPED", "PASSED"]:
                        # Split on '::' and take the last part, then suffix with $
                        test_name = testcase["testcase_name"].split("::")[-1] + "$"
                        failed_test_names.append(test_name)

                # Store mapping of suite to its failed tests
                suite_name = suite["testsuite_name"]
                suite_test_mapping[suite_name] = failed_test_names
                filtered_suites.append(suite)

                # Track UNDEFINED plans without testcase data so that we can
                # rerun them separately without a restrictive test filter.
                if (
                    suite.get("testsuite_result") == "UNDEFINED"
                    and not failed_test_names
                ):
                    undefined_suites.append(f"{suite_name}$")

            # Process and store data for filtered test suites
            if filtered_suites:
                # Suffix the suite name with $ to indicate that it is an end of a string match
                suite_names = "|".join(
                    suite["testsuite_name"] + "$" for suite in filtered_suites
                )
                # Store suite names, compose, and suite-to-tests mapping
                self.processed_data[key] = (
                    suite_names,
                    details["source_compose"],
                    suite_test_mapping,
                    _unique_preserve(undefined_suites),
                    self.uuid_source_map.get(key),
                )
                self.rerun_uuids.append(key)

        # Identify tasks that were not parsed (Error, No XML, Canceled, etc.)
        for req_url in self.req_url_list:
            # Extract UUID (simple split, assuming valid URL from parse_tasks)
            uuid = req_url.rstrip("/").split("/")[-1]

            if uuid not in self.parsed_dict:
                # Task is missing from parsed results -> Fallback candidate
                logger.debug(
                    f"Task {uuid} missing from parsed results, adding as fallback candidate."
                )
                self.processed_data[uuid] = (
                    None,  # No suite names
                    None,  # No compose
                    None,  # No mapping
                    None,  # No undefined filters
                    self.uuid_source_map.get(uuid) or self.uuid_source_map.get(req_url),
                )
                self.rerun_uuids.append(uuid)

        # Log and display qualifying plans for a re-run
        if self.processed_data:
            info_table = Table(box=box.ROUNDED, show_lines=True)
            info_table.add_column("Original Request")
            info_table.add_column("Source Compose Name")
            info_table.add_column("Arch")
            info_table.add_column("Re-run Plans")
            info_table.add_column("Re-run Tests")

            logger.info("The following plans qualify for a re-run:")
            for req in self.processed_data.keys():
                data = self.processed_data[req]
                # Handle fallback entries (data[0] is None)
                if data[0] is None:
                    info_table.add_row(
                        escape(req),
                        "N/A",
                        "Unknown",
                        "FALLBACK (Original Filter)",
                        "",
                        end_section=True,
                    )
                    continue

                suite_names_list = [s.replace("$", "") for s in data[0].split("|")]
                rerun_source_compose = data[1]
                suite_test_mapping = data[2] if len(data) > 2 and data[2] else {}
                rerun_arch = self.parsed_dict[req]["testsuites"][0]["testsuite_arch"]

                # Build rows for plans and tests
                for i, suite_name in enumerate(suite_names_list):
                    failed_tests = suite_test_mapping.get(suite_name, [])
                    is_last_plan = i == len(suite_names_list) - 1

                    # Only show request details on the very first row
                    req_col = req if i == 0 else ""
                    comp_col = rerun_source_compose if i == 0 else ""
                    arch_col = rerun_arch if i == 0 else ""

                    # Add Plan row
                    # Divider needed only if this is the last plan AND no tests follow
                    is_plan_row_final = is_last_plan and not failed_tests
                    info_table.add_row(
                        escape(req_col),
                        escape(comp_col),
                        escape(arch_col),
                        escape(suite_name),
                        "",
                        end_section=is_plan_row_final,
                    )

                    # Add Test rows
                    if failed_tests:
                        for j, test_name in enumerate(failed_tests):
                            display_name = test_name.rstrip("$")
                            is_last_test = j == len(failed_tests) - 1
                            # Divider needed if this is the last test of the last plan
                            is_test_row_final = is_last_plan and is_last_test

                            info_table.add_row(
                                "",
                                "",
                                "",
                                "",
                                escape(display_name),
                                end_section=is_test_row_final,
                            )
            console.print(info_table)
            if self.ctx.cli_args.dryrun:
                return
        else:
            logger.info("None of the provided tasks qualify for a re-run.")
            logger.debug(
                "All the results seem to be PASSing, time to celebrate! \U0001f389"
            )

    def drop_payload_keys(self, keys_to_drop: list) -> None:
        """
        Drop additional keys from all rerun payloads.

        Args:
            keys_to_drop: List of key paths to drop. Supports dot notation for nested keys.
                         For environments, use "environments.0.key" (there's always exactly one environment).
                         (e.g., ["some_key", "nested.key", "environments.0.tmt.context.some_key"])
        """
        for payload in self.rerun_payloads:
            for key_path in keys_to_drop:
                self._drop_nested_key(payload, key_path)

    def drop_payload_keys_by_pattern(self, key_path: str, pattern: str) -> None:
        """
        Drop keys matching a pattern from a specific path in all rerun payloads.

        Args:
            key_path: Path to the dictionary containing keys to filter (e.g., "environments.0.variables").
                     Supports dot notation and array indices.
            pattern: Pattern to match keys against. Can be:
                    - Prefix pattern: "PACKIT_*" (matches keys starting with "PACKIT_")
                    - Suffix pattern: "*_suffix" (matches keys ending with "_suffix")
                    - Contains pattern: "*middle*" (matches keys containing "middle")
                    - Exact pattern: "exact_key" (matches exact key name)
        """
        for payload in self.rerun_payloads:
            target_dict = self._get_nested_value(payload, key_path)
            if isinstance(target_dict, dict):
                keys_to_drop = self._match_keys(target_dict.keys(), pattern)
                for key in keys_to_drop:
                    target_dict.pop(key, None)

    def _get_nested_value(self, payload: Dict[str, Any], key_path: str) -> Any:
        """Get a nested value from payload, supporting dot notation and array indices."""
        keys = key_path.split(".")
        current = payload

        for key in keys:
            if key.isdigit():
                idx = int(key)
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return None

        return current

    def _match_keys(self, keys: Any, pattern: str) -> list:
        """
        Match keys against a pattern.

        Args:
            keys: Iterable of key names
            pattern: Pattern string (supports * as wildcard)

        Returns:
            List of matching keys
        """
        if not pattern:
            return []

        matches = []

        # Handle different pattern types
        if pattern.startswith("*") and pattern.endswith("*"):
            # Contains pattern: *middle*
            substring = pattern[1:-1]
            matches = [k for k in keys if substring in k]
        elif pattern.startswith("*"):
            # Suffix pattern: *_suffix
            suffix = pattern[1:]
            matches = [k for k in keys if k.endswith(suffix)]
        elif pattern.endswith("*"):
            # Prefix pattern: PACKIT_*
            prefix = pattern[:-1]
            matches = [k for k in keys if k.startswith(prefix)]
        else:
            # Exact match (no wildcard)
            matches = [k for k in keys if k == pattern]

        return matches

    def _drop_nested_key(self, payload: Dict[str, Any], key_path: str) -> None:
        """
        Drop a key from payload, supporting dot notation for nested keys.
        Supports array indices (e.g., "environments.0.key" for the single environment).
        """
        keys = key_path.split(".")
        current = payload

        for key in keys[:-1]:
            # Handle array index
            if key.isdigit():
                idx = int(key)
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return  # Invalid index or not an array
            else:
                # Regular dict key
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return  # Path doesn't exist, nothing to drop

        # Drop the final key
        final_key = keys[-1]
        if isinstance(current, dict):
            current.pop(final_key, None)

    def overwrite_payload_values(self, updates: Dict[str, Any]) -> None:
        """
        Overwrite values in all rerun payloads.

        Args:
            updates: Dictionary of key paths to new values. Supports dot notation for nested keys.
                    For environments, use "environments.0.key" (there's always exactly one environment supported for rerun).
                    (e.g., {"some_key": "value", "environments.0.tmt.context.key": "value"})
        """
        for payload in self.rerun_payloads:
            for key_path, value in updates.items():
                self._set_nested_key(payload, key_path, value)

    def _set_nested_key(
        self, payload: Dict[str, Any], key_path: str, value: Any
    ) -> None:
        """
        Set a value in payload, supporting dot notation for nested keys and array indices.
        Creates nested dicts if needed. Supports array indices (e.g., "environments.0.key" for the single environment).
        """
        keys = key_path.split(".")
        current = payload

        for i, key in enumerate(keys[:-1]):
            # Handle array index
            if key.isdigit():
                idx = int(key)
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return  # Invalid index
            else:
                # Regular dict key
                if key not in current or not isinstance(current[key], (dict, list)):
                    # Create dict if next key is not a digit (not an array index)
                    if i + 1 < len(keys) - 1 and not keys[i + 1].isdigit():
                        current[key] = {}
                    else:
                        return  # Can't create array
                current = current[key]

        # Set the final value
        final_key = keys[-1]
        if isinstance(current, dict):
            current[final_key] = value

    def build_rerun_payloads(self, uuids):
        """
        Build re-run payloads for the qualifying tasks by retrieving detailed information
        from the Testing Farm API.

        Args:
            uuids (list): List of UUIDs for tasks that qualify for re-run.

        Returns:
            list: A list of filtered payloads ready for re-submission.

        Raises:
            ValidationError: If any request has multiple environments (not supported for rerun).
        """
        self.rerun_payloads = []

        for request in uuids:
            # Fetch the task details from the API
            response = http_get(
                os.path.join(
                    str(self.ctx.testing_farm_endpoint.api_endpoint_url), request
                ),
                timeout=REQUEST_TIMEOUT_DEFAULT,
            )
            request_details = response.json()

            match_uuid = request_details.get("id")
            environments_requested = request_details.get("environments_requested", [])

            # Fail fast if multiple environments detected
            if len(environments_requested) > 1:
                from enge.utils.errors import ValidationError

                logger.critical(
                    f"Rerun of multi-environment requests is not supported. "
                    f"Request {match_uuid} has {len(environments_requested)} environments."
                )
                logger.critical(
                    "Please rerun requests with only a single environment, or schedule a job for each environment separately."
                )
                raise ValidationError(
                    f"Multi-environment rerun not supported: request {match_uuid} has {len(environments_requested)} environments"
                )

            if len(environments_requested) == 0:
                logger.warning(
                    f"No environments found in request {match_uuid}, skipping"
                )
                continue

            processed_entry = self.processed_data.get(match_uuid)
            undefined_plan_filters: List[str] = []
            source_path = None
            has_specific_plans = False

            if processed_entry:
                if processed_entry[0] is not None:
                    has_specific_plans = True
                if len(processed_entry) > 3 and processed_entry[3]:
                    undefined_plan_filters = processed_entry[3]
                if len(processed_entry) > 4:
                    source_path = processed_entry[4]

            # Handle Fallback vs Specific Rerun
            if not has_specific_plans:
                state = request_details.get("state", "").lower()
                if state in ["queued", "running"]:
                    logger.warning(f"Request {match_uuid} is {state}, skipping rerun.")
                    continue

                logger.info(
                    f"Request {match_uuid} has no parseable results (state: {state}). "
                    "Rerunning with original plan filter (fallback)."
                )
                # Fallback: Use original payload as is (no specific plan/test filter)

            elif processed_entry:
                # Update plan name and test name with filtered data for rerun
                # Ensure test.fmf structure exists
                if "test" not in request_details:
                    request_details["test"] = {}
                if "fmf" not in request_details["test"]:
                    request_details["test"]["fmf"] = {}

                request_details["test"]["fmf"]["name"] = processed_entry[0]

                # Ensure plan_filter is removed so that specific plan selection works
                request_details["test"]["fmf"]["plan_filter"] = None

                # Set test_name if we have failed test names from xunit results
                if len(processed_entry) > 2 and processed_entry[2]:
                    suite_test_mapping = processed_entry[2]
                    # Collect all failed test names from all suites
                    all_failed_tests = []
                    for suite_name, test_names in suite_test_mapping.items():
                        all_failed_tests.extend(test_names)
                    if all_failed_tests:
                        request_details["test"]["fmf"]["test_name"] = "|".join(
                            all_failed_tests
                        )

            # Remove unnecessary keys from the payload
            keys_to_remove = {
                "id",
                "user_id",
                "token_id",
                "notes",
                "result",
                "run",
                "user",
                "queued_time",
                "run_time",
                "created",
                "updated",
                "state",
            }
            filtered_payload = {
                k: v for k, v in request_details.items() if k not in keys_to_remove
            }
            filtered_payload["_enge_source_path"] = source_path
            filtered_payload["_original_uuid"] = match_uuid

            # Update environment key for re-run compatibility
            filtered_payload["environments"] = filtered_payload.pop(
                "environments_requested"
            )

            # Re-pin compose to the latest available nightly
            env = filtered_payload["environments"][0]
            original_compose = env.get("os", {}).get("compose")
            composes_prod_url = self.ctx.config.get("testing_farm", {}).get(
                "composes_prod_url", ""
            )
            env["os"]["compose"] = repin_compose(original_compose, composes_prod_url)

            # Append the filtered payload for re-run
            self.rerun_payloads.append(filtered_payload)

            # Add a plan-only rerun when UNDEFINED plans would be skipped
            test_filters = (
                filtered_payload.get("test", {}).get("fmf", {}).get("test_name")
            )
            if undefined_plan_filters and test_filters:
                plan_only_payload = copy.deepcopy(filtered_payload)
                plan_only_payload.setdefault("test", {}).setdefault("fmf", {})
                plan_only_payload["test"]["fmf"]["name"] = "|".join(
                    _unique_preserve(undefined_plan_filters)
                )
                plan_only_payload["test"]["fmf"].pop("test_name", None)
                logger.info(
                    "Request %s includes %d UNDEFINED plan(s) with no testcase "
                    "information; submitting an additional plan-only rerun.",
                    match_uuid,
                    len(undefined_plan_filters),
                )
                self.rerun_payloads.append(plan_only_payload)

        return self.rerun_payloads


def _get_next_rerun_tag(tags: List[str]) -> str:
    """
    Determine the next rerun tag based on existing tags.
    No rerun tag -> rerun
    rerun -> rerun1
    rerun1 -> rerun2
    """
    max_index = -1
    has_base_rerun = False

    for tag in tags:
        if tag == "rerun":
            has_base_rerun = True
        elif tag.startswith("rerun") and tag[5:].isdigit():
            index = int(tag[5:])
            if index > max_index:
                max_index = index

    if max_index > -1:
        return f"rerun{max_index + 1}"
    if has_base_rerun:
        return "rerun1"
    return "rerun"


def _resolve_set_name_from_parent(
    parent_run_id: Optional[str],
    original_uuid: Optional[str],
    runs_dir: str,
) -> Optional[str]:
    """Derive set_name for a rerun request from the parent manifest.

    Used solely to stamp the ``set`` launch attribute — naming uses the
    rerun payload's own tmt context, not the parent manifest.
    """
    if not parent_run_id or not original_uuid:
        return None
    try:
        manifest = ManifestReader.get_run(Path(runs_dir), parent_run_id)
    except Exception:
        return None
    for req in manifest.get("requests", []):
        if req.get("task_id") == original_uuid:
            return req.get("set")
    return None


def _create_rerun_launch_for_payload(
    payload: Dict[str, Any],
    is_dryrun: bool,
    ctx: AppContext,
    run_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    original_uuid: Optional[str] = None,
) -> Optional[str]:
    """Create a ReportPortal launch for a single rerun payload."""
    environments = payload.get("environments", [])
    if not environments:
        return None

    env = environments[0]
    tmt_context = env.get("tmt", {}).get("context", {})
    event_name = tmt_context.get("event")

    if not event_name or event_name not in RP_COMPATIBLE_EVENT:
        return None

    tier = tmt_context.get("tier")
    arch = env.get("arch")
    upgrade_path = tmt_context.get("upgrade_path")

    from enge.utils.source_target_parser import _generate_auto_launch_name

    launch_name = _generate_auto_launch_name(
        architecture=arch,
        tier=tier,
        upgrade_path=upgrade_path,
    )

    set_name = _resolve_set_name_from_parent(
        parent_run_id, original_uuid, ctx.manifest_runs_dir
    )

    rerun_context = {
        "tier": tier,
        "architecture": arch,
        "upgrade_path": upgrade_path,
        "set_name": set_name,
    }

    from enge.reportportal.__main__ import ReportPortalLaunch

    if is_dryrun:
        try:
            rp_launch = ReportPortalLaunch(ctx)
            payload_data = rp_launch.generate_launch_payload(
                name=launch_name,
                context=rerun_context,
                tmt_context=tmt_context,
                extra_tags=["rerun"],
            )

            import json
            from enge.utils import redact_sensitive

            logger.info("DRY RUN | ReportPortal launch payload that would be sent:")
            print(json.dumps(redact_sensitive(payload_data), indent=4))
            return "dryrun_placeholder"
        except Exception as e:
            logger.warning(f"DRY RUN | Could not generate ReportPortal payload: {e}")
            return None
    else:
        try:
            rp_launch = ReportPortalLaunch(ctx)
            launch_uuid = rp_launch.create_launch(
                name=launch_name,
                context=rerun_context,
                tmt_context=tmt_context,
                extra_tags=["rerun"],
                run_id=run_id,
                parent_run_id=parent_run_id,
            )
            return launch_uuid
        except Exception as e:
            logger.error(f"Failed to create ReportPortal launch: {e}")
            return None


def main(ctx: AppContext):
    """
    Main function to qualify tasks for re-run, build their re-run payloads,
    and submit the requests via the Testing Farm API.
    """
    jobs = RerunJobs(ctx)

    # Qualify tasks for re-run
    jobs.qualify_results()

    # Build re-run payloads (extract data from original requests)
    jobs.build_rerun_payloads(jobs.rerun_uuids)

    # Common payload cleanup before processing
    jobs.overwrite_payload_values(
        {
            "environments.0.tmt.context.initiator": "enge",
            "environments.0.tmt.context.trigger": "rerun",
        }
    )
    jobs.drop_payload_keys_by_pattern("environments.0.variables", "PACKIT_*")
    jobs.drop_payload_keys_by_pattern("environments.0.variables", "CI_*")
    jobs.drop_payload_keys(["environments.0.tmt.context.uniq_id"])

    # Set up the submitter (only for API key and headers)
    submit = SubmitTest(ctx)
    base_tags = submit.set_tag or []

    submit.api_key = ctx.testing_farm.get("api_key")

    # Build authorization header
    req_header = {"Authorization": f"Bearer {submit.api_key}"}

    is_dryrun = getattr(ctx.cli_args, "dryrun", False)

    from enge.utils.manifest import ManifestWriter
    from enge.utils.ulid import generate_ulid
    import sys

    parent_run_id, inherited_tags = _resolve_parent_lineage(
        jobs.task_source, ctx.manifest_runs_dir
    )
    parent_request_index = _build_parent_request_index(
        parent_run_id, ctx.manifest_runs_dir
    )

    manifest_writer = ManifestWriter(
        run_id=generate_ulid(),
        command="rerun",
        argv=sys.argv,
        tags=_unique_preserve([*inherited_tags, *base_tags, "rerun"]),
        parent_run_id=parent_run_id,
    )

    for i, payload in enumerate(jobs.rerun_payloads):
        original_uuid = payload.pop("_original_uuid", None)
        if original_uuid:
            jobs._set_nested_key(
                payload, "environments.0.tmt.context.rerun_of", original_uuid
            )

        launch_uuid = _create_rerun_launch_for_payload(
            payload,
            is_dryrun,
            ctx,
            run_id=None if is_dryrun else manifest_writer.run_id,
            parent_run_id=parent_run_id,
            original_uuid=original_uuid,
        )

        if launch_uuid:
            if launch_uuid == "dryrun_placeholder":
                uniq_id_value = "00000000-0000-0000-0000-000000000000"
            else:
                uniq_id_value = launch_uuid

            jobs._set_nested_key(
                payload, "environments.0.tmt.context.uniq_id", uniq_id_value
            )

            from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX
            from enge.utils.source_target_parser import (
                generate_reportportal_environment_variables,
            )

            rp_config_vars = generate_reportportal_environment_variables(
                ctx.config,
                cli_args=ctx.cli_args,
            )

            rp_base_vars = {
                k: v
                for k, v in rp_config_vars.items()
                if not (k.endswith("LAUNCH") or k.endswith("LAUNCH_DESCRIPTION"))
            }

            upload_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}UPLOAD_TO_LAUNCH"
            if launch_uuid == "dryrun_placeholder":
                rp_base_vars[upload_key] = "00000000-0000-0000-0000-000000000000"
            else:
                rp_base_vars[upload_key] = launch_uuid

            if payload.get("environments") and len(payload["environments"]) > 0:
                env = payload["environments"][0]
                if "tmt" not in env:
                    env["tmt"] = {}
                env["tmt"]["environment"] = rp_base_vars

        payload.pop("_enge_source_path", None)

        request_data = {}

        test_fmf = payload.get("test", {}).get("fmf", {})
        if test_fmf:
            plan_name = test_fmf.get("name", "")
            if plan_name:
                plan_parts = [
                    p.rstrip("$") for p in plan_name.split("|") if p.rstrip("$")
                ]
                request_data["plan"] = (
                    ", ".join(plan_parts) if plan_parts else plan_name.rstrip("$")
                )

            test_name = test_fmf.get("test_name", "")
            if test_name:
                test_parts = [
                    t.rstrip("$") for t in test_name.split("|") if t.rstrip("$")
                ]
                request_data["test_name"] = (
                    ", ".join(test_parts) if test_parts else test_name.rstrip("$")
                )

            request_data["planfilter"] = test_fmf.get("plan_filter")
            request_data["testfilter"] = test_fmf.get("test_filter")
            request_data["tests_git_url"] = test_fmf.get("url")
            request_data["tests_git_ref"] = test_fmf.get("ref")

        if payload.get("environments") and len(payload["environments"]) > 0:
            env = payload["environments"][0]

            compose = env.get("os", {}).get("compose")
            if compose:
                request_data["compose"] = compose

            artifacts = env.get("artifacts", [])
            if artifacts:
                request_data["artifacts"] = artifacts

            arch = env.get("arch")
            if arch:
                request_data["architectures"] = [arch]

            tmt = env.get("tmt", {})
            if tmt:
                request_data["tmt_context"] = tmt.get("context", {})
                request_data["environment_variables"] = env.get("variables", {})
                request_data["event"] = tmt.get("context", {}).get("event")

        submit.populate_from_request_data(request_data)

        submit.send_request(payload, req_header)

        task_id = None
        if submit.log_artifact_url:
            task_id = submit.log_artifact_url.rsplit("/", 1)[-1]
        if task_id and not is_dryrun:
            parent_entry = parent_request_index.get(original_uuid, {})
            env_vars = request_data.get("environment_variables") or {}
            artifacts = request_data.get("artifacts") or []
            manifest_writer.add_request(
                task_id,
                set_name=parent_entry.get("set"),
                tier=parent_entry.get("tier"),
                arch=(
                    request_data.get("architectures", [None])[0]
                    if request_data.get("architectures")
                    else None
                ),
                plan=request_data.get("plan"),
                source_compose=request_data.get("compose"),
                target_compose=parent_entry.get("target_compose"),
                artifacts_url=submit.log_artifact_url,
                launch_uuid=launch_uuid,
                rerun_of=original_uuid,
                source=env_vars.get("SOURCE_RELEASE"),
                target=env_vars.get("TARGET_RELEASE"),
                git_ref=request_data.get("tests_git_ref"),
                event=request_data.get("event"),
                build_references=[a["id"] for a in artifacts if a.get("id")],
            )
            manifest_writer.flush(
                Path(ctx.manifest_runs_dir), Path(ctx.manifest_latest)
            )
