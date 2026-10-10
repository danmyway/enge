"""Log contract for report xunit-fetch connection failures (L51).

Off VPN the xunit host is unreachable, so every task's xunit fetch raises a
``requests`` ConnectionError.  The contract: no CRITICAL line, one ERROR line
per failing task (the worker pool's), ONE WARNING per invocation naming the
VPN, the raw error at DEBUG, and grading stays MISSING_RESULTS.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

import requests
import requests.exceptions

from enge.report.concurrent_parser import (
    ConcurrentRequestParser,
    TaskResult,
    parse_request_xunit_concurrent,
)
from enge.utils.globals import ExitCode
from tests._helpers import captured_logs, make_app_context, matching

LOGGER_NAME = "enge.report.concurrent_parser"
CONNECTION_DETAIL = "simulated: no route to host"
VPN_SENTENCE = "Please verify that you are connected to the VPN."

_XML_PASSED = """\
<testsuites overall-result="passed">
  <testsuite name="/plans/smoke" result="passed">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="/tests/basic" result="passed"/>
  </testsuite>
</testsuites>
"""


def _uuid(n):
    return f"{n:08x}-0000-0000-0000-{n:012x}"


def _make_task_result(n, **overrides):
    defaults = dict(
        request_uuid=_uuid(n),
        request_source_compose="RHEL-9",
        request_target_release="10",
        request_upgrade_path="9 to 10",
        request_arch="x86_64",
        request_state="COMPLETE",
        request_datetime_created="2026-01-01T00:00:00",
        request_plan="/plans/smoke",
        request_plan_filter="",
        request_summary="Undefined",
        request_result_overall="Undefined",
        results_xml_url=f"http://artifacts.example.com/{n}/results.xml",
        url=f"http://tf.example.com/api/{n:08x}",
    )
    defaults.update(overrides)
    return TaskResult(**defaults)


def _ok_response():
    response = MagicMock()
    response.status_code = 200
    response.text = _XML_PASSED
    response.content = _XML_PASSED.encode()
    return response


class _ReportXunitCase(unittest.TestCase):
    def _run(self, behaviours):
        """Run the real report pipeline; *behaviours* maps task number to
        the exception to raise from ``Session.get`` (None = a 200 reply).

        Returns (parsed_dict, retval, task_results, log records)."""
        tasks = {n: _make_task_result(n) for n in behaviours}
        by_url = {task.url: task for task in tasks.values()}
        by_xml_url = {task.results_xml_url: behaviours[n] for n, task in tasks.items()}

        def fetch_task_info(url, process_state=True):
            return by_url[url]

        def session_get(url, *args, **kwargs):
            error = by_xml_url[url]
            if error is not None:
                raise error
            return _ok_response()

        ctx = make_app_context(action="report", extra_cli={"download": False})
        with (
            patch.object(
                ConcurrentRequestParser,
                "_fetch_task_info",
                side_effect=fetch_task_info,
            ),
            patch.object(requests.Session, "get", side_effect=session_get),
            captured_logs(LOGGER_NAME, logging.DEBUG) as records,
        ):
            parsed, retval, task_results = parse_request_xunit_concurrent(
                ctx, request_url_list=list(by_url), tasks_source="test"
            )
        return parsed, retval, task_results, records

    @staticmethod
    def _conn_error():
        return requests.exceptions.ConnectionError(CONNECTION_DETAIL)


class TestConnectionFailureCharacterization(_ReportXunitCase):
    def test_each_failing_task_keeps_one_error_line_and_grades_4(self):
        _, retval, task_results, records = self._run(
            {1: self._conn_error(), 2: self._conn_error()}
        )
        for n in (1, 2):
            lines = matching(
                records,
                f"[{_uuid(n)}] Exception fetching XML: "
                "Failed to fetch XML results due to connection error",
                level=logging.ERROR,
            )
            self.assertEqual(len(lines), 1, lines)
        self.assertEqual(
            [tr.retval for tr in task_results],
            [ExitCode.MISSING_RESULTS] * 2,
        )
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)

    def test_non_connection_xml_failure_does_not_mention_the_vpn(self):
        _, _, _, records = self._run(
            {1: requests.exceptions.ReadTimeout("simulated read timeout")}
        )
        # Premise guard: the timeout really took the handled-in-fetch path.
        self.assertEqual(
            len(
                matching(
                    records,
                    "XML fetch failed (ReadTimeout); graded as missing results",
                    level=logging.WARNING,
                )
            ),
            1,
        )
        self.assertEqual(matching(records, "VPN", level=None), [])


class TestConnectionFailureLogContract(_ReportXunitCase):
    def test_connection_failure_logs_no_critical(self):
        _, _, _, records = self._run({1: self._conn_error(), 2: self._conn_error()})
        critical = [r.getMessage() for r in records if r.levelno >= logging.CRITICAL]
        self.assertEqual(critical, [])

    def test_one_vpn_summary_per_invocation(self):
        _, _, _, records = self._run(
            {1: self._conn_error(), 2: self._conn_error(), 3: self._conn_error()}
        )
        summaries = matching(records, VPN_SENTENCE, level=logging.WARNING)
        self.assertEqual(len(summaries), 1, summaries)
        self.assertEqual(
            summaries[0],
            "Could not download results for 3 task(s): the connection failed. "
            f"{VPN_SENTENCE}",
        )

    def test_connection_detail_goes_to_debug_with_the_task_id(self):
        self._run_and_check_debug({1: self._conn_error(), 2: self._conn_error()})

    def _run_and_check_debug(self, behaviours):
        _, _, _, records = self._run(behaviours)
        for n in behaviours:
            short = _uuid(n).split("-")[0]
            lines = [
                m
                for m in matching(records, CONNECTION_DETAIL, level=logging.DEBUG)
                if short in m
            ]
            self.assertEqual(len(lines), 1, lines)

    def test_summary_counts_only_connection_failures(self):
        _, _, _, records = self._run(
            {1: self._conn_error(), 2: RuntimeError("boom"), 3: None}
        )
        # Premise guard: the non-connection failure reached the pool's
        # generic handler.
        self.assertEqual(
            len(
                matching(
                    records,
                    f"[{_uuid(2)}] Exception fetching XML: boom",
                    level=logging.ERROR,
                )
            ),
            1,
        )
        summaries = matching(records, VPN_SENTENCE, level=logging.WARNING)
        self.assertEqual(len(summaries), 1, summaries)
        self.assertIn("for 1 task(s)", summaries[0])


if __name__ == "__main__":
    unittest.main()
