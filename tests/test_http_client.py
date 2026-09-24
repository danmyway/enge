import http.server
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import requests

from enge.utils import http_client
from enge.utils.http_client import (
    get_session,
    http_get,
    http_post,
    http_put,
    http_delete,
)


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    """Count every request that arrives, then reply as the server is configured."""

    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:
        # Increment on arrival, before any sleep: a request the client later
        # abandons still reached the server, and that is exactly what these
        # tests are counting.
        self.server.hits[self.command] = self.server.hits.get(self.command, 0) + 1
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if self.server.sleep_seconds:
                time.sleep(self.server.sleep_seconds)
            body = b"{}"
            self.send_response(self.server.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if self.server.retry_after is not None:
                self.send_header("Retry-After", str(self.server.retry_after))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The client timed out and hung up while we were sleeping.
            pass

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, format, *args):  # noqa: A002 - signature is fixed by stdlib
        pass


class _SessionResetCase(unittest.TestCase):
    """Reset both module singletons so a session never leaks between tests."""

    def setUp(self):
        super().setUp()
        self._reset_sessions()
        self.addCleanup(self._reset_sessions)

    @staticmethod
    def _reset_sessions():
        http_client._session = None
        http_client._create_session = None


class TestHttpClient(_SessionResetCase):
    @patch("enge.utils.http_client.requests.Session")
    def test_get_session_singleton(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        s1 = get_session()
        s2 = get_session()

        self.assertIs(s1, s2)
        mock_session_cls.assert_called_once()

    @patch("enge.utils.http_client.get_session")
    def test_http_get_calls_session(self, mock_get_session):
        mock_sess = MagicMock()
        mock_get_session.return_value = mock_sess

        http_get("https://example.com", timeout=5, params={"a": 1})

        mock_sess.get.assert_called_once()
        args, kwargs = mock_sess.get.call_args
        self.assertEqual(args[0], "https://example.com")
        self.assertEqual(kwargs["timeout"], 5)
        self.assertEqual(kwargs["params"], {"a": 1})

    @patch("enge.utils.http_client._get_create_session")
    def test_http_post_calls_session(self, mock_get_create_session):
        mock_sess = MagicMock()
        mock_get_create_session.return_value = mock_sess

        http_post("https://example.com", json={"k": "v"})

        mock_sess.post.assert_called_once()
        args, kwargs = mock_sess.post.call_args
        self.assertEqual(args[0], "https://example.com")
        self.assertIn("timeout", kwargs)
        self.assertEqual(kwargs["json"], {"k": "v"})

    @patch("enge.utils.http_client.get_session")
    def test_http_put_calls_session(self, mock_get_session):
        mock_sess = MagicMock()
        mock_get_session.return_value = mock_sess

        http_put("https://example.com", data=b"x")

        mock_sess.put.assert_called_once()
        args, kwargs = mock_sess.put.call_args
        self.assertEqual(args[0], "https://example.com")
        self.assertIn("timeout", kwargs)
        self.assertEqual(kwargs["data"], b"x")

    @patch("enge.utils.http_client.get_session")
    def test_http_delete_calls_session(self, mock_get_session):
        mock_sess = MagicMock()
        mock_get_session.return_value = mock_sess

        http_delete("https://example.com", headers={"X": "1"})

        mock_sess.delete.assert_called_once()
        args, kwargs = mock_sess.delete.call_args
        self.assertEqual(args[0], "https://example.com")
        self.assertIn("timeout", kwargs)
        self.assertEqual(kwargs["headers"], {"X": "1"})


class TestRetryPolicyAgainstLocalServer(_SessionResetCase):
    """Count what actually reaches a server, per HTTP method.

    A request-creating POST must never be re-sent once it may have been
    processed: a duplicate Testing Farm create leaves an orphan request that
    no manifest records and neither report, cancel nor rerun can see.
    """

    def setUp(self):
        super().setUp()
        # Retry backoff would otherwise add ~3s of sleeping to every
        # retried case; the schedule is not what these tests assert.
        patcher = patch.object(http_client, "_BACKOFF_FACTOR", 0, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start_server(self, status=200, sleep_seconds=0.0, retry_after=None):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
        server.daemon_threads = True
        server.hits = {}
        server.status = status
        server.sleep_seconds = sleep_seconds
        server.retry_after = retry_after
        # serve_forever's default 0.5s poll interval is what shutdown() waits
        # on, so it would dominate this module's runtime as pure teardown.
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    @staticmethod
    def _url(server):
        host, port = server.server_address[:2]
        return f"http://{host}:{port}/"

    # --- POST: never retried once the request may have been processed ---

    def test_post_is_not_retried_on_502(self):
        server = self._start_server(status=502)

        response = http_post(self._url(server), timeout=5)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(server.hits.get("POST"), 1)

    def test_post_is_not_retried_on_read_timeout(self):
        server = self._start_server(status=200, sleep_seconds=0.6)

        with self.assertRaises(requests.exceptions.ReadTimeout):
            http_post(self._url(server), timeout=0.2)

        self.assertEqual(server.hits.get("POST"), 1)

    def test_post_is_not_retried_on_503_with_retry_after(self):
        server = self._start_server(status=503, retry_after=0)

        response = http_post(self._url(server), timeout=5)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(server.hits.get("POST"), 1)

    def test_post_is_still_retried_on_429(self):
        server = self._start_server(status=429)

        with self.assertRaises(requests.exceptions.RetryError):
            http_post(self._url(server), timeout=5)

        self.assertEqual(server.hits.get("POST"), 4)

    # --- GET/PUT/DELETE: idempotent, retry policy unchanged ---

    def test_get_is_still_retried_on_502(self):
        server = self._start_server(status=502)

        with self.assertRaises(requests.exceptions.RetryError):
            http_get(self._url(server), timeout=5)

        self.assertEqual(server.hits.get("GET"), 4)

    def test_get_is_still_retried_on_read_timeout(self):
        server = self._start_server(status=200, sleep_seconds=0.6)

        with self.assertRaises(requests.exceptions.ConnectionError):
            http_get(self._url(server), timeout=0.2)

        self.assertEqual(server.hits.get("GET"), 4)

    def test_put_and_delete_are_still_retried_on_502(self):
        server = self._start_server(status=502)

        with self.assertRaises(requests.exceptions.RetryError):
            http_put(self._url(server), timeout=5)
        with self.assertRaises(requests.exceptions.RetryError):
            http_delete(self._url(server), timeout=5)

        self.assertEqual(server.hits.get("PUT"), 4)
        self.assertEqual(server.hits.get("DELETE"), 4)

    # --- the create session itself ---

    def test_create_session_is_a_distinct_singleton(self):
        s1 = http_client._get_create_session()
        s2 = http_client._get_create_session()

        self.assertIs(s1, s2)
        self.assertIsNot(s1, get_session())


if __name__ == "__main__":
    unittest.main()
