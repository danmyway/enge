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


def _add_input_source_args(parser: argparse.ArgumentParser) -> None:
    """
    Add common input source arguments (-f/--file, -i/--input, --get-tag)
    to a parser. Used by report, rerun, reportportal, and cancel subcommands.
    """
    parser.add_argument(
        "-f",
        "--file",
        action="append",
        metavar="FILE",
        help="Filepath containing request IDs, artifact URLs, or request URLs to parse. "
        "Can be provided multiple times: -f file1 -f ~/file2",
    )

    parser.add_argument(
        "-i",
        "--input",
        action="append",
        metavar="ID_OR_URL",
        help="Request ID, artifact URL, or request URL to parse from command line. "
        "Can be provided multiple times: -i id1 -i id2",
    )

    parser.add_argument(
        "--get-tag",
        action="append",
        metavar="TAG",
        help="Query for all task results under a given tag. Can be used multiple times.",
    )


def _add_dryrun_arg(
    parser: argparse.ArgumentParser, help_text: Optional[str] = None
) -> None:
    """
    Add --dryrun argument to a parser.

    Args:
        parser: The parser to add the argument to
        help_text: Custom help text. If None, uses a default message.
    """
    default_help = (
        "Print the payload that would be sent to Testing Farm without sending it."
    )
    parser.add_argument(
        "--dryrun",
        action="store_true",
        help=help_text or default_help,
    )


def _add_date_filter_args(parser: argparse.ArgumentParser) -> None:
    """Add ``--since`` and ``--until`` date filter arguments to a parser."""
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="Only consider items from on or after DATE "
        "(YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).",
    )
    parser.add_argument(
        "--until",
        metavar="DATE",
        help="Only consider items from on or before DATE "
        "(YYYY-MM-DD or relative: 6h, 3d, 2w, 1m, 1y).",
    )


def _add_tagging_args(parser: argparse.ArgumentParser) -> None:
    """
    Add common tagging arguments (--set-tag, --auto-tag) to a parser.
    Used by test and rerun subcommands.
    """
    parser.add_argument(
        "--set-tag",
        action="append",
        metavar="TAG",
        help="Tag the archived task file with a custom tag. Can be used multiple times.",
    )

    parser.add_argument(
        "--auto-tag",
        action="store_true",
        help="Automatically tag archived task files with set name, architecture, and tier information. "
        "Tags will be in the format: setname.arch.tier (e.g., pre-release.x86_64.tier0). "
        "Can be combined with --set-tag for additional custom tags.",
    )


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
        "Can be provided in a format of <major>.<minor> (e.g. 8.10), explicit compose name (e.g. RHEL-8.10.0-Nightly), "
        "CentOS Stream format (e.g. CentOS-Stream-9, stream-9, cs-9, stream9, cs9), "
        "or AMI source alias/name for Alma Linux or Rocky Linux (e.g. alma97, rocky97, "
        "'AlmaLinux OS 9.7.20251118 x86_64'). AMI aliases are configured in [sources.ami]. "
        "If --target is not specified, the path is resolved to <major + 1>.<minor - 6> (e.g. 8.10 -> 9.4). "
        "Required unless --set is provided.",
    )

    test.add_argument(
        "--target",
        help="Target compose for upgrade. "
        "Can be provided in a format of <major>.<minor> (e.g. 9.4) or explicit compose name (e.g. RHEL-9.4.0-Nightly). "
        "When --source is CentOS Stream, providing only the major version (e.g. 10) is allowed. "
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
        "--pool",
        help="Specify a provisioning pool from Testing Farm.",
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

    # Execution control
    _add_dryrun_arg(test)
    _add_tagging_args(test)

    # ==================== REPORT SUBCOMMAND ====================
    report = subparsers.add_parser(
        "report",
        help="Report results for requested tasks.",
        description="Parse task IDs, Testing Farm artifact URLs, "
        "or Testing Farm API request URLs from multiple sources.",
        parents=[common],
    )

    # Input sources
    _add_input_source_args(report)

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

    # Date filters (effective with --get-tag, filters by archive filename timestamp)
    _add_date_filter_args(report)

    # ==================== RERUN SUBCOMMAND ====================
    rerun = subparsers.add_parser(
        "rerun",
        help="Parse given tasks and rerun specified jobs.",
        description="Rerun failed or errored tasks from previous runs.",
        parents=[common],
    )

    # Input sources
    _add_input_source_args(rerun)

    # Rerun control
    _add_tagging_args(rerun)
    _add_dryrun_arg(rerun)

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

    rp_action.add_argument(
        "--delete-logs",
        action="store_true",
        help="Delete all log entries from a ReportPortal launch. "
        "Uses the report module to find the matching launch via TMT context.",
    )

    rp_action.add_argument(
        "--delete-stale",
        action="store_true",
        help="Delete stale launches — stopped/interrupted launches with no test items.",
    )

    # Log enrichment (can be combined with --finish to enrich then finish)
    reportportal.add_argument(
        "--enrich-logs",
        action="store_true",
        help="Fetch all available artifacts from the Testing Farm artifact endpoint "
        "and upload them as logs to the corresponding ReportPortal launch. "
        "Can be combined with --finish to enrich logs before finishing the launch. "
        "With --all-launches: enriches launches directly from RP test-item descriptions "
        "(no TF task input needed). Already-enriched launches are skipped.",
    )

    # Operate on all IN_PROGRESS launches without providing task input
    reportportal.add_argument(
        "--all-launches",
        action="store_true",
        help="Operate on launches in the ReportPortal project without "
        "providing Testing Farm task input. "
        "Supported with --finish, --enrich-logs, and --delete-logs. "
        "With --enrich-logs alone: enriches all launches (any status). "
        "With --finish --enrich-logs: enriches then finishes IN_PROGRESS launches. "
        "Launches already enriched by enge are skipped automatically.",
    )

    # Date filters (effective with --all-launches and --delete-stale)
    _add_date_filter_args(reportportal)

    # Input sources (for --finish and --enrich-logs actions)
    _add_input_source_args(reportportal)

    # ReportPortal control options
    _add_dryrun_arg(
        reportportal,
        help_text="Show what would be sent to ReportPortal without actually sending it.",
    )

    # ==================== CANCEL SUBCOMMAND ====================
    cancel = subparsers.add_parser(
        "cancel",
        help="Cancel Testing Farm tasks.",
        description="Cancel running or queued Testing Farm tasks by sending DELETE requests.",
        parents=[common],
    )

    # Input sources
    _add_input_source_args(cancel)

    # Cancel control
    _add_dryrun_arg(
        cancel,
        help_text="Show which tasks would be cancelled without actually cancelling them.",
    )

    parsed_args = parser.parse_args(args)

    return parsed_args
