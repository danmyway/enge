# #!/usr/bin/env python3
import logging
import re
import os
from typing import Dict, List, Any, Optional, Callable

from enge.utils.arg_parser import get_arguments
from enge.utils.config_parser import (
    load_config,
)
from enge.utils.globals import (
    DEFAULT_USER_CONFIG_PATHS,
    RP_COMPATIBLE_EVENT,
)
from enge.utils.errors import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)


class TestingFarmEndpoint:
    def __init__(self, api_endpoint_url, log_artifact_baseurl):
        missing = []
        if not api_endpoint_url:
            missing.append("[testing_farm].api_endpoint_url")
        if not log_artifact_baseurl:
            missing.append("[testing_farm].log_artifact_baseurl")
        if missing:
            raise ConfigurationError(
                f"Required Testing Farm URL(s) not configured: {', '.join(missing)}"
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

        # Fill missing API tokens from environment variables.
        # Priority: user config > environment variable.
        self._apply_env_var_fallbacks()

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

    def _apply_env_var_fallbacks(self):
        """Populate missing API tokens from environment variables.

        Priority: user config value > environment variable.
        Only fills in a value when the config key is empty or missing.

        Supported environment variables:
            TESTING_FARM_API_TOKEN  -> [testing_farm].api_key
            REPORTPORTAL_API_TOKEN -> [reportportal].token
        """
        _ENV_FALLBACKS = (
            ("testing_farm", "api_key", "TESTING_FARM_API_TOKEN"),
            ("reportportal", "token", "REPORTPORTAL_API_TOKEN"),
        )

        for section, key, env_var in _ENV_FALLBACKS:
            section_dict = self.config.get(section)
            if not isinstance(section_dict, dict):
                continue

            current_value = section_dict.get(key, "")
            if current_value:
                # Config already has a value — keep it
                continue

            env_value = os.environ.get(env_var, "")
            if env_value:
                section_dict[key] = env_value
                logger.info(
                    f"Using {env_var} environment variable for [{section}].{key}"
                )

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

            tests_section = (
                self.config.get("tests", {}) if hasattr(self.config, "get") else {}
            )
            available_sets_dict = (
                tests_section.get("set", {}) if isinstance(tests_section, dict) else {}
            )
            available_sets = (
                list(available_sets_dict.keys())
                if isinstance(available_sets_dict, dict)
                else []
            )

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
        """Formerly validated git_ref via a redundant resolve_effective_values call.

        git_ref validation now lives in _initialize_test_attributes where the
        effective values are already resolved (single call per set).
        """
        pass

    def _collect_set_value(self, key: str) -> Optional[Any]:
        """Collect a value for a given key from all referenced sets; return the first non-empty.

        This mirrors "validate at the end" by checking resolved sources beyond top-level config.
        """
        cli_sets = getattr(self.cli_args, "set", None)
        if not cli_sets:
            return None
        try:
            sets_cfg = self.config.get("tests", {}).get("set", {})
            for set_name in cli_sets:
                val = sets_cfg.get(set_name, {}).get(key)
                if val:
                    return val
        except Exception:
            return None
        return None

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
                # Only check for None: "" is treated as absent at merge time
                # so it should never reach the validator from a real config load.
                value = section.get(key)
                if value is None:
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
            detail = "\n".join(f"  - {e}" for e in errors)
            raise ConfigurationError(
                f"Operational defaults validation failed:\n{detail}"
            )

    def _validate_required_config(self):
        """Validate required configuration for operations that need it."""
        if getattr(self.cli_args, "action", None) in ["test", "rerun"]:
            _HINTS = {
                "api_key": "Set in config or export TESTING_FARM_API_TOKEN",
                "cloud_resources_tag": "Ask your team for the BusinessUnit tag",
                "api_endpoint_url": "Set the Testing Farm API endpoint in config",
                "log_artifact_baseurl": "Set the artifact base URL in config",
                "composes_prod_url": "Set the composes API URL in config",
            }

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
                    if not value:
                        hint = _HINTS.get(key, "")
                        msg = f"Missing required value: [{section_name}].{key}"
                        if hint:
                            msg += f"\n    Hint: {hint}"
                        errors.append(msg)

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
                        _RP_HINTS = {
                            "token": "Set in config or export REPORTPORTAL_API_TOKEN",
                            "url": "Set the ReportPortal instance URL in config",
                            "project": "Set the ReportPortal project name in config",
                        }
                        for key in ["token", "url", "project"]:
                            if not rp_cfg.get(key):
                                hint = _RP_HINTS.get(key, "")
                                msg = f"Missing required value: [reportportal].{key} (required when event is set)"
                                if hint:
                                    msg += f"\n    Hint: {hint}"
                                errors.append(msg)
            except Exception:
                # Do not block on detection failures here; other validation will catch structural issues
                pass

            if errors:
                logger.critical("Required configuration validation failed:")
                for error in errors:
                    logger.critical(f"  - {error}")
                detail = "\n".join(f"  - {e}" for e in errors)
                raise ConfigurationError(f"Required configuration missing:\n{detail}")

        # ReportPortal configuration required for the 'reportportal' subcommand
        # Only require token here; URL/project may be provided elsewhere and are optional overrides in config
        if getattr(self.cli_args, "action", None) == "reportportal":
            rp_cfg = self.config.get("reportportal", {})
            if not isinstance(rp_cfg, dict):
                logger.critical("[reportportal] section must be a dictionary")
                raise ConfigurationError("ReportPortal configuration invalid")
            if not rp_cfg.get("token"):
                logger.critical(
                    "Missing required value: [reportportal].token\n"
                    "    Hint: Set in config or export REPORTPORTAL_API_TOKEN"
                )
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

        if not self.testing_farm or not self.testing_farm.get("api_key"):
            errors.append(
                "Testing Farm API key not configured!\n"
                "    Hint: Set [testing_farm].api_key or export TESTING_FARM_API_TOKEN"
            )

        if not self.project or not self.project.get("name"):
            errors.append(
                "Project name not configured!\n"
                "    Hint: Set [project].name in config"
            )

        tests_repo_url = self.tests.get("git_url") or self.project.get("repo_url")
        if not tests_repo_url:
            errors.append(
                "Tests repository URL not configured!\n"
                "    Hint: Set [tests].git_url or [project].repo_url in config"
            )

        # Validate [tests].context type if present
        tests_section = (
            self.config.get("tests", {}) if hasattr(self.config, "get") else {}
        )
        if (
            tests_section
            and "context" in tests_section
            and not isinstance(tests_section.get("context"), dict)
        ):
            errors.append("[tests].context must be a dictionary")

        # Shape validation: [tests].tiers must be a list of strings;
        # [tests].tier must be a table (dict).  A mixup between the two keys
        # must fail loudly — never produce a KeyError: 0 at dispatch time.
        if tests_section:
            config_tiers = tests_section.get("tiers")
            config_tier = tests_section.get("tier")
            shape_error = False
            if config_tiers is not None and not isinstance(config_tiers, list):
                errors.append(
                    "[tests].tiers must be a list of strings (tier selection); "
                    "[tests].tier must be a table (filter definitions). "
                    f"Got [tests].tiers={type(config_tiers).__name__!r}."
                )
                shape_error = True
            if config_tier is not None and not isinstance(config_tier, dict):
                errors.append(
                    "[tests].tier must be a table (filter definitions); "
                    "[tests].tiers must be a list of strings (tier selection). "
                    f"Got [tests].tier={type(config_tier).__name__!r}."
                )
                shape_error = True
            if not shape_error and isinstance(config_tiers, list):
                if not all(isinstance(t, str) for t in config_tiers):
                    errors.append(
                        "[tests].tiers must contain only strings (tier names)."
                    )

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
            detail = "\n".join(f"  - {e}" for e in errors)
            raise ConfigurationError(f"Static configuration invalid:\n{detail}")

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
                    "--source is required unless provided via [tests].source or --set\n"
                    "    Hint: enge test -s 9.7 ... or enge test -S <set-name>"
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
            detail = "\n".join(f"  - {e}" for e in errors)
            raise ValidationError(f"Option dependency validation failed:\n{detail}")

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
            "pool",
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

    # Public validation methods for external use
    def validate_opts(self) -> bool:
        """Public method to validate all options."""
        try:
            self._validate_all_options()
            return True
        except SystemExit:
            return False

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
