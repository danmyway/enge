#!/usr/bin/env python3
"""
Command-line argument parser for enge.

This module defines all CLI arguments and subcommands for the enge tool,
ensuring clear, consistent, and conflict-free argument definitions.
"""

import argparse
import pathlib
from typing import Optional


def _copr_ref_type(value):
    """Lazy converter for --copr to avoid importing heavy deps at import time."""
    from .tf_artifact import CoprRef

    return CoprRef(value)


def _brew_ref_type(value):
    """Lazy converter for --brew to avoid importing heavy deps at import time."""
    from .tf_artifact import BrewRef

    return BrewRef(value)


def get_arguments(args: Optional[list] = None) -> argparse.Namespace:
    """
    Define and parse command-line arguments for enge.

    Args:
        args: Optional list of arguments to parse (for testing).
              If None, parses from sys.argv.

    Returns:
        argparse.Namespace: Parsed command-line arguments
    """
    # Global arguments
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="Custom path to the config file.")
    common.add_argument(
        "-d", "--debug", action="store_true", help="Print out additional information."
    )

    parser = argparse.ArgumentParser(
        description="Send requests to and get results back from Testing Farm conveniently.",
        formatter_class=argparse.RawTextHelpFormatter,
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="action", help="Available commands")

    # ==================== TEST SUBCOMMAND ====================
    test = subparsers.add_parser(
        "test",
        help="Dispatch a job to the Testing Farm API endpoint.",
        description="Send requests to Testing Farm conveniently.",
        parents=[common],
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
        type=_copr_ref_type,
        action="append",
        nargs="?",
        default=None,
        const=None,
        help="Test a fedora-copr-build. "
        "The pull request reference (pr123) or BuildID can be provided either in the config file or as an argument."
        "If neither of copr/brew is specified, the compose build is tested.",
    )

    artifact_type.add_argument(
        "--brew",
        type=_brew_ref_type,
        action="append",
        nargs="?",
        default=None,
        const=None,
        help="Test a brew build RC. "
        "Accepts either version reference (0.1.2-3) or TaskID. Both are validated via Brew API. "
        "Task IDs are resolved to their NVR, and the NVR is used in the Testing Farm payload. "
        "If neither of copr/brew is specified, the compose build is tested.",
    )

    # Test planning and filtering
    test.add_argument(
        "--tier",
        action="append",
        help="Test tier(s) to be executed. Multiple tiers can be provided. "
        "Can be combined with --plan to override config plans with specific plans.",
    )

    test.add_argument(
        "--set",
        action="append",
        help="Test set to be executed. Multiple sets can be provided. "
        "Can be combined with --plan to override set plans with specific plans.",
    )

    test.add_argument(
        "--set-regex",
        action="append",
        metavar="REGEX",
        help=(
            "Regular expression to select test sets by name. "
            "Matches against names under [tests.set.<name>] (Python regex). "
            "Can be specified multiple times; expands to concrete set names before validation."
        ),
    )

    test.add_argument(
        "--plan",
        action="append",
        help=(
            "Plans to be executed. Multiple plans can be provided. "
            "Can be used standalone (without --tier/--set). "
            "When combined with --tier or --set, overrides any plans from config/set. "
            "Requires source and architectures via CLI or [tests] config when used standalone."
        ),
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
        "--only-rhsm-mock-cdn",
        action="store_true",
        help="Add tag:rhsm to the combined plan filter. "
        "This filters tests to only those tagged with 'rhsm'.",
    )

    test.add_argument(
        "--no-rhsm",
        action="store_true",
        help="Add tag:-rhsm to the combined plan filter. "
        "This excludes tests tagged with 'rhsm' from execution.",
    )

    test.add_argument(
        "--only-rhsm-stage-cdn",
        action="store_true",
        help="Add tag:rhsm to the combined plan filter, "
        "set product_phase=rc in TMT context, and set RHSM_MODE=stage in environment variables. "
        "This is used for testing RHSM stage environment.",
    )

    test.add_argument(
        "--event",
        help="Event name for launch naming. "
        "If specified, this will be used in the launch name instead of the set name. "
        "Can also be configured in test set configuration.",
    )

    test.add_argument(
        "--git-url",
        help="URL to the tests metadata repository. "
        "If not specified, uses the default one from the config file.",
    )

    test.add_argument(
        "--git-ref",
        help="Git ref (branch, tag, or commit) to checkout the test suite from. "
        "If not specified, uses the default one from the config file.",
    )

    test.add_argument(
        "--architectures",
        "--arch",
        action="append",
        help="Target architectures for testing. "
        "Specify multiple architectures as separate arguments (e.g., --architectures x86_64 aarch64). "
        "If not specified, uses the default ones from the config file.",
    )

    test.add_argument(
        "--parallel-limit",
        type=int,
        metavar="N",
        help="Maximum number of plans to run in parallel. Overrides configuration files and hardcoded default (20).",
    )

    test.add_argument(
        "--environment",
        action="append",
        metavar="VAR=VAL",
        help="Additional environment variables to be set in the request. "
        "Can be provided multiple times: --environment VAR1=VAL1 --environment VAR2=VAL2",
    )

    test.add_argument(
        "--context",
        action="append",
        metavar="KEY=VAL",
        help=(
            "Additional TMT context key-value pairs. "
            "Merges into the generated context; warnings are shown on overrides (config, set, or duplicate CLI). "
            "Can be provided multiple times: --context key1=val1 --context key2=val2"
        ),
    )

    # ReportPortal integration
    test.add_argument(
        "--rp-launch",
        help="Override ReportPortal launch name from configuration. "
        "Sets TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH environment variable.",
    )

    test.add_argument(
        "--rp-description",
        help="Override ReportPortal description from configuration. "
        "Sets TMT_PLUGIN_REPORT_REPORTPORTAL_DESCRIPTION environment variable.",
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

    test.add_argument(
        "--auto-tag",
        action="store_true",
        help="Automatically tag archived task files with set name, architecture, and tier information. "
        "Tags will be in the format: setname.arch.tier (e.g., pre-release.x86_64.tier0). "
        "Can be combined with --set-tag for additional custom tags.",
    )

    # ==================== REPORT SUBCOMMAND ====================
    report = subparsers.add_parser(
        "report",
        help="Report results for requested tasks.",
        description="Parse task IDs, Testing Farm artifact URLs, "
        "or Testing Farm API request URLs from multiple sources.",
        parents=[common],
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

    report.add_argument(
        "--show-ids",
        action="store_true",
        help="Display only a list of UUIDs queried from the requested inputs.",
    )

    report.add_argument(
        "--jira",
        action="store_true",
        help="Display tables formatted for JIRA comments.",
    )

    # ==================== RERUN SUBCOMMAND ====================
    rerun = subparsers.add_parser(
        "rerun",
        help="Parse given tasks and rerun specified jobs.",
        description="Rerun failed or errored tasks from previous runs.",
        parents=[common],
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
        "--auto-tag",
        action="store_true",
        help="Automatically tag archived task files with set name, architecture, and tier information. "
        "Tags will be in the format: setname.arch.tier (e.g., pre-release.x86_64.tier0). "
        "Can be combined with --set-tag for additional custom tags.",
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

    # ==================== REPORTPORTAL SUBCOMMAND ====================
    reportportal = subparsers.add_parser(
        "reportportal",
        help="Manage ReportPortal launches.",
        description="Create and manage ReportPortal launches through the ReportPortal API.",
        parents=[common],
    )

    # ReportPortal action type (mutually exclusive)
    rp_action = reportportal.add_mutually_exclusive_group()

    rp_action.add_argument(
        "--finish",
        action="store_true",
        help="Finish a ReportPortal launch. Uses the report module to check task state and finish the launch if ready.",
    )

    rp_action.add_argument(
        "--test",
        action="store_true",
        help="Test ReportPortal connection and show sample data for debugging.",
    )

    # Input sources (same as report and rerun) - for --finish action
    reportportal.add_argument(
        "-f",
        "--file",
        action="append",
        metavar="FILE",
        help="Filepath containing request IDs, artifact URLs, or request URLs. "
        "Can be provided multiple times: -f file1 -f ~/file2",
    )

    reportportal.add_argument(
        "-i",
        "--input",
        action="append",
        metavar="ID_OR_URL",
        help="Request ID, artifact URL, or request URL from command line. "
        "Can be provided multiple times: -i id1 -i id2",
    )

    reportportal.add_argument(
        "--get-tag",
        action="append",
        metavar="TAG",
        help="Query for all task results under a given tag. Can be used multiple times.",
    )

    # ReportPortal control options
    reportportal.add_argument(
        "--dryrun",
        action="store_true",
        help="Show what would be sent to ReportPortal without actually finishing launches.",
    )

    # ==================== CANCEL SUBCOMMAND ====================
    cancel = subparsers.add_parser(
        "cancel",
        help="Cancel Testing Farm tasks.",
        description="Cancel running or queued Testing Farm tasks by sending DELETE requests.",
        parents=[common],
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
