import logging
import os
import sys
from typing import Optional, Dict, Any

import requests
from prettytable import PrettyTable

from enge.dispatch.tf_send_request import SubmitTest
from enge.report.__main__ import parse_tasks, parse_request_xunit
from enge.utils.opt_manager import parsed_opts
from enge.utils import FormatText

colorize = FormatText()

logger = logging.getLogger(__name__)

# Don't print unnecessary log messages from the report module
report_logger = logging.getLogger("enge.report")
report_logger.setLevel(logging.WARNING)


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
        self.req_url_list, self.task_source = parse_tasks()

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
            self.req_url_list, self.task_source, True
        )

        for key, details in self.parsed_dict.items():
            # Determine the result filter based on CLI arguments
            result_filter = ["SKIPPED"]  # We want to filter out skipped plans

            if parsed_opts.cli_args.error:
                result_filter.extend("FAILED")
            elif parsed_opts.cli_args.fail:
                result_filter.extend("ERROR")

            # Filter test suites based on the result filter
            filtered_suites = [
                suite
                for suite in details["testsuites"]
                if suite["testsuite_result"] not in result_filter
            ]

            # Process and store data for filtered test suites
            suite_names = "|".join(suite["testsuite_name"] for suite in filtered_suites)
            if suite_names:
                self.processed_data[key] = (suite_names, details["target_name"])
                self.rerun_uuids.append(key)

        # Log and display qualifying plans for a re-run
        if self.processed_data:
            info_table = PrettyTable()
            info_table.field_names = [
                "Original Request",
                "Target",
                "Arch",
                "Re-run Plans",
            ]

            logger.info("The following plans qualify for a re-run:")
            for req in self.processed_data.keys():
                data = self.processed_data[
                    req
                ]  # Use direct access since we're iterating over keys
                rerun_plans = "\n".join(data[0].split("|"))
                rerun_target = data[1]
                rerun_arch = "placeholder_arch"
                row = [req, rerun_target, rerun_arch, rerun_plans]

                info_table.add_row(row, divider=True)
            info_table.align = "l"
            print(info_table)
            if parsed_opts.cli_args.dryrun:
                sys.exit(0)
        else:
            logger.info("None of the provided tasks qualify for a re-run.")
            logger.debug(
                colorize.format_text(
                    "All the results seem to be PASSing, time to celebrate! \U0001f389",
                    text_col=colorize.GREEN,
                    bold=True,
                )
            )

    def build_rerun_payloads(self, uuids):
        """
        Build re-run payloads for the qualifying tasks by retrieving detailed information
        from the Testing Farm API.

        Args:
            uuids (list): List of UUIDs for tasks that qualify for re-run.

        Returns:
            list: A list of filtered payloads ready for re-submission.
        """
        self.rerun_payloads = []

        for request in uuids:
            # Fetch the task details from the API
            response = requests.get(
                os.path.join(
                    str(parsed_opts.testing_farm_endpoint.api_endpoint_url), request
                )
            )
            request_details = response.json()

            match_uuid = request_details.get("id")
            if len(request_details.get("environments_requested")) > 1:
                logger.critical(
                    "There were multiple environments requested in the original task."
                )
                logger.critical(
                    "Cowardly refusing to continue due to the inability to correctly assign environments to failed plans."
                )
                sys.exit(99)

            # Determine the test plan to use for re-run based on the task state
            if request_details.get("state") == "error":
                logger.info(
                    "The original plan filtering will be used, "
                    f"since no plan from the original request {request} finished successfully."
                )
                self.plan = (
                    request_details["test"]["fmf"]["name"]
                    or request_details["test"]["fmf"]["plan_filter"]
                )
            else:
                if match_uuid in self.processed_data:
                    request_details["test"]["fmf"]["name"] = self.processed_data[
                        match_uuid
                    ][0]

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

            # Update environment key for re-run compatibility
            filtered_payload["environments"] = filtered_payload.pop(
                "environments_requested"
            )

            # Append the filtered payload for re-run
            self.rerun_payloads.append(filtered_payload)

        return self.rerun_payloads


