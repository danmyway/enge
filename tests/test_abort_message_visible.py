"""L43: Ctrl-C (and UserAbort) must print "Operation aborted by user".

`main()` used to log it with ``logging.info`` on the root logger, which
`setup_logging()` leaves at WARNING, so the line was dropped and Ctrl-C
printed nothing. The existing backstop test could not see this: it captures
through ``captured_logs("")``, which lowers the root logger to DEBUG.

These tests leave the root logger exactly as `setup_logging()` configures it
in production (no handlers beforehand, so `basicConfig` installs
`EngeLogHandler` at WARNING) and read what that handler renders to stderr.
"""

import io
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

import enge.__main__ as enge_main
import enge.utils.console as console_mod
from enge.utils.console import EngeLogHandler
from enge.utils.errors import UserAbort

from tests._helpers import captured_logs, make_app_context, matching

_LINE = "INFO     Operation aborted by user"


class TestAbortMessageVisible(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_root_level = root.level
        saved_enge_level = logging.getLogger("enge").level
        for handler in saved_handlers:
            root.removeHandler(handler)

        def restore():
            for handler in list(root.handlers):
                root.removeHandler(handler)
            for handler in saved_handlers:
                root.addHandler(handler)
            root.setLevel(saved_root_level)
            logging.getLogger("enge").setLevel(saved_enge_level)

        self.addCleanup(restore)

    def _run(self, exc):
        args = SimpleNamespace(debug=False, verbose=0, action="cancel")
        buf = io.StringIO()
        with (
            patch.object(enge_main, "get_arguments", return_value=args),
            patch.object(enge_main, "_handle_list_sets", return_value=False),
            patch("enge.utils.opt_manager.ParsedOpts"),
            patch("enge.utils.app_context.AppContext"),
            patch.object(enge_main, "_dispatch_action", side_effect=exc),
            patch.object(
                console_mod, "_log_console", Console(file=buf, no_color=True, width=200)
            ),
        ):
            code = enge_main.main()
        root = logging.getLogger()
        # Premise: production logging state, not a test-lowered root.
        self.assertEqual(root.level, logging.WARNING)
        self.assertTrue(any(isinstance(h, EngeLogHandler) for h in root.handlers))
        return code, [line.rstrip() for line in buf.getvalue().splitlines()]

    def test_keyboard_interrupt_prints_abort_line(self):
        code, lines = self._run(KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertIn(_LINE, lines)

    def test_user_abort_prints_abort_line(self):
        code, lines = self._run(UserAbort("no"))
        self.assertEqual(code, 130)
        self.assertIn(_LINE, lines)


class TestCancelInterruptLogsNothing(unittest.TestCase):
    """Q-L43-1: `enge cancel` re-raises Ctrl-C as UserAbort without logging
    a line of its own; `main()` prints the one abort line."""

    def test_cancel_interrupt_raises_user_abort_without_its_own_line(self):
        import enge.cancel.__main__ as cancel_main

        ctx = make_app_context(action="cancel")
        with (
            patch.object(cancel_main, "CancelJobs", side_effect=KeyboardInterrupt),
            captured_logs("enge") as records,
        ):
            with self.assertRaises(UserAbort) as caught:
                cancel_main.main(ctx)
        # Premise: the interrupt path ran (the abort carries cancel's text).
        self.assertEqual(str(caught.exception), "Cancellation interrupted by user")
        self.assertEqual(
            matching(records, "Cancellation interrupted by user", level=None), []
        )


if __name__ == "__main__":
    unittest.main()
