#!/usr/bin/env python3
"""DX-10: an unreachable compose list gets one actionable line, not urllib3 noise.

``fetch_data_from_url`` is the only reader of the compose list. When it
cannot connect (off VPN), the error must name the URL and the VPN, keep the
raw urllib3 detail at DEBUG, and never surface urllib3's own ``Retrying``
WARNINGs.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

import requests

from tests._helpers import captured_logs, matching

URL = "http://composes.example/composes-production.json"
RAW = (
    "HTTPConnectionPool(host='composes.example', port=80): Max retries "
    "exceeded with url: /composes-production.json (Caused by "
    "NameResolutionError(\"HTTPConnection(host='composes.example', port=80): "
    "Failed to resolve 'composes.example'\"))"
)


class TestComposeFetchNetworkError(unittest.TestCase):
    def _fetch_raising(self, exc):
        """Run fetch_data_from_url with http_get raising `exc`; return the error."""
        from enge.dispatch import pin_compose
        from enge.utils.errors import NetworkError

        with patch("enge.dispatch.pin_compose.http_get", side_effect=exc):
            with self.assertRaises(NetworkError) as cm:
                pin_compose.fetch_data_from_url(URL)
        return cm.exception

    def test_connection_error_names_the_url_and_the_vpn(self):
        error = self._fetch_raising(requests.exceptions.ConnectionError(RAW))

        message = str(error)
        self.assertIn(URL, message)
        self.assertIn("connected to the VPN", message)
        self.assertNotIn("Max retries exceeded", message)
        self.assertNotIn("HTTPConnectionPool", message)
        self.assertIsInstance(error.__cause__, requests.exceptions.ConnectionError)

    def test_connection_error_detail_is_logged_at_debug_only(self):
        with captured_logs("enge.dispatch.pin_compose") as records:
            self._fetch_raising(requests.exceptions.ConnectionError(RAW))

        self.assertEqual(
            len(matching(records, "Max retries exceeded", level=logging.DEBUG)), 1
        )
        self.assertEqual(
            [r.getMessage() for r in records if r.levelno > logging.DEBUG], []
        )

    def test_connect_timeout_gets_the_vpn_hint(self):
        error = self._fetch_raising(requests.exceptions.ConnectTimeout(RAW))

        self.assertIn("connected to the VPN", str(error))

    def test_http_status_error_keeps_its_message(self):
        from enge.dispatch import pin_compose
        from enge.utils.errors import NetworkError

        detail = f"404 Client Error: Not Found for url: {URL}"
        response = MagicMock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(detail)

        with patch("enge.dispatch.pin_compose.http_get", return_value=response):
            with self.assertRaises(NetworkError) as cm:
                pin_compose.fetch_data_from_url(URL)

        self.assertEqual(str(cm.exception), f"Error accessing {URL}: {detail}")
        self.assertNotIn("VPN", str(cm.exception))

    def test_urllib3_retry_warnings_are_not_emitted(self):
        import enge  # noqa: F401  (importing the package applies the pin)

        retry_logger = logging.getLogger("urllib3.connectionpool")

        self.assertTrue(retry_logger.isEnabledFor(logging.ERROR))
        self.assertFalse(retry_logger.isEnabledFor(logging.WARNING))


if __name__ == "__main__":
    unittest.main()
