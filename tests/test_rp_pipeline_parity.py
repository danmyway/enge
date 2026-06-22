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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch, MagicMock

from tests._helpers import make_app_context

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

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
        {"key": "uniq_id", "value": "aabbccdd1111"},
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
        "description": "https://artifacts.dev.testing-farm.io/aabbccdd-1111-2222-3333-task-uuid-1234",
    },
    {
        "id": 5002,
        "uuid": "item-uuid-002",
        "name": "/tests/plan1/test_verify",
        "type": "STEP",
        "status": "PASSED",
        "endTime": "2026-06-19T10:01:00.000Z",
        "description": "https://artifacts.dev.testing-farm.io/aabbccdd-1111-2222-3333-task-uuid-1234",
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

SAMPLE_XML_CONTENT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="/tests/plan1" tests="2" failures="1">
    <testcase name="test_upgrade" time="120.5">
      <failure message="upgrade failed"/>
    </testcase>
    <testcase name="test_verify" time="30.2"/>
    <log name="artifact-A.log"
         href="https://artifacts.dev.testing-farm.io/aabbccdd-1111-2222-3333-task-uuid-1234/artifact-A.log"/>
    <log name="artifact-B.log"
         href="https://artifacts.dev.testing-farm.io/aabbccdd-1111-2222-3333-task-uuid-1234/artifact-B.log"/>
  </testsuite>
</testsuites>"""

SAMPLE_TF_TASK_DATA = {
    "id": "aabbccdd-1111-2222-3333-task-uuid-1234",
    "state": "complete",
    "result": {"overall": "failed", "summary": "1 of 2 tests failed"},
    "created": "2026-06-19T09:00:00Z",
    "environments_requested": [
        {
            "arch": "x86_64",
            "os": {"compose": "RHEL-8.10.0-20260101.0"},
            "tmt": {
                "context": {
                    "uniq_id": "aabbccdd1111",
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
        """Return a context manager that patches all four http_* functions."""
        return _MultiPatch(
            patch(
                "enge.utils.http_client.http_get", side_effect=self._make_handler("GET")
            ),
            patch(
                "enge.utils.http_client.http_post",
                side_effect=self._make_handler("POST"),
            ),
            patch(
                "enge.utils.http_client.http_put", side_effect=self._make_handler("PUT")
            ),
            patch(
                "enge.utils.http_client.http_delete",
                side_effect=self._make_handler("DELETE"),
            ),
        )

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
