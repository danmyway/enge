# #!/usr/bin/env python3
import ast

from enge.utils.arg_parser import args
from enge.utils.config_parser import load_config
from enge.utils.globals import DEFAULT_CONFIG_PATHS


class ParsedOpts:
    def __init__(self, cli_args=None):
        config_paths = [args.config] if args.config else DEFAULT_CONFIG_PATHS
        self.config = load_config(paths=config_paths)
        self.cli_args = cli_args or {}

        self.options = self._get_config_options()
        self.tests_compose_mapping = ast.literal_eval(
            self.options.get("tests").get("composes")
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
                self.cli_args.tests_git_branch or self.tests.get("git_branch") or "main"
            )
            self.plans = self.cli_args.plans or [self.plans] or []

    def _get_config_options(self):
        """Extract all options from the config file into a nested dictionary."""
        config_options = {
            section: dict(self.config.items(section))
            for section in self.config.sections()
        }

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
