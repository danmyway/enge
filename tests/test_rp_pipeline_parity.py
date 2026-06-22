# tests/test_rp_pipeline_parity.py
"""Call-sequence parity harness for ReportPortal operations.

Records every HTTP call (method, url_path, key payload fields) made by
reportportal operations and compares against golden fixture files.
The golden fixtures are committed against UNMODIFIED code; the refactor
must reproduce them exactly.

Stubs:
  - http_get/http_post/http_put/http_delete: replaced by HttpRecorder
  - ConcurrentRequestParser._fetch_task_info: returns fixture TaskResult
  - ConcurrentRequestParser._fetch_xml_results: sets fixture xunit_content
"""

import json
import os
import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch, MagicMock

from tests._helpers import make_app_context

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# Frozen time for deterministic golden files
FROZEN_DT = datetime(2026, 6, 19, 12, 0, 0, 0)
FROZEN_DT_ISO = "2026-06-19T12:00:00.000Z"
FROZEN_DT_MS = str(int(FROZEN_DT.timestamp() * 1000))

# Task UUID — must be valid hex so _extract_tf_uuid regex matches
TASK_UUID = "aabbccdd-1111-2222-3333-444455551234"
TASK_URL = f"https://api.tf.example.com/v0.1/requests/{TASK_UUID}"
ARTIFACTS_BASE = f"https://artifacts.dev.testing-farm.io/{TASK_UUID}"

# --- ReportPortal config for all parity tests ---
RP_CONFIG = {
    "url": "https://rp.example.com",
    "token": "test-rp-token",
    "project": "test-project",
    "enrich_max_file_size": "5 MB",
}

# --- Sample RP API response data ---
SAMPLE_LAUNCH_IN_PROGRESS = {
    "id": 101,
    "uuid": "aabbccdd-1111-2222-3333-444455556666",
    "name": "TEST~2026-06-19~tier0~x86_64",
    "status": "IN_PROGRESS",
    "startTime": 1750000000000,
    "attributes": [
        {"key": "tier", "value": "tier0"},
        {"key": "architecture", "value": "x86_64"},
        {"key": "uniq_id", "value": "aabbccdd"},
        {"key": "event", "value": "TEST"},
    ],
    "description": "",
}

SAMPLE_LAUNCH_ENRICHED = {
    **SAMPLE_LAUNCH_IN_PROGRESS,
    "id": 102,
    "uuid": "aabbccdd-1111-2222-3333-444455557777",
    "attributes": [
        *SAMPLE_LAUNCH_IN_PROGRESS["attributes"],
        {"key": "logs_attached", "value": "true"},
    ],
}

SAMPLE_ITEMS = [
    {
        "id": 5001,
        "uuid": "item-uuid-001",
        "name": "/tests/plan1/test_upgrade",
        "type": "STEP",
        "status": "FAILED",
        "endTime": "2026-06-19T10:00:00.000Z",
        "description": ARTIFACTS_BASE,
    },
    {
        "id": 5002,
        "uuid": "item-uuid-002",
        "name": "/tests/plan1/test_verify",
        "type": "STEP",
        "status": "PASSED",
        "endTime": "2026-06-19T10:01:00.000Z",
        "description": ARTIFACTS_BASE,
    },
]

SAMPLE_LOGS_EMPTY: List[Dict[str, Any]] = []

SAMPLE_LOGS_WITH_HEADER = [
    {
        "id": 9001,
        "message": "### `artifact-A.log`\nsome content here",
        "level": "INFO",
    },
]

SAMPLE_XML_CONTENT = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<testsuites>\n"
    '  <testsuite name="/tests/plan1" tests="2" failures="1">\n'
    '    <testcase name="test_upgrade" time="120.5">\n'
    '      <failure message="upgrade failed"/>\n'
    "    </testcase>\n"
    '    <testcase name="test_verify" time="30.2"/>\n'
    "    <logs>\n"
    '      <log name="artifact-A.log"\n'
    f'           href="{ARTIFACTS_BASE}/artifact-A.log"/>\n'
    '      <log name="artifact-B.log"\n'
    f'           href="{ARTIFACTS_BASE}/artifact-B.log"/>\n'
    "    </logs>\n"
    "  </testsuite>\n"
    "</testsuites>"
)

