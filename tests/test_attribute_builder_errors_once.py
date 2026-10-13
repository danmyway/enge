"""`build_test_attributes` raises its validation errors without logging
them first (L63, the Q-DX15' log-or-raise rule): `main()` prints every
`ValidationError` as one `Validation error: ...` line, so a CRITICAL line
logged before the raise printed each error twice.

Each test pins the exact exception text first. That is the premise of its
negative assertion: the raise site was reached, so an empty log is a real
absence, not a missed path.
"""

import logging
import unittest

from enge.utils.arg_parser import get_arguments
from enge.utils.errors import ValidationError
from enge.utils.test_attribute_builder import build_test_attributes

from tests._helpers import captured_logs

LOGGER_NAME = "enge.utils.test_attribute_builder"

CONFIG = {
    "tests": {
        "git_url": "https://git.example/tests",
        "git_ref": "main",
        "tier": {"tier0": "tag:tier[0]"},
    }
}


def _raise_and_capture(testcase, argv):
    with captured_logs(LOGGER_NAME, logging.WARNING) as records:
        with testcase.assertRaises(ValidationError) as caught:
            build_test_attributes(get_arguments(argv), CONFIG)
    logged = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    return str(caught.exception), logged


class TestBuilderErrorsReportedOnce(unittest.TestCase):
    def setUp(self):
        # Production state: the module never sets its own level, so its
        # records propagate at whatever level main() configured.
        self.assertEqual(logging.getLogger(LOGGER_NAME).level, logging.NOTSET)

    def test_missing_source_is_raised_without_logging(self):
        message, logged = _raise_and_capture(self, ["test", "-T", "tier0"])

        self.assertEqual(message, "Source compose specification is required")
        self.assertEqual(logged, [])

    def test_missing_architectures_are_raised_without_logging(self):
        message, logged = _raise_and_capture(
            self, ["test", "-s", "CentOS-Stream-9", "-T", "tier0"]
        )

        self.assertEqual(message, "No architectures specified in CLI or config")
        self.assertEqual(logged, [])

    def test_malformed_context_carries_the_parser_detail_without_logging(self):
        message, logged = _raise_and_capture(
            self,
            [
                "test",
                "-s",
                "CentOS-Stream-9",
                "-T",
                "tier0",
                "--arch",
                "x86_64",
                "--context",
                "nokey",
            ],
        )

        self.assertEqual(
            message,
            "Failed to parse --context: Invalid context format: nokey. "
            "Expected KEY=VAL format.",
        )
        self.assertEqual(logged, [])
