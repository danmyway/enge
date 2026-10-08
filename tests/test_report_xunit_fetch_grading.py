"""L37: grading of a failed xunit fetch in ConcurrentRequestParser.

A fetch failure that is transient (HTTP 5xx, 408, 429, a read timeout) is
"results missing" (MISSING_RESULTS, 4) and so a rerun candidate; a fetch
failure that is permanent (404, other 4xx, a malformed URL) stays an ERROR
(TEST_ERROR, 3).  Dropped connections already raise NetworkError, which the
pool grades 4, and are pinned here only to prove that path is untouched.
"""

import logging
import unittest
from unittest import mock

import requests.exceptions as rexc

from enge.report.concurrent_parser import ConcurrentRequestParser, TaskResult
from enge.report.__main__ import build_table
from enge.utils.errors import NetworkError
from enge.utils.globals import ExitCode

from tests._helpers import captured_logs, make_app_context, matching

LOGGER_NAME = "enge.report.concurrent_parser"

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

_UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
_UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
_API = "https://tf.example.com/api"
_ARTIFACTS = "https://tf.example.com/artifacts"


def _response(status, text=""):
    return mock.Mock(status_code=status, text=text, content=text.encode())


def _task(**overrides):
    fields = dict(
        request_uuid=_UUID_A,
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
        results_xml_url=f"{_ARTIFACTS}/{_UUID_A}/results.xml",
        url=f"{_API}/{_UUID_A}",
    )
    fields.update(overrides)
    return TaskResult(**fields)


class _FetchMixin:
    """Drive _fetch_xml_results against a scripted session.get."""

    def _fetch(self, outcome, **task_overrides):
        parser = ConcurrentRequestParser(make_app_context(action="report"))
        parser.session = mock.Mock()
        if isinstance(outcome, BaseException):
            parser.session.get.side_effect = outcome
        else:
            parser.session.get.return_value = outcome
        task = _task(**task_overrides)
        return parser._fetch_xml_results(task)


