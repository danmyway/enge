# #!/usr/bin/env python3
import ast
import sys

from src.enge.utils.arg_parser import args
from src.enge.utils.globals import DEFAULT_CONFIG_PATHS
import logging
from src.enge.utils.config_parser import load_config

LOGGER = logging.getLogger(__name__)


class ParsedOpts:
    def __init__(self, cli_args=None):
        self.config = load_config(paths=DEFAULT_CONFIG_PATHS)
        self.cli_args = cli_args or {}

        # Define CLI-to-config mapping for prioritized overrides
        self.cli_to_config_map = {
            "parallel_limit": ("tests", "parallel_limit"),
            "tests_git_url": ("tests", "git_url"),
            "tests_git_branch": ("tests", "git_branch"),
        }

        self.options = self._merge_options()
        self.tests_compose_mapping = ast.literal_eval(
            self.options.get("tests").get("composes")
        )

    def _get_config_options(self):
        """Extract all options from the config file into a nested dictionary."""
        config_options = {
            section: dict(self.config.items(section))
            for section in self.config.sections()
        }
        return config_options

    def _merge_options(self):
        """Merge config file options and CLI args with CLI precedence."""
        merged_options = self._get_config_options()

        # Override config options with CLI args where mappings are provided
        for cli_arg, value in self.cli_args.items():
            section_option = self.cli_to_config_map.get(cli_arg)
            if section_option and value:
                section, option = section_option
                merged_options.setdefault(section, {})[option] = value
            else:
                # Unmapped CLI arguments are added at the top level in `merged_options`
                merged_options[cli_arg] = value

        return merged_options

    def __getattr__(self, item):
        """Allow direct access to options as attributes, prioritizing nested sections."""
        for section, opts in self.options.items():
            if isinstance(opts, dict) and item in opts:
                return opts[item]
            elif item == section:  # Allow access to entire section as attribute
                return opts
        raise AttributeError(f"'ParsedOpts' object has no attribute '{item}'")


parsed_opts = ParsedOpts(cli_args=args)
