import logging
import os
import re
import sys
import uuid

from prettytable import PrettyTable

from enge.utils import FormatText
from enge.utils.opt_manager import parsed_opts

RETURN_VALUE = None
"""
 0 - All pass
 1 - Python exception or bailout
 2 - No error at least one fail
 3 - At least one error
 4 - No result
 everything else - consult with enge maintainer(s)
"""
ALL_PASS = 0
FAIL_HERE = 2
ERROR_HERE = 3
NO_RESULT = 4

LOGGER = logging.getLogger(__name__)
LATEST_TASKS_FILE = parsed_opts.archive_tasks_latest


def update_retval(new_value):
    global RETURN_VALUE
    if RETURN_VALUE is None or new_value > RETURN_VALUE:
        RETURN_VALUE = new_value


def parse_tasks():
    request_url_list = []

    def _get_tasks_source_data():
        source = None
        source_data = []
        if getattr(parsed_opts.cli_args, "input", None):
            LOGGER.debug("Getting tasks from command line input arguments")
            source_data.extend(parsed_opts.cli_args.input)

        if parsed_opts.cli_args.file:
            source = parsed_opts.cli_args.file
            for file in source:
                if os.path.exists(file):
                    task_ids = open(file).readlines()
                    source_data.extend(task_ids)
                else:
                    LOGGER.critical(
                        f"Given path {parsed_opts.cli_args.file} does not exist!"
                    )
                    sys.exit(1)

        if parsed_opts.cli_args.get_tag:
            default_path = parsed_opts.archive_tasks_default
            if not os.path.exists(default_path):
                LOGGER.critical(f"The given path {default_path} does not exist!")
                sys.exit(1)

            # Compile regex patterns for efficiency
            compiled_patterns = []
            for tag in parsed_opts.cli_args.get_tag:
                try:
                    compiled_patterns.append(re.compile(tag))
                except re.error as e:
                    LOGGER.error(f"Invalid regex pattern '{tag}': {e}")
                    sys.exit(1)

            source = []
            for file in os.listdir(default_path):
                file_extension = file.split(".", 1)[-1] if "." in file else ""

                # Check if any pattern matches the extension or full filename
                if any(
                    pattern.search(file_extension) or pattern.search(file)
                    for pattern in compiled_patterns
                ):
                    source.append(file)

            for file in source:
                file = os.path.join(default_path, file)
                task_ids = open(file).readlines()
                source_data.extend(task_ids)

        if not any(
            (
                parsed_opts.cli_args.file,
                getattr(parsed_opts.cli_args, "input", None),
                parsed_opts.cli_args.get_tag,
            )
        ):
            if not os.path.exists(LATEST_TASKS_FILE):
                LOGGER.critical(
                    f"The latest job file {LATEST_TASKS_FILE} does not exist!"
                )
                LOGGER.critical(
                    "Use the --file option with path to a file containing the job IDs. "
                    "Or pass the job IDs through the --input argument."
                )
                sys.exit(1)
            source = LATEST_TASKS_FILE
            source_data = open(source).readlines()

        return source, source_data

    tasks_source, tasks_source_data = _get_tasks_source_data()

    uuid_pattern = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )

    for task in tasks_source_data:
        task = task.strip().rstrip("/")
        if not task:
            continue

        match = uuid_pattern.search(task)
        if not match:
            LOGGER.debug(f"Cannot parse the UUID from {task}")
            continue

        matched_uuid = match.group(0)
        task = os.path.join(
            str(parsed_opts.testing_farm_endpoint.api_endpoint_url), matched_uuid
        )

        # Validate UUID
        task_id = None
        try:
            task_id = task.split("/")[-1]
            uuid.UUID(task_id)
        except ValueError:
            raise ValueError(task_id)

        request_url_list.append(task)

    return request_url_list, tasks_source


def parse_request_xunit(request_url_list=None, tasks_source=None, skip_pass=False):
    """Parse request xunit with concurrent requests for better performance."""
    from enge.report.concurrent_parser import parse_request_xunit_concurrent

    return parse_request_xunit_concurrent(request_url_list, tasks_source, skip_pass)


def _split_name(name, index):
    """A helper that splits a test name at the position given by index"""
    name_raw = name.split("/")
    try:
        name_raw.remove("")
    except ValueError:
        pass
    return "/".join(name_raw[index:])


def build_table_comparison():
    """
    Generate a table holding comparable results of several tests.

    This allows a clear comparison of several tft runs with particular test
    results side by side in respectable columns.
    Sample format:
    **     tft_run_uuid1  tft_run_uuid2
    test1   PASS            FAIL
    test2   -               PASS
    test3   PASS            PASS
    """
    planname_split_index = 0
    testname_split_index = 0
    if parsed_opts.cli_args.short:
        planname_split_index = -1
        testname_split_index = -1

    parsed_dict = parse_request_xunit(skip_pass=parsed_opts.cli_args.skip_pass)
    result_table = PrettyTable()
    uuids = list(parsed_dict.keys())
    fields = ["Test Plan"] + uuids
    result_table.field_names = fields
    # plan_name -> uuid run result for particular plan
    regroup_results_plans = {}
    # plan_name -> test_name -> uuid run result for particular test
    regroup_results_tests = {}
    unified_names_map = {}
    for plan_name in getattr(parsed_opts.cli_args, "unify", []) or []:
        name1, name2 = plan_name.split("=", 2)
        unified_names_map[name1] = plan_name
        unified_names_map[name2] = plan_name

    def _get_plan_key(testsuite_data):
        plan_key = _split_name(testsuite_data["testsuite_name"], planname_split_index)
        # check against unifed map to combine results
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
        if getattr(parsed_opts.cli_args, "show_tests", False):
            # Append first row with plan name only
            result_table.add_row([plan_name] + [""] * len(uuids))
            result_table.add_row(["*"] + [""] * len(uuids))
            for test_name, test_data in regroup_results_tests[plan_name].items():
                row_data = [f'{"*" * 4} {test_name}']
                for uuid in uuids:
                    row_data.append(colorize(test_data.get(uuid, "-")))
                result_table.add_row(row_data)
        else:
            row_data = [plan_name]
            for uuid in uuids:
                # Report just plans
                if not uuid in plan_data:
                    # this plan has not been executed for this run
                    row_data.append("-")
                else:
                    row_data.append(colorize(plan_data[uuid]["result"]))
            result_table.add_row(row_data)
    result_table.align = "l"

    return result_table


