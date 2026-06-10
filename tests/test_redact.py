"""Tests for redact_sensitive()."""

import unittest

from enge.utils import redact_sensitive


class TestRedactSensitive(unittest.TestCase):
    def test_redacts_token_key(self):
        obj = {"api_token": "secret123", "name": "test"}
        result = redact_sensitive(obj)
        self.assertEqual(result["api_token"], "***REDACTED***")
        self.assertEqual(result["name"], "test")

    def test_redacts_api_key(self):
        obj = {"api_key": "abc"}
        self.assertEqual(redact_sensitive(obj)["api_key"], "***REDACTED***")

    def test_redacts_password(self):
        obj = {"password": "hunter2"}
        self.assertEqual(redact_sensitive(obj)["password"], "***REDACTED***")

    def test_redacts_authorization(self):
        obj = {"Authorization": "Bearer xyz"}
        self.assertEqual(redact_sensitive(obj)["Authorization"], "***REDACTED***")

    def test_redacts_nested(self):
        obj = {"settings": {"secret_key": "val", "debug": True}}
        result = redact_sensitive(obj)
        self.assertEqual(result["settings"]["secret_key"], "***REDACTED***")
        self.assertTrue(result["settings"]["debug"])

    def test_redacts_in_list(self):
        obj = [{"token": "x"}, {"name": "y"}]
        result = redact_sensitive(obj)
        self.assertEqual(result[0]["token"], "***REDACTED***")
        self.assertEqual(result[1]["name"], "y")

    def test_does_not_mutate_input(self):
        obj = {"api_key": "keep"}
        redact_sensitive(obj)
        self.assertEqual(obj["api_key"], "keep")

    def test_case_insensitive(self):
        obj = {"API_KEY": "v", "Secret": "s", "PASSWORD": "p"}
        result = redact_sensitive(obj)
        for k in obj:
            self.assertEqual(result[k], "***REDACTED***")

    def test_passthrough_scalars(self):
        self.assertEqual(redact_sensitive(42), 42)
        self.assertEqual(redact_sensitive("hello"), "hello")
        self.assertIsNone(redact_sensitive(None))

    def test_empty_dict(self):
        self.assertEqual(redact_sensitive({}), {})

    def test_empty_list(self):
        self.assertEqual(redact_sensitive([]), [])


if __name__ == "__main__":
    unittest.main()
