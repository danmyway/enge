"""Characterization tests for report exit codes.

Pin the exit-code contract and the severity-precedence rule
(TEST_ERROR > TEST_FAILURE > MISSING_RESULTS > SUCCESS).
"""

import unittest
from unittest.mock import patch

from tests._helpers import make_app_context
from enge.report.concurrent_parser import (
    XMLParser,
    TaskResult,
    ConcurrentRequestParser,
)
from enge.utils.globals import ExitCode, worst_exit_code

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

_XML_UNKNOWN = """\
<testsuites overall-result="unknown">
  <testsuite name="/plans/smoke" result="unknown">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="/tests/basic" result="unknown"/>
  </testsuite>
</testsuites>
"""


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


# ── worst_exit_code precedence unit tests ───────────────────────────


class TestWorstExitCode(unittest.TestCase):
    """Verify the severity-precedence function directly."""

    def test_none_and_value(self):
        self.assertEqual(
            worst_exit_code(None, ExitCode.TEST_ERROR), ExitCode.TEST_ERROR
        )

    def test_value_and_none(self):
        self.assertEqual(worst_exit_code(ExitCode.SUCCESS, None), ExitCode.SUCCESS)

    def test_none_and_none(self):
        self.assertIsNone(worst_exit_code(None, None))

    def test_error_beats_missing(self):
        self.assertEqual(
            worst_exit_code(ExitCode.TEST_ERROR, ExitCode.MISSING_RESULTS),
            ExitCode.TEST_ERROR,
        )

    def test_missing_loses_to_error(self):
        self.assertEqual(
            worst_exit_code(ExitCode.MISSING_RESULTS, ExitCode.TEST_ERROR),
            ExitCode.TEST_ERROR,
        )

    def test_failure_beats_missing(self):
        self.assertEqual(
            worst_exit_code(ExitCode.TEST_FAILURE, ExitCode.MISSING_RESULTS),
            ExitCode.TEST_FAILURE,
        )

    def test_error_beats_failure(self):
        self.assertEqual(
            worst_exit_code(ExitCode.TEST_ERROR, ExitCode.TEST_FAILURE),
            ExitCode.TEST_ERROR,
        )

    def test_success_loses_to_everything(self):
        for code in (
            ExitCode.TEST_FAILURE,
            ExitCode.TEST_ERROR,
            ExitCode.MISSING_RESULTS,
        ):
            self.assertEqual(worst_exit_code(ExitCode.SUCCESS, code), code)


# ── XML parser exit-code tests ──────────────────────────────────────


class TestExitCodeFromXMLParser(unittest.TestCase):
    """Verify XMLParser.parse_xml_results sets task_result.retval correctly."""

    def test_passed_xml_yields_success(self):
        task = _make_task_result(xunit_content=_XML_PASSED)
        XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertEqual(task.retval, ExitCode.SUCCESS)

    def test_failed_xml_yields_test_failure(self):
        task = _make_task_result(xunit_content=_XML_FAILED)
        XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertEqual(task.retval, ExitCode.TEST_FAILURE)

    def test_error_xml_yields_test_error(self):
        task = _make_task_result(xunit_content=_XML_ERROR)
        XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_pipeline_error_xml_yields_test_error(self):
        task = _make_task_result(xunit_content=_XML_PIPELINE_ERROR)
        XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_no_xml_content_yields_no_retval_change(self):
        task = _make_task_result(xunit_content=None)
        XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertIsNone(task.retval)

    def test_unknown_overall_xml_yields_missing_results(self):
        """An unrecognized overall-result is an indeterminate TF response --
        a rerun candidate (MISSING_RESULTS), not a configuration error."""
        task = _make_task_result(xunit_content=_XML_UNKNOWN)
        with self.assertLogs("enge.report.concurrent_parser", level="ERROR"):
            XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertEqual(task.retval, ExitCode.MISSING_RESULTS)
        self.assertNotEqual(task.retval, ExitCode.CONFIG_ERROR)

    def test_unknown_overall_logs_error_naming_task_and_value(self):
        task = _make_task_result(xunit_content=_XML_UNKNOWN)
        with self.assertLogs("enge.report.concurrent_parser", level="ERROR") as cm:
            XMLParser.parse_xml_results(task, ctx=_report_ctx())
        self.assertTrue(
            any(
                task.request_uuid in message and "unknown" in message
                for message in cm.output
            ),
            cm.output,
        )


