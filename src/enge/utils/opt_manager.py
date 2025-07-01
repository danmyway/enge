# #!/usr/bin/env python3
import ast
import logging
import os
import sys

from enge.utils.arg_parser import args
from enge.utils.config_parser import load_config
from enge.utils.globals import DEFAULT_CONFIG_PATHS

logger = logging.getLogger(__name__)


class ParsedOpts:
    def __init__(self, cli_args=None):
        config_paths = [args.config] if args.config else DEFAULT_CONFIG_PATHS
        self.config = load_config(paths=config_paths)
        self.cli_args = cli_args or {}

        self.options = self._get_config_options()
        self.tests_compose_mapping = None
        self.archive_tasks_latest = os.path.expanduser(
            self.common.get("archive_tasks_latest") or "/tmp/enge_latest_jobs"
        )
        self.archive_tasks_default = os.path.expanduser(
            self.common.get("archive_tasks_default") or "~/.enge/jobs_archive/"
        )

        if self.cli_args.action == "test":
            self.parallel_limit = (
                self.cli_args.parallel_limit or self.tests.get("parallel_limit") or None
            )
            self.copr_reference = (
                (self.cli_args.copr.ref if self.cli_args.copr else None)
                or self.copr_api.get("build_reference")
                or None
            )
            self.brew_reference = (
                (self.cli_args.brew.ref if self.cli_args.brew else None)
                or self.brew_api.get("build_reference")
                or None
            )
            self.tests_git_url = (
                self.cli_args.tests_git_url or self.tests.get("git_url") or None
            )
            self.tests_git_branch = (
                self.cli_args.tests_git_branch or self.tests.get("git_branch") or None
            )
            self.plans = self.cli_args.plans or [self.plans] or []
            self.tests_compose_mapping = ast.literal_eval(
                self.options.get("tests").get("composes") or None
            )

    def _get_config_options(self):
        """Extract all options from the config file into a nested dictionary."""
        config_options = {
            section: dict(self.config.items(section))
            for section in self.config.sections()
        }

        # Define essential options for each section
        essential_options = {}
        if self.cli_args.action == "test":
            essential_options.update(
                {
                    "project": ["name", "owner", "repo_url"],
                    "tests": ["composes"],
                    "testing_farm": ["api_key", "cloud_resources_tag"],
                }
            )
            if self.cli_args.brew:
                essential_options.update({"brew_api": ["session_url", "taskid_url"]})

        def _log_empty_essential_options():
            """Log critical messages for missing essential options."""
            missing_opts = []
            for section, opts in config_options.items():
                required_opts = essential_options.get(section, [])
                for option in required_opts:
                    if not opts.get(
                        option, ""
                    ).strip():  # Check if the essential option is missing
                        missing_opts.append((section, option))

            return missing_opts

        # Log missing essential values
        missing_opts = _log_empty_essential_options()
        if missing_opts:
            for section, option in missing_opts:
                logger.debug(
                    "Missing value for essential option '%s' in section '%s'.",
                    option,
                    section,
                )
            logger.critical(
                "Essential options values are missing from the configuration."
            )
            sys.exit(99)

        return config_options

    def __getattr__(self, item):
        """Allow direct access to options as attributes, prioritizing nested sections."""
        for section, opts in self.options.items():
            if isinstance(opts, dict) and item in opts:
                return opts[item]
            elif item == section:  # Allow access to entire section as attribute
                return opts
        raise AttributeError(f"'ParsedOpts' object has no attribute '{item}'")


parsed_opts = ParsedOpts(cli_args=args)
