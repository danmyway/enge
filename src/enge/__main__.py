#!/usr/bin/env python3

import logging
import sys

from enge.utils.opt_manager import parsed_opts


def main():
    loglevel = logging.INFO
    logformat = "%(levelname)-8s | %(message)s"

    if parsed_opts.cli_args.debug:
        loglevel = logging.DEBUG

    logging.basicConfig(
        level=loglevel,
        format=logformat,
    )

    if parsed_opts.cli_args.action == "test":
        from enge.dispatch.__main__ import main as dispatch

        sys.exit(dispatch())

    elif parsed_opts.cli_args.action == "report":
        from enge.report.__main__ import main as report

        sys.exit(report())


if __name__ == "__main__":
    sys.exit(main())
