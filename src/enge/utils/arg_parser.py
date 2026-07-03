#!/usr/bin/env python3
"""
Command-line argument parser for enge.

This module defines all CLI arguments and subcommands for the enge tool,
ensuring clear, consistent, and conflict-free argument definitions.
"""

import argparse
import pathlib

try:
    import argcomplete
except ImportError:
    argcomplete = None
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
        "-n",
        "--dry-run",
        "--dryrun",
        action="store_true",
        dest="dryrun",
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


def _add_format_arg(parser: argparse.ArgumentParser, choices=None) -> None:
    """Add -o/--format output format argument to a parser."""
    parser.add_argument(
        "-o",
        "--format",
        dest="output_format",
        choices=choices or ["terminal", "gitlab", "json"],
        nargs="?",
        default="terminal",
        const="gitlab",
        help="Output format. 'terminal' (default): colored output. "
        "'gitlab' (default when -o is used without value): markdown code blocks and tables. "
        "'json': machine-readable JSON to stdout (suppresses other output).",
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
        help="Deprecated: context is now always recorded in manifests. "
        "This flag is a no-op and will be removed in a future release.",
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
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase output verbosity. -v for verbose, -vv for full debug.",
    )
    common.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help=argparse.SUPPRESS,
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
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "examples:\n"
            "  enge test -s 9.7 -T tier0                           # compose build\n"
            "  enge test --copr lp:pr123 -s 9.7 -T tier0           # COPR PR build\n"
            "  enge test --brew leapp-0.1-2.el9 -T tier0            # brew build\n"
            "  enge test -S pre-release-smoke                       # pre-configured test set\n"
            "  enge test -S pre-release-smoke -n                    # preview payload\n"
        ),
    )

    test.add_argument(
        "-s",
        "--source",
        required=False,  # Will be validated later based on whether --set is provided
        help="Source compose to be upgraded. "
        "Can be provided in a format of <major>.<minor> (e.g. 8.10), explicit compose name (e.g. RHEL-8.10.0-Nightly), "
        "symbolic RHUI compose (e.g. RHEL-8-rhui, RHEL-8-sap-hana-rhui, "
        "RHEL-8-sap-netweaver-rhui), "
        "CentOS Stream format (e.g. CentOS-Stream-9, stream-9, cs-9, stream9, cs9), "
        "or AMI source alias/name for Alma Linux or Rocky Linux (e.g. alma97, rocky97, "
        "'AlmaLinux OS 9.7.20251118 x86_64'). AMI aliases are configured in [sources.ami]. "
        "If --target is not specified, the path is resolved to <major + 1>.<minor - 6> (e.g. 8.10 -> 9.4). "
        "Required unless --set is provided.",
    )

    test.add_argument(
        "-t",
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
        help="COPR build reference: alias:version (e.g., lp:pr123, lpr:main) or numeric BuildID. "
        "Aliases: lp=leapp, lpr=leapp-repository. "
        "If neither --copr nor --brew is specified, compose artifacts are used.",
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
        "-T",
        "--tier",
        action="append",
        help="Test tier(s) to be executed. Multiple tiers can be provided. "
        "Can be combined with --plan to override config plans with specific plans.",
    )

    test.add_argument(
        "-S",
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
        "-p",
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
        "--test-filter",
        "--testfilter",
        dest="testfilter",
        help="Filter tests using FMF filter syntax. "
        "This allows fine-grained filtering of which tests to run.",
    )

    test.add_argument(
        "--plan-filter",
        "--planfilter",
        dest="planfilter",
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
        help="Target architecture. Can be specified multiple times: --arch x86_64 --arch aarch64. "
        "If not specified, uses the default from the config file.",
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

    _add_dryrun_arg(test)
    _add_tagging_args(test)
    _add_format_arg(test)

    # Discovery
    test.add_argument(
        "--list-sets",
        action="store_true",
        help="List available test sets from config and exit.",
    )

    test.add_argument(
        "--list-sets-detail",
        action="store_true",
        help="List available test sets with full configuration detail and exit.",
    )

    # ==================== REPORT SUBCOMMAND ====================
    report = subparsers.add_parser(
        "report",
        help="Report results for requested tasks.",
        description="Parse task IDs, Testing Farm artifact URLs, "
        "or Testing Farm API request URLs from multiple sources.",
        parents=[common],
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "examples:\n"
            "  enge report                                          # report latest run\n"
            "  enge report -i <uuid>                                # report specific task\n"
            "  enge report --list                                   # browse all runs\n"
            "  enge report --list --set smoke --since 3d            # filter runs\n"
            "  enge report --run <run_id>                           # report specific run\n"
            "  enge report --tag regression --show-tests            # detailed test view\n"
            "  enge report -f tasks.txt -w                          # wait for completion\n"
            "  enge report --get-tag v1 --get-tag v2 --compare      # compare runs (legacy)\n"
        ),
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
        help=argparse.SUPPRESS,
    )

    _add_format_arg(report, choices=["terminal", "gitlab", "json"])

    _add_date_filter_args(report)

    report.add_argument(
        "--list",
        action="store_true",
        help="List all runs in the manifest store as a table. "
        "Combinable with --set/--tier/--arch/--tag/--since/--until to narrow results.",
    )

    report.add_argument(
        "--run",
        metavar="RUN_ID",
        help="Select a specific run by manifest ID. "
        "Use 'enge report --list' to browse available runs.",
    )

    report.add_argument(
        "--set",
        dest="filter_set",
        metavar="SET",
        help="Filter runs by test set name.",
    )

    report.add_argument(
        "--tier",
        dest="filter_tier",
        metavar="TIER",
        help="Filter runs by tier.",
    )

    report.add_argument(
        "--arch",
        dest="filter_arch",
        metavar="ARCH",
        help="Filter runs by architecture.",
    )

    report.add_argument(
        "--tag",
        dest="filter_tag",
        metavar="TAG",
        help="Filter runs by tag.",
    )

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
    _add_format_arg(rerun)

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
        "--run",
        metavar="RUN_ID",
        help="Select a specific run by manifest ID for rerun.",
    )

    # ==================== REPORTPORTAL SUBCOMMAND ====================
    reportportal = subparsers.add_parser(
        "reportportal",
        help="Manage ReportPortal launches.",
        description="Create and manage ReportPortal launches through the ReportPortal API.",
        parents=[common],
    )

    rp_subparsers = reportportal.add_subparsers(dest="rp_subcommand")

    # --- finish ---
    rp_finish = rp_subparsers.add_parser(
        "finish",
        help="Finish ReportPortal launches by resolving task state.",
        parents=[common],
    )
    rp_finish.add_argument(
        "--enrich",
        action="store_true",
        dest="enrich",
        help="Enrich launches with artifact logs before finishing.",
    )
    rp_finish.add_argument(
        "--all",
        action="store_true",
        dest="all_launches",
        help="Operate on all IN_PROGRESS launches (no task input needed).",
    )
    _add_input_source_args(rp_finish)
    _add_date_filter_args(rp_finish)
    _add_dryrun_arg(
        rp_finish,
        help_text="Show what would be sent to ReportPortal without actually sending it.",
    )

    # --- enrich ---
    rp_enrich = rp_subparsers.add_parser(
        "enrich",
        help="Enrich launches with Testing Farm artifact logs.",
        parents=[common],
    )
    rp_enrich.add_argument(
        "--all",
        action="store_true",
        dest="all_launches",
        help="Enrich all launches (any status, no task input needed).",
    )
    _add_input_source_args(rp_enrich)
    _add_date_filter_args(rp_enrich)
    _add_dryrun_arg(
        rp_enrich,
        help_text="Show what would be sent to ReportPortal without actually sending it.",
    )

    # --- delete-logs ---
    rp_delete_logs = rp_subparsers.add_parser(
        "delete-logs",
        help="Delete all log entries from ReportPortal launches.",
        parents=[common],
    )
    rp_delete_logs.add_argument(
        "--all",
        action="store_true",
        dest="all_launches",
        help="Delete logs from all IN_PROGRESS launches.",
    )
    _add_input_source_args(rp_delete_logs)
    _add_dryrun_arg(
        rp_delete_logs,
        help_text="Show which logs would be deleted without actually deleting them.",
    )

    # --- delete-stale ---
    rp_delete_stale = rp_subparsers.add_parser(
        "delete-stale",
        help="Delete stale launches (stopped/interrupted with no test items).",
        parents=[common],
    )
    _add_date_filter_args(rp_delete_stale)
    _add_dryrun_arg(
        rp_delete_stale,
        help_text="Show which launches would be deleted without actually deleting them.",
    )

    # --- check ---
    rp_subparsers.add_parser(
        "check",
        help="Test ReportPortal connection and show sample data.",
        parents=[common],
    )

    # --- Deprecated flag-verb aliases (hidden from help) ---
    rp_compat = reportportal.add_mutually_exclusive_group()
    rp_compat.add_argument("--finish", action="store_true", help=argparse.SUPPRESS)
    rp_compat.add_argument("--test", action="store_true", help=argparse.SUPPRESS)
    rp_compat.add_argument(
        "--delete-logs",
        action="store_true",
        dest="delete_logs",
        help=argparse.SUPPRESS,
    )
    rp_compat.add_argument(
        "--delete-stale",
        action="store_true",
        dest="delete_stale",
        help=argparse.SUPPRESS,
    )
    reportportal.add_argument(
        "--enrich-logs",
        action="store_true",
        dest="enrich_logs",
        help=argparse.SUPPRESS,
    )
    reportportal.add_argument(
        "--all-launches",
        action="store_true",
        dest="all_launches",
        help=argparse.SUPPRESS,
    )
    _add_input_source_args(reportportal)
    _add_date_filter_args(reportportal)
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

    cancel.add_argument(
        "--run",
        metavar="RUN_ID",
        help="Select a specific run by manifest ID to cancel.",
    )

    # Cancel control
    _add_dryrun_arg(
        cancel,
        help_text="Show which tasks would be cancelled without actually cancelling them.",
    )

    # ==================== MIGRATE-ARCHIVE SUBCOMMAND ====================
    subparsers.add_parser(
        "migrate-archive",
        help="Convert legacy archive files to JSON manifests.",
        description="One-time migration of ~/.enge/jobs_archive/ files into "
        "the manifest store. Non-destructive and idempotent.",
        parents=[common],
    )

    if argcomplete:
        argcomplete.autocomplete(parser)
    parsed_args = parser.parse_args(args)

    return parsed_args
