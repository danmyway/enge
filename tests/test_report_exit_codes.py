"""Characterization tests for report exit codes.

Golden-pin the exit-code contract: 0 (all pass), 2 (fail), 3 (error),
4 (no result).
"""

import unittest
from unittest.mock import patch

from tests._helpers import make_app_context
from enge.report.concurrent_parser import (
    XMLParser,
    TaskResult,
    ConcurrentRequestParser,
)

# ── Fixture XML snippets ────────────────────────────────────────────

_XML_PASSED = """\
<testsuites overall-result="passed">
  <testsuite name="/plans/smoke" result="passed">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="/tests/basic" result="passed"/>
  </testsuite>
</testsuites>
"""

_XML_FAILED = """\
<testsuites overall-result="failed">
  <testsuite name="/plans/smoke" result="failed">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="/tests/basic" result="failed"/>
  </testsuite>
</testsuites>
"""

_XML_ERROR = """\
<testsuites overall-result="error">
  <testsuite name="/plans/smoke" result="error">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="/tests/basic" result="error"/>
  </testsuite>
</testsuites>
"""

_XML_PIPELINE_ERROR = """\
<testsuites overall-result="error">
  <testsuite name="pipeline" result="error">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="pipeline" result="error"/>
  </testsuite>
</testsuites>
"""

ALL_PASS = 0
FAIL_HERE = 2
ERROR_HERE = 3
NO_RESULT = 4


def _make_task_result(xunit_content=None, state="COMPLETE", **overrides):
    defaults = dict(
        request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
        request_source_compose="RHEL-9",
        request_target_release="10",
        request_upgrade_path="9 to 10",
        request_arch="x86_64",
        request_state=state,
        request_datetime_created="2026-01-01T00:00:00",
        request_plan="/plans/smoke",
        request_plan_filter="",
        request_summary="Undefined",
        request_result_overall="Undefined",
        results_xml_url="http://example.com/results.xml",
        url="http://example.com/api/00000000",
        xunit_content=xunit_content,
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


def _report_ctx(**cli_overrides):
    cli = {"download": False}
    cli.update(cli_overrides)
    return make_app_context(action="report", extra_cli=cli)


class TestExitCodeFromXMLParser(unittest.TestCase):
    """Verify XMLParser.parse_xml_results sets task_result.retval correctly."""

    def test_passed_xml_yields_exit_0(self):
        task = _make_task_result(xunit_content=_XML_PASSED)
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task, ctx=ctx)
        self.assertEqual(task.retval, ALL_PASS)

    def test_failed_xml_yields_exit_2(self):
        task = _make_task_result(xunit_content=_XML_FAILED)
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task, ctx=ctx)
        self.assertEqual(task.retval, FAIL_HERE)

    def test_error_xml_yields_exit_3(self):
        task = _make_task_result(xunit_content=_XML_ERROR)
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task, ctx=ctx)
        self.assertEqual(task.retval, ERROR_HERE)

    def test_pipeline_error_xml_yields_exit_3(self):
        task = _make_task_result(xunit_content=_XML_PIPELINE_ERROR)
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task, ctx=ctx)
        self.assertEqual(task.retval, ERROR_HERE)

    def test_no_xml_content_yields_no_retval_change(self):
        task = _make_task_result(xunit_content=None)
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task, ctx=ctx)
        self.assertIsNone(task.retval)

    def test_max_semantics_across_tasks(self):
        task_fail = _make_task_result(xunit_content=_XML_FAILED)
        task_error = _make_task_result(
            xunit_content=_XML_ERROR,
            request_uuid="bbbbbbbb-0000-0000-0000-000000000002",
        )
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task_fail, ctx=ctx)
        XMLParser.parse_xml_results(task_error, ctx=ctx)
        self.assertEqual(task_fail.retval, FAIL_HERE)
        self.assertEqual(task_error.retval, ERROR_HERE)
        self.assertEqual(max(task_fail.retval, task_error.retval), ERROR_HERE)

    def test_max_semantics_error_then_pass_on_same_task(self):
        task = _make_task_result(xunit_content=_XML_ERROR)
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task, ctx=ctx)
        self.assertEqual(task.retval, ERROR_HERE)


class TestExitCodeFromTaskState(unittest.TestCase):
    """Verify ConcurrentRequestParser._process_task_state sets task retval."""

    def test_error_state_yields_exit_3(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="ERROR")
        parser._process_task_state(task)
        self.assertEqual(task.retval, ERROR_HERE)

    def test_running_state_without_wait_yields_no_result(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="RUNNING")
        parser._process_task_state(task)
        self.assertEqual(task.retval, NO_RESULT)
        self.assertTrue(task.should_skip)

    def test_queued_state_without_wait_yields_no_result(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="QUEUED")
        parser._process_task_state(task)
        self.assertEqual(task.retval, NO_RESULT)
        self.assertTrue(task.should_skip)

    def test_canceled_state_skipped_no_retval_in_process(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="CANCELED")
        task.should_skip = True
        task.skip_reason = "canceled"
        parser._process_task_state(task)
        self.assertIsNone(task.retval)


class TestExitCodeFromMainEntrypoint(unittest.TestCase):
    """Verify report main() returns the correct exit code end-to-end."""

    def _make_ctx(self, **cli_overrides):
        cli = {
            "compare": False,
            "show_ids": False,
            "output_format": "terminal",
            "jira": False,
            "short": False,
            "skip_pass": False,
            "show_tests": False,
        }
        cli.update(cli_overrides)
        return make_app_context(action="report", extra_cli=cli)

    def test_main_returns_none_when_tables_prebuilt(self):
        from rich.table import Table
        import enge.report.__main__ as rm

        ctx = self._make_ctx()
        table = Table()
        table.add_column("Test")
        table.add_row("dummy")
        code = rm.main(ctx, result_table=[(table, {"task": "info"})])
        self.assertIsNone(code)

    def test_main_returns_none_when_no_content(self):
        from rich.table import Table
        import enge.report.__main__ as rm

        ctx = self._make_ctx()
        table = Table()
        table.add_column("Test")
        code = rm.main(ctx, result_table=[(table, {})])
        self.assertIsNone(code)

    def test_main_show_ids_returns_0(self):
        import enge.report.__main__ as rm

        ctx = self._make_ctx(
            show_ids=True,
            input=["aaaaaaaa-0000-0000-0000-000000000001"],
        )
        code = rm.main(ctx)
        self.assertEqual(code, ALL_PASS)

    def test_main_returns_retval_from_build_table(self):
        import enge.report.__main__ as rm
        from rich.table import Table

        ctx = self._make_ctx()
        table = Table()
        table.add_column("Test")
        table.add_row("dummy")

        with patch.object(
            rm, "build_table", return_value=([(table, {"k": "v"})], FAIL_HERE)
        ):
            code = rm.main(ctx)
        self.assertEqual(code, FAIL_HERE)

    def test_main_returns_retval_from_build_table_comparison(self):
        import enge.report.__main__ as rm
        from rich.table import Table

        ctx = self._make_ctx(compare=True)
        table = Table()
        table.add_column("Test")
        table.add_row("dummy")

        with patch.object(
            rm, "build_table_comparison", return_value=([(table, {})], ERROR_HERE)
        ):
            code = rm.main(ctx)
        self.assertEqual(code, ERROR_HERE)


if __name__ == "__main__":
    unittest.main()
