"""`SubmitTest.send_request` against replies Testing Farm (TF) can give.

A create request TF rejects (non-2xx, no usable id, non-JSON body) or whose
outcome is unknown (transport error) must raise `SubmissionError` after
logging exactly one ERROR record, and must never leave an artifact URL behind:
callers record a manifest entry from that URL.
"""

import http.server
import logging
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from enge.utils import http_client
from tests._helpers import captured_logs

LOGGER_NAME = "enge.dispatch.tf_send_request"
HTTP_POST = "enge.dispatch.tf_send_request.http_post"
API_KEY = "s3cr3t-token"
ARTIFACT_BASE = "https://artifacts.example"
ENDPOINT = "https://api.tf.example/v0.1/requests"

_NO_JSON = object()


class _Reply:
    """The slice of requests.Response that send_request reads."""

    def __init__(self, status_code, body=_NO_JSON, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text if body is _NO_JSON else str(body)

    def json(self):
        if self._body is _NO_JSON:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def _make_submit(endpoint=ENDPOINT):
    from enge.dispatch.tf_send_request import SubmitTest

    ctx = SimpleNamespace(
        testing_farm_endpoint=SimpleNamespace(
            api_endpoint_url=endpoint, log_artifact_baseurl=ARTIFACT_BASE
        ),
        cli_args=SimpleNamespace(dryrun=False, action="test", wait=False),
    )
    submit = SubmitTest(ctx)
    submit.api_key = API_KEY
    submit.silent_output = True
    return submit


class TestSendRequestFailures(unittest.TestCase):
    def _send(self, submit, reply=None, error=None):
        """Send once through a stubbed http_post.

        Returns (raised SubmissionError or None, records at >= WARNING,
        all records, the http_post mock).
        """
        try:
            from enge.utils.errors import SubmissionError
        except ImportError:
            # Before the fix the class does not exist; an empty tuple catches
            # nothing, so a characterization test can still run.
            SubmissionError = ()

        with (
            captured_logs(LOGGER_NAME) as records,
            patch(HTTP_POST, side_effect=[error] if error else [reply]) as post,
        ):
            try:
                submit.send_request({"test": {}}, {"Authorization": "Bearer x"})
                raised = None
            except SubmissionError as e:
                raised = e
        post.assert_called_once()
        return raised, records, post

    def _assert_rejected(self, raised, records):
        """Raised once, logged once at ERROR, same text both places."""
        self.assertIsNotNone(raised, "send_request did not raise SubmissionError")
        loud = [r for r in records if r.levelno >= logging.WARNING]
        self.assertEqual(len(loud), 1, [r.getMessage() for r in loud])
        self.assertEqual(loud[0].levelno, logging.ERROR)
        self.assertEqual(loud[0].getMessage(), str(raised))
        return str(raised)

    def test_non_2xx_json_is_rejected_and_logged_once(self):
        submit = _make_submit()
        raised, records, _ = self._send(
            submit, _Reply(401, {"message": "invalid api key"})
        )

        message = self._assert_rejected(raised, records)
        self.assertTrue(
            message.startswith("Testing Farm rejected the request (HTTP 401): "),
            message,
        )
        self.assertIn("invalid api key", message)
        self.assertIsNone(submit.log_artifact_url)

    def test_non_json_error_body_is_rejected(self):
        raised, records, _ = self._send(
            _make_submit(),
            _Reply(502, text="<html>\n  <body>502   Bad Gateway</body></html>"),
        )

        self.assertEqual(
            self._assert_rejected(raised, records),
            "Testing Farm rejected the request (HTTP 502): "
            "<html> <body>502 Bad Gateway</body></html>",
        )

    def test_non_2xx_with_an_id_is_rejected(self):
        submit = _make_submit()
        raised, records, _ = self._send(submit, _Reply(500, {"id": "ghost"}))

        message = self._assert_rejected(raised, records)
        self.assertTrue(
            message.startswith("Testing Farm rejected the request (HTTP 500)")
        )
        self.assertIsNone(submit.log_artifact_url)

    def test_2xx_without_id_is_rejected(self):
        raised, records, _ = self._send(_make_submit(), _Reply(200, {"state": "new"}))

        message = self._assert_rejected(raised, records)
        self.assertTrue(
            message.startswith("Testing Farm returned HTTP 200 without a request id: "),
            message,
        )

    def test_2xx_with_null_id_is_rejected(self):
        submit = _make_submit()
        raised, records, _ = self._send(submit, _Reply(200, {"id": None}))

        self._assert_rejected(raised, records)
        self.assertIsNone(submit.log_artifact_url)

    def test_2xx_non_object_json_is_rejected(self):
        raised, records, _ = self._send(_make_submit(), _Reply(200, ["a", "b"]))

        self._assert_rejected(raised, records)

    def test_2xx_non_json_body_is_rejected(self):
        raised, records, _ = self._send(_make_submit(), _Reply(200, text="OK"))

        self.assertEqual(
            self._assert_rejected(raised, records),
            "Testing Farm returned HTTP 200 without a request id: OK",
        )

    def test_body_excerpt_is_truncated(self):
        raised, records, _ = self._send(_make_submit(), _Reply(400, text="x" * 5000))

        message = self._assert_rejected(raised, records)
        self.assertTrue(message.endswith(" (truncated)"), message[-40:])
        self.assertLess(len(message), 400)

    def test_body_excerpt_redacts_sensitive_keys(self):
        raised, records, _ = self._send(
            _make_submit(), _Reply(401, {"api_key": "abc123", "message": "denied"})
        )

        message = self._assert_rejected(raised, records)
        self.assertIn("denied", message)
        self.assertNotIn("abc123", message)
        for record in records:
            self.assertNotIn("abc123", record.getMessage())

    def test_body_excerpt_scrubs_the_api_token(self):
        raised, records, _ = self._send(
            _make_submit(), _Reply(401, {"message": f"bad token {API_KEY}"})
        )

        message = self._assert_rejected(raised, records)
        self.assertIn("bad token", message)
        self.assertNotIn(API_KEY, message)
        for record in records:
            self.assertNotIn(API_KEY, record.getMessage())

    def test_rate_limit_exhaustion_is_rejected_as_not_created(self):
        raised, records, _ = self._send(
            _make_submit(),
            error=requests.exceptions.RetryError("too many 429 error responses"),
        )

        message = self._assert_rejected(raised, records)
        self.assertIn("HTTP 429", message)
        self.assertIn("it was not created", message)
        self.assertNotIn("may have been created", message)

    def test_transport_error_says_the_request_may_exist(self):
        for exc_type in (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ConnectTimeout,
        ):
            with self.subTest(error=exc_type.__name__):
                raw = f"raw-{exc_type.__name__}-detail"
                raised, records, _ = self._send(_make_submit(), error=exc_type(raw))

                message = self._assert_rejected(raised, records)
                self.assertIn(f"({exc_type.__name__})", message)
                self.assertIn(
                    "It may have been created: check Testing Farm before "
                    "re-dispatching.",
                    message,
                )
                self.assertNotIn(raw, message)
                debug = [
                    r
                    for r in records
                    if r.levelno == logging.DEBUG and raw in r.getMessage()
                ]
                self.assertEqual(len(debug), 1)

    def test_failed_request_clears_the_previous_artifact_url(self):
        # One SubmitTest serves several requests on rerun.
        submit = _make_submit()
        first, _, _ = self._send(submit, _Reply(200, {"id": "task-1"}))
        self.assertIsNone(first)
        self.assertEqual(submit.log_artifact_url, f"{ARTIFACT_BASE}/task-1")

        raised, _, _ = self._send(submit, _Reply(400, {"message": "bad"}))

        self.assertIsNotNone(raised)
        self.assertIsNone(submit.log_artifact_url)
        self.assertIsNone(submit.dispatch_summary)

    def test_accepted_request_sets_the_artifact_url(self):
        submit = _make_submit()
        raised, records, _ = self._send(submit, _Reply(200, {"id": "task-1"}))

        self.assertIsNone(raised)
        self.assertEqual(submit.log_artifact_url, f"{ARTIFACT_BASE}/task-1")
        self.assertEqual([r for r in records if r.levelno >= logging.WARNING], [])

    def test_persistent_429_through_the_real_session(self):
        from enge.utils.errors import SubmissionError

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.server.hits += 1
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b'{"message": "slow down"}'
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.hits = 0
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def reset():
            http_client._session = None
            http_client._create_session = None

        reset()
        self.addCleanup(reset)
        patcher = patch.object(http_client, "_BACKOFF_FACTOR", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

        host, port = server.server_address[:2]
        submit = _make_submit(f"http://{host}:{port}/")
        with captured_logs(LOGGER_NAME):
            with self.assertRaises(SubmissionError) as cm:
                submit.send_request({"test": {}}, {})

        self.assertEqual(server.hits, 4)
        self.assertIn("HTTP 429", str(cm.exception))
        self.assertIn("it was not created", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