def build_table():
    parsed_dict = parse_request_xunit(skip_pass=parsed_opts.cli_args.skip_pass)

    # For multiple UUIDs, we'll create separate tables
    tables_list = []
    uuid_url_mapping = {}

    planname_split_index = 0
    testname_split_index = 0

    if parsed_opts.cli_args.short:
        planname_split_index = -1
        testname_split_index = -1

    for task_uuid, data in parsed_dict.items():
        # Create metadata title for each table
        result_url = (
            f"{parsed_opts.testing_farm_endpoint.log_artifact_baseurl}/{task_uuid}"
        )
        # Get architecture from first testsuite or default to 'Unknown'
        arch = (
            data["testsuites"][0]["testsuite_arch"] if data["testsuites"] else "Unknown"
        )

        result_table = PrettyTable()
        # Keep table title clean - metadata will be displayed separately

        # prepare field names - no more UUID, Target, Arch columns
        fields = ["Test Plan", "Plan Result"]
        if getattr(parsed_opts.cli_args, "show_tests", False):
            fields += ["Test Case", "Test Result"]
        result_table.field_names = fields

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

        def add_row(*args, **kwargs):
            result_table.add_row(list(_gen_row(*args, **kwargs)))

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
                for testcase in testsuite_data["testcases"]:
                    if testcase["testcase_result"] == "SKIPPED":
                        continue
                    testcase_result = testcase["testcase_result"]
                    add_row(
                        testcase=colorize(
                            testcase_result,
                            _split_name(
                                testcase["testcase_name"], testname_split_index
                            ),
                        ),
                        testcase_result=colorize(testcase_result),
                    )

        result_table.align = "l"

        # Store metadata for display
        metadata = {
            "SourceCompose:": data.get("target_name", None),
            "Plan:": data.get("plan", None),
            "PlanFilter:": data.get("plan_filter", None),
            "TargetVersion:": data.get("target_release", None),
            "UpgradePath:": data.get("upgrade_path", None),
            "Architecture:": arch,
            "TaskUUID:": task_uuid,
            "ResultURL:": result_url,
        }

        tables_list.append((result_table, metadata))

    # Return list of (table, metadata) tuples
    return tables_list


def get_color_format(result):
    color_format_default = FormatText.END
    if result == "PASSED":
        return FormatText.GREEN + FormatText.BOLD
    elif result == "FAILED":
        return FormatText.RED + FormatText.BOLD
    elif result in ("ERROR", "UNDEFINED", "PENDING"):
        return FormatText.YELLOW + FormatText.BOLD
    return color_format_default


def colorize(result, label=None, color_format_default=FormatText.END):
    """
    Colorize provided label (or result) using color associated to the provided result.

    :return: Colorized label (if provided) or result (if label is not provided)
    :rtype: str
    """
    label = label if label else result
    return get_color_format(result) + label + color_format_default


def main(result_table=None):
    # Handle --show-ids option
    if getattr(parsed_opts.cli_args, "show_ids", False):
        request_url_list, _ = parse_tasks()
        if request_url_list:
            for request_url in request_url_list:
                # Extract UUID from the URL
                task_id = request_url.split("/")[-1]
                print(task_id)
        else:
            LOGGER.info("No UUIDs found!")
        return ALL_PASS

    if result_table is None:
        if parsed_opts.cli_args.compare:
            # For comparison mode, wrap in tuple format for consistency
            comparison_table = build_table_comparison()
            result_table = [(comparison_table, None)]  # No metadata for comparison mode
        else:
            result_table = build_table()

    # Handle list of (table, metadata) tuples
    has_content = False
    for table, metadata in result_table:
        if table.rowcount > 0:
            print()
            print(
                FormatText.format_text(
                    "~~~ REQUEST METADATA ~~~~~~~~~~~~~~", text_col=FormatText.DIM
                )
            )
            # Display metadata block before table
            if metadata:
                for title, value in metadata.items():
                    if value is None:
                        continue
                    print(
                        FormatText.format_text(
                            f"{title:<20}{value}", text_col=FormatText.DIM
                        )
                    )
            if parsed_opts.cli_args.jira:
                print("{noformat}")
                print(table)
                print("{noformat}")
            else:
                print(table)

            has_content = True

    if not has_content:
        LOGGER.info("Nothing to report!")

    # Get return value from concurrent parser
    try:
        from enge.report.concurrent_parser import get_return_value

        return get_return_value()
    except ImportError:
        return RETURN_VALUE