# ── Task-state exit-code tests ──────────────────────────────────────


class TestExitCodeFromTaskState(unittest.TestCase):
    def test_error_state_yields_test_error(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="ERROR")
        parser._process_task_state(task)
        self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_running_state_without_wait_yields_missing(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="RUNNING")
        parser._process_task_state(task)
        self.assertEqual(task.retval, ExitCode.MISSING_RESULTS)
        self.assertTrue(task.should_skip)

    def test_queued_state_without_wait_yields_missing(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="QUEUED")
        parser._process_task_state(task)
        self.assertEqual(task.retval, ExitCode.MISSING_RESULTS)
        self.assertTrue(task.should_skip)

    def test_canceled_state_skipped_no_retval_in_process(self):
        ctx = _report_ctx(wait=False)
        parser = ConcurrentRequestParser(ctx)
        task = _make_task_result(state="CANCELED")
        task.should_skip = True
        task.skip_reason = "canceled"
        parser._process_task_state(task)
        self.assertIsNone(task.retval)


# ── Mixed-set severity precedence tests (the decisive ones) ────────


class TestMixedSetSeverityPrecedence(unittest.TestCase):
    """Verify that error dominates missing, not the other way around.

    These tests encode the decided contract:
    TEST_ERROR(3) > TEST_FAILURE(2) > MISSING_RESULTS(4) > SUCCESS(0).
    The numeric order (0 < 2 < 3 < 4) would wrongly make MISSING_RESULTS
    win via max(); these tests prove the precedence function is correct.
    """

    def test_mixed_set_error_dominates_missing(self):
        """ERROR + MISSING in same report → TEST_ERROR, NOT 4."""
        task_error = _make_task_result(
            xunit_content=_XML_ERROR,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
        )
        task_missing = _make_task_result(
            xunit_content=None,
            state="RUNNING",
            request_uuid="bbbbbbbb-0000-0000-0000-000000000002",
        )
        ctx = _report_ctx(wait=False)
        XMLParser.parse_xml_results(task_error, ctx=ctx)
        parser = ConcurrentRequestParser(ctx)
        parser._process_task_state(task_missing)

        agg = worst_exit_code(task_error.retval, task_missing.retval)
        self.assertEqual(agg, ExitCode.TEST_ERROR)

    def test_mixed_set_error_dominates_failure(self):
        """ERROR + FAILURE → TEST_ERROR."""
        task_error = _make_task_result(
            xunit_content=_XML_ERROR,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
        )
        task_fail = _make_task_result(
            xunit_content=_XML_FAILED,
            request_uuid="cccccccc-0000-0000-0000-000000000003",
        )
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task_error, ctx=ctx)
        XMLParser.parse_xml_results(task_fail, ctx=ctx)

        agg = worst_exit_code(task_error.retval, task_fail.retval)
        self.assertEqual(agg, ExitCode.TEST_ERROR)

    def test_mixed_set_failure_dominates_missing(self):
        """FAILURE + MISSING → TEST_FAILURE, NOT 4."""
        task_fail = _make_task_result(
            xunit_content=_XML_FAILED,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
        )
        task_missing = _make_task_result(
            xunit_content=None,
            state="RUNNING",
            request_uuid="bbbbbbbb-0000-0000-0000-000000000002",
        )
        ctx = _report_ctx(wait=False)
        XMLParser.parse_xml_results(task_fail, ctx=ctx)
        parser = ConcurrentRequestParser(ctx)
        parser._process_task_state(task_missing)

        agg = worst_exit_code(task_fail.retval, task_missing.retval)
        self.assertEqual(agg, ExitCode.TEST_FAILURE)

    def test_mixed_set_failure_dominates_unknown_overall(self):
        """FAILURE + unknown-overall (-> MISSING_RESULTS) -> TEST_FAILURE,
        NOT MISSING_RESULTS. Proves the unknown-overall remap participates
        correctly in severity precedence rather than masking a real
        failure from the same report."""
        task_fail = _make_task_result(
            xunit_content=_XML_FAILED,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
        )
        task_unknown = _make_task_result(
            xunit_content=_XML_UNKNOWN,
            request_uuid="dddddddd-0000-0000-0000-000000000004",
        )
        ctx = _report_ctx()
        XMLParser.parse_xml_results(task_fail, ctx=ctx)
        with self.assertLogs("enge.report.concurrent_parser", level="ERROR"):
            XMLParser.parse_xml_results(task_unknown, ctx=ctx)

        self.assertEqual(task_unknown.retval, ExitCode.MISSING_RESULTS)
        agg = worst_exit_code(task_fail.retval, task_unknown.retval)
        self.assertEqual(agg, ExitCode.TEST_FAILURE)


