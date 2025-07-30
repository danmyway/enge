#!/usr/bin/env python3

import logging
import sys

from enge.utils.opt_manager import parsed_opts


def setup_logging():
    """Setup logging configuration based on debug flag."""
    log_level = logging.DEBUG if parsed_opts.cli_args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)-8s | %(message)s")


def main():
    """Main entry point for enge CLI."""
    setup_logging()

    if parsed_opts.cli_args.action == "test":
        from enge.dispatch.__main__ import main as dispatch_main

        return dispatch_main()
    elif parsed_opts.cli_args.action == "report":
        from enge.report.__main__ import main as report_main

        return report_main()
    elif parsed_opts.cli_args.action == "rerun":
        from enge.rerun.__main__ import main as rerun_main

        return rerun_main()
    elif parsed_opts.cli_args.action == "cancel":
        from enge.cancel.__main__ import main as cancel_main

        return cancel_main()
    elif parsed_opts.cli_args.action == "reportportal":
        from enge.reportportal.__main__ import main as reportportal_main

        return reportportal_main()
    else:
        logging.error("No valid action specified")
        return 1


if __name__ == "__main__":
    sys.exit(main())
