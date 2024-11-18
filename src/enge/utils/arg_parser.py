#!/usr/bin/env python3

from . import FormatText

import argparse


def get_arguments():
    """Define command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Send requests to and get the results back from the Testing Farm conveniently.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("--config", "-c", help="Custom path to the config file.")

    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Print out additional information for each request.",
    )

    subparsers = parser.add_subparsers(dest="action")

    test = subparsers.add_parser(
        "test",
        help="Dispatch a job to the Testing Farm API endpoint.",
        description="Send requests to the Testing Farm conveniently.",
    )

    test.add_argument(
        "artifact_type",
        metavar="artifact_type",
        help="Choose which type of artifact to test. Choices: %(choices)s",
    )

    reference = test.add_mutually_exclusive_group(required=True)

    reference.add_argument(
        "-r",
        "--reference",
        nargs=1,
        help=f"""
        For brew: Specify the reference version to find the correct artifact (e.g. 0.1-2, 0.1.2).
        For copr: Specify the pull request reference to find the correct artifact (e.g. pr123, main, master, ...).
        {FormatText.format_text('Mutually exclusive with respect to --task-id.', bold=True)}""",
    )

    reference.add_argument(
        "-i",
        "--task-id",
        nargs=1,
        help=f"""
        For brew: Specify the TASK ID for required brew build.
        {FormatText.bold}NOTE: Double check, that you are passing TASK ID for copr builds, not BUILD ID otherwise the
        Testing Farm won't be able to install the package.{FormatText.end}
        For copr: Specify the BUILD ID for required copr build.
        {FormatText.format_text('Mutually exclusive with respect to --reference.', bold=True)}""",
    )

    test.add_argument(
        "-g",
        "--tests-git-url",
        help="URL to the tests metadata repository.",
    )

    test.add_argument(
        "-b",
        "--tests-git-branch",
        help="Git branch to checkout tests from.",
    )

    test.add_argument(
        "--architecture",
        "--arch",
        default="x86_64",
        help="""Choose suitable architecture.\nDefault: '%(default)s'.""",
    )

    test.add_argument(
        "-p",
        "--plans",
        required=True,
        nargs="+",
        help="""Specify a test plan or multiple plans to request at testing farm.
        To run whole set of tiers use /plans/tier*/
        Accepts multiple space separated values, sends as a separate request.""",
    )

    test.add_argument(
        "--planfilter",
        "--pf",
        nargs="?",
        help="""Filter plans.
        The specified plan filter will be used in tmt plan ls --filter <YOUR-FILTER> command.
        By default enabled: true filter is applied.
    """,
    )

    test.add_argument(
        "--testfilter",
        "--tf",
        nargs="?",
        help="""Filter tests.
        The specified test filter will be used in tmt run discover plan test --filter <YOUR-FILTER> command.
    """,
    )

    test.add_argument(
        "-t",
        "--target",
        nargs="+",
        help="""Choose targeted test run. For c2r targeted OS, for leapp targeted upgrade path.""",
    )

    test.add_argument(
        "-w",
        "--wait",
        default=20,
        type=int,
        help="""Provide number of seconds to wait for successful response.\nDefault is 20 seconds.""",
    )

    test.add_argument(
        "-l",
        "--parallel-limit",
        type=int,
        help="Redefine the limit of plans run in parallel.",
    )

    test.add_argument(
        "--dryrun",
        action="store_true",
        help="Print out just the payload that would be sent to the testing farm.\nDo not actually send any request.",
    )

    test.add_argument(
        "-u", "--uefi", action="store_true", help="Request UEFI in provisioning."
    )
    report = subparsers.add_parser(
        "report",
        help="Report results for requested tasks.",
        description="Parses task IDs, Testing Farm artifact URLs "
        "or Testing Farm API request URLs from multiple sources.",
    )
    report.add_argument(
        "--showarch",
        action="store_true",
        help="Display architecture. By default the architecture is not shown.",
    )
    report.add_argument(
        "-l2",
        "--level2",
        action="store_true",
        help="Display test view detail. By default the report shows only plan view.",
    )
    report.add_argument(
        "-s",
        "--short",
        action="store_true",
        help="Display short test and plan names.",
    )
    report.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help="Wait for the job to complete. Print the table afterwards",
    )
    report.add_argument(
        "-d",
        "--download-logs",
        action="store_true",
        help="Download logs for requested run(s).",
    )
    report.add_argument(
        "--skip-pass",
        action="store_true",
        help="Skip PASSED results while showing table and while downloading logs.",
    )
    report.add_argument(
        "--compare",
        action="store_true",
        help="Build a comparison table for several runs results",
    )
    report.add_argument(
        "-u",
        "--unify-results",
        action="append",
        help="Plans name to be treated as one in plan1=plan2 format, useful for runs comparison in case of renaming.",
    )
    tasks_source = report.add_mutually_exclusive_group()
    tasks_source.add_argument(
        "-l",
        "--latest",
        action="store_true",
        help=f"""
        Parse the request_ids, artifact URLs or request URLs from the latest run.
        {FormatText.format_text('Mutually exclusive with respect to --file and --cmd.', bold=True)}
    """,
    )
    tasks_source.add_argument(
        "-f",
        "--file",
        action="append",
        help=f"""
        A filepath is the source for the request_ids, artifact URLs or request URLs to parse.
        Can be provided multiple times -f file1 -f ~/file2 ...
        {FormatText.format_text('Mutually exclusive with respect to --latest and --cmd.', bold=True)}
    """,
    )
    tasks_source.add_argument(
        "-c",
        "--cmd",
        action="append",
        help=f"""
        Commandline is the source for the request_ids, artifact URLs or request URLs to parse.
        Can be provided multiple times -c id1 -c id2 ...
        {FormatText.format_text('Mutually exclusive with respect to --file and --latest.', bold=True)}
    """,
    )

    return vars(parser.parse_args())


args = get_arguments()
