import json
import logging
from datetime import timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from enge.report import results_cache
from enge.utils import parse_date_arg
from enge.utils.app_context import AppContext
from enge.utils.console import console
from enge.utils.globals import ExitCode
from enge.utils.manifest import ManifestReader
from enge.utils.task_resolver import parse_tasks, parse_tasks_with_map  # noqa: F401

LOGGER = logging.getLogger(__name__)


def parse_request_xunit(
    request_url_list=None, tasks_source=None, skip_pass=False, *, ctx
):
    """Parse request xunit — returns the parsed dict only."""
    from enge.report.concurrent_parser import parse_request_xunit_concurrent

    parsed_dict, _retval, _task_results = parse_request_xunit_concurrent(
        ctx, request_url_list, tasks_source, skip_pass
    )
    return parsed_dict


def _parse_request_xunit_with_retval(
    ctx, request_url_list=None, tasks_source=None, skip_pass=False
):
    from enge.report.concurrent_parser import parse_request_xunit_concurrent

    return parse_request_xunit_concurrent(
        ctx, request_url_list, tasks_source, skip_pass
    )


def _split_name(name, index):
    """A helper that splits a test name at the position given by index"""
    name_raw = name.split("/")
    try:
        name_raw.remove("")
    except ValueError:
        pass
    return "/".join(name_raw[index:])


def build_table_comparison(ctx):
    tables_list = []

    planname_split_index = 0
    testname_split_index = 0
    if ctx.cli_args.short:
        planname_split_index = -1
        testname_split_index = -1

    parsed_dict, retval, task_results = _parse_request_xunit_with_retval(
        ctx, skip_pass=ctx.cli_args.skip_pass
    )
    result_table = Table(box=box.ROUNDED)

    # Sort UUIDs by architecture first, then by creation date within each architecture
    def get_arch_and_timestamp(uuid):
        data = parsed_dict[uuid]
        arch = (
            data["testsuites"][0]["testsuite_arch"] if data["testsuites"] else "Unknown"
        )
        created = data.get("created", "")
        return (arch, created)

    uuids = sorted(parsed_dict.keys(), key=get_arch_and_timestamp)

    # Create headers with architecture and index, store mapping for later display
    headers = []
    uuid_mapping = {}

    for i, uid in enumerate(uuids, 1):
        data = parsed_dict[uid]
        # Get architecture from first testsuite or default to Unknown
        arch = (
            data["testsuites"][0]["testsuite_arch"] if data["testsuites"] else "Unknown"
        )
        # Use format: "arch (index)" for cleaner headers
        header = f"{arch} ({i})"
        headers.append(header)
        # Store mapping for display below table
        result_url = f"{ctx.testing_farm_endpoint.log_artifact_baseurl}/{uid}"
        uuid_mapping[i] = {"uuid": uid, "url": result_url, "arch": arch}

    fields = ["Test Plan"] + headers
    for field in fields:
        result_table.add_column(field, justify="left")
    # plan_name -> uuid run result for particular plan
    regroup_results_plans = {}
    # plan_name -> test_name -> uuid run result for particular test
    regroup_results_tests = {}
    unified_names_map = {}
    for plan_name in getattr(ctx.cli_args, "unify", []) or []:
        # Only split on the first '=' to support values containing '='
        name1, name2 = plan_name.split("=", 1)
        unified_names_map[name1] = plan_name
        unified_names_map[name2] = plan_name

    def _get_plan_key(testsuite_data):
        plan_key = _split_name(testsuite_data["testsuite_name"], planname_split_index)
        # check against unified map to combine results
        return unified_names_map.get(plan_key) or plan_key

    for task_uuid, data in parsed_dict.items():
        for testsuite_data in data["testsuites"]:
            res_uuid = {
                "result": testsuite_data["testsuite_result"],
                "testcases": sorted(
                    (x for x in testsuite_data["testcases"]),
                    key=lambda x: x["testcase_name"],
                ),
            }
            plan_key = _get_plan_key(testsuite_data)
            try:
                regroup_results_plans[plan_key][task_uuid] = res_uuid
            except KeyError:
                regroup_results_plans[plan_key] = {task_uuid: res_uuid}
            # Preparation for plans -> tests mapping
            if plan_key not in regroup_results_tests:
                regroup_results_tests[plan_key] = {}
            # Now process testcases for easier level2 table processing
            for testcase_data in testsuite_data["testcases"]:
                plan_key = _get_plan_key(testsuite_data)
                test_key = _split_name(
                    testcase_data["testcase_name"], testname_split_index
                )
                if test_key not in regroup_results_tests[plan_key]:
                    regroup_results_tests[plan_key][test_key] = {}
                try:
                    regroup_results_tests[plan_key][test_key][task_uuid] = (
                        testcase_data["testcase_result"]
                    )
                except KeyError:
                    regroup_results_tests[plan_key][test_key] = {
                        task_uuid: testcase_data["testcase_result"]
                    }

    for plan_name, plan_data in regroup_results_plans.items():
        if getattr(ctx.cli_args, "show_tests", False):
            result_table.add_row(escape(plan_name), *[""] * len(uuids))
            test_items = list(regroup_results_tests[plan_name].items())
            for i, (test_name, test_data) in enumerate(test_items):
                row_data = [f'{"*" * 4} {escape(test_name)}']
                for uuid in uuids:
                    row_data.append(colorize(test_data.get(uuid, "-")))
                result_table.add_row(*row_data, end_section=(i == len(test_items) - 1))
        else:
            row_data = [plan_name]
            for uuid in uuids:
                # Report just plans
                if uuid not in plan_data:
                    # this plan has not been executed for this run
                    row_data.append("-")
                else:
                    row_data.append(colorize(plan_data[uuid]["result"]))
            result_table.add_row(*row_data)

    tables_list.append((result_table, uuid_mapping))

    return tables_list, retval, task_results


