"""`enge rerun` against create requests Testing Farm (TF) rejects.

Drives the real `rerun.main` with two failed tasks and the REAL `SubmitTest`,
stubbing only the network boundary (`http_post`, `http_get`) and the launch,
compose and xunit helpers. One `SubmitTest` serves every payload, so what a
rejected request leaves behind on it is what the next iteration records.
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from enge.utils.globals import ExitCode
from tests._helpers import captured_logs, make_app_context

UUID_ONE = "aaaaaaaa-0000-0000-0000-00000000000a"
UUID_TWO = "bbbbbbbb-0000-0000-0000-00000000000b"
HTTP_POST = "enge.dispatch.tf_send_request.http_post"


class _Reply:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _tf_record(task_uuid, plan="/plan/tier0"):
    resp = MagicMock()
    resp.json.return_value = {
        "id": task_uuid,
        "environments_requested": [{"os": {"compose": "RHEL-9.0"}, "arch": "x86_64"}],
        "test": {"fmf": {"name": plan}},
    }
    return resp


def _parsed(task_uuid, test_name):
    return {
        "source_compose": "RHEL-9.0",
        "testsuites": [
            {
                "testsuite_name": "/plan/tier0",
                "testsuite_result": "FAILED",
                "testsuite_arch": "x86_64",
                "testcases": [
                    {"testcase_name": test_name, "testcase_result": "FAILED"}
                ],
            }
        ],
    }


class TestRerunRejectedRequests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def _rerun(self, replies, logger_name="enge.rerun.__main__"):
        """Run rerun.main with `replies` served to the create POSTs in order.

        Returns (exit value, records, the create-POST mock).
        """
        from enge.rerun.__main__ import main

        api = "https://tf.example.com/api"
        urls = [f"{api}/{UUID_ONE}", f"{api}/{UUID_TWO}"]
        records_by_url = {
            urls[0]: _tf_record(UUID_ONE),
            urls[1]: _tf_record(UUID_TWO),
        }

        def fake_get(url, **kwargs):
            return records_by_url[url.rstrip("/")]

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )
        with (
            captured_logs(logger_name) as records,
            patch(
                "enge.rerun.__main__._create_rerun_launch_for_payload",
                return_value=None,
            ),
            patch(
                "enge.rerun.__main__.repin_compose", side_effect=lambda c, u, **kw: c
            ),
            patch("enge.rerun.__main__.http_get", side_effect=fake_get),
            patch(
                "enge.rerun.__main__.parse_request_xunit",
                return_value={
                    UUID_ONE: _parsed(UUID_ONE, "test::one"),
                    UUID_TWO: _parsed(UUID_TWO, "test::two"),
                },
            ),
            patch(
                "enge.rerun.__main__.parse_tasks_with_map",
                return_value=(
                    urls,
                    None,
                    {UUID_ONE: None, UUID_TWO: None, urls[0]: None, urls[1]: None},
                ),
            ),
            patch("builtins.print"),
            patch(HTTP_POST, side_effect=replies) as post,
        ):
            value = main(ctx)
        # Premise of every test here: both payloads were sent, none skipped.
        self.assertEqual(post.call_count, 2)
        return value, records, post

    @staticmethod
    def _parent_of(post, index):
        """Which failed task the index-th POST was a rerun of."""
        test_name = post.call_args_list[index].kwargs["json"]["test"]["fmf"][
            "test_name"
        ]
        for needle, parent in (("one$", UUID_ONE), ("two$", UUID_TWO)):
            if test_name.endswith(needle):
                return parent
        raise AssertionError(f"unrecognised test_name {test_name!r}")

    def _manifests(self):
        return sorted(self.runs_dir.glob("*.json")) if self.runs_dir.exists() else []

    def _recorded(self):
        manifests = self._manifests()
        self.assertEqual(len(manifests), 1, manifests)
        data = json.loads(manifests[0].read_text())
        return [(r["task_id"], r["rerun_of"]) for r in data["requests"]]

    def test_rejected_request_is_not_recorded_under_the_previous_id(self):
        _, _, post = self._rerun(
            [_Reply(200, {"id": "new-1"}), _Reply(400, {"message": "bad"})]
        )

        self.assertEqual(self._recorded(), [("new-1", self._parent_of(post, 0))])

    def test_partial_failure_returns_2(self):
        value, records, _ = self._rerun(
            [_Reply(200, {"id": "new-1"}), _Reply(400, {"message": "bad"})]
        )

        self.assertEqual(value, ExitCode.TEST_FAILURE)
        warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
        self.assertEqual(warnings, ["1 requests failed"])
        run_ids = [r for r in records if "Run ID:" in r.getMessage()]
        self.assertEqual(len(run_ids), 1)

    def test_all_rejected_returns_1_and_writes_no_manifest(self):
        value, records, _ = self._rerun(
            [_Reply(401, {"message": "no"}), _Reply(401, {"message": "no"})]
        )

        self.assertEqual(value, ExitCode.EXCEPTION)
        self.assertEqual(self._manifests(), [])
        self.assertEqual([r for r in records if "Run ID:" in r.getMessage()], [])
        critical = [r.getMessage() for r in records if r.levelno == logging.CRITICAL]
        self.assertEqual(critical, ["No requests were successfully submitted!"])

    def test_transport_error_does_not_abort_the_remaining_requests(self):
        value, _, post = self._rerun(
            [requests.exceptions.ReadTimeout("slow"), _Reply(200, {"id": "new-2"})]
        )

        self.assertEqual(value, ExitCode.TEST_FAILURE)
        self.assertEqual(self._recorded(), [("new-2", self._parent_of(post, 1))])

    def test_all_accepted_returns_none(self):
        value, records, post = self._rerun(
            [_Reply(200, {"id": "new-1"}), _Reply(200, {"id": "new-2"})],
            logger_name="enge.rerun",
        )

        self.assertIsNone(value)
        self.assertEqual(
            self._recorded(),
            [("new-1", self._parent_of(post, 0)), ("new-2", self._parent_of(post, 1))],
        )
        loud = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        self.assertEqual(loud, [])


if __name__ == "__main__":
    unittest.main()