SAMPLE_TF_TASK_DATA = {
    "id": TASK_UUID,
    "state": "complete",
    "result": {"overall": "failed", "summary": "1 of 2 tests failed"},
    "created": "2026-06-19T09:00:00Z",
    "environments_requested": [
        {
            "arch": "x86_64",
            "os": {"compose": "RHEL-8.10.0-20260101.0"},
            "tmt": {
                "context": {
                    "uniq_id": "aabbccdd",
                    "tier": "tier0",
                    "architecture": "x86_64",
                    "event": "TEST",
                },
            },
        }
    ],
    "test": {"fmf": {"url": "https://github.com/oamg/leapp", "ref": "main"}},
}

# TF task in RUNNING state (for TF guard tests)
SAMPLE_TF_TASK_DATA_RUNNING = {**SAMPLE_TF_TASK_DATA, "state": "running"}
SAMPLE_TF_TASK_DATA_CANCELED = {**SAMPLE_TF_TASK_DATA, "state": "canceled"}


def _normalize_url_path(url: str) -> str:
    """Strip scheme+host from URL, keep path + query for comparison."""
    # https://rp.example.com/api/v1/test-project/launch -> /api/v1/test-project/launch
    return "/" + url.split("/", 3)[-1] if "://" in url else url


@dataclass
class RecordedCall:
    method: str
    url_path: str
    payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"method": self.method, "url_path": self.url_path}
        if self.payload is not None:
            d["payload"] = self.payload
        return d


class HttpRecorder:
    """Drop-in replacement for http_get/post/put/delete that records calls
    and returns canned responses.

    Usage:
        recorder = HttpRecorder()
        recorder.register("GET", "/api/v1/test-project/launch", response_json={...})
        with recorder.patch():
            # code under test runs, all http_* calls are recorded
            ...
        calls = recorder.calls  # list[RecordedCall]
    """

    def __init__(self):
        self.calls: List[RecordedCall] = []
        self._routes: List[Tuple[str, str, Dict[str, Any], int]] = []

    def register(
        self,
        method: str,
        url_pattern: str,
        response_json: Any = None,
        response_text: str = "",
        status_code: int = 200,
    ):
        """Register a canned response for a method + URL pattern.

        url_pattern is matched as a substring of the request URL path.
        """
        body = {"json": response_json, "text": response_text}
        self._routes.append((method.upper(), url_pattern, body, status_code))

    def _find_response(self, method: str, url: str):
        url_path = _normalize_url_path(url)
        for route_method, pattern, body, status in self._routes:
            if route_method == method.upper() and pattern in url_path:
                resp = MagicMock()
                resp.status_code = status
                resp.json.return_value = body.get("json") or {}
                resp.text = body.get("text", "")
                resp.headers = {"Content-Length": str(len(resp.text))}
                return resp
        # Default: return empty 200
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        resp.text = ""
        resp.headers = {}
        return resp

    def _make_handler(self, method: str):
        def handler(url, **kwargs):
            url_path = _normalize_url_path(url)
            payload = None
            if "json" in kwargs and kwargs["json"] is not None:
                payload = kwargs["json"]
            elif "files" in kwargs and kwargs["files"] is not None:
                # Multipart upload — extract the JSON part
                for name, file_tuple in kwargs["files"]:
                    if name == "json_request_part":
                        payload = json.loads(file_tuple[1])
                        break
            elif "params" in kwargs and kwargs["params"] is not None:
                payload = (
                    dict(kwargs["params"])
                    if isinstance(kwargs["params"], list)
                    else kwargs["params"]
                )
            self.calls.append(
                RecordedCall(method=method, url_path=url_path, payload=payload)
            )
            return self._find_response(method, url)

        return handler

    def patch(self):
        """Return a context manager that patches all http_* functions.

        Patches at every import site: the canonical module plus each
        consumer that does ``from http_client import http_*``.
        ``operations.py`` only imports ``http_get``; ``__main__.py``
        imports all four.
        """
        method_map = [
            ("GET", "http_get"),
            ("POST", "http_post"),
            ("PUT", "http_put"),
            ("DELETE", "http_delete"),
        ]
        # operations.py only imports http_get
        ops_methods = [("GET", "http_get")]

        patches = []
        # Canonical module — has all four
        for method, fn_name in method_map:
            patches.append(
                patch(
                    f"enge.utils.http_client.{fn_name}",
                    side_effect=self._make_handler(method),
                )
            )
        # __main__.py — has all four
        for method, fn_name in method_map:
            patches.append(
                patch(
                    f"enge.reportportal.__main__.{fn_name}",
                    side_effect=self._make_handler(method),
                )
            )
        # operations.py — only http_get
        for method, fn_name in ops_methods:
            patches.append(
                patch(
                    f"enge.reportportal.operations.{fn_name}",
                    side_effect=self._make_handler(method),
                )
            )
        return _MultiPatch(*patches)

    def to_json(self) -> str:
        """Serialize recorded calls to deterministic JSON."""
        return json.dumps(
            [c.to_dict() for c in self.calls],
            sort_keys=True,
            indent=2,
        )


