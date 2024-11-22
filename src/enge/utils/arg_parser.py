#!/usr/bin/env python3

import argparse
import pathlib

from .tf_artifact import CoprRef, BrewRef


def get_arguments():
    """Define command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Send requests to and get the results back from the Testing Farm conveniently.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument("-c", "--config", help="Custom path to the config file.")

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

    artifact_type = test.add_mutually_exclusive_group(required=True)

    artifact_type.add_argument(
        "--copr",
        type=CoprRef,
        nargs="?",
        default=None,
        const=CoprRef(None),
        help="Test a fedora-copr-build. "
        "The pull request reference (pr123) or the BuildID needs to be provided either in the config file or as an argument.",
    )

    artifact_type.add_argument(
        "--brew",
        type=BrewRef,
        nargs="?",
        default=None,
        const=BrewRef(None),
        help="Test a brew build RC. "
        "The verison reference (0.1.2-3) or TaskID needs to be provided either in the config file or as an argument.",
    )

    test.add_argument(
        "-p",
        "--plans",
        nargs="+",
        help="Specify a test plan or multiple plans to request at testing farm."
        " Accepts multiple space separated values, sends as a separate request."
        " To run whole set of tiers use /plans/",
    )

    test.add_argument(
        "--planfilter",
        "--pf",
        nargs="?",
        help="Filter plans. "
        "The specified plan filter will be used in tmt plan ls --filter <YOUR-FILTER> command. "
        "By default enabled: true filter is applied by the Testing Farm.",
    )

    test.add_argument(
        "--testfilter",
        "--tf",
        nargs="?",
        help="Filter tests. "
        "The specified test filter will be used in tmt run discover plan test --filter <YOUR-FILTER> command.",
    )

    test.add_argument(
        "-t",
        "--target",
        nargs="+",
        help="Choose a target system for the test run.",
    )

    test.add_argument(
        "-g",
        "--tests-git-url",
        help="URL to the tests metadata repository.",
    )

    test.add_argument(
        "-b",
        "--tests-git-branch",
        help="Git branch to checkout the test suite from.",
    )

    test.add_argument(
        "--architecture",
        "--arch",
        default="x86_64",
        help="Redefine suitable architecture.\nDefault: '%(default)s'.",
    )

    test.add_argument(
        "-w",
        "--wait",
        type=int,
        help="Provide number of seconds to wait for successful response.",
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

    test.add_argument(
        "--tag", nargs=1, help="Tag the archived task file with a custom tag."
    )

    report = subparsers.add_parser(
        "report",
        help="Report results for requested tasks.",
        description="Parse task IDs, Testing Farm artifact URLs "
        "or Testing Farm API request URLs from multiple sources.",
    )
    tasks_source = report.add_mutually_exclusive_group()
    tasks_source.add_argument(
        "-f",
        "--file",
        action="append",
        help="A filepath is the source for the request_ids, artifact URLs or request URLs to parse. "
        "Can be provided multiple times -f file1 -f ~/file2",
    )
    tasks_source.add_argument(
        "-c",
        "--cmd",
        action="append",
        help="Commandline is the source for the request_ids, artifact URLs or request URLs to parse. "
        "Can be provided multiple times -c id1 -c id2",
    )
    tasks_source.add_argument(
        "--tag", action="append", help="Query for all task results under a given tag."
    )
    report.add_argument(
        "-p",
        "--default-path",
        type=pathlib.Path,
        help="Redefine the default path to the archived task files.",
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
        "--showarch",
        action="store_true",
        help="Display architecture. By default the architecture is not shown.",
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
        help="Plan name to be treated as one in plan1=plan2 format, useful for runs comparison in case of renaming.",
    )

    return parser.parse_args()


args = get_arguments()
