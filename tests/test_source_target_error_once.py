"""L46: a source/target parse error is printed once, not two or three times.

`parse_source_target_config` logged every ValueError at ERROR and re-raised
it; `build_test_attributes` logged it again at CRITICAL and raised a
summary-only exception; `main()` then printed that summary. A cloud-image
alias whose value matches no image pattern therefore produced three lines,
two of them carrying the same detail. The log-or-raise rule (DX-15, Q-L43-1):
the exception carries the detail, nobody on the way logs it, and `main()`
prints it once.

The `main()` tests run the real `main()` and the real `ParsedOpts`; only
`load_config` and `sys.argv` are patched. Each asserts its premise first
(exit code, then that the intended error reached `main()` with its detail)
and then the target (one line at WARNING or above carries the detail, and it
is the only line at ERROR or above).
"""

import copy
import logging
import os
import sys
import unittest
from unittest.mock import patch

import enge.__main__ as enge_main
from enge.utils.source_target_parser import parse_source_target_config

from tests._helpers import captured_logs, matching

_DETAIL = "Cloud image alias 'bad_alias' resolved to 'NotAnImageName'"

_CONFIG = {
    "common": {"logs_directory": "/var/tmp/enge/logs/"},
    "testing_farm": {
        "api_key": "test-tf-key",
        "cloud_resources_tag": "test-biz-tag",
        "api_endpoint_url": "https://api.example.tf",
        "log_artifact_baseurl": "https://logs.example.tf",
        "composes_prod_url": "https://composes.example.tf",
    },
    "project": {
        "name": "test-project",
        "repo_url": "https://github.com/oamg/test",
    },
    "tests": {
        "git_url": "https://github.com/oamg/tests",
        "git_ref": "main",
        "tier": {"tier0": "tag:tier[0]"},
        "set": {
            "badset": {
                "source": "bad_alias",
                "architectures": ["x86_64"],
                "tiers": ["tier0"],
            }
        },
    },
    "copr_api": {
        "owner": "",
        "owner_is_group": True,
        "repository": "",
        "package": "",
        "build_references": [],
    },
    "brew_api": {"session_url": "", "taskid_url": ""},
    "reportportal": {"url": "", "project": ""},
    "sources": {"images": {"bad_alias": "NotAnImageName"}},
}

_TOKEN_VARS = ("TESTING_FARM_API_TOKEN", "REPORTPORTAL_API_TOKEN")


class _LoggingState(unittest.TestCase):
    """`main()` -> `setup_logging()` mutates process-global logging state.
    Detach any root handler another test leaked (so `basicConfig` installs
    the production handler), and restore everything afterwards."""

    def setUp(self):
        root = logging.getLogger()
        enge_logger = logging.getLogger("enge")
        self._root_level = root.level
        self._root_handlers = list(root.handlers)
        self._enge_level = enge_logger.level
        for handler in self._root_handlers:
            root.removeHandler(handler)
        self.addCleanup(self._restore_logging)

    def _restore_logging(self):
        root = logging.getLogger()
        root.setLevel(self._root_level)
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in self._root_handlers:
            root.addHandler(handler)
        logging.getLogger("enge").setLevel(self._enge_level)


class TestSourceTargetErrorPrintedOnce(_LoggingState):
    def _run_main(self, argv):
        with (
            patch.object(sys, "argv", argv),
            patch(
                "enge.utils.opt_manager.load_config",
                return_value=copy.deepcopy(_CONFIG),
            ),
            patch.dict(os.environ),
        ):
            for var in _TOKEN_VARS:
                os.environ.pop(var, None)
            with captured_logs("") as records:
                code = enge_main.main()
        return code, records

    def _assert_printed_once(self, code, records, *, exit_code, prefix):
        messages = [f"{r.levelname} {r.getMessage()}" for r in records]
        # Premise: the intended error reached main() with its detail.
        self.assertEqual(code, exit_code, messages)
        lead = [
            r.getMessage()
            for r in records
            if r.levelno == logging.CRITICAL and r.getMessage().startswith(prefix)
        ]
        self.assertEqual(len(lead), 1, messages)
        self.assertEqual(lead[0].count(_DETAIL), 1, messages)
        # Target: the detail is printed once, and nothing else is loud.
        printed = [
            r
            for r in records
            if r.levelno >= logging.WARNING and _DETAIL in r.getMessage()
        ]
        self.assertEqual(len(printed), 1, messages)
        loud = [r for r in records if r.levelno >= logging.ERROR]
        self.assertEqual(len(loud), 1, messages)

    def test_set_path_error_printed_once(self):
        code, records = self._run_main(["enge", "test", "-S", "badset", "-n"])
        self._assert_printed_once(
            code,
            records,
            exit_code=99,
            prefix="Configuration error: Failed to load test sets: ",
        )

    def test_source_flag_error_printed_once(self):
        code, records = self._run_main(
            ["enge", "test", "-s", "bad_alias", "-T", "tier0", "--arch", "x86_64", "-n"]
        )
        self._assert_printed_once(
            code,
            records,
            exit_code=2,
            prefix="Validation error: Failed to parse source/target configuration: ",
        )


class TestParseSourceTargetConfigRaisesWithoutLogging(unittest.TestCase):
    def test_error_is_raised_not_logged(self):
        config = copy.deepcopy(_CONFIG)
        with captured_logs("enge.utils.source_target_parser") as records:
            with self.assertRaises(ValueError) as cm:
                parse_source_target_config("bad_alias", None, config)
        # Premise: the alias error is the one raised.
        self.assertIn(_DETAIL, str(cm.exception))
        self.assertEqual(matching(records, _DETAIL, level=None), [])

    def test_error_message_unchanged(self):
        config = copy.deepcopy(_CONFIG)
        with self.assertRaises(ValueError) as cm:
            parse_source_target_config("bad_alias", None, config)
        self.assertEqual(
            str(cm.exception),
            "Cloud image alias 'bad_alias' resolved to 'NotAnImageName' which "
            "does not match any known cloud image name pattern (Alma Linux, "
            "Rocky Linux or Oracle Linux)",
        )


if __name__ == "__main__":
    unittest.main()