class _MultiPatch:
    """Context manager that enters multiple unittest.mock.patch objects."""

    def __init__(self, *patches):
        self._patches = patches
        self._mocks = []

    def __enter__(self):
        self._mocks = [p.__enter__() for p in self._patches]
        return self._mocks

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.__exit__(*args)


def make_rp_context(
    action="reportportal",
    extra_cli=None,
    extra_config=None,
    **overrides,
):
    """Build an AppContext configured for reportportal parity tests."""
    cli = {
        "finish": False,
        "enrich_logs": False,
        "delete_logs": False,
        "delete_stale": False,
        "test": False,
        "all_launches": False,
        "since": None,
        "until": None,
    }
    if extra_cli:
        cli.update(extra_cli)
    return make_app_context(
        action=action,
        extra_cli=cli,
        extra_config={"reportportal": RP_CONFIG, **(extra_config or {})},
        **overrides,
    )


# ===================================================================
# Task-result builder
# ===================================================================


def _make_task_result(state="complete", overall="failed", xml_content=None):
    """Build a mock TaskResult for the from-tasks path."""
    from enge.report.concurrent_parser import TaskResult

    return TaskResult(
        request_uuid=TASK_UUID,
        request_source_compose="RHEL-8.10.0-20260101.0",
        request_target_release="10.0",
        request_upgrade_path="8.10 -> 10.0",
        request_arch="x86_64",
        request_state=state.upper(),
        request_datetime_created="2026-06-19T09:00:00Z",
        request_plan="/tests/plan1",
        request_plan_filter="tag:tier0",
        request_summary="1 of 2 tests failed",
        request_result_overall=overall,
        results_xml_url=f"{ARTIFACTS_BASE}/results.xml",
        url=TASK_URL,
        xunit_content=xml_content,
    )


# ===================================================================
# Golden-file parity base class
# ===================================================================