# ── main() entrypoint tests ────────────────────────────────────────


class TestExitCodeFromMainEntrypoint(unittest.TestCase):
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

    def test_main_show_ids_returns_success(self):
        import enge.report.__main__ as rm

        ctx = self._make_ctx(
            show_ids=True,
            input=["aaaaaaaa-0000-0000-0000-000000000001"],
        )
        code = rm.main(ctx)
        self.assertEqual(code, ExitCode.SUCCESS)

    def test_main_returns_retval_from_build_table(self):
        import enge.report.__main__ as rm
        from rich.table import Table

        ctx = self._make_ctx()
        table = Table()
        table.add_column("Test")
        table.add_row("dummy")

        with patch.object(
            rm,
            "build_table",
            return_value=([(table, {"k": "v"})], ExitCode.TEST_FAILURE, []),
        ):
            code = rm.main(ctx)
        self.assertEqual(code, ExitCode.TEST_FAILURE)

    def test_main_compare_flag_delegates_to_enge_compare(self):
        """build_table_comparison is deleted outright (ratified design);
        --compare is now a deprecation alias that delegates to
        'enge compare' -- see test_report_compare_deprecation.py for the
        full alias contract (warning, read-only, no cache write)."""
        import enge.report.__main__ as rm

        ctx = self._make_ctx(compare=True)

        with patch(
            "enge.compare.__main__.main", return_value=ExitCode.TEST_ERROR
        ) as mock_compare_main:
            code = rm.main(ctx)

        mock_compare_main.assert_called_once_with(ctx)
        self.assertEqual(code, ExitCode.TEST_ERROR)


class TestReportResultsCacheWiring(unittest.TestCase):
    """Pins the integration point between the report flow and the
    results-cache writer: main() must forward the raw TaskResult list to
    results_cache.cache_report_results, and a caching failure must never
    prevent the report's table/exit-code output (defense in depth on top
    of cache_report_results' own internal exception handling)."""

    def _make_ctx(self, **cli_overrides):
        cli = {
            "list": False,
            "show_ids": False,
            "compare": False,
            "jira": False,
            "short": False,
            "skip_pass": False,
            "show_tests": False,
        }
        cli.update(cli_overrides)
        return make_app_context(action="report", extra_cli=cli)

    def test_main_forwards_task_results_to_cache_writer(self):
        import enge.report.__main__ as rm
        from rich.table import Table

        ctx = self._make_ctx()
        table = Table()
        table.add_column("Test")
        table.add_row("dummy")
        sentinel_task_results = ["sentinel-task-result"]

        with (
            patch.object(
                rm,
                "build_table",
                return_value=(
                    [(table, {"k": "v"})],
                    ExitCode.SUCCESS,
                    sentinel_task_results,
                ),
            ),
            patch("enge.report.results_cache.cache_report_results") as mock_cache,
        ):
            rm.main(ctx)

        mock_cache.assert_called_once_with(ctx, sentinel_task_results, refresh=False)

    def test_cache_writer_exception_does_not_break_report_output(self):
        import enge.report.__main__ as rm
        from rich.table import Table

        ctx = self._make_ctx()
        table = Table()
        table.add_column("Test")
        table.add_row("dummy")

        with (
            patch.object(
                rm,
                "build_table",
                return_value=([(table, {"k": "v"})], ExitCode.TEST_FAILURE, ["x"]),
            ),
            patch(
                "enge.report.results_cache.cache_report_results",
                side_effect=RuntimeError("unexpected cache bug"),
            ),
        ):
            code = rm.main(ctx)

        self.assertEqual(code, ExitCode.TEST_FAILURE)


