#!/usr/bin/env python3
import json
import logging
import os
import time
from typing import Optional, Dict, Any, List

from enge.utils.http_client import http_get, http_post

from enge.utils import FormatText, get_datetime
from enge.utils.opt_manager import parsed_opts
from enge.utils.globals import (
    REQUEST_TIMEOUT_DEFAULT,
    REQUEST_POLL_TIMEOUT,
    RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT,
)

LOGGER = logging.getLogger(__name__)


class SubmitTest:
    def __init__(
        self,
        shared_archive_filename: Optional[str] = None,
        launch_uuid: Optional[str] = None,
    ):
        self.api_key: Optional[str] = None
        self.tests_git_url: Optional[str] = None
        self.tests_git_ref: Optional[str] = None
        self.launch_uuid = launch_uuid
        self.target_compose: Optional[str] = None
        self.plan: Optional[str] = None
        self.planfilter: Optional[str] = None
        self.testfilter: Optional[str] = None
        self.test_name: Optional[str] = None

        self.compose: Optional[str] = None
        self.artifacts: List[Dict[str, str]] = []  # List of artifact dictionaries
        self.business_unit_tag: Optional[str] = None
        self.tmt_distro: Optional[str] = None
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
        self.auto_tag_enabled: bool = getattr(parsed_opts.cli_args, "auto_tag", False)
        self.auto_generated_tags: List[str] = []

    def set_launch_uuid(self, launch_uuid: Optional[str]) -> None:
        """Set the ReportPortal launch UUID after creation."""
        self.launch_uuid = launch_uuid
        LOGGER.debug(f"Updated SubmitTest launch_uuid to: {launch_uuid}")

    def _enrich_tmt_context_with_brew_nvrs(
        self, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Return a copy of context enriched with brew NVR info (package: version-release)."""
        enriched = dict(context)
        for artifact in self.artifacts or []:
            if artifact.get("type") != "redhat-brew-build" or "id" not in artifact:
                continue
            nvr = artifact["id"]
            package_name = artifact.get("package", "")
            if not (nvr and package_name):
                continue
            nvr_parts = nvr.rsplit("-", 2)
            if len(nvr_parts) >= 3:
                version, release = nvr_parts[-2], nvr_parts[-1]
                version_release = f"{version}-{release}"
                if (
                    package_name in enriched
                    and enriched[package_name] != version_release
                ):
                    LOGGER.warning(
                        f"TMT context key '{package_name}' already exists with value '{enriched[package_name]}', overwriting with '{version_release}'"
                    )
                enriched[package_name] = version_release
            else:
                LOGGER.warning(f"Could not parse NVR: {nvr}")
        return enriched

    def get_complete_tmt_context(self) -> Dict[str, Any]:
        """Get the complete TMT context including artifact information."""
        base_tmt_context = {"distro": self.tmt_distro}
        if self.set_tmt_context:
            base_tmt_context.update(self.set_tmt_context)
        return self._enrich_tmt_context_with_brew_nvrs(base_tmt_context)

    def add_artifact(
        self,
        artifact_id: str,
        artifact_type: str,
        packages: List[str],
        nvr: Optional[str] = None,
    ):
        """
        Add an artifact to the list of artifacts for this test request.

        Args:
            artifact_id: Build ID or identifier
            artifact_type: Type of artifact (e.g., "fedora-copr-build")
            packages: List of package names
            nvr: NVR string for display/logging
        """
        if not packages:
            LOGGER.warning(f"No packages provided for artifact {artifact_id}")
            packages = []

        artifact_dict = {
            "id": artifact_id,
            "type": artifact_type,
            "packages": packages,
        }

        if nvr is not None:
            artifact_dict["nvr"] = nvr

        self.artifacts.append(artifact_dict)

    def set_auto_tags(
        self,
        set_name: Optional[str] = None,
        architecture: Optional[str] = None,
        tier: Optional[str] = None,
    ):
        """
        Set auto-generated tags based on set name, architecture, and tier.

        Args:
            set_name: Name of the test set (optional)
            architecture: Target architecture (optional)
            tier: Test tier (optional)
        """
        if not self.auto_tag_enabled:
            return

        auto_tags = []

        # Generate the most specific combined tag possible, avoiding duplicates
        if set_name and architecture and tier:
            # All three components - use combined tag only
            auto_tags.append(f"{set_name}.{tier}.{architecture}")
        elif architecture and tier:
            # Two components - use combined tag only
            auto_tags.append(f"{architecture}.{tier}")
        else:
            # Individual components when we don't have enough for a meaningful combination
            if set_name:
                auto_tags.append(set_name)
            if architecture:
                auto_tags.append(architecture)
            if tier:
                auto_tags.append(tier)

        self.auto_generated_tags = auto_tags
        LOGGER.debug(f"Generated auto tags: {auto_tags}")

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

        # Extract and set target compose from TARGET_COMPOSE_URL if available
        if environment_variables and "TARGET_COMPOSE_URL" in environment_variables:
            from enge.utils.source_target_parser import parse_target_compose_from_url

            self.target_compose = parse_target_compose_from_url(
                environment_variables["TARGET_COMPOSE_URL"]
            )

            # Add target compose to TMT context if successfully parsed
            if self.target_compose and tmt_context is not None:
                tmt_context["target_compose"] = self.target_compose

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

        # Combine manual and auto-generated tags, eliminating duplicates
        all_tags = set()
        if self.set_tag:
            all_tags.update(self.set_tag)
        if self.auto_generated_tags:
            all_tags.update(self.auto_generated_tags)

        # Add tags to filename if any exist
        if all_tags:
            sorted_tags = sorted(list(all_tags))  # Sort for consistent ordering
            self.archive_tasks_file = ".".join([self.archive_tasks_file] + sorted_tags)

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

        # Separate ReportPortal environment variables from regular variables
        from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX

        regular_env_vars = {}
        reportportal_env_vars = {}

        for key, value in env_vars.items():
            if key.startswith(TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX):
                reportportal_env_vars[key] = value
            else:
                regular_env_vars[key] = value

        # Build the base TMT context (arch will be set per environment)
        base_tmt_context = {"distro": self.tmt_distro}

        # Merge with additional TMT context if available
        if tmt_context:
            base_tmt_context.update(tmt_context)

        # Add NVR information to TMT context if we have brew artifacts via shared helper
        base_tmt_context = self._enrich_tmt_context_with_brew_nvrs(base_tmt_context)

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

            # Build TMT configuration with context and environment
            tmt_config = {"context": arch_tmt_context}

            # Handle ReportPortal environment variables for TMT
            if reportportal_env_vars or self.launch_uuid:
                # If we created a launch (self.launch_uuid), exclude LAUNCH vars and set UPLOAD_TO_LAUNCH
                if self.launch_uuid:
                    filtered_rp_vars = {}
                    for key, value in reportportal_env_vars.items():
                        # Exclude launch and launch description variables when enge manages launches
                        # Keep UPLOAD_TO_LAUNCH if provided
                        if not (
                            key.endswith("LAUNCH") or key.endswith("LAUNCH_DESCRIPTION")
                        ) or key.endswith("UPLOAD_TO_LAUNCH"):
                            filtered_rp_vars[key] = value

                    # Add launch ID if available from ReportPortal launch creation
                    # In dry run mode, use a placeholder UUID so the structure matches real runs
                    if self.launch_uuid or getattr(
                        parsed_opts.cli_args, "dryrun", False
                    ):
                        from enge.utils.globals import (
                            TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX,
                        )

                        upload_to_launch_key = (
                            f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}UPLOAD_TO_LAUNCH"
                        )
                        # Use actual UUID or deterministic placeholder for dry run
                        launch_uuid_value = (
                            self.launch_uuid or "00000000-0000-0000-0000-000000000000"
                        )
                        filtered_rp_vars[upload_to_launch_key] = launch_uuid_value
                        LOGGER.debug(
                            f"Added {upload_to_launch_key}={launch_uuid_value} for {arch}"
                        )

                    if filtered_rp_vars:
                        tmt_config["environment"] = filtered_rp_vars
                else:
                    if reportportal_env_vars:
                        tmt_config["environment"] = reportportal_env_vars

            environment_config = {
                "arch": arch,
                "os": {"compose": self.compose},
                "settings": {
                    "provisioning": {
                        "tags": {"BusinessUnit": self.business_unit_tag},
                    }
                },
                "tmt": tmt_config,
                "variables": regular_env_vars,
            }

            # Only include artifacts if we have any artifacts (for copr/brew builds)
            if self.artifacts:
                environment_config["artifacts"] = [
                    {
                        "id": artifact["id"],
                        "type": artifact["type"],
                        "packages": artifact["packages"],
                    }
                    for artifact in self.artifacts
                ]

            environments.append(environment_config)

        self.payload_raw = {
            "test": {
                "fmf": {
                    "url": self.tests_git_url,
                    "ref": self.tests_git_ref,
                    "name": self.plan,
                    "test_name": self.test_name,
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
        response_timeout = RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT
        clear_line = "\x1b[2K"
        while True:
            response = http_get(log_artifact_url, timeout=REQUEST_POLL_TIMEOUT)
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
                # Show NVR and packages
                packages = artifact.get("packages", [])
                pkg_count = len(packages)

                if artifact.get("nvr"):
                    artifact_info += f"                     • {artifact['type']}: {artifact['id']} ({artifact['nvr']})\n"
                else:
                    pkg_str = (
                        f"{pkg_count} package(s)"
                        if pkg_count > 1
                        else (packages[0] if packages else "no packages")
                    )
                    artifact_info += f"                     • {artifact['type']}: {artifact['id']} ({pkg_str})\n"

                # Always show package list if multiple packages
                if pkg_count > 1:
                    for pkg in packages:
                        artifact_info += f"                       - {pkg}\n"
        else:
            artifact_info = "   Artifacts:        Using compose artifacts\n"

        # Format plan information
        plan_info = f"   Plan:             {self.plan if self.plan else 'Auto-selected via plan filter'}\n"
        if self.test_name:
            plan_info += f"   Test name:        {self.test_name}\n"
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

        # Format target compose information
        target_compose_info = ""
        if self.target_compose:
            target_compose_info = f"   Target compose:   {self.target_compose}\n"

        self.dispatch_summary = (
            FormatText.format_text(f"{summary_header}\n", bold=True)
            + f"   Source compose:   {self.compose}\n"
            + target_compose_info
            + plan_info
            + arch_info
            + artifact_info
            + f"   Test results:     {self.log_artifact_url}\n"
            "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
        )

        def _handle_dry_run(payload_raw=self.build_payload()):
            from pygments import highlight, lexers, formatters

            LOGGER.info("DRY RUN | Printing out requested payload:")
            payload_formatted = json.dumps(payload_raw, indent=4)
            colorful_json = highlight(
                payload_formatted, lexers.JsonLexer(), formatters.TerminalFormatter()
            )
            self.dispatch_summary = colorful_json
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
            response = http_post(
                self.testing_farm_endpoint,
                json=payload_raw,
                headers=header,
                timeout=REQUEST_TIMEOUT_DEFAULT,
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
