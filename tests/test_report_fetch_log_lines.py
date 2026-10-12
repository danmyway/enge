"""Log lines of a failed report fetch (L54, L55, ruling Q-L55-1).

L54: when the Testing Farm API itself is unreachable (typically off VPN),
the task-info phase logged each task's raw urllib3 error at ERROR and never
named the VPN. Now the raw error goes to DEBUG and ONE WARNING per
invocation names the VPN, exactly as L51 does for the xunit phase. Grading
(MISSING_RESULTS) is unchanged.

L55: a task whose xunit could not be fetched printed up to four WARNING
lines. Now it prints at most one, carrying the failure, the grading when it
is missing results, and the request URL when there is no result summary.

Q-L55-1: when tasks were requested but none had reportable data, `report`
ended with `INFO Nothing to report!`. Now it ends with one WARNING naming
how many tasks had no data; `Nothing to report!` stays for runs where
nothing failed (all canceled, or all passed under --skip-pass).
"""

import logging
import unittest
from unittest import mock

import requests.exceptions as rexc

import enge.report.__main__ as report_main
from enge.report.concurrent_parser import ConcurrentRequestParser, TaskResult
from enge.utils.globals import ExitCode

from tests._helpers import captured_logs, make_app_context, matching

_UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
_UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
_API = "https://tf.example.com/api"
_ARTIFACTS = "https://tf.example.com/artifacts"
_DETAIL = "simulated: name resolution failed"
_VPN = "Please verify that you are connected to the VPN."
_NOTHING = "Nothing to report!"

_XML_PASSED = """\
<testsuites overall-result="passed">
  <testsuite name="/plans/smoke" result="passed">
    <testing-environment><property name="arch" value="x86_64"/></testing-environment>
    <testcase name="/tests/basic" result="passed"/>
  </testsuite>
</testsuites>
"""


def _info(uuid, *, state="complete", summary=True):
    result = {"overall": "passed", "summary": "ok"} if summary else {}
    return {
        "id": uuid,
        "state": state,
        "created": "2026-01-01T00:00:00",
        "environments_requested": [
            {
                "arch": "x86_64",
                "os": {"compose": "RHEL-9"},
                "variables": {"SOURCE_RELEASE": "9", "TARGET_RELEASE": "10"},
            }
        ],
        "test": {"fmf": {"name": "/plans/smoke"}},
        "result": result,
    }


def _response(status, text=""):
    return mock.Mock(status_code=status, text=text, content=text.encode())


def _task(uuid=_UUID_A, *, summary=True):
    value = "passed" if summary else "Undefined"
    return TaskResult(
        request_uuid=uuid,
        request_source_compose="RHEL-9",
        request_target_release="10",
        request_upgrade_path="9 to 10",
        request_arch="x86_64",
        request_state="COMPLETE",
        request_datetime_created="2026-01-01T00:00:00",
        request_plan="/plans/smoke",
        request_plan_filter="",
        request_summary="ok" if summary else "Undefined",
        request_result_overall=value,
        results_xml_url=f"{_ARTIFACTS}/{uuid}/results.xml",
        url=f"{_API}/{uuid}",
    )


class _BadJson:
    """Task info whose body is not JSON: response.json() raises ValueError,
    which _fetch_task_info does not catch, so the worker pool does."""

    def __init__(self, value):
        self.value = value


