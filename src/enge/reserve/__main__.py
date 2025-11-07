#!/usr/bin/env python3
"""
Reserve subcommand entrypoint - PLACEHOLDER for future implementation.

This module will eventually provide standalone reservation functionality
that is independent of the test dispatch workflow. This is different from
the --reserve flag used with 'enge test', which reserves machines after
test completion.

Future functionality might include:
- Reserving a machine directly without running tests
- Managing existing reservations
- Listing active reservations
- Extending reservation duration
- Releasing reservations early

Usage (future):
    enge reserve --compose RHEL-9.7.0-Nightly --arch x86_64 --duration 120
    enge reserve --list
    enge reserve --release <reservation-id>
"""

import logging
import sys

LOGGER = logging.getLogger(__name__)


def main():
    """
    Main entrypoint for the reserve subcommand.

    Currently not implemented - placeholder for future functionality.
    """
    LOGGER.error(
        "The 'enge reserve' subcommand is not yet implemented. "
        "Use 'enge test --reserve' to reserve machines after test completion."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
