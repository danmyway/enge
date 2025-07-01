#!/usr/bin/env python3

import sys

from enge.utils.opt_manager import parsed_opts


def main():
    if parsed_opts.cli_args.action == "test":
        from enge.dispatch.__main__ import main as dispatch

        sys.exit(dispatch())

    elif parsed_opts.cli_args.action == "report":
        from enge.report.__main__ import main as report

        sys.exit(report())

    elif parsed_opts.cli_args.action == "rerun":
        from enge.rerun.__main__ import main as rerun

        sys.exit(rerun())


if __name__ == "__main__":
    sys.exit(main())
