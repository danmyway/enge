# #!/usr/bin/env python3
import logging
import os
import sys

from enge.utils.arg_parser import args
from enge.utils.config_parser import (
    load_config,
    validate_required_config,
    validate_operational_defaults,
    validate_test_sets,
)
from enge.utils.globals import DEFAULT_CONFIG_PATHS
from enge.utils.source_target_parser import (
    parse_source_target_config,
    generate_upgrade_path_alias,
    generate_environment_variables,
    generate_tmt_context,
    parse_environment_variables,
    merge_environment_variables,
    parse_architectures,
    parse_test_sets,
    resolve_effective_values,
    merge_set_environment_variables,
)

logger = logging.getLogger(__name__)


class ParsedOpts:
    def __init__(self, cli_args=None):
        self.cli_args = cli_args or args
        config_paths = (
            [self.cli_args.config]
            if self.cli_args.config
            else list(DEFAULT_CONFIG_PATHS)
        )
        self.config = load_config(paths=config_paths)

        # Always validate operational defaults (these should always be present)
        if not validate_operational_defaults(self.config):
            sys.exit(99)

        # Validate user-specific configuration for operations that need it
        if getattr(self.cli_args, "action", None) in ["test", "rerun"]:
            if not validate_required_config(self.config):
                sys.exit(99)

        self.options = self._get_config_options()

        # Get archive paths from config (validated by operational defaults check)
        # These values are guaranteed to be present due to operational defaults validation
        archive_latest = self.common.get("archive_tasks_latest")
        archive_default = self.common.get("archive_tasks_default")
        assert isinstance(
            archive_latest, str
        ), "archive_tasks_latest should be validated as string"
        assert isinstance(
            archive_default, str
        ), "archive_tasks_default should be validated as string"

        self.archive_tasks_latest = os.path.expanduser(archive_latest)
        self.archive_tasks_default = os.path.expanduser(archive_default)

        if getattr(self.cli_args, "action", None) == "test":
            # Handle test sets first
            cli_sets = getattr(self.cli_args, "set", None)
            if cli_sets:
                # Validate test sets
                if not validate_test_sets(self.config, cli_sets):
                    sys.exit(99)

                # Process each test set independently (don't merge)
                try:
                    self.individual_test_sets = []
                    for set_name in cli_sets:
                        set_config = self.config["tests"]["set"][set_name]
                        logger.debug(f"Processing test set '{set_name}': {set_config}")

                        # Resolve effective values for this specific set (CLI > Set > Config)
                        effective_values = resolve_effective_values(
                            self.cli_args, set_config, self.config
                        )

                        # Store the set with its effective values for dispatch
                        self.individual_test_sets.append(
                            {
                                "name": set_name,
                                "config": set_config,
                                "effective_values": effective_values,
                            }
                        )

                    logger.info(f"Loaded test sets: {', '.join(cli_sets)}")
                except ValueError as e:
                    logger.critical(f"Failed to load test sets: {e}")
                    sys.exit(99)

                # Use the first test set's values for backward compatibility with global attributes
                # (This is mainly for validation and logging purposes)
                first_set_values = self.individual_test_sets[0]["effective_values"]
                effective_values = first_set_values

            else:
                self.individual_test_sets = []
                # No test sets, use regular config resolution
                effective_values = resolve_effective_values(
                    self.cli_args, {}, self.config
                )

            # Set parallel limit from effective values
            self.parallel_limit = effective_values.get(
                "parallel_limit"
            ) or self.tests.get("parallel_limit")

            # Handle artifact references with safe attribute access
            copr_artifact = getattr(self.cli_args, "copr", None)
            brew_artifact = getattr(self.cli_args, "brew", None)

            # Handle artifact references with test set override support
            set_copr_api = effective_values.get("copr_api", {})
            set_brew_api = effective_values.get("brew_api", {})

            # Collect all COPR references (CLI has priority, then test set, then config)
            self.copr_references = []
            if copr_artifact:
                # CLI artifacts (can be multiple from --copr arg1 --copr arg2)
                if isinstance(copr_artifact, list):
                    for artifact in copr_artifact:
                        if hasattr(artifact, "ref") and artifact.ref:
                            self.copr_references.extend(
                                artifact.ref
                                if isinstance(artifact.ref, list)
                                else [artifact.ref]
                            )
                else:
                    if hasattr(copr_artifact, "ref") and copr_artifact.ref:
                        self.copr_references.extend(
                            copr_artifact.ref
                            if isinstance(copr_artifact.ref, list)
                            else [copr_artifact.ref]
                        )
            else:
                # Test set or config references
                config_ref = set_copr_api.get("build_references") or self.copr_api.get(
                    "build_references"
                )
                if config_ref:
                    if isinstance(config_ref, list):
                        self.copr_references.extend(config_ref)
                    else:
                        self.copr_references.append(config_ref)

            # Collect all Brew references (CLI has priority, then test set, then config)
            self.brew_references = []
            if brew_artifact:
                # CLI artifacts (can be multiple from --brew arg1 --brew arg2)
                if isinstance(brew_artifact, list):
                    for artifact in brew_artifact:
                        if hasattr(artifact, "ref") and artifact.ref:
                            self.brew_references.extend(
                                artifact.ref
                                if isinstance(artifact.ref, list)
                                else [artifact.ref]
                            )
                else:
                    if hasattr(brew_artifact, "ref") and brew_artifact.ref:
                        self.brew_references.extend(
                            brew_artifact.ref
                            if isinstance(brew_artifact.ref, list)
                            else [brew_artifact.ref]
                        )
            else:
                # Test set or config references
                config_ref = set_brew_api.get("build_references") or self.brew_api.get(
                    "build_references"
                )
                if config_ref:
                    if isinstance(config_ref, list):
                        self.brew_references.extend(config_ref)
                    else:
                        self.brew_references.append(config_ref)

            # Keep single reference attributes for backward compatibility
            self.copr_reference = (
                self.copr_references[0] if self.copr_references else None
            )
            self.brew_reference = (
                self.brew_references[0] if self.brew_references else None
            )

            # Handle git URL and branch with effective values
            self.tests_git_url = (
                getattr(self.cli_args, "git_url", None)
                or self.tests.get("git_url")
                or self.project.get("repo_url")
            )
            self.tests_git_branch = effective_values.get(
                "git_branch"
            ) or self.tests.get("git_branch")

            # Handle plans with proper fallback
            cli_plans = getattr(self.cli_args, "plan", None)
            config_plans = self.tests.get("plan", [])
            self.plans = cli_plans or config_plans or []

            # Handle source/target configuration with effective values
            source_value = effective_values.get("source")
            target_value = effective_values.get("target")

            if not source_value:
                logger.critical("Source compose specification is required!")
                sys.exit(99)

            try:
                # Parse source and target specifications
                self.source_spec, self.target_spec = parse_source_target_config(
                    source_value, target_value
                )

                # Generate derived values
                self.upgrade_path_alias = generate_upgrade_path_alias(
                    self.source_spec, self.target_spec
                )

                # Generate automatic environment variables
                auto_env_vars = generate_environment_variables(
                    self.source_spec,
                    self.target_spec,
                    has_copr=bool(self.copr_references),
                    has_brew=bool(self.brew_references),
                )

                # Parse CLI environment variables
                cli_env_args = getattr(self.cli_args, "environment", None)
                cli_env_vars = parse_environment_variables(cli_env_args)

                # Get environment variables from test sets
                set_env_vars = effective_values.get("environment", {})

                # Merge environment variables (CLI > Test Set > Automatic)
                self.environment_variables = merge_set_environment_variables(
                    auto_env_vars, set_env_vars, cli_env_vars
                )

                # Parse architectures with effective values
                arch_input = effective_values.get("architectures")

                if not arch_input:
                    logger.critical("No architectures specified in CLI or config!")
                    sys.exit(99)

                self.architectures = parse_architectures(arch_input)

                # Determine boot method (CLI --uefi overrides config, falls back to "bios")
                config_boot_method = self.common.get("boot_method", "bios")
                boot_method = (
                    "uefi"
                    if getattr(self.cli_args, "uefi", False)
                    else config_boot_method
                )

                # Generate TMT context (architecture will be set per environment)
                self.tmt_context = generate_tmt_context(
                    self.source_spec, self.target_spec, boot_method=boot_method
                )

                # Handle CLI planfilter (tier-based filtering is handled in dispatch)
                cli_planfilter = getattr(self.cli_args, "planfilter", None)

                if cli_planfilter:
                    # CLI planfilter overrides everything
                    self.plan_filter = cli_planfilter
                    logger.info(f"Using CLI plan filter: {self.plan_filter}")
                else:
                    self.plan_filter = None

                # Store effective tiers for use in dispatch
                self.effective_tiers = effective_values.get("tiers")

                # Store test set config for potential use in dispatch
                self.test_set_config = (
                    {}
                )  # No longer needed as test sets are processed individually

                logger.info(f"Source: {self.source_spec['compose_name']}")
                logger.info(f"Upgrade path: {self.upgrade_path_alias}")

                # Log architectures
                if len(self.architectures) == 1:
                    logger.info(f"Architecture: {self.architectures[0]}")
                else:
                    logger.info(f"Architectures: {', '.join(self.architectures)}")

                # Log effective tiers if set
                if self.effective_tiers:
                    logger.info(f"Tiers: {', '.join(self.effective_tiers)}")

                # Log target compose if TARGET_COMPOSE_URL is specified via --environment
                if "TARGET_COMPOSE_URL" in cli_env_vars:
                    logger.debug("Target compose URL specified.")
                    logger.info(
                        f"Target compose: {os.path.basename(cli_env_vars['TARGET_COMPOSE_URL'].strip('/'))}"
                    )

                # Log any overridden automatic variables
                for var_name, cli_value in cli_env_vars.items():
                    if (
                        var_name in auto_env_vars
                        and auto_env_vars[var_name] != cli_value
                    ):
                        logger.info(
                            f"Environment variable {var_name} overridden: {auto_env_vars[var_name]} -> {cli_value}"
                        )

                # Log test set environment variables if any
                if set_env_vars:
                    logger.debug(f"Test set environment variables: {set_env_vars}")
                    for var_name, set_value in set_env_vars.items():
                        if (
                            var_name in auto_env_vars
                            and auto_env_vars[var_name] != set_value
                        ):
                            logger.info(
                                f"Environment variable {var_name} overridden by test set: {auto_env_vars[var_name]} -> {set_value}"
                            )

            except ValueError as e:
                logger.critical(f"Failed to parse source/target configuration: {e}")
                sys.exit(99)

    def _get_config_options(self):
        """Extract all options from the config file into a nested dictionary."""
        if not hasattr(self.config, "keys"):
            # If config is not a proper dict (shouldn't happen with new loader)
            logger.critical("Invalid configuration format")
            sys.exit(99)

        config_options = {
            section: (
                dict(self.config[section])
                if hasattr(self.config[section], "items")
                else self.config[section]
            )
            for section in self.config.keys()
        }

        # Essential options are now validated by validate_required_config()
        # This method just returns the structured config
        return config_options

    def __getattr__(self, item):
        """Allow direct access to options as attributes, prioritizing nested sections."""
        if hasattr(self.config, "get"):
            # Try to get the section directly
            if item in self.config:
                return self.config[item]

        # Fall back to searching in options structure
        if hasattr(self, "options"):
            for section, opts in self.options.items():
                if isinstance(opts, dict) and item in opts:
                    return opts[item]
                elif item == section:  # Allow access to entire section as attribute
                    return opts

        raise AttributeError(f"'ParsedOpts' object has no attribute '{item}'")


parsed_opts = ParsedOpts(cli_args=args)
