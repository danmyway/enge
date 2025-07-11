#!/usr/bin/env python3
import json
import logging
import os
import time
from typing import Optional, Dict, Any, List

import requests

from enge.utils import FormatText, get_datetime
from enge.utils.opt_manager import parsed_opts

LOGGER = logging.getLogger(__name__)


class SubmitTest:
    def __init__(self, shared_archive_filename: Optional[str] = None):
        self.api_key: Optional[str] = None
        self.tests_git_url: Optional[str] = None
        self.tests_git_branch: Optional[str] = None
        self.plan: Optional[str] = None
        self.planfilter: Optional[str] = None
        self.testfilter: Optional[str] = None
        # Get required config values (validated by centralized validation)
        boot_method = parsed_opts.common.get("boot_method")
        assert boot_method, "boot_method validated by centralized validation"

        self.compose: Optional[str] = None
        self.artifacts: List[Dict[str, str]] = []  # List of artifact dictionaries
        self.business_unit_tag: Optional[str] = None
        self.tmt_distro: Optional[str] = None
        self.boot_method: str = boot_method
        self.parallel_limit: Optional[int] = None
        self.authorization_header: Dict[str, str] = {}
        self.payload_raw: Dict[str, Any] = {}
        self.latest_tasks_file: Optional[str] = None
        self.archive_tasks_default_path: Optional[str] = None
        self.archive_tasks_file: Optional[str] = None

        # Use shared archive filename if provided, otherwise generate one
        if shared_archive_filename:
            self.archive_tasks_filename = shared_archive_filename
            self.datetime_stamp = "shared"  # Not needed for shared filename
        else:
            self.datetime_stamp = get_datetime()
            self.archive_tasks_filename = f"enge_jobs_archive_{self.datetime_stamp}"

        self.task_id: Optional[str] = None
        self.log_artifact_base_url: str = str(
            parsed_opts.testing_farm_endpoint.log_artifact_baseurl
        )
        self.testing_farm_endpoint: str = str(
            parsed_opts.testing_farm_endpoint.api_endpoint_url
        )
        # Set-specific data (will be overridden by set_specific_data if provided)
        self.set_architectures: Optional[List[str]] = None
        self.set_environment_variables: Optional[Dict[str, str]] = None
        self.set_tmt_context: Optional[Dict[str, Any]] = None
        self.request_status: Optional[str] = None
        self.log_artifact_url: Optional[str] = None
        self.dispatch_summary: Optional[str] = None
        self.print_header: bool = True
        self.set_tag: Optional[List[str]] = getattr(
            parsed_opts.cli_args, "set_tag", None
        )

    def add_artifact(
        self,
        artifact_id: str,
        artifact_type: str,
        package: str,
        nvr: Optional[str] = None,
    ):
        """Add an artifact to the list of artifacts for this test request."""
        artifact_dict = {"id": artifact_id, "type": artifact_type, "package": package}
        if nvr is not None:
            artifact_dict["nvr"] = nvr
        self.artifacts.append(artifact_dict)

    def set_specific_data(
        self,
        architectures: List[str],
        environment_variables: Dict[str, str],
        tmt_context: Dict[str, Any],
    ):
        """Set test set-specific data that overrides global configuration."""
        self.set_architectures = architectures
        self.set_environment_variables = environment_variables
        self.set_tmt_context = tmt_context

    def record_task_ids(self, task_id):
        self.latest_tasks_file = parsed_opts.archive_tasks_latest
        self.archive_tasks_default_path = parsed_opts.archive_tasks_default

        # Ensure we have a valid path for archive files
        if not self.archive_tasks_default_path:
            LOGGER.warning("Archive default path not configured, skipping archive")
            return

        self.archive_tasks_file = os.path.join(
            self.archive_tasks_default_path, self.archive_tasks_filename
        )

        # Add set_tag if provided
        if self.set_tag:
            self.archive_tasks_file = ".".join([self.archive_tasks_file] + self.set_tag)

        def _handle_archive_files():
            if self.latest_tasks_file and os.path.exists(self.latest_tasks_file):
                os.unlink(self.latest_tasks_file)
            if self.archive_tasks_default_path and not os.path.exists(
                self.archive_tasks_default_path
            ):
                os.makedirs(self.archive_tasks_default_path)

        _handle_archive_files()

        if self.latest_tasks_file and self.archive_tasks_file:
            with open(self.latest_tasks_file, "a") as latest_jobs_file:
                latest_jobs_file.write(f"{task_id}\n")

            with open(self.archive_tasks_file, "a") as latest_jobs_archive:
                latest_jobs_archive.write(f"{task_id}\n")

    def build_payload(self):
        # Payload documentation > https://testing-farm.gitlab.io/api/#operation/requestsPost
        self.authorization_header = {"Authorization": f"Bearer {self.api_key}"}

        # Get environment variables and TMT context - use set-specific data if available
        env_vars = (
            self.set_environment_variables
            if self.set_environment_variables is not None
            else getattr(parsed_opts, "environment_variables", {})
        )
        tmt_context = (
            self.set_tmt_context
            if self.set_tmt_context is not None
            else getattr(parsed_opts, "tmt_context", {})
        )

        # Build the base TMT context (arch will be set per environment)
        base_tmt_context = {
            "distro": self.tmt_distro,
            "boot_method": self.boot_method,
        }

        # Merge with additional TMT context if available
        if tmt_context:
            base_tmt_context.update(tmt_context)

        # Get architectures - use set-specific data if available
        architectures = (
            self.set_architectures
            if self.set_architectures is not None
            else getattr(parsed_opts, "architectures", [])
        )

        # Build environment configurations for each architecture
        environments = []
        for arch in architectures:
            # Create architecture-specific TMT context
            arch_tmt_context = base_tmt_context.copy()
            arch_tmt_context["arch"] = arch

            environment_config = {
                "arch": arch,
                "os": {"compose": self.compose},
                "settings": {
                    "provisioning": {
                        "tags": {"BusinessUnit": self.business_unit_tag},
                    }
                },
                "tmt": {"context": arch_tmt_context},
                "hardware": {
                    "boot": {
                        "method": self.boot_method,
                    }
                },
                "variables": env_vars,
            }

            # Only include artifacts if we have any artifacts (for copr/brew builds)
            if self.artifacts:
                environment_config["artifacts"] = [
                    {
                        "id": artifact["id"],
                        "type": artifact["type"],
                        "packages": [artifact["package"]],
                    }
                    for artifact in self.artifacts
                ]

            environments.append(environment_config)

        self.payload_raw = {
            "test": {
                "fmf": {
                    "url": self.tests_git_url,
                    "ref": self.tests_git_branch,
                    "name": self.plan,
                    "plan_filter": self.planfilter,
                    "test_filter": self.testfilter,
                }
            },
            "environments": environments,
            "settings": {"pipeline": {"parallel-limit": self.parallel_limit}},
        }

        return self.authorization_header, self.payload_raw

    def _response_watcher(self, log_artifact_url):
        # Hardcoded 20 second timeout for Testing Farm API response (as per README)
        response_timeout = 20
        clear_line = "\x1b[2K"
        while True:
            response = requests.get(log_artifact_url)
            response_status = response.status_code
            response_message = response.reason
            print(end=clear_line)
            print(
                FormatText.format_text(
                    f"Waiting for a successful response for {response_timeout} seconds. ",
                    bold=True,
                ),
                f"Current response is: {response_status} {response_message}",
                end="\r",
                flush=True,
            )
            time.sleep(1)
            response_timeout -= 1
            if response_status > 200 and response_timeout == 0:
                print(end=clear_line)
                print(
                    f"{FormatText.BOLD}Processing the request takes longer this time.\n"
                    f"The request response is still {response_status} {response_message}\n"
                    f"Here is the link for the requested job, try refreshing the website after a couple of minutes.\n",
                    flush=True,
                )
                print(self.dispatch_summary)
                break
            elif response_status == 200:
                print("\nResponse successful!\n")
                print(self.dispatch_summary)
                break

    def assess_summary_message(self):
        # Always show a clear summary separator for consistency
        if self.print_header:
            # First request - show full context
            summary_header = "\n~ REQUEST SUMMARY ~"
        else:
            # Subsequent requests - show simpler separator
            summary_header = "\n~ SUMMARY ~"

        # Build artifact information display
        artifact_info = ""
        if self.artifacts:
            artifact_info = (
                f"   Artifacts:        {len(self.artifacts)} build(s) included\n"
            )
            for artifact in self.artifacts:
                # Show NVR if available, otherwise show package name
                if artifact.get("nvr"):
                    artifact_info += f"                     • {artifact['type']}: {artifact['id']} ({artifact['nvr']})\n"
                else:
                    artifact_info += f"                     • {artifact['type']}: {artifact['id']} ({artifact['package']})\n"
        else:
            artifact_info = "   Artifacts:        Using compose artifacts\n"

        # Format plan information
        plan_info = f"   Plan:             {self.plan if self.plan else 'Auto-selected via plan filter'}\n"
        if self.planfilter:
            plan_info += f"   Plan filter:      {self.planfilter}\n"
        if self.testfilter:
            plan_info += f"   Test filter:      {self.testfilter}\n"

        # Format architecture information - use set-specific data if available
        architectures = (
            self.set_architectures
            if self.set_architectures is not None
            else getattr(parsed_opts, "architectures", [])
        )
        if len(architectures) == 1:
            arch_info = f"   Architecture:     {architectures[0]}\n"
        else:
            arch_info = f"   Architectures:    {', '.join(architectures)}\n"

        self.dispatch_summary = (
            FormatText.format_text(f"{summary_header}\n", bold=True)
            + f"   Source compose:   {self.compose}\n"
            + plan_info
            + arch_info
            + artifact_info
            + f"   Test results:     {self.log_artifact_url}\n"
            "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
        )

        def _handle_dry_run(payload_raw=self.build_payload()):
            from pygments import highlight, lexers, formatters

            print_payload_dryrun_msg = "\nDRY RUN  | Printing out requested payload:"
            payload_formatted = json.dumps(payload_raw, indent=4)
            colorful_json = highlight(
                payload_formatted, lexers.JsonLexer(), formatters.TerminalFormatter()
            )
            self.dispatch_summary = f"{print_payload_dryrun_msg}\n{colorful_json}"
            return self.dispatch_summary

        if getattr(parsed_opts.cli_args, "dryrun", False):
            self.dispatch_summary = _handle_dry_run()

        return self.dispatch_summary

    def send_request(self, payload_raw, header):
        # Check for dry run first - don't send actual request if dry run is enabled
        if getattr(parsed_opts.cli_args, "dryrun", False):
            LOGGER.debug("Dry run mode - skipping actual request to Testing Farm")
            self.dispatch_summary = self.assess_summary_message()
            print(self.dispatch_summary)
            return

        try:
            response = requests.post(
                self.testing_farm_endpoint, json=payload_raw, headers=header
            )
            task_id = response.json()["id"]
            self.log_artifact_url = f"{self.log_artifact_base_url}/{task_id}"
            self.dispatch_summary = self.assess_summary_message()
            # Only wait for response if --no-wait flag is not set
            if getattr(parsed_opts.cli_args, "action", None) != "rerun" and getattr(
                parsed_opts.cli_args, "wait", False
            ):
                self._response_watcher(self.log_artifact_url)
            else:
                print(self.dispatch_summary)

            self.record_task_ids(task_id)
        except KeyError:
            LOGGER.error(json.dumps(response.json(), indent=2, sort_keys=True))
