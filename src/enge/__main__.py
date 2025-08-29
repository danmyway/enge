#!/usr/bin/env python3

import logging
import sys

from enge import ColorizedFormatter
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

# Placeholder for tests to patch without triggering opt_manager initialization
parsed_opts = None


def setup_logging():
    """Setup logging configuration based on debug flag."""
    # Pre-parse CLI args to get --debug without triggering ParsedOpts
    try:
        cli_args = get_arguments()
        debug_enabled = bool(getattr(cli_args, "debug", False))
    except Exception:
        debug_enabled = False

    log_level = logging.DEBUG if debug_enabled else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)-8s | %(message)s")
    # Attach colorized formatter to the root handler if present
    root_logger = logging.getLogger()
    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setFormatter(ColorizedFormatter("%(levelname)-8s | %(message)s"))


def main():
    """Main entry point for enge CLI."""
    setup_logging()
    try:
        # Resolve parsed_opts lazily to avoid argparse parsing at import time
        resolved_opts = parsed_opts
        if resolved_opts is None:
            from enge.utils.opt_manager import parsed_opts as _parsed_opts

            resolved_opts = _parsed_opts

        if resolved_opts.cli_args.action == "test":
            from enge.dispatch.__main__ import main as dispatch_main

            return dispatch_main()
        elif resolved_opts.cli_args.action == "report":
            from enge.report.__main__ import main as report_main

            return report_main()
        elif resolved_opts.cli_args.action == "rerun":
            from enge.rerun.__main__ import main as rerun_main

            return rerun_main()
        elif resolved_opts.cli_args.action == "cancel":
            from enge.cancel.__main__ import main as cancel_main

            return cancel_main()
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