class _ParityTestBase(unittest.TestCase):
    """Shared helper for golden-file parity tests."""

    maxDiff = None

    def _golden_path(self, name: str) -> str:
        return os.path.join(FIXTURES_DIR, name)

    def _run_and_compare(self, recorder, golden_name: str):
        """Compare recorded calls against golden file.

        On first run (golden file missing), writes the file and fails
        with instructions to inspect and re-run.
        """
        actual_json = recorder.to_json()
        golden_file = self._golden_path(golden_name)

        if not os.path.exists(golden_file):
            os.makedirs(os.path.dirname(golden_file), exist_ok=True)
            with open(golden_file, "w") as f:
                f.write(actual_json + "\n")
            self.fail(
                f"Golden file generated at {golden_file}. "
                "Inspect it, then re-run to confirm baseline."
            )

        with open(golden_file) as f:
            expected_json = f.read().rstrip("\n")

        self.assertEqual(
            actual_json,
            expected_json,
            f"Call sequence drift in {golden_name} — "
            "reportportal output differs from golden baseline",
        )

    def _assert_no_calls_matching(self, recorder, method: str, url_substring: str):
        """Assert no recorded calls match the given method + URL pattern."""
        matches = [
            c
            for c in recorder.calls
            if c.method == method and url_substring in c.url_path
        ]
        self.assertEqual(
            len(matches),
            0,
            f"Expected ZERO {method} calls to '{url_substring}', "
            f"got {len(matches)}: {[c.to_dict() for c in matches]}",
        )

    def _assert_has_calls_matching(self, recorder, method: str, url_substring: str):
        """Assert at least one recorded call matches."""
        matches = [
            c
            for c in recorder.calls
            if c.method == method and url_substring in c.url_path
        ]
        self.assertGreater(
            len(matches),
            0,
            f"Expected at least one {method} call to '{url_substring}', "
            f"got none. "
            f"All calls: {[c.to_dict() for c in recorder.calls]}",
        )

    @staticmethod
    def _freeze_time():
        """Patch datetime.now() in operations and __main__ modules."""
        mock_dt = MagicMock(wraps=datetime)
        mock_dt.now.return_value = FROZEN_DT
        return _MultiPatch(
            patch("enge.reportportal.operations.datetime", mock_dt),
            patch("enge.reportportal.__main__.datetime", mock_dt),
        )

    def _patch_from_tasks(self, task_state="complete", task_overall="failed"):
        """Return a stack of patches for the from-tasks path.

        Stubs parse_tasks, ConcurrentRequestParser, and
        extract_tmt_context_from_task so the code-under-test never
        touches the network but follows its real branching logic.
        """
        task_result_fetched = _make_task_result(state=task_state, overall=task_overall)
        task_result_with_xml = _make_task_result(
            state=task_state,
            overall=task_overall,
            xml_content=SAMPLE_XML_CONTENT,
        )
        tmt_context = SAMPLE_TF_TASK_DATA["environments_requested"][0]["tmt"]["context"]

        mock_parser = MagicMock()
        mock_parser.__enter__ = MagicMock(return_value=mock_parser)
        mock_parser.__exit__ = MagicMock(return_value=False)
        mock_parser._fetch_task_info.return_value = task_result_fetched
        mock_parser._fetch_xml_results.return_value = task_result_with_xml
        mock_parser_cls = MagicMock(return_value=mock_parser)

        return (
            patch(
                "enge.reportportal.operations.parse_tasks",
                return_value=([TASK_URL], {}),
            ),
            patch(
                "enge.reportportal.operations.ConcurrentRequestParser",
                mock_parser_cls,
            ),
            patch(
                "enge.reportportal.operations.extract_tmt_context_from_task",
                return_value=tmt_context,
            ),
        )


# ===================================================================
# Task 2: Finish parity tests
# ===================================================================


class TestFinishFromTasksParity(_ParityTestBase):
    """Parity test: finish_launch_from_task call sequence."""

    def _setup_recorder(self):
        recorder = HttpRecorder()
        # RP: list launches (find_launch_by_uniq_id searches IN_PROGRESS
        # then all)
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_IN_PROGRESS],
                "page": {"totalPages": 1},
            },
        )
        # RP: finish launch
        recorder.register("PUT", "/finish", response_json={"msg": "ok"})
        return recorder

    def test_finish_tasks_parity(self):
        """Finish from tasks: golden call sequence."""
        ctx = make_rp_context(extra_cli={"finish": True, "input": [TASK_URL]})
        recorder = self._setup_recorder()
        patches = self._patch_from_tasks()

        with recorder.patch(), self._freeze_time(), patches[0], patches[1], patches[2]:
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import finish_launch_from_task

            rp = ReportPortalLaunch(ctx)
            finish_launch_from_task(rp)

        self._assert_has_calls_matching(recorder, "PUT", "/finish")
        self._run_and_compare(recorder, "rp_calls_finish_tasks.json")

    def test_finish_tasks_running_tf(self):
        """TF guard: RUNNING task produces ZERO finish calls."""
        ctx = make_rp_context(extra_cli={"finish": True, "input": [TASK_URL]})
        recorder = self._setup_recorder()
        patches = self._patch_from_tasks(task_state="RUNNING", task_overall="Undefined")

        with recorder.patch(), patches[0], patches[1], patches[2]:
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import finish_launch_from_task

            rp = ReportPortalLaunch(ctx)
            finish_launch_from_task(rp)

        self._assert_no_calls_matching(recorder, "PUT", "/finish")
        self._run_and_compare(recorder, "rp_calls_finish_tasks_running_tf.json")


