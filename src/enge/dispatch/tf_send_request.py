#!/usr/bin/env python3
import json
import logging
import os
import time
from typing import Optional, Dict, Any, List

from enge.utils.http_client import http_get, http_post

from enge.utils import get_datetime, redact_sensitive
from rich.markup import escape
from enge.utils.console import console
from enge.utils.source_target_parser import (
    normalize_tmt_compose_context,
    parse_target_compose_from_url,
)
from enge.utils.globals import (
    REQUEST_TIMEOUT_DEFAULT,
    REQUEST_POLL_TIMEOUT,
    RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT,
    TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX,
)

LOGGER = logging.getLogger(__name__)


def clear_latest_jobs_file(ctx):
    """Remove the latest-jobs file at the start of a dispatch run.

    Called once per enge invocation before any record_task_ids() calls so that
    a fresh run always starts with an empty file rather than appending to
    leftovers from a previous run.
    """
    path = ctx.archive_tasks_latest
    if path:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def maybe_clear_latest_jobs_file(ctx) -> None:
    """Clear the latest-jobs file unless this is a dry-run."""
    if not getattr(ctx.cli_args, "dryrun", False):
        clear_latest_jobs_file(ctx)


class SubmitTest:
    def __init__(
        self,
        ctx=None,
        shared_archive_filename: Optional[str] = None,
        launch_uuid: Optional[str] = None,
    ):
        # Temporary backward compatibility during migration: if ctx is not provided,
        # fall back to the parsed_opts singleton. This supports unmigrated callers
        # (set_flow, dispatch/__main__) until Task 4 and Task 5 migrate them.
        if ctx is None:
            from enge.utils.opt_manager import parsed_opts

            ctx = parsed_opts
        self.ctx = ctx
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
        self.skip_guest_setup: bool = False
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
            ctx.testing_farm_endpoint.log_artifact_baseurl
        )
        self.testing_farm_endpoint: str = str(
            ctx.testing_farm_endpoint.api_endpoint_url
        )
        # Set-specific data (will be overridden by set_specific_data if provided)
        self.set_architectures: Optional[List[str]] = None
        self.set_pool: Optional[str] = None
        self.set_environment_variables: Optional[Dict[str, str]] = None
        self.set_tmt_context: Optional[Dict[str, Any]] = None
        self.request_status: Optional[str] = None
        self.log_artifact_url: Optional[str] = None
        self.dispatch_summary: Optional[str] = None
        self.set_tag: Optional[List[str]] = getattr(ctx.cli_args, "set_tag", None)
        self.auto_tag_enabled: bool = getattr(ctx.cli_args, "auto_tag", False)
        self.auto_generated_tags: List[str] = []
        self.compact_output: bool = False
        self.silent_output: bool = False

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
        upgrade_path_tag: Optional[str] = None,
    ):
        """
        Set auto-generated tags based on set name, architecture, tier, and detailed upgrade path.

        Args:
            set_name: Name of the test set (optional)
            architecture: Target architecture (optional)
            tier: Test tier (optional)
            upgrade_path_tag: Detailed upgrade path alias (e.g., "98to102")
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

        if upgrade_path_tag:
            auto_tags.append(upgrade_path_tag)

        self.auto_generated_tags = auto_tags
        LOGGER.debug(f"Generated auto tags: {auto_tags}")

    def set_specific_data(
        self,
        architectures: List[str],
        environment_variables: Dict[str, str],
        tmt_context: Dict[str, Any],
        pool: Optional[str] = None,
    ):
        """Set test set-specific data that overrides global configuration."""
        self.set_architectures = architectures
        self.set_pool = pool
        self.set_environment_variables = environment_variables
        self.set_tmt_context = tmt_context

        # Extract and set target compose from TARGET_COMPOSE_URL if available
        if environment_variables and "TARGET_COMPOSE_URL" in environment_variables:
            self.target_compose = parse_target_compose_from_url(
                environment_variables["TARGET_COMPOSE_URL"]
            )

            # Add target compose to TMT context if successfully parsed
            if self.target_compose and tmt_context is not None:
                tmt_context["target_compose"] = self.target_compose

    def populate_from_request_data(self, request_data: Dict[str, Any]) -> None:
        """
        Populate SubmitTest attributes from extracted request data (e.g., from rerun).

        Args:
            request_data: Dictionary containing fields extracted from a Testing Farm request
        """
        self.tests_git_url = request_data.get("tests_git_url")
        self.tests_git_ref = request_data.get("tests_git_ref")
        self.plan = request_data.get("plan")
        self.planfilter = request_data.get("planfilter")
        self.testfilter = request_data.get("testfilter")
        self.test_name = request_data.get("test_name")
        self.compose = request_data.get("compose")
        self.artifacts = request_data.get("artifacts", [])
        self.business_unit_tag = request_data.get("business_unit_tag")
        self.tmt_distro = request_data.get("tmt_distro")
        self.parallel_limit = request_data.get("parallel_limit")

        # Set architectures and use set_specific_data for TMT context and env vars
        architectures = request_data.get("architectures", [])
        tmt_context = request_data.get("tmt_context", {})
        env_vars = request_data.get("environment_variables", {})
        pool = request_data.get("pool")

        if architectures:
            self.set_specific_data(architectures, env_vars, tmt_context, pool=pool)
        else:
            # Fallback: set TMT context and env vars directly if no architectures
            self.set_tmt_context = tmt_context
            self.set_environment_variables = env_vars
            self.set_pool = pool

    def record_task_ids(self, task_id):
        self.latest_tasks_file = self.ctx.archive_tasks_latest
        self.archive_tasks_default_path = self.ctx.archive_tasks_default

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
            else getattr(self.ctx, "environment_variables", {})
        )
        tmt_context = (
            self.set_tmt_context
            if self.set_tmt_context is not None
            else getattr(self.ctx, "tmt_context", {})
        )

        # Separate ReportPortal environment variables from regular variables
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

        base_tmt_context = normalize_tmt_compose_context(base_tmt_context)

        # Get architectures - use set-specific data if available
        architectures = (
            self.set_architectures
            if self.set_architectures is not None
            else getattr(self.ctx, "architectures", [])
        )

        # Get pool - use set-specific data if available
        pool = (
            self.set_pool
            if self.set_pool is not None
            else getattr(self.ctx, "pool", None)
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
                    if self.launch_uuid or getattr(self.ctx.cli_args, "dryrun", False):
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
            }

            if pool:
                environment_config["pool"] = pool

            env_settings = {
                "provisioning": {
                    "tags": {"BusinessUnit": self.business_unit_tag},
                }
            }
            if self.skip_guest_setup:
                env_settings["pipeline"] = {"skip_guest_setup": True}
            environment_config["settings"] = env_settings
            environment_config["tmt"] = tmt_config
            environment_config["variables"] = regular_env_vars

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
        response_timeout = RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT
        with console.status("Waiting for response...") as status:
            while True:
                response = http_get(log_artifact_url, timeout=REQUEST_POLL_TIMEOUT)
                response_status = response.status_code
                response_message = response.reason
                status.update(
                    f"Waiting for response ({response_timeout}s)... "
                    f"{response_status} {response_message}"
                )
                time.sleep(1)
                response_timeout -= 1
                if response_status > 200 and response_timeout == 0:
                    break
                elif response_status == 200:
                    break

        if response_status == 200:
            LOGGER.info("Response successful!")
        else:
            LOGGER.warning(
                f"Processing the request takes longer this time. "
                f"Response is still {response_status} {response_message}. "
                f"Try refreshing the link after a couple of minutes."
            )
        print(self.dispatch_summary)

    def assess_summary_message(self):
        from rich.panel import Panel
        from rich.table import Table
        from io import StringIO
        from rich.console import Console as RenderConsole

        kv = Table.grid(padding=(0, 2))
        kv.add_column(style="bold")
        kv.add_column()

        kv.add_row("Source compose:", escape(self.compose or ""))
        if self.target_compose:
            kv.add_row("Target compose:", escape(self.target_compose))

        kv.add_row(
            "Plan:", escape(self.plan) if self.plan else "Auto-selected via plan filter"
        )
        if self.test_name:
            kv.add_row("Test name:", escape(self.test_name))
        if self.planfilter:
            kv.add_row("Plan filter:", escape(self.planfilter))
        if self.testfilter:
            kv.add_row("Test filter:", escape(self.testfilter))

        architectures = (
            self.set_architectures
            if self.set_architectures is not None
            else getattr(self.ctx, "architectures", [])
        )
        if len(architectures) == 1:
            kv.add_row("Architecture:", architectures[0])
        else:
            kv.add_row("Architectures:", ", ".join(architectures))

        pool = (
            self.set_pool
            if self.set_pool is not None
            else getattr(self.ctx, "pool", None)
        )
        if pool:
            kv.add_row("Pool:", pool)

        if self.artifacts:
            artifact_lines = []
            for artifact in self.artifacts:
                packages = (
                    artifact.get("packages", [])
                    if artifact.get("packages")
                    else [f"{artifact.get('type')}: {artifact.get('id')}"]
                )
                if artifact.get("nvr"):
                    artifact_lines.append(
                        f"• {artifact['type']}: {artifact['id']} ({artifact['nvr']})"
                    )
                else:
                    pkg_str = (
                        f"{len(packages)} package(s)"
                        if len(packages) > 1
                        else (packages[0] if packages else "no packages")
                    )
                    artifact_lines.append(
                        f"• {artifact['type']}: {artifact['id']} ({pkg_str})"
                    )
                if len(packages) > 1:
                    for pkg in packages:
                        artifact_lines.append(f"  - {pkg}")
            kv.add_row(f"Artifacts ({len(self.artifacts)}):", "\n".join(artifact_lines))
        else:
            kv.add_row("Artifacts:", "Using compose artifacts")

        results_url = self.log_artifact_url or "[pending]"
        kv.add_row("Test results:", results_url)

        panel = Panel(
            kv, title="[bold]REQUEST SUMMARY[/]", border_style="dim", expand=False
        )

        buf = StringIO()
        from enge.utils.console import console as _console

        terminal_width = min(_console.width, 200)
        render_console = RenderConsole(file=buf, no_color=True, width=terminal_width)
        render_console.print(panel)
        self.dispatch_summary = buf.getvalue()

        if getattr(self.ctx.cli_args, "dryrun", False):
            if self.payload_raw:
                payload_to_display = self.payload_raw
            else:
                _, payload_to_display = self.build_payload()
            self.dryrun_payload = redact_sensitive(payload_to_display)
            output_format = getattr(self.ctx.cli_args, "output_format", "terminal")
            if output_format != "json":
                LOGGER.info("DRY RUN | Printing out requested payload:")
                print(json.dumps(redact_sensitive(payload_to_display), indent=4))
            self.dispatch_summary = None

        return self.dispatch_summary

    def send_request(self, payload_raw, header):
        # Check for dry run first - don't send actual request if dry run is enabled
        if getattr(self.ctx.cli_args, "dryrun", False):
            LOGGER.debug("Dry run mode - skipping actual request to Testing Farm")
            self.payload_raw = payload_raw
            self.assess_summary_message()
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
            if self.silent_output:
                pass
            elif getattr(self.ctx.cli_args, "action", None) != "rerun" and getattr(
                self.ctx.cli_args, "wait", False
            ):
                self._response_watcher(self.log_artifact_url)
                self.dispatch_summary = None
            elif self.compact_output:
                LOGGER.info(
                    f"Submitted: {self.log_artifact_url}",
                    extra={"style": "bold green"},
                )
            else:
                print(self.dispatch_summary)

            self.record_task_ids(task_id)
        except KeyError:
            LOGGER.error(json.dumps(response.json(), indent=2, sort_keys=True))
