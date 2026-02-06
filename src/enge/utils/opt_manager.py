# #!/usr/bin/env python3
import logging
import re
import os
import sys
from typing import Dict, List, Any, Optional, Callable
from contextlib import contextmanager

from enge.utils.arg_parser import get_arguments
from enge.utils.config_parser import (
    load_config,
)
from enge.utils.globals import (
    DEFAULT_USER_CONFIG_PATHS,
    PARALLEL_LIMIT_DEFAULT,
    RP_COMPATIBLE_EVENT,
)
from enge.utils.source_target_parser import (
    parse_source_target_config,
    generate_upgrade_path_alias,
    generate_environment_variables,
    generate_tmt_context,
    apply_centos_context_overrides,
    parse_environment_variables,
    parse_tmt_context,
    merge_tmt_context,
    parse_architectures,
    resolve_effective_values,
    merge_set_environment_variables,
)
from enge.utils.errors import ConfigurationError, ValidationError

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
        # Avoid import-time parsing; default to parsing now if not provided
        self.cli_args = cli_args if cli_args is not None else get_arguments()
        self._validation_hooks: Dict[str, Callable] = {}

        # Load configuration
        config_paths = (
            [self.cli_args.config]
            if self.cli_args.config
            else list(DEFAULT_USER_CONFIG_PATHS)
        )
        self.config = load_config(paths=config_paths)

        # Expand --set-regex into concrete set names before any validation
        self._expand_set_regex_arguments()

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

        # Phase 4: Validate effective configuration (after merge/priority resolution)
        self._validate_effective_configuration()

        # Phase 5: Register runtime validation hooks
        self._register_runtime_validation_hooks()

        logger.debug("Centralized validation completed successfully")

    def _expand_set_regex_arguments(self) -> None:
        """Expand --set-regex patterns into concrete set names.

        This runs after configuration load and before validation so that
        existing validation and dispatch logic operates on resolved set names.
        """
        try:
            action = getattr(self.cli_args, "action", None)
            if action != "test":
                return
            patterns = getattr(self.cli_args, "set_regex", None)
            if not patterns:
                return

            tests_section = self.config.get("tests", {})
            available_sets_dict = tests_section.get("set", {})
            available_sets = list(available_sets_dict.keys())

            if not available_sets:
                raise ValidationError(
                    "No test sets configured; --set-regex cannot be used."
                )

            # Start with any explicitly provided --set values
            resolved_sets = []
            explicit_sets = getattr(self.cli_args, "set", None) or []
            for name in explicit_sets:
                if name not in resolved_sets:
                    resolved_sets.append(name)

            errors: List[str] = []

            for pattern in patterns:
                try:
                    regex = re.compile(pattern)
                except re.error as e:
                    errors.append(f"Invalid --set-regex pattern '{pattern}': {e}")
                    continue

                matched = [name for name in available_sets if regex.search(name)]
                if not matched:
                    errors.append(
                        f"--set-regex pattern '{pattern}' matched no sets. Available: {available_sets}"
                    )
                    continue

                for name in matched:
                    if name not in resolved_sets:
                        resolved_sets.append(name)

                logger.info(
                    "--set-regex '%s' expanded to: %s",
                    pattern,
                    ", ".join(matched),
                )

            if errors:
                for err in errors:
                    logger.error(err)
                raise ValidationError(
                    "One or more --set-regex patterns were invalid or matched nothing"
                )

            # Replace CLI --set with expanded list
            setattr(self.cli_args, "set", resolved_sets)

        except Exception as e:
            # Surface as validation error for consistent handling
            if isinstance(e, (ValidationError, ConfigurationError)):
                raise
            logger.critical(f"Failed to process --set-regex: {e}")
            raise ValidationError("Failed to process --set-regex")

    def _validate_effective_configuration(self):
        """Validate required values after resolving effective configuration.

        This mirrors validation at the end of the merge: CLI > Set > Config.
        """
        errors: List[str] = []

        action = getattr(self.cli_args, "action", None)
        if action == "test":
            cli_sets = getattr(self.cli_args, "set", None)
            if cli_sets:
                try:
                    sets_cfg = self.config.get("tests", {}).get("set", {})
                except Exception:
                    sets_cfg = {}

                for set_name in cli_sets:
                    set_cfg = (
                        sets_cfg.get(set_name, {}) if isinstance(sets_cfg, dict) else {}
                    )
                    try:
                        effective = resolve_effective_values(
                            self.cli_args, set_cfg, self.config
                        )
                    except Exception:
                        effective = {}
                    if not effective.get("git_ref"):
                        errors.append(
                            f"Missing effective git_ref (CLI/Set/Config) for set '{set_name}'"
                        )
            else:
                try:
                    effective = resolve_effective_values(self.cli_args, {}, self.config)
                except Exception:
                    effective = {}
                if not effective.get("git_ref"):
                    errors.append("Missing effective git_ref (CLI/Config)")

        if errors:
            logger.critical("Effective configuration validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            raise ConfigurationError("Effective configuration invalid")

    def _validate_operational_defaults(self):
        """Validate operational defaults are present."""
        # Only core operational defaults here; other requirements are validated contextually
        operational_defaults = {
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
                # Generic check for other operational defaults
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
            raise ConfigurationError("Operational defaults validation failed")

    def _validate_required_config(self):
        """Validate required configuration for operations that need it."""
        if getattr(self.cli_args, "action", None) in ["test", "rerun"]:
            required_sections = {
                "testing_farm": [
                    "api_key",
                    "cloud_resources_tag",
                    "api_endpoint_url",
                    "log_artifact_baseurl",
                    "composes_prod_url",
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

            # Conditional requirements based on artifact usage (brew)
            try:
                brew_used = False
                cli_brew = getattr(self.cli_args, "brew", None)
                if cli_brew:
                    brew_used = True
                else:
                    cli_sets = getattr(self.cli_args, "set", None)
                    if cli_sets:
                        sets_cfg = self.config.get("tests", {}).get("set", {})
                        for set_name in cli_sets:
                            set_brew_refs = (
                                sets_cfg.get(set_name, {})
                                .get("brew_api", {})
                                .get("build_references")
                            )
                            if set_brew_refs:
                                brew_used = True
                                break
                    else:
                        top_brew_refs = self.config.get("brew_api", {}).get(
                            "build_references"
                        )
                        if top_brew_refs:
                            brew_used = True

                if brew_used:
                    brew_section = self.config.get("brew_api", {})
                    if not isinstance(brew_section, dict):
                        errors.append("[brew_api] section must be a dictionary")
                    else:
                        for key in ["session_url", "taskid_url"]:
                            if not brew_section.get(key):
                                provided_by_set = False
                                cli_sets = getattr(self.cli_args, "set", None)
                                if cli_sets:
                                    sets_cfg = self.config.get("tests", {}).get(
                                        "set", {}
                                    )
                                    for set_name in cli_sets:
                                        if (
                                            sets_cfg.get(set_name, {})
                                            .get("brew_api", {})
                                            .get(key)
                                        ):
                                            provided_by_set = True
                                            break
                                if not provided_by_set:
                                    errors.append(
                                        f"Missing required value: [brew_api].{key} (required when using brew artifacts)"
                                    )
            except Exception:
                # Do not block on detection failures
                pass

            # Fail fast: if an RP-compatible event is declared (via CLI or test sets),
            # require complete ReportPortal credentials (token, url, project).
            try:
                rp_event_required = False
                event_name = getattr(self.cli_args, "event", None)
                if event_name and event_name in RP_COMPATIBLE_EVENT:
                    rp_event_required = True
                else:
                    cli_sets = getattr(self.cli_args, "set", None)
                    if cli_sets:
                        sets_cfg = self.config.get("tests", {}).get("set", {})
                        for set_name in cli_sets:
                            set_event = (
                                sets_cfg.get(set_name, {}).get("event")
                                if isinstance(sets_cfg, dict)
                                else None
                            )
                            if set_event and set_event in RP_COMPATIBLE_EVENT:
                                rp_event_required = True
                                break

                if rp_event_required:
                    rp_cfg = self.config.get("reportportal", {})
                    if not isinstance(rp_cfg, dict):
                        errors.append("[reportportal] section must be a dictionary")
                    else:
                        for key in ["token", "url", "project"]:
                            if not rp_cfg.get(key):
                                errors.append(
                                    f"Missing required value: [reportportal].{key} (required when event is set for ReportPortal)"
                                )
            except Exception:
                # Do not block on detection failures here; other validation will catch structural issues
                pass

            if errors:
                logger.critical("Required configuration validation failed:")
                for error in errors:
                    logger.critical(f"  - {error}")
                raise ConfigurationError("Required configuration missing")

        # ReportPortal configuration required for the 'reportportal' subcommand
        # Only require token here; URL/project may be provided elsewhere and are optional overrides in config
        if getattr(self.cli_args, "action", None) == "reportportal":
            rp_cfg = self.config.get("reportportal", {})
            if not isinstance(rp_cfg, dict):
                logger.critical("[reportportal] section must be a dictionary")
                raise ConfigurationError("ReportPortal configuration invalid")
            if not rp_cfg.get("token"):
                logger.critical("Missing required value: [reportportal].token")
                raise ConfigurationError("ReportPortal configuration missing")

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

        # Validate [tests].context type if present
        tests_section = self.config.get("tests", {})
        if (
            tests_section
            and "context" in tests_section
            and not isinstance(tests_section.get("context"), dict)
        ):
            errors.append("[tests].context must be a dictionary")

        # Validate architectures with priority: CLI > Set > Config
        # - When using sets, per-set architectures are validated later during resolution
        # - When not using sets, require architectures from CLI or config
        cfg_architectures = self.tests.get("architectures")
        cli_architectures = getattr(self.cli_args, "architectures", None)
        action = getattr(self.cli_args, "action", None)
        using_sets = action == "test" and bool(getattr(self.cli_args, "set", None))

        if action == "test":
            if using_sets:
                # Do not require top-level architectures; sets will supply them.
                # If top-level are provided, validate type/content.
                if cfg_architectures is not None:
                    if not isinstance(cfg_architectures, list):
                        errors.append(
                            "Architectures must be a list in [tests] section!"
                        )
                    elif not all(
                        isinstance(arch, str) and arch.strip()
                        for arch in cfg_architectures
                    ):
                        errors.append(
                            "All architectures must be non-empty strings in [tests] section!"
                        )
            else:
                effective_architectures = cli_architectures or cfg_architectures
                if not effective_architectures:
                    errors.append(
                        "No architectures configured. Provide --arch/--architectures, set [tests].architectures, or use --set with per-set architectures."
                    )
                else:
                    if not isinstance(effective_architectures, list):
                        errors.append(
                            "Architectures must be a list when provided via config or CLI."
                        )
                    elif not all(
                        isinstance(arch, str) and arch.strip()
                        for arch in effective_architectures
                    ):
                        errors.append("All architectures must be non-empty strings.")

        if errors:
            logger.critical("Static configuration validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            raise ConfigurationError("Static configuration invalid")

    def _validate_cli_arguments(self):
        """Validate CLI argument combinations and requirements."""
        errors = []

        # CLI argument interdependency validation (from arg_parser.py)
        if getattr(self.cli_args, "action", None) == "test":
            has_source_cli = bool(getattr(self.cli_args, "source", None))
            has_sets = bool(getattr(self.cli_args, "set", None))
            # Allow source to come from config when not using sets
            has_source_cfg = bool(self.config.get("tests", {}).get("source"))
            if not has_source_cli and not has_sets and not has_source_cfg:
                errors.append(
                    "--source is required unless provided via [tests].source or --set"
                )

        if errors:
            logger.critical("CLI argument validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            raise ValidationError("CLI argument validation failed")

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
            config_plans = self.tests.get("plans", [])

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

            # Warn on mismatches between CLI and config sources/targets
            cli_source = getattr(self.cli_args, "source", None)
            cli_target = getattr(self.cli_args, "target", None)

            if not cli_sets:
                cfg_source = self.tests.get("source")
                cfg_target = self.tests.get("target")
                if cli_source and cfg_source and cli_source != cfg_source:
                    logger.warning(
                        "CLI --source overrides [tests].source (values differ: %s != %s)",
                        cli_source,
                        cfg_source,
                    )
                if cli_target and cfg_target and cli_target != cfg_target:
                    logger.warning(
                        "CLI --target overrides [tests].target (values differ: %s != %s)",
                        cli_target,
                        cfg_target,
                    )
            else:
                # For each test set, warn if CLI source/target overrides set values
                try:
                    test_sets_cfg = self.config["tests"]["set"]
                    for set_name in cli_sets:
                        set_cfg = test_sets_cfg.get(set_name, {})
                        set_source = set_cfg.get("source")
                        set_target = set_cfg.get("target")
                        if cli_source and set_source and cli_source != set_source:
                            logger.warning(
                                "CLI --source overrides [tests.set.%s].source (values differ: %s != %s)",
                                set_name,
                                cli_source,
                                set_source,
                            )
                        if cli_target and set_target and cli_target != set_target:
                            logger.warning(
                                "CLI --target overrides [tests.set.%s].target (values differ: %s != %s)",
                                set_name,
                                cli_target,
                                set_target,
                            )
                except Exception:
                    # If structure is missing, it's already reported above
                    pass

        if errors:
            logger.critical("Option dependency validation failed:")
            for error in errors:
                logger.critical(f"  - {error}")
            raise ValidationError("Option dependency validation failed")

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
            "git_url",
            "git_ref",
            "parallel_limit",
            "tiers",
            "plans",  # Add plans as a valid key
            "event",  # Add event as a valid key
            "copr_api",
            "brew_api",
            "environment",
            "reportportal",
            "context",
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

        # Validate plans if present
        if "plans" in set_config:
            plans = set_config["plans"]
            if not isinstance(plans, list):
                logger.error(f"Test set '{set_name}': 'plans' must be a list")
                return False
            if not all(isinstance(plan, str) for plan in plans):
                logger.error(f"Test set '{set_name}': all plans must be strings")
                return False

        # Validate nested dictionary structures
        for dict_key in [
            "copr_api",
            "brew_api",
            "environment",
            "reportportal",
            "context",
        ]:
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
            from enge.utils.http_client import http_get

            response = http_get(url, timeout=10)
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

    # ------------------------------------------------------------------
    # _initialize_test_attributes and its helpers
    # ------------------------------------------------------------------

    def _resolve_test_sets(self) -> dict:
        """Resolve test sets and effective values; set ``parallel_limit``.

        Returns the *effective_values* dict used by subsequent helpers.
        """
        cli_sets = getattr(self.cli_args, "set", None)

        if cli_sets:
            try:
                self.individual_test_sets = []
                for set_name in cli_sets:
                    set_config = self.config["tests"]["set"][set_name]
                    logger.debug(f"Processing test set '{set_name}': {set_config}")

                    effective_values = resolve_effective_values(
                        self.cli_args, set_config, self.config
                    )

                    self._warn_arch_override(
                        set_config.get("architectures"),
                        label=f"[tests.set.{set_name}].architectures",
                    )

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
                raise ConfigurationError("Failed to load test sets") from e

            effective_values = self.individual_test_sets[0]["effective_values"]
        else:
            self.individual_test_sets = []
            effective_values = resolve_effective_values(self.cli_args, {}, self.config)
            self._warn_arch_override(
                self.tests.get("architectures"),
                label="[tests].architectures",
            )

        self.parallel_limit = (
            effective_values.get("parallel_limit")
            or self.tests.get("parallel_limit")
            or PARALLEL_LIMIT_DEFAULT
        )

        return effective_values

    def _warn_arch_override(self, config_arch: Optional[Any], *, label: str) -> None:
        """Log a warning when CLI ``--architectures`` differs from *config_arch*."""
        cli_arch = getattr(self.cli_args, "architectures", None)
        if not cli_arch or not config_arch:
            return
        try:
            cli_set = {str(a).strip() for a in cli_arch if a}
            cfg_set = {str(a).strip() for a in config_arch if a}
            if cli_set and cfg_set and cli_set != cfg_set:
                logger.warning(
                    "CLI --architectures overrides %s (values differ: %s != %s)",
                    label,
                    sorted(cfg_set),
                    sorted(cli_set),
                )
        except Exception:
            pass

    @staticmethod
    def _collect_refs_from_cli(cli_artifact) -> List[str]:
        """Extract build references from a CLI artifact argument (single or list)."""
        refs: List[str] = []
        artifacts = cli_artifact if isinstance(cli_artifact, list) else [cli_artifact]
        for artifact in artifacts:
            if hasattr(artifact, "ref") and artifact.ref:
                if isinstance(artifact.ref, list):
                    refs.extend(artifact.ref)
                else:
                    refs.append(artifact.ref)
        return refs

    def _collect_artifact_references(self, effective_values: dict) -> None:
        """Populate ``copr_references`` and ``brew_references``."""
        copr_cli = getattr(self.cli_args, "copr", None)
        brew_cli = getattr(self.cli_args, "brew", None)

        self.copr_references = self._collect_refs_for_type(
            copr_cli,
            effective_values.get("copr_api", {}),
            self.copr_api,
        )
        self.brew_references = self._collect_refs_for_type(
            brew_cli,
            effective_values.get("brew_api", {}),
            self.brew_api,
        )

    @staticmethod
    def _collect_refs_for_type(
        cli_artifact, set_api: dict, config_api: dict
    ) -> List[str]:
        """Collect build references for a single artifact type (COPR or Brew)."""
        refs: List[str] = []
        if cli_artifact:
            artifacts = (
                cli_artifact if isinstance(cli_artifact, list) else [cli_artifact]
            )
            for artifact in artifacts:
                if hasattr(artifact, "ref") and artifact.ref:
                    if isinstance(artifact.ref, list):
                        refs.extend(artifact.ref)
                    else:
                        refs.append(artifact.ref)
        else:
            config_ref = set_api.get("build_references") or config_api.get(
                "build_references"
            )
            if config_ref:
                if isinstance(config_ref, list):
                    refs.extend(config_ref)
                else:
                    refs.append(config_ref)
        return refs

    def _resolve_source_and_environment(self, effective_values: dict) -> tuple:
        """Parse source/target specs and merge environment variables.

        Sets ``tests_git_url``, ``tests_git_ref``, ``plans``, ``source_spec``,
        ``target_spec``, ``upgrade_path_alias``, ``environment_variables``,
        ``architectures``, and ``effective_tiers``.

        Returns ``(cli_env_vars, auto_env_vars, set_env_vars)`` for use by
        downstream helpers and logging.
        """
        # Git URL / ref / plans
        self.tests_git_url = (
            getattr(self.cli_args, "git_url", None)
            or self.tests.get("git_url")
            or self.project.get("repo_url")
        )
        self.tests_git_ref = effective_values.get("git_ref") or self.tests.get(
            "git_ref"
        )
        cli_plans = getattr(self.cli_args, "plan", None)
        self.plans = cli_plans or self.tests.get("plans", []) or []

        # Source / target parsing
        source_value = effective_values.get("source")
        target_value = effective_values.get("target")
        if not source_value:
            logger.critical("Source compose specification is required!")
            raise ValidationError("Source compose specification is required")

        self.source_spec, self.target_spec = parse_source_target_config(
            source_value, target_value, self.config
        )
        self.upgrade_path_alias = generate_upgrade_path_alias(
            self.source_spec, self.target_spec
        )

        # Environment variables
        copr_artifact = getattr(self.cli_args, "copr", None)
        brew_artifact = getattr(self.cli_args, "brew", None)
        auto_env_vars = generate_environment_variables(
            self.source_spec,
            self.target_spec,
            has_copr=bool(copr_artifact or self.copr_references),
            has_brew=bool(brew_artifact or self.brew_references),
        )
        cli_env_vars = parse_environment_variables(
            getattr(self.cli_args, "environment", None)
        )
        set_env_vars = effective_values.get("environment", {})

        self.environment_variables = merge_set_environment_variables(
            auto_env_vars,
            set_env_vars,
            cli_env_vars,
            self.config,
            self.cli_args,
            effective_values.get("reportportal", {}),
            None,
            None,
            None,
            auto_env_vars.get("SOURCE_RELEASE"),
            auto_env_vars.get("TARGET_RELEASE"),
            self.source_spec["compose_name"],
            self.target_spec["compose_name"],
            event=effective_values.get("event"),
        )

        # Architectures
        arch_input = effective_values.get("architectures")
        if not arch_input:
            logger.critical("No architectures specified in CLI or config!")
            raise ValidationError("No architectures specified in CLI or config")
        self.architectures = parse_architectures(arch_input)

        # Tiers
        self.effective_tiers = effective_values.get("tiers")

        return cli_env_vars, auto_env_vars, set_env_vars

    def _resolve_tmt_context_and_flags(self, effective_values: dict, cli_sets) -> None:
        """Build TMT context, apply overrides (CentOS, RHSM), and set plan filter."""
        first_tier = self.effective_tiers[0] if self.effective_tiers else None
        self.tmt_context = generate_tmt_context(
            self.source_spec,
            self.target_spec,
            event=effective_values.get("event"),
            tier=first_tier,
        )
        self.tmt_context = apply_centos_context_overrides(
            self.tmt_context,
            self.source_spec,
            self.target_spec,
            self.environment_variables,
        )

        # Non-set mode: apply config context then CLI overrides
        if not cli_sets:
            config_context = effective_values.get("context", {}) or {}
            if config_context:
                self.tmt_context = merge_tmt_context(self.tmt_context, config_context)
            try:
                cli_context = parse_tmt_context(getattr(self.cli_args, "context", None))
                if cli_context:
                    self.tmt_context = merge_tmt_context(self.tmt_context, cli_context)
            except ValueError as e:
                logger.critical(f"Failed to parse --context: {e}")
                raise ValidationError("Invalid --context format") from e

        # RHSM stage CDN flag
        if getattr(self.cli_args, "only_rhsm_stage_cdn", False):
            self.environment_variables["RHSM_MODE"] = "stage"
            logger.info(
                "Added RHSM_MODE=stage to environment variables (--only-rhsm-stage-cdn)"
            )
            self.tmt_context["product_phase"] = "rc"
            logger.info("Added product_phase=rc to TMT context (--only-rhsm-stage-cdn)")

        # Plan filter
        cli_planfilter = getattr(self.cli_args, "planfilter", None)
        if cli_planfilter:
            self.plan_filter = cli_planfilter
            logger.info(f"Using CLI plan filter: {self.plan_filter}")
        else:
            self.plan_filter = None

        self.test_set_config = {}

    @staticmethod
    def _log_initialization_summary(
        cli_env_vars: dict,
        auto_env_vars: dict,
        set_env_vars: dict,
        source_spec: dict,
        upgrade_path_alias: str,
        architectures: list,
        effective_tiers: Optional[list],
    ) -> None:
        """Log a summary of the resolved configuration."""
        logger.info(f"Source: {source_spec['compose_name']}")
        logger.info(f"Upgrade path: {upgrade_path_alias}")

        if len(architectures) == 1:
            logger.info(f"Architecture: {architectures[0]}")
        else:
            logger.info(f"Architectures: {', '.join(architectures)}")

        if effective_tiers:
            logger.info(f"Tiers: {', '.join(effective_tiers)}")

        if "TARGET_COMPOSE_URL" in cli_env_vars:
            from enge.utils.source_target_parser import (
                parse_target_compose_from_url,
            )

            logger.debug("Target compose URL specified.")
            target_compose_url = cli_env_vars["TARGET_COMPOSE_URL"]
            target_compose = parse_target_compose_from_url(target_compose_url)
            if target_compose:
                logger.info(f"Target compose: {target_compose}")
            else:
                logger.info(
                    f"Target compose: {os.path.basename(target_compose_url.strip('/'))}"
                )

        for var_name, cli_value in cli_env_vars.items():
            if var_name in auto_env_vars and auto_env_vars[var_name] != cli_value:
                logger.warning(
                    f"Environment variable {var_name} overridden: "
                    f"{auto_env_vars[var_name]} -> {cli_value}"
                )

        if set_env_vars:
            logger.debug(f"Test set environment variables: {set_env_vars}")
            for var_name, set_value in set_env_vars.items():
                if var_name in auto_env_vars and auto_env_vars[var_name] != set_value:
                    logger.info(
                        f"Environment variable {var_name} overridden by test set: "
                        f"{auto_env_vars[var_name]} -> {set_value}"
                    )

    def _initialize_test_attributes(self):
        """Initialize test-specific attributes after validation."""
        effective_values = self._resolve_test_sets()
        self._collect_artifact_references(effective_values)

        try:
            cli_env_vars, auto_env_vars, set_env_vars = (
                self._resolve_source_and_environment(effective_values)
            )
            self._resolve_tmt_context_and_flags(
                effective_values,
                getattr(self.cli_args, "set", None),
            )
            self._log_initialization_summary(
                cli_env_vars,
                auto_env_vars,
                set_env_vars,
                self.source_spec,
                self.upgrade_path_alias,
                self.architectures,
                self.effective_tiers,
            )
        except ValueError as e:
            logger.critical(f"Failed to parse source/target configuration: {e}")
            raise ValidationError("Failed to parse source/target configuration") from e

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
            raise ConfigurationError("Invalid configuration format")

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


class _LazyParsedOpts:
    """Lazy accessor for a singleton ParsedOpts instance.

    Creates the ParsedOpts only upon first attribute access, parsing CLI args
    at that moment. This avoids side effects during module import and makes the
    package more friendly to library usage.
    """

    _instance: Optional[ParsedOpts] = None

    def _ensure(self) -> ParsedOpts:
        if self._instance is None:
            self._instance = ParsedOpts()
        return self._instance

    def __getattr__(self, item):
        return getattr(self._ensure(), item)

    def set(self, instance: "ParsedOpts") -> None:
        self._instance = instance

    @contextmanager
    def use(self, instance: "ParsedOpts"):
        previous = self._instance
        self._instance = instance
        try:
            yield
        finally:
            self._instance = previous


parsed_opts = _LazyParsedOpts()
