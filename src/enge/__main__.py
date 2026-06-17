#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK

import logging
import sys

from enge.utils.errors import (
    ConfigurationError,
    ValidationError,
    NetworkError,
    UserAbort,
)
from enge.utils.globals import (
    EXIT_CONFIG_ERROR,
    EXIT_PARTIAL_FAILURE,
    EXIT_INTERRUPT,
    EXIT_GENERAL_ERROR,
)
from enge.utils.arg_parser import get_arguments
from enge.utils.console import console, configure_console, EngeLogHandler

# Placeholder for tests to patch without triggering opt_manager initialization
parsed_opts = None


def setup_logging():
    """Setup logging configuration based on verbosity flags."""
    from enge.utils.globals import VERBOSE

    logging.addLevelName(VERBOSE, "VERBOSE")

    try:
        cli_args = get_arguments()
        verbosity = getattr(cli_args, "verbose", 0) or 0
        debug_flag = bool(getattr(cli_args, "debug", False))
        output_format = getattr(cli_args, "output_format", "terminal")
    except Exception:
        verbosity = 0
        debug_flag = False
        output_format = "terminal"

    if debug_flag or verbosity >= 2:
        log_level = logging.DEBUG
    elif verbosity == 1:
        log_level = VERBOSE
    else:
        log_level = logging.INFO

    configure_console(output_format)

    handler = EngeLogHandler()
    logging.basicConfig(level=log_level, handlers=[handler], format="%(message)s")


def _handle_list_sets():
    """Handle --list-sets and --list-sets-detail before full validation."""
    cli_args = get_arguments()
    if getattr(cli_args, "action", None) != "test":
        return False
    list_brief = getattr(cli_args, "list_sets", False)
    list_detail = getattr(cli_args, "list_sets_detail", False)
    if not list_brief and not list_detail:
        return False

    from enge.utils.config_parser import load_config
    from enge.utils.globals import DEFAULT_USER_CONFIG_PATHS
    from rich.table import Table
    from rich import box

    config_paths = (
        [cli_args.config] if cli_args.config else list(DEFAULT_USER_CONFIG_PATHS)
    )
    config = load_config(paths=config_paths)
    sets_cfg = config.get("tests", {}).get("set", {})

    if not sets_cfg:
        console.print("No test sets configured.")
        return True

    def _format_list(value):
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value) if value is not None else ""

    if list_detail:
        table = Table(box=box.ROUNDED, show_lines=True)
        table.add_column("Set", style="bold")
        table.add_column("Source")
        table.add_column("Target")
        table.add_column("Architectures")
        table.add_column("Tiers")
        table.add_column("Event")
        table.add_column("Artifacts")
        table.add_column("Environment")

        for name, cfg in sets_cfg.items():
            artifacts = []
            for api_key in ["copr_api", "brew_api"]:
                refs = cfg.get(api_key, {}).get("build_references")
                if refs:
                    label = api_key.replace("_api", "")
                    artifacts.append(f"{label}: {_format_list(refs)}")
            env = cfg.get("environment", {})
            env_str = ", ".join(f"{k}={v}" for k, v in env.items()) if env else ""

            table.add_row(
                name,
                cfg.get("source", ""),
                cfg.get("target", "(derived)"),
                _format_list(cfg.get("architectures", [])),
                _format_list(cfg.get("tiers", [])),
                cfg.get("event", ""),
                "\n".join(artifacts),
                env_str,
            )

        console.print(table)
    else:
        table = Table(box=box.ROUNDED, show_lines=True)
        table.add_column("Set", style="bold")
        table.add_column("Path")
        table.add_column("Architectures")
        table.add_column("Tiers")

        for name, cfg in sets_cfg.items():
            src = cfg.get("source", "")
            tgt = cfg.get("target")
            path = f"{src} → {tgt}" if tgt else src

            table.add_row(
                name,
                path,
                _format_list(cfg.get("architectures", [])),
                _format_list(cfg.get("tiers", [])),
            )

        console.print(table)

    return True


def main():
    """Main entry point for enge CLI."""
    setup_logging()
    try:
        if _handle_list_sets():
            return 0

        # Resolve parsed_opts lazily to avoid argparse parsing at import time
        resolved_opts = parsed_opts
        if resolved_opts is None:
            from enge.utils.opt_manager import parsed_opts as _parsed_opts

            resolved_opts = _parsed_opts

        from enge.utils.app_context import AppContext

        ctx = AppContext.from_parsed_opts(resolved_opts)

        if resolved_opts.cli_args.action == "test":
            from enge.dispatch.__main__ import main as dispatch_main

            return dispatch_main()
        elif resolved_opts.cli_args.action == "report":
            from enge.report.__main__ import main as report_main

            return report_main(ctx)
        elif resolved_opts.cli_args.action == "rerun":
            from enge.rerun.__main__ import main as rerun_main

            return rerun_main()
        elif resolved_opts.cli_args.action == "cancel":
            from enge.cancel.__main__ import main as cancel_main

            return cancel_main(ctx)
        elif resolved_opts.cli_args.action == "reportportal":
            from enge.reportportal.__main__ import main as reportportal_main

            return reportportal_main()
        else:
            logging.error("No valid action specified")
            return EXIT_GENERAL_ERROR
    except ConfigurationError as e:
        logging.critical(f"Configuration error: {e}")
        return EXIT_CONFIG_ERROR
    except ValidationError as e:
        logging.critical(f"Validation error: {e}")
        return EXIT_PARTIAL_FAILURE
    except NetworkError as e:
        logging.critical(f"Network error: {e}")
        return EXIT_GENERAL_ERROR
    except UserAbort:
        logging.info("Operation aborted by user")
        return EXIT_INTERRUPT


if __name__ == "__main__":
    sys.exit(main())