class TestFinishFromQueryParity(_ParityTestBase):
    """Parity test: finish_all_in_progress_launches call sequence."""

    def _setup_recorder(self, launches=None, items=None, tf_task_data=None):
        recorder = HttpRecorder()
        launch_list = launches or [SAMPLE_LAUNCH_IN_PROGRESS]
        item_list = items or SAMPLE_ITEMS

        # RP: get_all_launches (pages 1..N)
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": launch_list,
                "page": {"totalPages": 1},
            },
        )
        # RP: get_launch_test_items
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": item_list,
                "page": {"totalPages": 1},
            },
        )
        # TF: task info query (for TF state derivation)
        tf_data = tf_task_data or SAMPLE_TF_TASK_DATA
        recorder.register(
            "GET",
            TASK_UUID,
            response_json=tf_data,
        )
        # RP: finish launch
        recorder.register("PUT", "/finish", response_json={"msg": "ok"})
        return recorder

    def test_finish_query_parity(self):
        """Finish from query: golden call sequence."""
        ctx = make_rp_context(extra_cli={"finish": True, "all_launches": True})
        recorder = self._setup_recorder()

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import (
                finish_all_in_progress_launches,
            )

            rp = ReportPortalLaunch(ctx)
            finish_all_in_progress_launches(rp)

        self._assert_has_calls_matching(recorder, "PUT", "/finish")
        self._run_and_compare(recorder, "rp_calls_finish_query.json")

    def test_finish_query_running_tf(self):
        """TF guard: RUNNING TF task produces ZERO finish calls."""
        ctx = make_rp_context(extra_cli={"finish": True, "all_launches": True})
        recorder = self._setup_recorder(tf_task_data=SAMPLE_TF_TASK_DATA_RUNNING)

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import (
                finish_all_in_progress_launches,
            )

            rp = ReportPortalLaunch(ctx)
            finish_all_in_progress_launches(rp)

        self._assert_no_calls_matching(recorder, "PUT", "/finish")
        self._run_and_compare(recorder, "rp_calls_finish_query_running_tf.json")


# ===================================================================
# Task 3: Enrich parity tests
# ===================================================================


class TestEnrichFromTasksParity(_ParityTestBase):
    """Parity test: enrich_logs_from_task call sequence."""

    def _setup_recorder(self):
        recorder = HttpRecorder()
        # RP: list launches (find_launch_by_uniq_id)
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_IN_PROGRESS],
                "page": {"totalPages": 1},
            },
        )
        # RP: get_launch_test_items
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": SAMPLE_ITEMS,
                "page": {"totalPages": 1},
            },
        )
        # RP: get_launch_logs (for dedup check)
        recorder.register(
            "GET",
            "/log",
            response_json={
                "content": SAMPLE_LOGS_EMPTY,
                "page": {"totalPages": 1},
            },
        )
        # RP: upload logs
        recorder.register(
            "POST",
            "/log",
            response_json={"responses": []},
            status_code=201,
        )
        # RP: get launch by id (for _stamp_logs_attached)
        recorder.register(
            "GET",
            "/launch/101",
            response_json=SAMPLE_LAUNCH_IN_PROGRESS,
        )
        # RP: update launch (stamp logs_attached)
        recorder.register("PUT", "/update", response_json={"msg": "ok"})
        # Artifact download
        recorder.register(
            "GET",
            "artifacts.dev.testing-farm.io",
            response_text="artifact log content here",
        )
        return recorder

    def test_enrich_tasks_parity(self):
        """Enrich from tasks: golden call sequence."""
        ctx = make_rp_context(extra_cli={"enrich_logs": True, "input": [TASK_URL]})
        recorder = self._setup_recorder()
        patches = self._patch_from_tasks()

        with recorder.patch(), self._freeze_time(), patches[0], patches[1], patches[2]:
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import enrich_logs_from_task

            rp = ReportPortalLaunch(ctx)
            enrich_logs_from_task(rp)

        self._assert_has_calls_matching(recorder, "POST", "/log")
        self._run_and_compare(recorder, "rp_calls_enrich_tasks.json")

    def test_enrich_tasks_running_tf(self):
        """TF guard: RUNNING task produces ZERO upload calls."""
        ctx = make_rp_context(extra_cli={"enrich_logs": True, "input": [TASK_URL]})
        recorder = self._setup_recorder()
        patches = self._patch_from_tasks(task_state="RUNNING", task_overall="Undefined")

        with recorder.patch(), patches[0], patches[1], patches[2]:
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import enrich_logs_from_task

            rp = ReportPortalLaunch(ctx)
            enrich_logs_from_task(rp)

        self._assert_no_calls_matching(recorder, "POST", "/log")
        self._run_and_compare(recorder, "rp_calls_enrich_tasks_running_tf.json")


