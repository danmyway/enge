import copy
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, Iterable, List
from datetime import datetime

from enge.utils.http_client import http_get
from prettytable import PrettyTable

from enge.dispatch.tf_send_request import SubmitTest
from enge.report.__main__ import parse_tasks_with_map, parse_request_xunit
from enge.utils.opt_manager import parsed_opts
from enge.utils.globals import REQUEST_TIMEOUT_DEFAULT, RP_COMPATIBLE_EVENT
from enge.utils import FormatText
from enge.utils.nested_dict import (
    get_nested_value,
    set_nested_key,
    drop_nested_key,
    match_keys,
)

colorize = FormatText()

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


def _collect_inherited_tags(
    task_source: Optional[Any],
    cli_args: Optional[Any] = None,
    archive_default_path: Optional[str] = None,
) -> List[str]:
    """
    Collect tags from archive files referenced by the rerun command.

    Returns:
        Ordered list of inherited tags with the 'rerun' marker appended when applicable.
    """
    archive_paths = _resolve_archive_sources(
        task_source,
        (
            archive_default_path
            if archive_default_path is not None
            else getattr(parsed_opts, "archive_tasks_default", None)
        ),
        cli_args or parsed_opts.cli_args,
    )
    if not archive_paths:
        return []

    inherited: List[str] = []
    for archive_path in archive_paths:
        inherited.extend(_extract_tags_from_filename(archive_path))

    inherited.append("rerun")
    tags = _unique_preserve(inherited)

    if tags:
        logger.debug("Inheriting archive tags for rerun: %s", tags)

    return tags


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

    def __init__(self):
        self.rerun_payloads = []
        self.parsed_dict = {}
        self.processed_data = {}
        self.rerun_uuids = []

        # Retrieve task URLs and their source from the report module
        self.req_url_list, self.task_source, self.uuid_source_map = (
            parse_tasks_with_map()
        )

    # ------------------------------------------------------------------
    # qualify_results and its helpers
    # ------------------------------------------------------------------

    def _filter_suites_for_uuid(self, key: str, details: dict) -> None:
        """Filter test suites for a single UUID and store qualifying data."""
        result_filter = ["SKIPPED", "PASSED"]
        if parsed_opts.cli_args.error:
            result_filter.append("FAILED")
        elif parsed_opts.cli_args.fail:
            result_filter.append("ERROR")

        filtered_suites = []
        suite_test_mapping: Dict[str, List[str]] = {}
        undefined_suites: List[str] = []

        for suite in details["testsuites"]:
            if suite["testsuite_result"] in result_filter:
                continue

            failed_test_names = [
                testcase["testcase_name"].split("::")[-1] + "$"
                for testcase in suite.get("testcases", [])
                if testcase["testcase_result"] not in ["SKIPPED", "PASSED"]
            ]

            suite_name = suite["testsuite_name"]
            suite_test_mapping[suite_name] = failed_test_names
            filtered_suites.append(suite)

            if suite.get("testsuite_result") == "UNDEFINED" and not failed_test_names:
                undefined_suites.append(f"{suite_name}$")

        if filtered_suites:
            suite_names = "|".join(
                suite["testsuite_name"] + "$" for suite in filtered_suites
            )
            self.processed_data[key] = (
                suite_names,
                details["source_compose"],
                suite_test_mapping,
                _unique_preserve(undefined_suites),
                self.uuid_source_map.get(key),
            )
            self.rerun_uuids.append(key)

    def _add_unparsed_fallbacks(self) -> None:
        """Add tasks that were not parseable as fallback rerun candidates."""
        for req_url in self.req_url_list:
            uuid = req_url.rstrip("/").split("/")[-1]
            if uuid not in self.parsed_dict:
                logger.debug(
                    f"Task {uuid} missing from parsed results, adding as fallback candidate."
                )
                self.processed_data[uuid] = (
                    None,
                    None,
                    None,
                    None,
                    self.uuid_source_map.get(uuid) or self.uuid_source_map.get(req_url),
                )
                self.rerun_uuids.append(uuid)

    def _display_qualification_table(self) -> None:
        """Print a summary table of plans qualifying for rerun."""
        info_table = PrettyTable()
        info_table.field_names = [
            "Original Request",
            "Source Compose Name",
            "Arch",
            "Re-run Plans",
            "Re-run Tests",
        ]

        logger.info("The following plans qualify for a re-run:")
        for req, data in self.processed_data.items():
            if data[0] is None:
                info_table.add_row(
                    [req, "N/A", "Unknown", "FALLBACK (Original Filter)", ""],
                    divider=True,
                )
                continue

            suite_names_list = [s.replace("$", "") for s in data[0].split("|")]
            rerun_source_compose = data[1]
            suite_test_mapping = data[2] if len(data) > 2 and data[2] else {}
            rerun_arch = self.parsed_dict[req]["testsuites"][0]["testsuite_arch"]

            for i, suite_name in enumerate(suite_names_list):
                failed_tests = suite_test_mapping.get(suite_name, [])
                is_last_plan = i == len(suite_names_list) - 1

                req_col = req if i == 0 else ""
                comp_col = rerun_source_compose if i == 0 else ""
                arch_col = rerun_arch if i == 0 else ""

                info_table.add_row(
                    [req_col, comp_col, arch_col, suite_name, ""],
                    divider=(is_last_plan and not failed_tests),
                )

                if failed_tests:
                    for j, test_name in enumerate(failed_tests):
                        info_table.add_row(
                            ["", "", "", "", test_name.rstrip("$")],
                            divider=(is_last_plan and j == len(failed_tests) - 1),
                        )

        info_table.align = "l"
        print(info_table)

    def qualify_results(self):
        """Parse task results, filter by CLI flags, and prepare data for rerun."""
        logger.info(
            "Looking for tasks from the requested sources, this may take a while."
        )

        for i in self.req_url_list:
            logger.debug(f"Parsing the payload from: {i}")
        self.parsed_dict = parse_request_xunit(
            self.req_url_list, self.task_source, False
        )

        for key, details in self.parsed_dict.items():
            self._filter_suites_for_uuid(key, details)

        self._add_unparsed_fallbacks()

        if self.processed_data:
            self._display_qualification_table()
            if parsed_opts.cli_args.dryrun:
                return
        else:
            logger.info("None of the provided tasks qualify for a re-run.")
            logger.debug(
                colorize.format_text(
                    "All the results seem to be PASSing, time to celebrate! \U0001f389",
                    text_col=colorize.GREEN,
                    bold=True,
                )
            )

    def drop_payload_keys(self, keys_to_drop: list) -> None:
        """Drop keys (dot-notation paths) from all rerun payloads."""
        for payload in self.rerun_payloads:
            for key_path in keys_to_drop:
                drop_nested_key(payload, key_path)

    def drop_payload_keys_by_pattern(self, key_path: str, pattern: str) -> None:
        """Drop keys matching *pattern* at *key_path* in all rerun payloads."""
        for payload in self.rerun_payloads:
            target_dict = get_nested_value(payload, key_path)
            if isinstance(target_dict, dict):
                for key in match_keys(target_dict.keys(), pattern):
                    target_dict.pop(key, None)

    def overwrite_payload_values(self, updates: Dict[str, Any]) -> None:
        """Overwrite values (dot-notation paths) in all rerun payloads."""
        for payload in self.rerun_payloads:
            for key_path, value in updates.items():
                set_nested_key(payload, key_path, value)

    # ------------------------------------------------------------------
    # build_rerun_payloads and its helpers
    # ------------------------------------------------------------------

    _PAYLOAD_KEYS_TO_REMOVE = frozenset(
        {
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
    )

    @staticmethod
    def _fetch_and_validate_request(request_uuid: str) -> Optional[dict]:
        """Fetch request details from the API and validate environment count.

        Returns the request details dict, or ``None`` if the request should be
        skipped.  Raises ``ValidationError`` for multi-environment requests.
        """
        response = http_get(
            os.path.join(
                str(parsed_opts.testing_farm_endpoint.api_endpoint_url),
                request_uuid,
            ),
            timeout=REQUEST_TIMEOUT_DEFAULT,
        )
        request_details = response.json()

        match_uuid = request_details.get("id")
        environments = request_details.get("environments_requested", [])

        if len(environments) > 1:
            from enge.utils.errors import ValidationError

            logger.critical(
                f"Rerun of multi-environment requests is not supported. "
                f"Request {match_uuid} has {len(environments)} environments."
            )
            logger.critical(
                "Please rerun requests with only a single environment, "
                "or schedule a job for each environment separately."
            )
            raise ValidationError(
                f"Multi-environment rerun not supported: request {match_uuid} "
                f"has {len(environments)} environments"
            )

        if len(environments) == 0:
            logger.warning(f"No environments found in request {match_uuid}, skipping")
            return None

        return request_details

    @staticmethod
    def _apply_rerun_filters(
        request_details: dict,
        processed_entry: Optional[tuple],
        has_specific_plans: bool,
    ) -> bool:
        """Apply plan/test filters to *request_details* in place.

        Returns ``False`` when the request should be skipped entirely.
        """
        match_uuid = request_details.get("id")

        if not has_specific_plans:
            state = request_details.get("state", "").lower()
            if state in ["queued", "running"]:
                logger.warning(f"Request {match_uuid} is {state}, skipping rerun.")
                return False

            logger.info(
                f"Request {match_uuid} has no parseable results (state: {state}). "
                "Rerunning with original plan filter (fallback)."
            )
            return True

        if processed_entry:
            request_details.setdefault("test", {}).setdefault("fmf", {})

            request_details["test"]["fmf"]["name"] = processed_entry[0]
            request_details["test"]["fmf"]["plan_filter"] = None

            if len(processed_entry) > 2 and processed_entry[2]:
                all_failed_tests = []
                for test_names in processed_entry[2].values():
                    all_failed_tests.extend(test_names)
                if all_failed_tests:
                    request_details["test"]["fmf"]["test_name"] = "|".join(
                        all_failed_tests
                    )

        return True

    def _clean_payload(
        self,
        request_details: dict,
        match_uuid: str,
        source_path: Optional[str],
    ) -> dict:
        """Strip API-only keys and prepare payload for resubmission."""
        filtered = {
            k: v
            for k, v in request_details.items()
            if k not in self._PAYLOAD_KEYS_TO_REMOVE
        }
        filtered["_enge_source_path"] = source_path
        filtered["_original_uuid"] = match_uuid
        filtered["environments"] = filtered.pop("environments_requested")
        return filtered

    def build_rerun_payloads(self, uuids):
        """Build rerun payloads for qualifying tasks.

        Returns:
            list: Filtered payloads ready for re-submission.
        """
        self.rerun_payloads = []

        for request_uuid in uuids:
            request_details = self._fetch_and_validate_request(request_uuid)
            if request_details is None:
                continue

            match_uuid = request_details.get("id")
            processed_entry = self.processed_data.get(match_uuid)

            undefined_plan_filters: List[str] = []
            source_path = None
            has_specific_plans = False

            if processed_entry:
                has_specific_plans = processed_entry[0] is not None
                if len(processed_entry) > 3 and processed_entry[3]:
                    undefined_plan_filters = processed_entry[3]
                if len(processed_entry) > 4:
                    source_path = processed_entry[4]

            if not self._apply_rerun_filters(
                request_details, processed_entry, has_specific_plans
            ):
                continue

            filtered_payload = self._clean_payload(
                request_details, match_uuid, source_path
            )
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


def _create_rerun_launch_for_payload(
    payload: Dict[str, Any], is_dryrun: bool
) -> Optional[str]:
    """
    Create a ReportPortal launch for a single rerun payload.
    """
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

    # Build context for launch creation (reuse existing helper pattern)
    rerun_context = {
        "tier": tier,
        "architecture": arch,
    }

    # Generate launch name with RERUN prefix: RERUN~EVENT_NAME~timestamp~tier~arch
    timestamp = datetime.now().strftime("%Y-%m-%d")
    tier_str = tier or "unknown"
    arch_str = arch or "unknown"
    launch_name = f"RERUN~{event_name.upper()}~{timestamp}~{tier_str}~{arch_str}"

    from enge.reportportal.__main__ import ReportPortalLaunch

    if is_dryrun:
        try:
            rp_launch = ReportPortalLaunch()
            # Pass full TMT context for attribute generation, mirroring dispatch flow
            payload_data = rp_launch.generate_launch_payload(
                name=launch_name, context=rerun_context, tmt_context=tmt_context
            )

            # Add "rerun" tag manually if not present
            if "tags" in payload_data:
                if "rerun" not in payload_data["tags"]:
                    payload_data["tags"].append("rerun")
            else:
                payload_data["tags"] = ["rerun"]

            try:
                from pygments import highlight, lexers, formatters
                import json

                payload_formatted = json.dumps(payload_data, indent=4)
                colorful_json = highlight(
                    payload_formatted,
                    lexers.JsonLexer(),
                    formatters.TerminalFormatter(),
                )
                logger.info("DRY RUN | ReportPortal launch payload that would be sent:")
                print(colorful_json)
            except Exception:
                logger.info("DRY RUN | ReportPortal launch payload that would be sent:")
                print(json.dumps(payload_data, indent=4))
            return "dryrun_placeholder"
        except Exception as e:
            logger.warning(f"DRY RUN | Could not generate ReportPortal payload: {e}")
            return None
    else:
        try:
            rp_launch = ReportPortalLaunch()
            # Override generate_launch_payload temporarily or modify launch after creation?
            # Better: The create_launch method uses generate_launch_payload internally.
            # We can't easily inject tags into generate_launch_payload without modifying ReportPortalLaunch class
            # or subclassing it.
            # BUT: generate_launch_payload is a method on the instance.
            # We can monkey-patch it or just rely on the standard tags + tmt_context attributes.
            # Wait, the user wants 'rerun' tag in ADDITION to 'automated', 'enge'.

            # Let's subclass temporarily to inject the tag
            class RerunReportPortalLaunch(ReportPortalLaunch):
                def generate_launch_payload(
                    self, name=None, description=None, context=None, tmt_context=None
                ):
                    data = super().generate_launch_payload(
                        name, description, context, tmt_context
                    )
                    if "tags" in data:
                        if "rerun" not in data["tags"]:
                            data["tags"].append("rerun")
                    else:
                        data["tags"] = ["rerun"]
                    return data

            rp_launch = RerunReportPortalLaunch()
            launch_uuid = rp_launch.create_launch(
                name=launch_name, context=rerun_context, tmt_context=tmt_context
            )
            return launch_uuid
        except Exception as e:
            logger.error(f"Failed to create ReportPortal launch: {e}")
            return None


def _inject_reportportal_vars(payload: Dict[str, Any], launch_uuid: str) -> None:
    """Set ReportPortal env vars on *payload* for an already-created launch."""
    from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX
    from enge.utils.source_target_parser import (
        generate_reportportal_environment_variables,
    )

    effective_uuid = (
        "00000000-0000-0000-0000-000000000000"
        if launch_uuid == "dryrun_placeholder"
        else launch_uuid
    )

    set_nested_key(payload, "environments.0.tmt.context.uniq_id", effective_uuid)

    rp_config_vars = generate_reportportal_environment_variables(
        config=parsed_opts.config,
        cli_args=parsed_opts.cli_args,
    )

    # Keep only base vars — strip LAUNCH and LAUNCH_DESCRIPTION
    rp_base_vars = {
        k: v
        for k, v in rp_config_vars.items()
        if not (k.endswith("LAUNCH") or k.endswith("LAUNCH_DESCRIPTION"))
    }

    upload_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}UPLOAD_TO_LAUNCH"
    rp_base_vars[upload_key] = effective_uuid

    environments = payload.get("environments", [])
    if environments:
        env = environments[0]
        env.setdefault("tmt", {})["environment"] = rp_base_vars