# ── Exception-to-exit-code mapping (via __main__) ──────────────────


class TestReportExceptionMapping(unittest.TestCase):
    """report exceptions route through __main__'s error mapping to ExitCode."""

    @patch("enge.__main__.get_arguments")
    @patch("enge.utils.opt_manager.ParsedOpts")
    @patch("enge.report.__main__.main")
    def test_report_exception_maps_to_exit_1(
        self, mock_report_main, mock_po_cls, mock_get_args
    ):
        from types import SimpleNamespace

        import enge.__main__ as enge_main
        from enge.utils.globals import EXIT_GENERAL_ERROR
        from enge.utils.opt_manager import TestingFarmEndpoint

        mock_get_args.return_value = SimpleNamespace(debug=False)
        mock_po_cls.return_value = SimpleNamespace(
            cli_args=SimpleNamespace(action="report", debug=False),
            config={
                "testing_farm": {
                    "api_key": "k",
                    "api_endpoint_url": "https://tf.example.com/api",
                    "log_artifact_baseurl": "https://tf.example.com/artifacts",
                },
                "common": {},
                "project": {},
                "tests": {},
                "reportportal": {},
            },
            testing_farm_endpoint=TestingFarmEndpoint(
                "https://tf.example.com/api",
                "https://tf.example.com/artifacts",
            ),
        )

        from enge.utils.errors import NetworkError

        mock_report_main.side_effect = NetworkError("net down")
        code = enge_main.main()
        self.assertEqual(code, EXIT_GENERAL_ERROR)


# ── Ungraded-task grading tests (AUD-C1) ────────────────────────────