class TestEnrichFromQueryParity(_ParityTestBase):
    """Parity test: enrich_all_launches call sequence."""

    def _setup_recorder(self, launches=None, logs=None, tf_task_data=None):
        recorder = HttpRecorder()
        launch_list = launches or [SAMPLE_LAUNCH_IN_PROGRESS]
        log_list = logs if logs is not None else SAMPLE_LOGS_EMPTY

        # RP: get_all_launches
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": launch_list,
                "page": {"totalPages": 1},
            },
        )
        # RP: get_launch_test_items
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": SAMPLE_ITEMS,
                "page": {"totalPages": 1},
            },
        )
        # results.xml fetch (must precede TASK_UUID route — both
        # match the artifacts URL, but /results.xml is more specific)
        recorder.register(
            "GET",
            "/results.xml",
            response_text=SAMPLE_XML_CONTENT,
        )
        # TF: task info (for TF state check)
        tf_data = tf_task_data or SAMPLE_TF_TASK_DATA
        recorder.register(
            "GET",
            TASK_UUID,
            response_json=tf_data,
        )
        # Artifact download (must precede /log to avoid matching
        # /log in the artifacts URL)
        recorder.register(
            "GET",
            "artifacts.dev.testing-farm.io",
            response_text="artifact log content here",
        )
        # RP: get_launch_logs (dedup check)
        recorder.register(
            "GET",
            "/log",
            response_json={
                "content": log_list,
                "page": {"totalPages": 1},
            },
        )
        # RP: upload logs
        recorder.register(
            "POST",
            "/log",
            response_json={"responses": []},
            status_code=201,
        )
        # RP: update launch (stamp)
        recorder.register("PUT", "/update", response_json={"msg": "ok"})
        return recorder

    def test_enrich_query_parity(self):
        """Enrich from query: golden call sequence."""
        ctx = make_rp_context(extra_cli={"enrich_logs": True, "all_launches": True})
        recorder = self._setup_recorder()

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import enrich_all_launches

            rp = ReportPortalLaunch(ctx)
            enrich_all_launches(rp, status_filter=None)

        self._assert_has_calls_matching(recorder, "POST", "/log")
        self._run_and_compare(recorder, "rp_calls_enrich_query.json")

    def test_enrich_query_running_tf(self):
        """TF guard: RUNNING TF task produces ZERO upload calls."""
        ctx = make_rp_context(extra_cli={"enrich_logs": True, "all_launches": True})
        recorder = self._setup_recorder(tf_task_data=SAMPLE_TF_TASK_DATA_RUNNING)

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import enrich_all_launches

            rp = ReportPortalLaunch(ctx)
            enrich_all_launches(rp, status_filter=None)

        self._assert_no_calls_matching(recorder, "POST", "/log")
        self._run_and_compare(recorder, "rp_calls_enrich_query_running_tf.json")


