import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from enge.dispatch import tf_send_request
from enge.dispatch.tf_send_request import SubmitTest
from enge.utils.globals import RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT

# _response_watcher decrements its counter once per poll, whatever the status,
# so a non-200 status is polled exactly RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT
# times before the loop gives up.
EXPECTED_POLLS = RESPONSE_WATCHER_WAIT_SECONDS_DEFAULT
POLL_BOUND = 50


class TestResponseWatcherTermination(unittest.TestCase):
    """The watcher must return for every status, never poll forever."""

    def _run_watcher(self, status_code, reason="stub"):
        """Run the watcher against a fixed status; return the poll count.

        The stubbed GET raises on poll POLL_BOUND + 1, so a watcher that does
        not terminate fails the test instead of hanging the suite.
        """
        polls = []

        def fake_get(url, timeout=None):
            polls.append(url)
            if len(polls) > POLL_BOUND:
                raise AssertionError(f"watcher did not exit within {POLL_BOUND} polls")
            return SimpleNamespace(status_code=status_code, reason=reason)

        fake_self = SimpleNamespace(dispatch_summary="summary")
        with (
            mock.patch.object(tf_send_request, "http_get", fake_get),
            mock.patch.object(tf_send_request.time, "sleep", lambda _s: None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            SubmitTest._response_watcher(fake_self, "http://tf.example/requests/1")
        return len(polls)

    def test_status_below_200_exits_when_counter_exhausted(self):
        polls = self._run_watcher(102, "Processing")
        self.assertGreaterEqual(polls, 1)
        self.assertEqual(polls, EXPECTED_POLLS)

    def test_status_500_exits_when_counter_exhausted(self):
        self.assertEqual(self._run_watcher(500, "Server Error"), EXPECTED_POLLS)

    def test_status_200_exits_immediately(self):
        self.assertEqual(self._run_watcher(200, "OK"), 1)


if __name__ == "__main__":
    unittest.main()
