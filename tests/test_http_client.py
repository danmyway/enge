import unittest
from unittest.mock import patch, MagicMock

from enge.utils.http_client import (
    get_session,
    http_get,
    http_post,
    http_put,
    http_delete,
)


class TestHttpClient(unittest.TestCase):
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

    @patch("enge.utils.http_client.get_session")
    def test_http_post_calls_session(self, mock_get_session):
        mock_sess = MagicMock()
        mock_get_session.return_value = mock_sess

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


if __name__ == "__main__":
    unittest.main()