class _ReportHarness(unittest.TestCase):
    def setUp(self):
        # Importing enge.rerun.__main__ sets the "enge.report" logger to
        # WARNING (rerun silences report's INFO lines), and test discovery
        # imports it before any test runs. `enge report` never imports rerun,
        # so put that logger back to its production state for these tests.
        report_logger = logging.getLogger("enge.report")
        saved = report_logger.level
        report_logger.setLevel(logging.NOTSET)
        self.addCleanup(report_logger.setLevel, saved)

    def _ctx(self, uuids, **cli):
        extra = {"input": list(uuids), "download": False, "skip_pass": False}
        extra.update(cli)
        return make_app_context(action="report", extra_cli=extra)

    def _run_main(self, info, xunit, **cli):
        """The real report main(); info maps uuid -> task-info JSON or an
        exception; xunit maps uuid -> (status, body) or an exception.

        Returns (exit code, every record under the enge logger)."""

        def fake_get(_session, url, **_kwargs):
            for uuid, value in info.items():
                if url == f"{_API}/{uuid}":
                    if isinstance(value, Exception):
                        raise value
                    resp = mock.Mock(status_code=200)
                    if isinstance(value, _BadJson):
                        resp.json.side_effect = ValueError("not json")
                    else:
                        resp.json.return_value = value
                    resp.raise_for_status.return_value = None
                    return resp
            for uuid, value in xunit.items():
                if url == f"{_ARTIFACTS}/{uuid}/results.xml":
                    if isinstance(value, Exception):
                        raise value
                    return _response(*value)
            raise AssertionError(f"unexpected GET {url}")

        ctx = self._ctx(info, **cli)
        with (
            mock.patch("requests.Session.get", fake_get),
            mock.patch("enge.report.concurrent_parser.time.sleep"),
            mock.patch.object(report_main.results_cache, "cache_report_results"),
            captured_logs("enge") as records,
        ):
            code = report_main.main(ctx)
        return code, records


class TestTaskInfoConnectionFailure(_ReportHarness):
    """L54."""

    def _off_vpn(self):
        error = rexc.ConnectionError(_DETAIL)
        return self._run_main({_UUID_A: error, _UUID_B: error}, {})

    def test_grading_and_one_error_per_task_unchanged(self):
        code, records = self._off_vpn()
        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        for uuid in (_UUID_A, _UUID_B):
            errors = matching(records, f"{_API}/{uuid}", level=logging.ERROR)
            self.assertEqual(len(errors), 1, errors)

    def test_one_vpn_warning_per_invocation(self):
        _, records = self._off_vpn()
        warnings = matching(records, _VPN, level=logging.WARNING)
        self.assertEqual(
            warnings,
            [
                "Could not fetch task info for 2 task(s): the connection failed. "
                f"{_VPN}"
            ],
        )

    def test_raw_connection_error_only_at_debug(self):
        _, records = self._off_vpn()
        # Premise: the raw error was captured at all.
        self.assertEqual(len(matching(records, _DETAIL, level=logging.DEBUG)), 2)
        loud = [
            r.getMessage()
            for r in records
            if r.levelno >= logging.WARNING and _DETAIL in r.getMessage()
        ]
        self.assertEqual(loud, [])

    def test_vpn_warning_counts_only_connection_failures(self):
        not_json = _info(_UUID_B)
        code, records = self._run_main(
            {_UUID_A: rexc.ConnectionError(_DETAIL), _UUID_B: _BadJson(not_json)}, {}
        )
        # Premise: the second task failed in the pool, not as a connection.
        self.assertEqual(
            len(
                matching(
                    records,
                    f"Exception fetching task info for {_API}/{_UUID_B}: not json",
                    level=logging.ERROR,
                )
            ),
            1,
        )
        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        warnings = matching(records, _VPN, level=logging.WARNING)
        self.assertEqual(
            warnings,
            [
                "Could not fetch task info for 1 task(s): the connection failed. "
                f"{_VPN}"
            ],
        )

    def test_non_connection_failure_does_not_mention_the_vpn(self):
        _, records = self._run_main({_UUID_A: rexc.HTTPError("simulated 500")}, {})
        # Premise: the failure was reported.
        self.assertEqual(
            len(matching(records, f"{_API}/{_UUID_A}", level=logging.ERROR)), 1
        )
        self.assertEqual(matching(records, "VPN", level=None), [])