def build_table(ctx):
    parsed_dict, retval, task_results = _parse_request_xunit_with_retval(
        ctx, skip_pass=ctx.cli_args.skip_pass
    )

    tables_list = []

    planname_split_index = 0
    testname_split_index = 0

    if ctx.cli_args.short:
        planname_split_index = -1
        testname_split_index = -1

    for task_uuid, data in parsed_dict.items():
        result_url = f"{ctx.testing_farm_endpoint.log_artifact_baseurl}/{task_uuid}"
        # Get architecture from first testsuite or default to 'Unknown'
        arch = (
            data["testsuites"][0]["testsuite_arch"] if data["testsuites"] else "Unknown"
        )

        result_table = Table(box=box.ROUNDED)
        # Keep table title clean - metadata will be displayed separately

        # prepare field names - no more UUID, Target, Arch columns
        fields = ["Test Plan", "Plan Result"]
        if getattr(ctx.cli_args, "show_tests", False):
            fields += ["Test Case", "Test Result"]
        for field in fields:
            result_table.add_column(field, justify="left")

        def _gen_row(
            testplan="",
            testplan_result="",
            testcase="",
            testcase_result="",
        ):
            if "Test Plan" in fields:
                yield testplan
            if "Plan Result" in fields:
                yield testplan_result
            if "Test Case" in fields:
                yield testcase
            if "Test Result" in fields:
                yield testcase_result

        def add_row(*args, end_section=False, **kwargs):
            result_table.add_row(
                *list(_gen_row(*args, **kwargs)), end_section=end_section
            )

        # Build table for this specific UUID
        for testsuite_data in data["testsuites"]:
            if testsuite_data["testsuite_result"] == "SKIPPED":
                continue
            testsuite_result = testsuite_data["testsuite_result"]
            add_row(
                testplan=colorize(
                    testsuite_result,
                    _split_name(testsuite_data["testsuite_name"], planname_split_index),
                ),
                testplan_result=colorize(testsuite_result),
            )
            if "Test Case" in fields:
                visible = [
                    tc
                    for tc in testsuite_data["testcases"]
                    if tc["testcase_result"] != "SKIPPED"
                ]
                for i, testcase in enumerate(visible):
                    testcase_result = testcase["testcase_result"]
                    add_row(
                        testcase=colorize(
                            testcase_result,
                            _split_name(
                                testcase["testcase_name"], testname_split_index
                            ),
                        ),
                        testcase_result=colorize(testcase_result),
                        end_section=(i == len(visible) - 1),
                    )

        # Store metadata for display
        metadata = {
            "SourceCompose:": data.get("source_compose", None),
            "Plan:": data.get("plan", None),
            "PlanFilter:": data.get("plan_filter", None),
            "TargetVersion:": data.get("target_release", None),
            "UpgradePath:": data.get("upgrade_path", None),
            "Architecture:": arch,
            "TaskUUID:": task_uuid,
            "ResultURL:": result_url,
        }

        tables_list.append((result_table, metadata))

    return tables_list, retval, task_results


def _rich_style_for_result(result):
    """Return the rich markup style name for a test result."""
    if result == "PASSED":
        return "bold green"
    elif result == "FAILED":
        return "bold red"
    elif result in ("ERROR", "UNDEFINED", "PENDING"):
        return "bold yellow"
    return ""


def colorize(result, label=None):
    """
    Colorize provided label (or result) using rich markup for the given result.

    :return: Rich-markup-wrapped label (if provided) or result (if label is not provided)
    :rtype: str
    """
    label = label if label else result
    style = _rich_style_for_result(result)
    if style:
        return f"[{style}]{escape(str(label))}[/]"
    return escape(str(label))