def _strip_dollar_join(raw: str) -> str:
    """Split a ``|``-delimited string, strip ``$`` suffixes, and rejoin with ``', '``."""
    parts = [p.rstrip("$") for p in raw.split("|") if p.rstrip("$")]
    return ", ".join(parts) if parts else raw.rstrip("$")


def _extract_request_data_from_payload(payload: Dict[str, Any]) -> dict:
    """Build a *request_data* dict from a rerun payload for summary display."""
    request_data: Dict[str, Any] = {}

    test_fmf = payload.get("test", {}).get("fmf", {})
    if test_fmf:
        plan_name = test_fmf.get("name", "")
        if plan_name:
            request_data["plan"] = _strip_dollar_join(plan_name)
        test_name = test_fmf.get("test_name", "")
        if test_name:
            request_data["test_name"] = _strip_dollar_join(test_name)

        request_data["planfilter"] = test_fmf.get("plan_filter")
        request_data["testfilter"] = test_fmf.get("test_filter")
        request_data["tests_git_url"] = test_fmf.get("url")
        request_data["tests_git_ref"] = test_fmf.get("ref")

    environments = payload.get("environments", [])
    if environments:
        env = environments[0]
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

    return request_data


def _resolve_rerun_tags(payload: Dict[str, Any], base_tags: List[str]) -> List[str]:
    """Determine the combined tag list for a single rerun payload."""
    source_path = payload.pop("_enge_source_path", None)
    if source_path:
        extracted = _extract_tags_from_filename(Path(source_path))
        current_tags = extracted + [_get_next_rerun_tag(extracted)]
    else:
        current_tags = ["rerun"]
    return _unique_preserve([*base_tags, *current_tags])


