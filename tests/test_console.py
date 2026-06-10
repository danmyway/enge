"""Tests for the console proxy and logging infrastructure."""

import logging
import unittest

from enge.utils.console import (
    _ConsoleProxy,
    _current,
    _log_console,
    configure_console,
    console,
    EngeLogHandler,
    ENGE_THEME,
)


class TestConsoleProxy(unittest.TestCase):
    def test_console_is_proxy(self):
        self.assertIsInstance(console, _ConsoleProxy)

    def test_proxy_delegates_width(self):
        self.assertEqual(console.width, _current.width)

    def test_configure_json_makes_quiet(self):
        configure_console("json")
        from enge.utils.console import _current as cur

        self.assertTrue(cur.quiet)
        configure_console("terminal")

    def test_configure_gitlab_no_color(self):
        configure_console("gitlab")
        from enge.utils.console import _current as cur

        self.assertTrue(cur.no_color)
        configure_console("terminal")

    def test_configure_terminal_default(self):
        configure_console("terminal")
        from enge.utils.console import _current as cur

        self.assertFalse(cur.quiet)

    def test_log_console_uses_stderr(self):
        self.assertTrue(_log_console.stderr)


class TestEngeLogHandler(unittest.TestCase):
    def test_handler_emits_without_error(self):
        handler = EngeLogHandler()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

    def test_handler_uses_extra_style(self):
        handler = EngeLogHandler()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="styled",
            args=(),
            exc_info=None,
        )
        record.style = "bold green"
        handler.emit(record)


if __name__ == "__main__":
    unittest.main()