def handle_reportportal_launch(
    context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Handle ReportPortal launch creation if --rp option is specified.

    Args:
        context: Optional context for launch name generation (set_name, tier, architecture, etc.)

    Returns:
        Optional[str]: Launch UUID if created, None otherwise (None for dry run)
    """
    if not getattr(parsed_opts.cli_args, "rp", False):
        return None

    try:
        # Get the launch name that would be set in TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH
        from enge.utils.source_target_parser import (
            generate_reportportal_environment_variables,
        )

        # Generate environment variables to get the launch name
        rp_env_vars = generate_reportportal_environment_variables(
            parsed_opts.config,
            parsed_opts.cli_args,
            context.get("set_name") if context else None,
            context.get("architecture") if context else None,
            context.get("tier") if context else None,
            context.get("source_release") if context else None,
            context.get("target_release") if context else None,
            context.get("source_compose") if context else None,
            target_compose=None,
            event=context.get("event") if context else None,
        )

        # Extract the launch name from TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH
        from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX

        launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
        launch_name = rp_env_vars.get(launch_key)

        # Check for dry run mode - don't create actual launch if dry run is enabled
        if getattr(parsed_opts.cli_args, "dryrun", False):
            from enge.reportportal.__main__ import ReportPortalLaunch

            # Create a ReportPortalLaunch instance just for dry run payload generation
            try:
                rp_launch = ReportPortalLaunch()
                dry_run_payload = rp_launch.generate_launch_payload(
                    name=launch_name, context=context
                )

                # Pretty print the launch payload like the main dryrun does
                import json

                try:
                    from pygments import highlight, lexers, formatters

                    payload_formatted = json.dumps(dry_run_payload, indent=4)
                    colorful_json = highlight(
                        payload_formatted,
                        lexers.JsonLexer(),
                        formatters.TerminalFormatter(),
                    )
                    logger.info(
                        "DRY RUN | ReportPortal launch payload that would be sent:"
                    )
                    print(colorful_json)
                except ImportError:
                    # Fallback if pygments is not available
                    payload_formatted = json.dumps(dry_run_payload, indent=4)
                    logger.info(
                        "DRY RUN | ReportPortal launch payload that would be sent:"
                    )
                    print(payload_formatted)

                logger.info(
                    "DRY RUN | ReportPortal launch creation skipped (--dryrun mode)"
                )

            except Exception as e:
                logger.info(
                    "DRY RUN | Would create ReportPortal launch with name: %s",
                    launch_name or "auto-generated",
                )
                logger.info(
                    "DRY RUN | Could not generate launch payload for preview: %s", e
                )
                logger.info(
                    "DRY RUN | ReportPortal launch creation skipped (--dryrun mode)"
                )

            return None

        from enge.reportportal.__main__ import ReportPortalLaunch

        logger.info("Creating ReportPortal launch (--rp option specified)")
        rp_launch = ReportPortalLaunch()

        launch_uuid = rp_launch.create_launch(name=launch_name, context=context)
        rp_launch.store_launch_uuid(launch_uuid)

        return launch_uuid

    except Exception as e:
        logger.error(f"Failed to create ReportPortal launch: {e}")
        logger.warning("Continuing with rerun execution without ReportPortal launch")
        return None


def main():
    """
    Main function to qualify tasks for re-run, build their re-run payloads,
    and submit the requests via the Testing Farm API.
    """
    # Handle ReportPortal launch creation if requested (with minimal context for rerun)
    rerun_context = {
        "set_name": "rerun",  # Indicate this is a rerun operation
    }
    reportportal_launch_uuid = handle_reportportal_launch(rerun_context)

    jobs = RerunJobs()
    submit = SubmitTest()

    # Set up the submitter
    submit.print_header = True

    # Qualify tasks for re-run
    jobs.qualify_results()

    # Build re-run payloads
    jobs.build_rerun_payloads(jobs.rerun_uuids)

    # Set API key for submission
    submit.api_key = parsed_opts.testing_farm.get("api_key")

    # Build request headers and send re-run requests
    req_header, _ = submit.build_payload()
    for i, payload in enumerate(jobs.rerun_payloads):
        submit.compose = list(jobs.processed_data.values())[i][1]
        submit.plan = list(jobs.processed_data.values())[i][0]

        submit.send_request(payload, req_header)

        submit.print_header = False


if __name__ == "__main__":
    main()