def main():
    """Qualify tasks for re-run, build payloads, and submit via the Testing Farm API."""
    jobs = RerunJobs()
    jobs.qualify_results()
    jobs.build_rerun_payloads(jobs.rerun_uuids)

    # Common payload cleanup
    jobs.overwrite_payload_values(
        {
            "environments.0.tmt.context.initiator": "enge",
            "environments.0.tmt.context.trigger": "rerun",
        }
    )
    jobs.drop_payload_keys_by_pattern("environments.0.variables", "PACKIT_*")
    jobs.drop_payload_keys_by_pattern("environments.0.variables", "CI_*")
    jobs.drop_payload_keys(["environments.0.tmt.context.uniq_id"])

    # Submitter setup
    submit = SubmitTest()
    base_tags = submit.set_tag or []
    submit.api_key = parsed_opts.testing_farm.get("api_key")
    req_header = {"Authorization": f"Bearer {submit.api_key}"}
    is_dryrun = getattr(parsed_opts.cli_args, "dryrun", False)

    for i, payload in enumerate(jobs.rerun_payloads):
        original_uuid = payload.pop("_original_uuid", None)
        if original_uuid:
            set_nested_key(
                payload, "environments.0.tmt.context.rerun_of", original_uuid
            )

        launch_uuid = _create_rerun_launch_for_payload(payload, is_dryrun)
        if launch_uuid:
            _inject_reportportal_vars(payload, launch_uuid)

        combined_tags = _resolve_rerun_tags(payload, base_tags)
        submit.set_tag = combined_tags
        logger.info(
            "Archiving rerun task %d/%d with tags: %s",
            i + 1,
            len(jobs.rerun_payloads),
            ", ".join(combined_tags),
        )

        submit.populate_from_request_data(_extract_request_data_from_payload(payload))
        submit.send_request(payload, req_header)


if __name__ == "__main__":
    main()