class TestEnrichDedupParity(_ParityTestBase):
    """Parity tests for the two dedup layers in enrich."""

    def test_dedup_attribute_level(self):
        """Launch with logs_attached attribute -> ZERO upload calls."""
        ctx = make_rp_context(extra_cli={"enrich_logs": True, "all_launches": True})
        recorder = HttpRecorder()
        # Return the already-enriched launch
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_ENRICHED],
                "page": {"totalPages": 1},
            },
        )
        # Items (should not be needed but register for safety)
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": SAMPLE_ITEMS,
                "page": {"totalPages": 1},
            },
        )

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import enrich_all_launches

            rp = ReportPortalLaunch(ctx)
            enrich_all_launches(rp, status_filter=None)

        self._assert_no_calls_matching(recorder, "POST", "/log")
        self._run_and_compare(recorder, "rp_calls_enrich_dedup_attribute.json")

    def test_dedup_message_header(self):
        """Existing log with artifact-A header -> artifact-A skipped."""
        ctx = make_rp_context(extra_cli={"enrich_logs": True, "all_launches": True})
        recorder = HttpRecorder()
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_IN_PROGRESS],
                "page": {"totalPages": 1},
            },
        )
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": SAMPLE_ITEMS,
                "page": {"totalPages": 1},
            },
        )
        # results.xml (must precede TASK_UUID route)
        recorder.register("GET", "/results.xml", response_text=SAMPLE_XML_CONTENT)
        # TF task info
        recorder.register(
            "GET",
            TASK_UUID,
            response_json=SAMPLE_TF_TASK_DATA,
        )
        # Artifact download
        recorder.register(
            "GET",
            "artifacts.dev.testing-farm.io",
            response_text="artifact log content here",
        )
        # Existing logs: has header for artifact-A but NOT artifact-B
        recorder.register(
            "GET",
            "/log",
            response_json={
                "content": SAMPLE_LOGS_WITH_HEADER,
                "page": {"totalPages": 1},
            },
        )
        # Upload (should only contain artifact-B)
        recorder.register(
            "POST",
            "/log",
            response_json={"responses": []},
            status_code=201,
        )
        # Update launch
        recorder.register("PUT", "/update", response_json={"msg": "ok"})

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import enrich_all_launches

            rp = ReportPortalLaunch(ctx)
            enrich_all_launches(rp, status_filter=None)

        # Must have uploaded something (artifact-B)
        self._assert_has_calls_matching(recorder, "POST", "/log")
        # Verify the uploaded batch does NOT contain artifact-A
        post_calls = [
            c for c in recorder.calls if c.method == "POST" and "/log" in c.url_path
        ]
        for call in post_calls:
            if call.payload:
                payload = call.payload
                # payload is the parsed JSON from multipart upload
                if isinstance(payload, list):
                    for entry in payload:
                        if isinstance(entry, dict) and "message" in entry:
                            self.assertNotIn(
                                "### `artifact-A.log`",
                                entry["message"],
                                "artifact-A.log should have been " "deduplicated",
                            )
        self._run_and_compare(recorder, "rp_calls_enrich_dedup_header.json")


class TestCombinedFlowParity(_ParityTestBase):
    """Parity test: combined enrich-then-finish preserves current order."""

    def test_combined_enrich_then_finish_order(self):
        """Combined flow: enrich POSTs must precede finish PUTs."""
        ctx = make_rp_context(
            extra_cli={
                "finish": True,
                "enrich_logs": True,
                "all_launches": True,
            }
        )
        recorder = HttpRecorder()
        # RP: launches
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_IN_PROGRESS],
                "page": {"totalPages": 1},
            },
        )
        # RP: items
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": SAMPLE_ITEMS,
                "page": {"totalPages": 1},
            },
        )
        # results.xml (must precede TASK_UUID route)
        recorder.register("GET", "/results.xml", response_text=SAMPLE_XML_CONTENT)
        # TF task info
        recorder.register(
            "GET",
            TASK_UUID,
            response_json=SAMPLE_TF_TASK_DATA,
        )
        # Artifact download
        recorder.register(
            "GET",
            "artifacts.dev.testing-farm.io",
            response_text="artifact log content here",
        )
        # logs (empty for enrich)
        recorder.register(
            "GET",
            "/log",
            response_json={
                "content": SAMPLE_LOGS_EMPTY,
                "page": {"totalPages": 1},
            },
        )
        # Upload logs
        recorder.register(
            "POST",
            "/log",
            response_json={"responses": []},
            status_code=201,
        )
        # Update launch (stamp)
        recorder.register("PUT", "/update", response_json={"msg": "ok"})
        # Finish launch
        recorder.register("PUT", "/finish", response_json={"msg": "ok"})

        with recorder.patch(), self._freeze_time():
            from enge.reportportal.__main__ import main

            main(ctx)

        # Order assertion: first POST /log (enrich), then PUT /finish
        post_indices = [
            i
            for i, c in enumerate(recorder.calls)
            if c.method == "POST" and "/log" in c.url_path
        ]
        finish_indices = [
            i
            for i, c in enumerate(recorder.calls)
            if c.method == "PUT" and "/finish" in c.url_path
        ]
        if post_indices and finish_indices:
            self.assertLess(
                max(post_indices),
                min(finish_indices),
                f"Enrich POST /log (indices {post_indices}) must "
                f"precede finish PUT /finish "
                f"(indices {finish_indices})",
            )

        self._run_and_compare(recorder, "rp_calls_combined_enrich_finish.json")


