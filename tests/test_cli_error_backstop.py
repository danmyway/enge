#!/usr/bin/env python3
"""DX-4 / L32: a malformed --since/--until is a ValidationError (exit 2), and
`main()` backstops KeyboardInterrupt (130) and unexpected exceptions (1),
printing a traceback only when the ``enge`` logger is at DEBUG.

`main()` logs through the root logger and `setup_logging()` re-derives the
``enge`` logger level from the parsed arguments on every call, so the tests
drive DEBUG through the ``debug`` flag of the patched `get_arguments`, never
by setting the logger level directly. Never put ``-v`` before the subcommand
in an argv here (DX-3: a global ``-v`` is ignored).
"""

import contextlib
import io
import logging
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import enge.__main__ as enge_main
from enge.utils.errors import ValidationError
from enge.utils.globals import ExitCode
from enge.utils.opt_manager import TestingFarmEndpoint

from tests._helpers import captured_logs, matching

_STUB_CONFIG = {
    "testing_farm": {
        "api_key": "k",
        "api_endpoint_url": "https://tf.example.com/api",
        "log_artifact_baseurl": "https://tf.example.com/artifacts",
    },
    "common": {"logs_directory": "/tmp/logs"},
    "project": {},
    "tests": {},
    "reportportal": {},
}

_ACCEPTED_FORMAT_TOKENS = ("YYYY-MM-DD", "6h", "3d", "2w", "1m", "1y")


def _stub_po(cli_args):
    return SimpleNamespace(
        cli_args=cli_args,
        config=_STUB_CONFIG,
        testing_farm_endpoint=TestingFarmEndpoint(
            "https://tf.example.com/api",
            "https://tf.example.com/artifacts",
        ),
    )


class _LoggingStateMixin(unittest.TestCase):
    """`main()` -> `setup_logging()` mutates process-global logging state
    (root level and handlers via basicConfig, the ``enge`` logger level).
    Put it back so test order cannot matter."""

    def setUp(self):
        root = logging.getLogger()
        enge_logger = logging.getLogger("enge")
        self._root_level = root.level
        self._root_handlers = list(root.handlers)
        self._enge_level = enge_logger.level
        self.addCleanup(self._restore_logging)

    def _restore_logging(self):
        root = logging.getLogger()
        root.setLevel(self._root_level)
        for handler in list(root.handlers):
            if handler not in self._root_handlers:
                root.removeHandler(handler)
        logging.getLogger("enge").setLevel(self._enge_level)


class _MainDriver(_LoggingStateMixin):
    """Enter the real `main()` with the dispatcher replaced."""

    def run_main_raising(self, exc, *, debug=False):
        args = SimpleNamespace(debug=debug, action="cancel")
        with (
            patch.object(enge_main, "get_arguments", return_value=args),
            patch("enge.utils.opt_manager.ParsedOpts", return_value=_stub_po(args)),
            patch.object(enge_main, "_dispatch_action", side_effect=exc),
        ):
            with captured_logs("") as records:
                try:
                    code = enge_main.main()
                except KeyboardInterrupt:
                    # An escaped KeyboardInterrupt would abort the whole
                    # runner and hide every other result; fail this test.
                    self.fail("KeyboardInterrupt escaped main()")
        return code, records


class TestDateParseValidation(_LoggingStateMixin):
    def _run_report_list(self, flag, value):
        from enge.utils.arg_parser import get_arguments

        argv = ["enge", "report", "--list", flag, value]
        args = get_arguments(args=argv[1:])
        with (
            patch.object(sys, "argv", argv),
            patch("enge.utils.opt_manager.ParsedOpts", return_value=_stub_po(args)),
        ):
            with captured_logs("") as records:
                code = enge_main.main()
        return code, records

    def _assert_one_validation_line(self, records):
        lines = matching(records, "Validation error:", level=logging.CRITICAL)
        self.assertEqual(len(lines), 1, [r.getMessage() for r in records])
        for token in _ACCEPTED_FORMAT_TOKENS:
            self.assertIn(token, lines[0])
        self.assertFalse([r for r in records if r.exc_info])

    def test_parse_date_arg_rejects_with_formats(self):
        from enge.utils import parse_date_arg

        with self.assertRaises(ValidationError) as caught:
            parse_date_arg("yesterday")
        for token in _ACCEPTED_FORMAT_TOKENS:
            self.assertIn(token, str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_report_list_bad_since_exits_2(self):
        code, records = self._run_report_list("--since", "yesterday")
        self.assertEqual(code, 2)
        self.assertEqual(code, ExitCode.TEST_FAILURE)
        self._assert_one_validation_line(records)

    def test_report_list_bad_until_exits_2(self):
        code, records = self._run_report_list("--until", "yesterday")
        self.assertEqual(code, 2)
        self.assertEqual(code, ExitCode.TEST_FAILURE)
        self._assert_one_validation_line(records)

    def test_valid_relative_since_still_accepted(self):
        from enge.utils import parse_date_arg, resolve_utc_window

        self.assertIsNotNone(parse_date_arg("6h"))
        since, until = resolve_utc_window("6h", None)
        self.assertIsNotNone(since)
        self.assertIsNone(until)


class TestInterruptBackstop(_MainDriver):
    def test_keyboard_interrupt_returns_130(self):
        code, records = self.run_main_raising(KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertEqual(code, ExitCode.INTERRUPT)
        self.assertFalse([r for r in records if r.exc_info])
        # Same text and level as the UserAbort handler.
        self.assertEqual(
            len(matching(records, "Operation aborted by user", level=logging.INFO)),
            1,
        )


class TestExceptionBackstop(_MainDriver):
    _LINE = "Unexpected error: RuntimeError: boom"

    def test_unexpected_exception_returns_1(self):
        code, records = self.run_main_raising(RuntimeError("boom"))
        self.assertEqual(code, 1)
        self.assertEqual(code, ExitCode.EXCEPTION)
        self.assertEqual(len(matching(records, self._LINE, level=logging.CRITICAL)), 1)

    def test_no_traceback_below_debug(self):
        code, records = self.run_main_raising(RuntimeError("boom"), debug=False)
        # Premise: the one-line error is there, so "no traceback" is not
        # vacuously true because nothing was logged at all.
        self.assertEqual(code, 1)
        self.assertEqual(len(matching(records, self._LINE, level=None)), 1)
        self.assertFalse([r for r in records if r.exc_info])

    def test_traceback_at_debug(self):
        code, records = self.run_main_raising(RuntimeError("boom"), debug=True)
        self.assertEqual(code, 1)
        with_tb = [r for r in records if r.exc_info]
        self.assertTrue(with_tb)
        self.assertTrue(
            any(r.exc_info[0] is RuntimeError for r in with_tb),
            [r.exc_info for r in with_tb],
        )

    def test_argparse_usage_error_not_swallowed(self):
        # setup_logging() sits outside main()'s try, so stub it: the
        # SystemExit must come from get_arguments() INSIDE the try block,
        # where an over-broad backstop (BaseException) would swallow it.
        argv = ["enge", "report", "--no-such-flag"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(enge_main, "setup_logging"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as caught:
                enge_main.main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