class TestXunitFallbackOneLine(unittest.TestCase):
    """L55: at most one WARNING per task from the xunit fallback."""

    def _warnings(self, outcome, *, summary):
        task = _task(summary=summary)
        parser = ConcurrentRequestParser(make_app_context(action="report"))
        parser.session = mock.Mock()
        if isinstance(outcome, Exception):
            parser.session.get.side_effect = outcome
        else:
            parser.session.get.return_value = outcome
        with captured_logs("enge.report.concurrent_parser") as records:
            parser._fetch_xml_results(task)
        return [r.getMessage() for r in records if r.levelno == logging.WARNING]

    def test_permanent_failure_without_summary(self):
        self.assertEqual(
            self._warnings(_response(404), summary=False),
            [
                f"[{_UUID_A}] XML fetch failed (HTTP 404); no result summary "
                f"either, please consult {_API}/{_UUID_A}"
            ],
        )

    def test_transient_failure_without_summary(self):
        self.assertEqual(
            self._warnings(_response(503), summary=False),
            [
                f"[{_UUID_A}] XML fetch failed (HTTP 503); graded as missing "
                f"results; no result summary either, please consult "
                f"{_API}/{_UUID_A}"
            ],
        )

    def test_raised_transient_failure_with_summary(self):
        self.assertEqual(
            self._warnings(rexc.ReadTimeout("slow"), summary=True),
            [f"[{_UUID_A}] XML fetch failed (ReadTimeout); graded as missing results"],
        )

    def test_raised_permanent_failure_with_summary(self):
        self.assertEqual(
            self._warnings(rexc.TooManyRedirects("loop"), summary=True),
            [f"[{_UUID_A}] XML fetch failed (TooManyRedirects)"],
        )

    def test_permanent_failure_with_summary_stays_quiet(self):
        self.assertEqual(self._warnings(_response(404), summary=True), [])


class TestNoReportableDataLine(_ReportHarness):
    """Q-L55-1: the last line of a report that rendered nothing."""

    def _last_loud(self, records):
        loud = [r for r in records if r.levelno >= logging.INFO]
        return loud[-1] if loud else None

    def test_all_xunit_failures_end_with_a_warning(self):
        code, records = self._run_main(
            {_UUID_A: _info(_UUID_A), _UUID_B: _info(_UUID_B)},
            {_UUID_A: (404, ""), _UUID_B: (404, "")},
        )
        self.assertEqual(code, ExitCode.TEST_ERROR)
        last = self._last_loud(records)
        self.assertEqual(last.levelno, logging.WARNING)
        self.assertEqual(
            last.getMessage(),
            "No reportable data for 2 task(s); see the warnings above.",
        )
        self.assertEqual(matching(records, _NOTHING, level=None), [])

    def test_unfetchable_task_info_counts(self):
        error = rexc.ConnectionError(_DETAIL)
        _, records = self._run_main({_UUID_A: error, _UUID_B: error}, {})
        last = self._last_loud(records)
        self.assertEqual(last.levelno, logging.WARNING)
        self.assertEqual(
            last.getMessage(),
            "No reportable data for 2 task(s); see the warnings above.",
        )

    def test_partial_data_renders_without_either_line(self):
        _, records = self._run_main(
            {_UUID_A: _info(_UUID_A), _UUID_B: _info(_UUID_B)},
            {_UUID_A: (200, _XML_PASSED), _UUID_B: (404, "")},
        )
        self.assertEqual(matching(records, "No reportable data for", level=None), [])
        self.assertEqual(matching(records, _NOTHING, level=None), [])

    def test_all_canceled_keeps_nothing_to_report(self):
        _, records = self._run_main({_UUID_A: _info(_UUID_A, state="canceled")}, {})
        last = self._last_loud(records)
        self.assertEqual((last.levelno, last.getMessage()), (logging.INFO, _NOTHING))
        self.assertEqual(matching(records, "No reportable data for", level=None), [])

    def test_all_passed_under_skip_pass_keeps_nothing_to_report(self):
        _, records = self._run_main(
            {_UUID_A: _info(_UUID_A)},
            {_UUID_A: (200, _XML_PASSED)},
            skip_pass=True,
        )
        last = self._last_loud(records)
        self.assertEqual((last.levelno, last.getMessage()), (logging.INFO, _NOTHING))


if __name__ == "__main__":
    unittest.main()