class TestReportGradesUngradedTasks(unittest.TestCase):
    """A requested task that report could not fetch or grade must still
    surface as MISSING_RESULTS in the aggregate exit code (QA-1,
    2026-09-24) -- previously such tasks were silently dropped and the
    run could exit 0 while a Testing Farm outage or schema miss ate one
    of the requested tasks."""

    def _run(self, urls, fetch_task_info_side_effect, fetch_xml_side_effect):
        from enge.report.concurrent_parser import parse_request_xunit_concurrent

        with (
            patch.object(
                ConcurrentRequestParser,
                "_fetch_task_info",
                side_effect=fetch_task_info_side_effect,
            ),
            patch.object(
                ConcurrentRequestParser,
                "_fetch_xml_results",
                side_effect=fetch_xml_side_effect,
            ),
        ):
            return parse_request_xunit_concurrent(
                _report_ctx(), request_url_list=urls, tasks_source="test"
            )

    def test_dropped_task_info_yields_missing_results(self):
        """U1: one task fetches fine, the other's task-info fetch returns
        None (404 / schema miss / retries exhausted) -- silently dropped
        before this fix."""
        urls = ["http://example.com/api/00000001", "http://example.com/api/00000002"]
        passed_task = _make_task_result(
            xunit_content=_XML_PASSED,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
            url=urls[0],
        )

        def fetch_task_info(url, process_state=True):
            return passed_task if url == urls[0] else None

        def fetch_xml(task_result):
            return task_result

        _, retval, _ = self._run(urls, fetch_task_info, fetch_xml)
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)

    def test_task_info_exception_yields_missing_results(self):
        """U2: the task-info fetch raises instead of returning None -- the
        pool's `except Exception` branch must grade the drop the same
        way as a plain None."""
        urls = ["http://example.com/api/00000001", "http://example.com/api/00000002"]
        passed_task = _make_task_result(
            xunit_content=_XML_PASSED,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
            url=urls[0],
        )

        def fetch_task_info(url, process_state=True):
            if url == urls[0]:
                return passed_task
            raise RuntimeError("boom")

        def fetch_xml(task_result):
            return task_result

        _, retval, _ = self._run(urls, fetch_task_info, fetch_xml)
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)

    def test_xml_fetch_network_error_yields_missing_results(self):
        """U3: both tasks are fetched, but one's xunit fetch raises
        NetworkError (a dropped connection) -- it must be graded
        MISSING_RESULTS instead of reaching the aggregate as retval=None."""
        from enge.utils.errors import NetworkError

        urls = ["http://example.com/api/00000001", "http://example.com/api/00000002"]
        passed_task = _make_task_result(
            xunit_content=_XML_PASSED,
            request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
            url=urls[0],
        )
        broken_task = _make_task_result(
            xunit_content=None,
            request_uuid="bbbbbbbb-0000-0000-0000-000000000002",
            url=urls[1],
        )

        def fetch_task_info(url, process_state=True):
            return passed_task if url == urls[0] else broken_task

        def fetch_xml(task_result):
            if task_result.request_uuid == broken_task.request_uuid:
                raise NetworkError("connection reset")
            return task_result

        _, retval, task_results = self._run(urls, fetch_task_info, fetch_xml)
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)
        by_uuid = {tr.request_uuid: tr for tr in task_results}
        self.assertEqual(
            by_uuid[broken_task.request_uuid].retval, ExitCode.MISSING_RESULTS
        )

    def test_all_tasks_dropped_yields_missing_results_and_empty_results(self):
        """U4: every requested task is dropped at the task-info phase --
        the report must still grade MISSING_RESULTS instead of silently
        succeeding with zero tasks to show."""
        urls = ["http://example.com/api/00000001"]

        def fetch_task_info(url, process_state=True):
            return None

        def fetch_xml(task_result):
            return task_result

        _, retval, task_results = self._run(urls, fetch_task_info, fetch_xml)
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)
        self.assertEqual(task_results, [])

    def test_dropped_task_does_not_mask_failure_or_error(self):
        """U5: precedence, with a premise guard. A dropped task alone
        grades 4 (first call proves the drop is graded at all); a
        dropped task alongside a real FAILED/ERROR result must never
        downgrade the aggregate below that real result
        (TEST_ERROR > TEST_FAILURE > MISSING_RESULTS > SUCCESS)."""
        urls = ["http://example.com/api/00000001", "http://example.com/api/00000002"]

        def fetch_task_info_factory(xunit_content):
            task = _make_task_result(
                xunit_content=xunit_content,
                request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
                url=urls[0],
            )

            def fetch_task_info(url, process_state=True):
                return task if url == urls[0] else None

            return fetch_task_info

        def fetch_xml(task_result):
            return task_result

        _, retval, _ = self._run(urls, fetch_task_info_factory(_XML_PASSED), fetch_xml)
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)

        _, retval, _ = self._run(urls, fetch_task_info_factory(_XML_FAILED), fetch_xml)
        self.assertEqual(retval, ExitCode.TEST_FAILURE)

        _, retval, _ = self._run(urls, fetch_task_info_factory(_XML_ERROR), fetch_xml)
        self.assertEqual(retval, ExitCode.TEST_ERROR)

    def test_fully_graded_run_still_uses_the_pool(self):
        """U6: negative pin, with a premise guard. When every task is
        fetched and graded cleanly, this fix must not introduce a false
        MISSING_RESULTS -- and the pool must actually have run both
        fetches, so the assertion above isn't passing vacuously."""
        from enge.report.concurrent_parser import parse_request_xunit_concurrent

        urls = ["http://example.com/api/00000001", "http://example.com/api/00000002"]
        tasks = {
            urls[0]: _make_task_result(
                xunit_content=_XML_PASSED,
                request_uuid="aaaaaaaa-0000-0000-0000-000000000001",
                url=urls[0],
            ),
            urls[1]: _make_task_result(
                xunit_content=_XML_PASSED,
                request_uuid="bbbbbbbb-0000-0000-0000-000000000002",
                url=urls[1],
            ),
        }

        def fetch_task_info(url, process_state=True):
            return tasks[url]

        def fetch_xml(task_result):
            return task_result

        with (
            patch.object(
                ConcurrentRequestParser,
                "_fetch_task_info",
                side_effect=fetch_task_info,
            ) as mock_info,
            patch.object(
                ConcurrentRequestParser,
                "_fetch_xml_results",
                side_effect=fetch_xml,
            ) as mock_xml,
        ):
            _, retval, _ = parse_request_xunit_concurrent(
                _report_ctx(), request_url_list=urls, tasks_source="test"
            )

        self.assertEqual(retval, ExitCode.SUCCESS)
        self.assertEqual(mock_info.call_count, 2)
        self.assertEqual(mock_xml.call_count, 2)


if __name__ == "__main__":
    unittest.main()