# ===================================================================
# Task 4: Delete-logs, delete-stale, check parity tests
# ===================================================================


SAMPLE_STALE_LAUNCH = {
    "id": 201,
    "uuid": "stale-aabb-ccdd-eeff-001122334455",
    "name": "STALE~2026-01-01~tier0~x86_64",
    "status": "STOPPED",
    "startTime": 1700000000000,
    "attributes": [],
}


class TestDeleteLogsFromTasksParity(_ParityTestBase):
    """Parity test: delete_logs_from_task call sequence."""

    def test_delete_logs_tasks_parity(self):
        ctx = make_rp_context(extra_cli={"delete_logs": True, "input": [TASK_URL]})
        recorder = HttpRecorder()
        # RP: list launches (find_launch_by_uniq_id)
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_IN_PROGRESS],
                "page": {"totalPages": 1},
            },
        )
        # RP: items (for get_launch_logs -> get_launch_test_items)
        recorder.register(
            "GET",
            "/item",
            response_json={
                "content": SAMPLE_ITEMS,
                "page": {"totalPages": 1},
            },
        )
        # RP: logs (for deletion)
        recorder.register(
            "GET",
            "/log",
            response_json={
                "content": [
                    {"id": 9001, "message": "log1"},
                    {"id": 9002, "message": "log2"},
                ],
                "page": {"totalPages": 1},
            },
        )
        # RP: delete logs
        recorder.register("DELETE", "/log", status_code=200)

        patches = self._patch_from_tasks()

        with recorder.patch(), patches[0], patches[1], patches[2]:
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import delete_logs_from_task

            rp = ReportPortalLaunch(ctx)
            delete_logs_from_task(rp)

        self._assert_has_calls_matching(recorder, "DELETE", "/log")
        self._run_and_compare(recorder, "rp_calls_delete_logs_tasks.json")


class TestDeleteStaleParity(_ParityTestBase):
    """Parity test: delete_stale_launches call sequence."""

    def test_delete_stale_parity(self):
        ctx = make_rp_context(extra_cli={"delete_stale": True})
        recorder = HttpRecorder()
        # RP: get_all_launches for STOPPED
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_STALE_LAUNCH],
                "page": {"totalPages": 1},
            },
        )
        # RP: get_launch_test_items (returns empty — stale)
        recorder.register(
            "GET",
            "/item",
            response_json={"content": [], "page": {"totalPages": 1}},
        )
        # RP: delete launch
        recorder.register("DELETE", "/launch/", status_code=200)

        with recorder.patch():
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import delete_stale_launches

            rp = ReportPortalLaunch(ctx)
            delete_stale_launches(rp)

        self._assert_has_calls_matching(recorder, "DELETE", "/launch/")
        self._run_and_compare(recorder, "rp_calls_delete_stale.json")


class TestCheckParity(_ParityTestBase):
    """Parity test: test_connection_and_data call sequence."""

    def test_check_parity(self):
        ctx = make_rp_context(extra_cli={"test": True, "input": [TASK_URL]})
        recorder = HttpRecorder()
        recorder.register(
            "GET",
            "/launch",
            response_json={
                "content": [SAMPLE_LAUNCH_IN_PROGRESS],
                "page": {"totalPages": 1},
            },
        )

        with recorder.patch(), patch(
            "enge.reportportal.operations.parse_tasks",
            return_value=([TASK_URL], {}),
        ):
            from enge.reportportal.__main__ import ReportPortalLaunch
            from enge.reportportal.operations import (
                test_connection_and_data,
            )

            rp = ReportPortalLaunch(ctx)
            test_connection_and_data(rp)

        self._assert_has_calls_matching(recorder, "GET", "/launch")
        self._run_and_compare(recorder, "rp_calls_check.json")