class TestXunitFetchGrading(_FetchMixin, unittest.TestCase):
    def test_5xx_grades_missing_results(self):
        ok = self._fetch(_response(200, _XML_PASSED))
        self.assertIsNone(ok.retval)
        self.assertEqual(ok.xunit_content, _XML_PASSED)
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                task = self._fetch(_response(status))
                self.assertEqual(task.retval, ExitCode.MISSING_RESULTS)
                self.assertIsNone(task.xunit_content)

    def test_408_and_429_grade_missing_results(self):
        for status in (408, 429):
            with self.subTest(status=status):
                task = self._fetch(_response(status))
                self.assertEqual(task.retval, ExitCode.MISSING_RESULTS)

    def test_timeout_class_grades_missing_results(self):
        premise = self._fetch(_response(503))
        self.assertEqual(premise.retval, ExitCode.MISSING_RESULTS)
        for exc in (
            rexc.ReadTimeout,
            rexc.ChunkedEncodingError,
            rexc.ContentDecodingError,
        ):
            with self.subTest(exc=exc.__name__):
                task = self._fetch(exc("boom"))
                self.assertEqual(task.retval, ExitCode.MISSING_RESULTS)

    def test_404_stays_test_error(self):
        task = self._fetch(_response(404))
        self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_other_4xx_stay_test_error(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                task = self._fetch(_response(status))
                self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_url_shape_errors_stay_test_error(self):
        for exc in (rexc.InvalidURL, rexc.MissingSchema, rexc.InvalidSchema):
            with self.subTest(exc=exc.__name__):
                task = self._fetch(exc("bad url"))
                self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_connection_errors_still_raise_network_error(self):
        for exc in (rexc.ConnectionError, rexc.ConnectTimeout):
            with self.subTest(exc=exc.__name__):
                with self.assertRaises(NetworkError):
                    self._fetch(exc("down"))

    def test_prior_test_error_is_not_downgraded(self):
        premise = self._fetch(_response(503))
        self.assertEqual(premise.retval, ExitCode.MISSING_RESULTS)
        task = self._fetch(_response(503), retval=ExitCode.TEST_ERROR)
        self.assertEqual(task.retval, ExitCode.TEST_ERROR)

    def test_error_message_names_the_failure(self):
        cases = (
            (_response(503), "XML not available (HTTP 503), using fallback"),
            (_response(404), "XML not available (HTTP 404), using fallback"),
            (
                rexc.ReadTimeout("slow"),
                "XML not available (ReadTimeout), using fallback",
            ),
        )
        for outcome, expected in cases:
            with self.subTest(expected=expected):
                task = self._fetch(outcome)
                self.assertEqual(task.error_message, expected)

    def test_transient_failure_logs_warning(self):
        for outcome, detail in (
            (_response(503), "HTTP 503"),
            (rexc.ReadTimeout("slow"), "ReadTimeout"),
        ):
            with self.subTest(detail=detail):
                with captured_logs(LOGGER_NAME) as records:
                    self._fetch(outcome)
                lines = matching(
                    records, "graded as missing results", level=logging.WARNING
                )
                self.assertEqual(len(lines), 1)
                self.assertIn(_UUID_A, lines[0])
                self.assertIn(detail, lines[0])

    def test_permanent_failure_logs_no_missing_results_warning(self):
        with captured_logs(LOGGER_NAME) as records:
            self._fetch(_response(503))
        self.assertEqual(
            len(matching(records, "graded as missing results", level=logging.WARNING)),
            1,
        )
        with captured_logs(LOGGER_NAME) as records:
            self._fetch(_response(404))
        self.assertEqual(matching(records, "graded as missing results", level=None), [])


class TestReportRoutesXunitFetchGrading(unittest.TestCase):
    """Enter through the real report entry so the grading is checked through
    task-info fetch, the worker pool and the aggregate."""

    @staticmethod
    def _task_info(uuid):
        return {
            "id": uuid,
            "state": "complete",
            "created": "2026-01-01T00:00:00",
            "environments_requested": [
                {
                    "arch": "x86_64",
                    "os": {"compose": "RHEL-9"},
                    "variables": {"SOURCE_RELEASE": "9", "TARGET_RELEASE": "10"},
                }
            ],
            "test": {"fmf": {"name": "/plans/smoke"}},
            "result": {"overall": "passed", "summary": "ok"},
        }

    def _build_table(self, xunit_by_uuid):
        """xunit_by_uuid maps uuid -> (status, body) for its results.xml."""
        info = {f"{_API}/{u}": self._task_info(u) for u in xunit_by_uuid}
        xunit = {f"{_ARTIFACTS}/{u}/results.xml": r for u, r in xunit_by_uuid.items()}

        def fake_get(_session, url, **_kwargs):
            if url in info:
                resp = mock.Mock(status_code=200)
                resp.json.return_value = info[url]
                resp.raise_for_status.return_value = None
                return resp
            status, body = xunit[url]
            return _response(status, body)

        ctx = make_app_context(
            action="report",
            extra_cli={
                "input": list(xunit_by_uuid),
                "download": False,
                "skip_pass": False,
            },
        )
        with mock.patch("requests.Session.get", fake_get):
            _tables, retval, _tasks = build_table(ctx)
        return retval

    def test_report_exits_4_on_xunit_5xx(self):
        premise = self._build_table({_UUID_A: (200, _XML_PASSED)})
        self.assertEqual(premise, ExitCode.SUCCESS)
        retval = self._build_table({_UUID_A: (503, "")})
        self.assertEqual(retval, ExitCode.MISSING_RESULTS)

    def test_report_failed_task_dominates_xunit_5xx(self):
        retval = self._build_table({_UUID_A: (503, ""), _UUID_B: (200, _XML_FAILED)})
        self.assertEqual(retval, ExitCode.TEST_FAILURE)


if __name__ == "__main__":
    unittest.main()