def _handle_list(ctx: AppContext) -> int:
    runs_dir = Path(ctx.manifest_runs_dir)
    cli_args = ctx.cli_args
    output_fmt = getattr(cli_args, "output_format", "terminal")

    filter_kwargs = {}
    if getattr(cli_args, "filter_set", None):
        filter_kwargs["set_name"] = cli_args.filter_set
    if getattr(cli_args, "filter_tier", None):
        filter_kwargs["tier"] = cli_args.filter_tier
    if getattr(cli_args, "filter_arch", None):
        filter_kwargs["arch"] = cli_args.filter_arch
    if getattr(cli_args, "filter_tag", None):
        filter_kwargs["tag"] = cli_args.filter_tag
    since_str = getattr(cli_args, "since", None)
    until_str = getattr(cli_args, "until", None)
    if since_str:
        dt = parse_date_arg(since_str)
        filter_kwargs["since"] = (
            dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        )
    if until_str:
        dt = parse_date_arg(until_str)
        dt = dt.replace(hour=23, minute=59, second=59)
        filter_kwargs["until"] = (
            dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        )

    if filter_kwargs:
        runs = ManifestReader.find_runs(runs_dir, **filter_kwargs)
    else:
        runs = ManifestReader.list_runs(runs_dir)

    if output_fmt == "json":
        print(json.dumps(runs, indent=2))
        return ExitCode.SUCCESS

    if not runs:
        LOGGER.info("No runs found in the manifest store.")
        return ExitCode.SUCCESS

    if output_fmt == "gitlab":
        print(
            "| Run ID | Created | Command | Set | Tier(s) | Arch(es) | Tags | Requests | Origin |"
        )
        print("|---|---|---|---|---|---|---|---|---|")
        for r in runs:
            ctx_data = r.get("context", {})
            print(
                f"| {r['run_id']} "
                f"| {r.get('created_at', '')} "
                f"| {r.get('command', '')} "
                f"| {ctx_data.get('set', '')} "
                f"| {', '.join(ctx_data.get('tiers', []))} "
                f"| {', '.join(ctx_data.get('architectures', []))} "
                f"| {', '.join(r.get('tags', []))} "
                f"| {r.get('request_count', 0)} "
                f"| {r.get('origin', 'native')} |"
            )
    else:
        table = Table(box=box.ROUNDED, title="Manifest Store")
        table.add_column("Run ID", style="bold")
        table.add_column("Created")
        table.add_column("Cmd")
        table.add_column("Set")
        table.add_column("Tier(s)")
        table.add_column("Arch(es)")
        table.add_column("Tags")
        table.add_column("Reqs", justify="right")
        table.add_column("Origin")

        for r in reversed(runs):
            ctx_data = r.get("context", {})
            created = r.get("created_at", "")
            tiers = ctx_data.get("tiers", [])
            archs = ctx_data.get("architectures", [])
            table.add_row(
                r["run_id"],
                created[:19].replace("T", " ") if created else "",
                r.get("command", ""),
                ctx_data.get("set", ""),
                ", ".join(tiers) if isinstance(tiers, list) else str(tiers),
                ", ".join(archs) if isinstance(archs, list) else str(archs),
                ", ".join(r.get("tags", [])),
                str(r.get("request_count", 0)),
                r.get("origin", "native"),
            )
        console.print(table)

    return ExitCode.SUCCESS


def main(ctx: AppContext, result_table=None):
    if getattr(ctx.cli_args, "list", False):
        return _handle_list(ctx)

    if getattr(ctx.cli_args, "show_ids", False):
        request_url_list, _ = parse_tasks(ctx)
        if request_url_list:
            for request_url in request_url_list:
                task_id = request_url.split("/")[-1]
                print(task_id)
        else:
            LOGGER.info("No UUIDs found!")
        return ExitCode.SUCCESS

    retval = None
    if result_table is None:
        if ctx.cli_args.compare:
            result_table, retval, task_results = build_table_comparison(ctx)
        else:
            result_table, retval, task_results = build_table(ctx)

        if task_results:
            try:
                results_cache.cache_report_results(ctx, task_results)
            except Exception:  # noqa: BLE001
                # Never let caching fail the report command -- caching is
                # a side effect of reporting, never a gate on it.
                # cache_report_results already guards its own known
                # failure modes internally; this is the outer backstop so
                # a caching bug can never take the report table down with
                # it (see CLAUDE.md "Results.json format" write policy).
                LOGGER.warning(
                    "Failed to update the local results cache; continuing",
                    exc_info=True,
                )

    has_content = False
    for table, metadata in result_table:
        if table.row_count > 0:
            console.print()
            console.print("~~~ REQUEST METADATA ~~~~~~~~~~~~~~", style="dim")

            if not ctx.cli_args.compare:
                for title, value in metadata.items():
                    if value is None:
                        continue
                    console.print(f"{title:<20}{value}", style="dim")

            output_fmt = getattr(ctx.cli_args, "output_format", "terminal")
            jira_mode = getattr(ctx.cli_args, "jira", False)
            if jira_mode:
                plain = Console(no_color=True, highlight=False)
                print("{noformat}")
                plain.print(table)
                print("{noformat}")
            elif output_fmt == "gitlab":
                plain = Console(no_color=True, highlight=False)
                print("```")
                plain.print(table)
                print("```")
            else:
                console.print(table)

            if ctx.cli_args.compare:
                console.print()
                console.print("~~~ TASK REFERENCE ~~~~~~~~~~~~~~~~", style="dim")
                for index, info in metadata.items():
                    if isinstance(info, dict) and "uuid" in info:
                        console.print(
                            f"({index}) {info['arch']}: {info['url']}",
                            style="dim",
                        )

            has_content = True

    if not has_content:
        LOGGER.info("Nothing to report!")

    return retval
