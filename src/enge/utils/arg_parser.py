#!/usr/bin/env python3
"""
Command-line argument parser for enge.

This module defines all CLI arguments and subcommands for the enge tool,
ensuring clear, consistent, and conflict-free argument definitions.
"""

import argparse
import pathlib
from typing import Optional

from .tf_artifact import CoprRef, BrewRef


def get_arguments(args: Optional[list] = None) -> argparse.Namespace:
    """
    Define and parse command-line arguments for enge.

    Args:
        args: Optional list of arguments to parse (for testing).
              If None, parses from sys.argv.

    Returns:
        argparse.Namespace: Parsed command-line arguments
    """
    parser = argparse.ArgumentParser(
        description="Send requests to and get results back from Testing Farm conveniently.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Global arguments
    parser.add_argument("-c", "--config", help="Custom path to the config file.")

    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Print out additional information for each request.",
    )

    subparsers = parser.add_subparsers(dest="action", help="Available commands")

    # ==================== TEST SUBCOMMAND ====================
    test = subparsers.add_parser(
        "test",
        help="Dispatch a job to the Testing Farm API endpoint.",
        description="Send requests to Testing Farm conveniently.",
    )

    test.add_argument(
        "--source",
        required=False,  # Will be validated later based on whether --set is provided
        help="Source compose to be upgraded. "
        "Can be provided in a format of <major>.<minor> (e.g. 8.10) or explicit compose name (e.g. RHEL-8.10.0-Nightly). "
        "If --target is not specified, the path is resolved to <major + 1>.<minor - 6> (e.g. 8.10 -> 9.4). "
        "Required unless --set is provided.",
    )

    test.add_argument(
        "--target",
        help="Target compose for upgrade. "
        "Can be provided in a format of <major>.<minor> (e.g. 9.4) or explicit compose name (e.g. RHEL-9.4.0-Nightly). "
        "If not specified, will be derived from source as <source_major + 1>.<source_minor - 6>",
    )

    # Artifact type (mutually exclusive)
    artifact_type = test.add_mutually_exclusive_group()

    artifact_type.add_argument(
        "--copr",
        type=CoprRef,
        action="append",
        nargs="?",
        default=None,
        const=CoprRef(None),
        help="Test a fedora-copr-build. "
        "The pull request reference (pr123) or BuildID can be provided either in the config file or as an argument."
        "If neither of copr/brew is specified, the compose build is tested.",
    )

    artifact_type.add_argument(
        "--brew",
        type=BrewRef,
        action="append",
        nargs="?",
        default=None,
        const=BrewRef(None),
        help="Test a brew build RC. "
        "The version reference (0.1.2-3) or TaskID can be provided either in the config file or as an argument."
        "If neither of copr/brew is specified, the compose build is tested.",
    )

    # Test planning and filtering
    plan_filter_type = test.add_mutually_exclusive_group()

    plan_filter_type.add_argument(
        "--plan",
        action="append",
        help="Plans to be executed. " "Multiple plans can be provided.",
    )

    plan_filter_type.add_argument(
        "--tier",
        action="append",
        help="Test tier(s) to be executed. " "Multiple tiers can be provided.",
    )

    plan_filter_type.add_argument(
        "--set",
        action="append",
        help="Test set to be executed. " "Multiple sets can be provided.",
    )

    test.add_argument(
        "--test",
        help="Specify test name to be executed. "
        "Only used in conjunction with --plan or --tier.",
    )

    test.add_argument(
        "--testfilter",
        help="Filter tests using FMF filter syntax. "
        "This allows fine-grained filtering of which tests to run.",
    )

    test.add_argument(
        "--planfilter",
        help="Filter plans using FMF filter syntax. "
        "This overrides any automatically generated plan filters from --tier.",
    )

    test.add_argument(
        "--git-url",
        help="URL to the tests metadata repository. "
        "If not specified, uses the default one from the config file.",
    )

    test.add_argument(
        "--git-branch",
        help="Git branch to checkout the test suite from. "
        "If not specified, uses the default one from the config file.",
    )

    test.add_argument(
        "--architectures",
        "--arch",
        nargs="+",
        help="Target architectures for testing. "
        "Specify multiple architectures as separate arguments (e.g., --architectures x86_64 aarch64). "
        "If not specified, uses the default ones from the config file.",
    )

    test.add_argument(
        "--environment",
        action="append",
        metavar="VAR=VAL",
        help="Additional environment variables to be set in the request. "
        "Can be provided multiple times: --environment VAR1=VAL1 --environment VAR2=VAL2",
    )

    # Execution control
    test.add_argument(
        "--wait",
        action="store_true",
        help="Wait for successful API response after submitting request.",
    )

    test.add_argument(
        "--dryrun",
        action="store_true",
        help="Print the payload that would be sent to Testing Farm without sending it.",
    )

    test.add_argument(
        "--set-tag",
        action="append",
        metavar="TAG",
        help="Tag the archived task file with a custom tag. Can be used multiple times.",
    )

    # ==================== REPORT SUBCOMMAND ====================
    report = subparsers.add_parser(
        "report",
        help="Report results for requested tasks.",
        description="Parse task IDs, Testing Farm artifact URLs, "
        "or Testing Farm API request URLs from multiple sources.",
    )

    # Input sources
    report.add_argument(
        "-f",
        "--file",
        action="append",
        metavar="FILE",
        help="Filepath containing request IDs, artifact URLs, or request URLs to parse. "
        "Can be provided multiple times: -f file1 -f ~/file2",
    )

    report.add_argument(
        "-i",
        "--input",
        action="append",
        metavar="ID_OR_URL",
        help="Request ID, artifact URL, or request URL to parse from command line. "
        "Can be provided multiple times: -i id1 -i id2",
    )

    report.add_argument(
        "--get-tag",
        action="append",
        metavar="TAG",
        help="Query for all task results under a given tag. Can be used multiple times.",
    )

    # Output control
    report.add_argument(
        "--path",
        type=pathlib.Path,
        metavar="PATH",
        help="Custom path to archived task files directory.",
    )

    report.add_argument(
        "--show-tests",
        action="store_true",
        help="Display detailed test view. By default, only plan view is shown.",
    )

    report.add_argument(
        "-s",
        "--short",
        action="store_true",
        help="Display shortened test and plan names.",
    )

    report.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help="Wait for the job to complete. Print the table afterwards",
    )

    report.add_argument(
        "--download", action="store_true", help="Download logs for requested run(s)."
    )

    report.add_argument(
        "--show-arch",
        action="store_true",
        help="Display architecture in results. By default, architecture is hidden.",
    )

    report.add_argument(
        "--skip-pass",
        action="store_true",
        help="Skip PASSED results in table and log downloads.",
    )

    report.add_argument(
        "--compare",
        action="store_true",
        help="Build a comparison table for multiple run results.",
    )

    report.add_argument(
        "--unify",
        action="append",
        metavar="PLAN1=PLAN2",
        help="Treat plan names as equivalent in 'plan1=plan2' format. "
        "Useful for comparing runs with renamed plans.",
    )

    # ==================== RERUN SUBCOMMAND ====================
    rerun = subparsers.add_parser(
        "rerun",
        help="Parse given tasks and rerun specified jobs.",
        description="Rerun failed or errored tasks from previous runs.",
    )

    # Input sources (same as report)
    rerun.add_argument(
        "-f",
        "--file",
        action="append",
        metavar="FILE",
        help="Filepath containing request IDs, artifact URLs, or request URLs to parse. "
        "Can be provided multiple times: -f file1 -f ~/file2",
    )

    rerun.add_argument(
        "-i",
        "--input",
        action="append",
        metavar="ID_OR_URL",
        help="Request ID, artifact URL, or request URL to parse from command line. "
        "Can be provided multiple times: -i id1 -i id2",
    )

    rerun.add_argument(
        "--get-tag",
        action="append",
        metavar="TAG",
        help="Query for all task results under a given tag. Can be used multiple times.",
    )

    # Rerun control
    rerun.add_argument(
        "--set-tag",
        action="append",
        metavar="TAG",
        help="Tag the archived task file with a custom tag. Can be used multiple times.",
    )

    rerun.add_argument(
        "--dryrun",
        action="store_true",
        help="Print the payload that would be sent to Testing Farm without sending it.",
    )

    rerun.add_argument(
        "--error",
        action="store_true",
        help="Rerun only jobs that reported ERROR state.",
    )

    rerun.add_argument(
        "--fail",
        action="store_true",
        help="Rerun only jobs that reported FAILED state.",
    )

    rerun.add_argument(
        "--show-arch",
        action="store_true",
        help="Display architecture in results. By default, architecture is hidden.",
    )

    # ==================== CANCEL SUBCOMMAND ====================
    cancel = subparsers.add_parser(
        "cancel",
        help="Cancel Testing Farm tasks.",
        description="Cancel running or queued Testing Farm tasks by sending DELETE requests.",
    )

    # Input sources (same as report and rerun)
    cancel.add_argument(
        "-f",
        "--file",
        action="append",
        metavar="FILE",
        help="Filepath containing request IDs, artifact URLs, or request URLs to cancel. "
        "Can be provided multiple times: -f file1 -f ~/file2",
    )

    cancel.add_argument(
        "-i",
        "--input",
        action="append",
        metavar="ID_OR_URL",
        help="Request ID, artifact URL, or request URL to cancel from command line. "
        "Can be provided multiple times: -i id1 -i id2",
    )

    cancel.add_argument(
        "--get-tag",
        action="append",
        metavar="TAG",
        help="Query for all tasks under a given tag to cancel. Can be used multiple times.",
    )

    # Cancel control
    cancel.add_argument(
        "--dryrun",
        action="store_true",
        help="Show which tasks would be cancelled without actually cancelling them.",
    )

    parsed_args = parser.parse_args(args)

    return parsed_args


# Parse arguments at module level
args = get_arguments()
