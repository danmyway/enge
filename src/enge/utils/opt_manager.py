# #!/usr/bin/env python3
import logging
import os
import sys
from typing import Dict, List, Any, Optional, Callable

from enge.utils.arg_parser import args
from enge.utils.config_parser import (
    load_config,
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


class TestingFarmEndpoint:
    def __init__(self, api_endpoint_url, log_artifact_baseurl):
        if not api_endpoint_url or not log_artifact_baseurl:
            raise ValueError(
                "Both api_endpoint_url and log_artifact_baseurl are required in [testing_farm] config."
            )
        self.api_endpoint_url = api_endpoint_url
        self.log_artifact_baseurl = log_artifact_baseurl


class ParsedOpts:
    def __init__(self, cli_args=None):
        self.cli_args = cli_args or args
        self._validation_hooks: Dict[str, Callable] = {}

        # Load configuration
        config_paths = (
            [self.cli_args.config]
            if self.cli_args.config
            else list(DEFAULT_CONFIG_PATHS)
        )
        self.config = load_config(paths=config_paths)

        # Centralized validation - this replaces all scattered validation
        self._validate_all_options()

        self.options = self._get_config_options()

        # Initialize Testing Farm endpoint after validation
        tf_cfg = self.testing_farm
        self.testing_farm_endpoint = TestingFarmEndpoint(
            tf_cfg.get("api_endpoint_url"), tf_cfg.get("log_artifact_baseurl")
        )

        # Initialize archive paths after validation
        archive_latest = self.common.get("archive_tasks_latest")
        archive_default = self.common.get("archive_tasks_default")
        # These are validated to be strings in _validate_static_configuration
        self.archive_tasks_latest = os.path.expanduser(str(archive_latest))
        self.archive_tasks_default = os.path.expanduser(str(archive_default))

        # Initialize test-specific attributes if this is a test action
        if getattr(self.cli_args, "action", None) == "test":
            self._initialize_test_attributes()

    def _validate_all_options(self):
        """Centralized validation entry point - replaces all scattered validation."""
        logger.debug("Starting centralized validation")

        # Phase 1: Static configuration validation
        self._validate_operational_defaults()
        self._validate_required_config()
        self._validate_static_configuration()

        # Phase 2: CLI argument validation
        self._validate_cli_arguments()

        # Phase 3: Option dependency validation
        self._validate_option_dependencies()

        # Phase 4: Register runtime validation hooks
        self._register_runtime_validation_hooks()

        logger.debug("Centralized validation completed successfully")

    def _validate_operational_defaults(self):
        """Validate operational defaults are present."""
        operational_defaults = {
            "tests": ["architectures", "git_branch", "parallel_limit"],
            "common": [
                "archive_tasks_latest",
                "archive_tasks_default",
                "logs_directory",
            ],
        }

        errors = []

        for section_name, required_keys in operational_defaults.items():
            if section_name not in self.config:
                errors.append(f"Missing operational section: [{section_name}]")
                continue

            section = self.config[section_name]
            if not isinstance(section, dict):
                errors.append(
                    f"Operational section [{section_name}] must be a dictionary"
                )
                continue

            for key in required_keys:
                value = section.get(key)
                if value is None or value == "":  # Check for None or empty string
                    errors.append(
                        f"Missing operational default: [{section_name}].{key}"
                    )

        if errors:
            logger.critical("Operational defaults validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            logger.critical(
                "This indicates a problem with the default configuration file."
            )
            sys.exit(99)

    def _validate_required_config(self):
        """Validate required configuration for operations that need it."""
        if getattr(self.cli_args, "action", None) in ["test", "rerun"]:
            required_sections = {
                "testing_farm": [
                    "api_key",
                    "cloud_resources_tag",
                    "api_endpoint_url",
                    "log_artifact_baseurl",
                ],
            }

            errors = []

            for section_name, required_keys in required_sections.items():
                if section_name not in self.config:
                    errors.append(f"Missing required section: [{section_name}]")
                    continue

                section = self.config[section_name]
                if not isinstance(section, dict):
                    errors.append(f"Section [{section_name}] must be a dictionary")
                    continue

                for key in required_keys:
                    value = section.get(key)
                    if not value:  # Empty string, None, or empty list/dict
                        errors.append(f"Missing required value: [{section_name}].{key}")

            if errors:
                logger.critical("Required configuration validation failed:")
                for error in errors:
                    logger.critical(f"  - {error}")
                sys.exit(99)

    def _validate_static_configuration(self):
        """Validate all static configuration rules."""
        errors = []

        # Validate archive paths type (consolidating assert statements)
        archive_latest = self.common.get("archive_tasks_latest")
        archive_default = self.common.get("archive_tasks_default")

        if not isinstance(archive_latest, str):
            errors.append("archive_tasks_latest must be a string")
        if not isinstance(archive_default, str):
            errors.append("archive_tasks_default must be a string")

        # Validate essential configuration sections (from dispatch/__main__.py)
        if not self.testing_farm or not self.testing_farm.get("api_key"):
            errors.append("Testing Farm API key not configured!")

        if not self.project or not self.project.get("name"):
            errors.append("Project name not configured!")

        # Validate git repository configuration
        tests_repo_url = self.tests.get("git_url") or self.project.get("repo_url")
        if not tests_repo_url:
            errors.append("Tests repository URL not configured!")

        # Validate architectures are configured (no empty defaults)
        architectures = self.tests.get("architectures")
        if not architectures:
            errors.append("No architectures configured in [tests] section!")
        elif not isinstance(architectures, list):
            errors.append("Architectures must be a list in [tests] section!")
        elif not all(isinstance(arch, str) and arch.strip() for arch in architectures):
            errors.append(
                "All architectures must be non-empty strings in [tests] section!"
            )

        if errors:
            logger.critical("Static configuration validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            sys.exit(99)

    def _validate_cli_arguments(self):
        """Validate CLI argument combinations and requirements."""
        errors = []

        # CLI argument interdependency validation (from arg_parser.py)
        if getattr(self.cli_args, "action", None) == "test":
            if not getattr(self.cli_args, "source", None) and not getattr(
                self.cli_args, "set", None
            ):
                errors.append("--source is required unless --set is provided")

        if errors:
            logger.critical("CLI argument validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            sys.exit(99)

    def _validate_option_dependencies(self):
        """Validate interdependencies between options."""
        errors = []

        # Test-specific validation
        if getattr(self.cli_args, "action", None) == "test":
            # Test sets validation
            cli_sets = getattr(self.cli_args, "set", None)
            if cli_sets:
                if not self._validate_test_sets_internal(cli_sets):
                    errors.append("Test sets validation failed")

            # Plan validation (from dispatch/__main__.py)
            cli_plans = getattr(self.cli_args, "plan", None)
            cli_tiers = getattr(self.cli_args, "tier", None)
            config_plans = self.tests.get("plan", [])

            if not cli_plans and not cli_tiers and not cli_sets and not config_plans:
                errors.append("No test plans specified in CLI or configuration!")

            # Validate tier configuration if tiers are specified
            if cli_tiers:
                tier_config = self.tests.get("tier", {})
                if not tier_config:
                    errors.append("No tier configuration found in config file!")
                else:
                    for tier in cli_tiers:
                        if tier not in tier_config:
                            available_tiers = list(tier_config.keys())
                            errors.append(
                                f"Tier '{tier}' not found. Available: {available_tiers}"
                            )

        if errors:
            logger.critical("Option dependency validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            sys.exit(99)

    def _validate_test_sets_internal(self, set_names: List[str]) -> bool:
        """Validate that specified test sets exist and are properly configured."""
        if not set_names:
            return True

        # Check if tests.set section exists
        if "tests" not in self.config:
            logger.error("Missing [tests] section in configuration")
            return False

        tests_section = self.config["tests"]
        if not isinstance(tests_section, dict):
            logger.error("[tests] section must be a dictionary")
            return False

        if "set" not in tests_section:
            logger.error("Missing [tests.set] section in configuration")
            return False

        test_sets = tests_section["set"]
        if not isinstance(test_sets, dict):
            logger.error("[tests.set] section must be a dictionary")
            return False

        # Validate each requested test set
        errors = []
        for set_name in set_names:
            if set_name not in test_sets:
                available_sets = list(test_sets.keys())
                errors.append(
                    f"Test set '{set_name}' not found. Available sets: {available_sets}"
                )
                continue

            set_config = test_sets[set_name]
            if not isinstance(set_config, dict):
                errors.append(f"Test set '{set_name}' must be a dictionary")
                continue

            # Validate test set structure
            if not self._validate_test_set_structure(set_name, set_config):
                errors.append(f"Test set '{set_name}' has invalid structure")

        if errors:
            logger.error("Test set validation failed:")
            for error in errors:
                logger.error(f"  - {error}")
            return False

        return True

    def _validate_test_set_structure(
        self, set_name: str, set_config: Dict[str, Any]
    ) -> bool:
        """Validate the structure of a single test set."""
        valid_keys = {
            "source",
            "target",
            "architectures",
            "git_branch",
            "parallel_limit",
            "tiers",
            "copr_api",
            "brew_api",
            "environment",
        }

        # Check for unknown keys
        unknown_keys = set(set_config.keys()) - valid_keys
        if unknown_keys:
            logger.warning(f"Test set '{set_name}' has unknown keys: {unknown_keys}")

        # Validate tiers if present
        if "tiers" in set_config:
            tiers = set_config["tiers"]
            if not isinstance(tiers, list):
                logger.error(f"Test set '{set_name}': 'tiers' must be a list")
                return False
            if not all(isinstance(tier, str) for tier in tiers):
                logger.error(f"Test set '{set_name}': all tiers must be strings")
                return False

        # Validate nested dictionary structures
        for dict_key in ["copr_api", "brew_api", "environment"]:
            if dict_key in set_config:
                value = set_config[dict_key]
                if not isinstance(value, dict):
                    logger.error(
                        f"Test set '{set_name}': '{dict_key}' must be a dictionary"
                    )
                    return False

        # Validate architectures format if present
        if "architectures" in set_config:
            arch = set_config["architectures"]
            if not isinstance(arch, list):
                logger.error(
                    f"Test set '{set_name}': 'architectures' must be a list of strings"
                )
                return False
            if not all(isinstance(item, str) and item.strip() for item in arch):
                logger.error(
                    f"Test set '{set_name}': 'architectures' list must contain only non-empty strings"
                )
                return False

        # Validate parallel_limit if present
        if "parallel_limit" in set_config:
            parallel_limit = set_config["parallel_limit"]
            if not isinstance(parallel_limit, int) or parallel_limit <= 0:
                logger.error(
                    f"Test set '{set_name}': 'parallel_limit' must be a positive integer"
                )
                return False

        return True

    def _register_runtime_validation_hooks(self):
        """Register hooks for runtime validation."""
        # Hook for git repository validation
        self.register_validation_hook(
            "validate_git_repository", self._validate_git_repository_hook
        )

        # Hook for artifact validation
        self.register_validation_hook("validate_artifact", self._validate_artifact_hook)

        # Hook for file existence validation
        self.register_validation_hook(
            "validate_file_exists", self._validate_file_exists_hook
        )

    def _validate_git_repository_hook(self, url: str) -> bool:
        """Runtime validation hook for git repository accessibility."""
        try:
            import requests

            response = requests.get(url, timeout=10)
            if response.status_code == 404:
                logger.critical(f"Git repository not found: {url}")
                return False
            return True
        except Exception as e:
            logger.warning(f"Could not validate git repository {url}: {e}")
            return True  # Don't fail on network issues during validation

    def _validate_artifact_hook(self, artifact_type: str, artifact_ref: str) -> bool:
        """Runtime validation hook for artifact validation."""
        # This will be called by tf_artifact.py instead of direct sys.exit
        if not artifact_ref:
            logger.critical(f"No {artifact_type} artifact reference provided!")
            return False
        return True

    def _validate_file_exists_hook(self, file_path: str) -> bool:
        """Runtime validation hook for file existence."""
        if not os.path.exists(file_path):
            logger.critical(f"File does not exist: {file_path}")
            return False
        return True

    def _initialize_test_attributes(self):
        """Initialize test-specific attributes after validation."""
        # Handle test sets first
        cli_sets = getattr(self.cli_args, "set", None)
        if cli_sets:
            # Test sets were already validated in _validate_option_dependencies
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
            first_set_values = self.individual_test_sets[0]["effective_values"]
            effective_values = first_set_values

        else:
            self.individual_test_sets = []
            # No test sets, use regular config resolution
            effective_values = resolve_effective_values(self.cli_args, {}, self.config)

        # Set parallel limit from effective values
        self.parallel_limit = effective_values.get("parallel_limit") or self.tests.get(
            "parallel_limit"
        )

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
        self.copr_reference = self.copr_references[0] if self.copr_references else None
        self.brew_reference = self.brew_references[0] if self.brew_references else None

        # Handle git URL and branch with effective values
        self.tests_git_url = (
            getattr(self.cli_args, "git_url", None)
            or self.tests.get("git_url")
            or self.project.get("repo_url")
        )
        self.tests_git_branch = effective_values.get("git_branch") or self.tests.get(
            "git_branch"
        )

        # Handle plans with proper fallback
        cli_plans = getattr(self.cli_args, "plan", None)
        config_plans = self.tests.get("plan", [])
        self.plans = cli_plans or config_plans or []

        # Handle source/target configuration with effective values
        source_value = effective_values.get("source")
        target_value = effective_values.get("target")

        # Source validation - centralized from scattered checks
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

            # Architecture validation - centralized from scattered checks
            if not arch_input:
                logger.critical("No architectures specified in CLI or config!")
                sys.exit(99)

            self.architectures = parse_architectures(arch_input)

            # Generate TMT context (architecture will be set per environment)
            self.tmt_context = generate_tmt_context(self.source_spec, self.target_spec)

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
                if var_name in auto_env_vars and auto_env_vars[var_name] != cli_value:
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

    # Public validation methods for external use
    def validate_opts(self) -> bool:
        """Public method to validate all options."""
        try:
            self._validate_all_options()
            return True
        except SystemExit:
            return False

    def check_dependencies(self) -> List[str]:
        """Check and return list of dependency validation errors without exiting."""
        errors = []

        # Collect all dependency errors without exiting
        try:
            # Check source/target configuration
            if getattr(self.cli_args, "action", None) == "test":
                if not hasattr(self, "source_spec") or not hasattr(self, "target_spec"):
                    errors.append("Source/target configuration not found!")

                # Check essential configuration
                if not self.testing_farm or not self.testing_farm.get("api_key"):
                    errors.append("Testing Farm API key not configured!")

                if not self.project or not self.project.get("name"):
                    errors.append("Project name not configured!")
        except Exception as e:
            errors.append(f"Validation check failed: {e}")

        return errors

    def register_validation_hook(self, hook_name: str, validation_func: Callable):
        """Register a validation hook for runtime validation."""
        self._validation_hooks[hook_name] = validation_func

    def run_validation_hook(self, hook_name: str, *args, **kwargs) -> bool:
        """Run a registered validation hook."""
        if hook_name in self._validation_hooks:
            return self._validation_hooks[hook_name](*args, **kwargs)
        return True

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

        # Essential options are now validated by centralized validation
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
