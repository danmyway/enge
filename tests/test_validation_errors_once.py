#!/usr/bin/env python3
"""DX-15: a config validation error is printed once, not twice.

Four `ParsedOpts` validators used to log a CRITICAL header, one CRITICAL line
per error, and then raise an exception carrying the same detail (commit
2da6802). `main()` prints that exception as `Configuration error: ...` or
`Validation error: ...`, so every error reached the terminal twice, with a
header line on top. Q-DX15' (a): the exception keeps the detail and the
validators stop logging it.

These tests run the real `main()` with the real `ParsedOpts` and capture the
root logger, because "printed once" is a property of the whole path. Only
`load_config` and `sys.argv` are patched. Every test asserts its premise
first (exit code, then that the intended error reached `main()` with its
detail) and only then the target (the detail appears once; nothing else is
at ERROR or above, which is what a leftover header line would be).

Never put ``-v``/``-c`` before the subcommand in an argv here (DX-3).
"""

import copy
import logging
import os
import sys
import unittest
from unittest.mock import patch

import enge.__main__ as enge_main

from tests._helpers import captured_logs, matching

# The shape of MINIMAL_CONFIG in tests/test_opt_manager.py, rebuilt here so
# this module imports nothing from another test module.
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
        "tier": {
            "tier0": "tag:tier[0]",
            "tier1": "tag:tier[01]",
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
    "sources": {"images": {}},
}

_TOKEN_VARS = ("TESTING_FARM_API_TOKEN", "REPORTPORTAL_API_TOKEN")
_DEFAULTS_SENTENCE = "This indicates a problem with the default configuration file."


class _LoggingState(unittest.TestCase):
    """`main()` -> `setup_logging()` mutates process-global logging state
    (root level and handlers, the ``enge`` logger level). Put it back so test
    order cannot matter."""

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


class TestValidationErrorsPrintOnce(_LoggingState):
    def _run_main(self, config, argv):
        """The real `main()` and the real `ParsedOpts`; only the config and
        argv are supplied. Returns (exit code, every record on the root
        logger)."""
        with (
            patch.object(sys, "argv", argv),
            patch("enge.utils.opt_manager.load_config", return_value=config),
            patch.dict(os.environ),
        ):
            for var in _TOKEN_VARS:
                os.environ.pop(var, None)
            with captured_logs("") as records:
                code = enge_main.main()
        return code, records

    def _assert_printed_once(self, code, records, *, exit_code, prefix, needle):
        messages = [f"{r.levelname} {r.getMessage()}" for r in records]
        # Premise: the intended error reached main() with its detail.
        self.assertEqual(code, exit_code, messages)
        lead = [
            r.getMessage()
            for r in records
            if r.levelno == logging.CRITICAL and r.getMessage().startswith(prefix)
        ]
        self.assertTrue(lead and needle in lead[0], messages)
        # Target: the detail appears once, and no header line sits beside it.
        self.assertEqual(len(matching(records, needle, level=None)), 1, messages)
        loud = [r for r in records if r.levelno >= logging.ERROR]
        self.assertEqual(len(loud), 1, messages)
        return lead[0]

    def test_operational_defaults_error_printed_once(self):
        config = copy.deepcopy(_CONFIG)
        del config["common"]
        code, records = self._run_main(config, ["enge", "report", "--list"])
        lead = self._assert_printed_once(
            code,
            records,
            exit_code=99,
            prefix="Configuration error:",
            needle="Missing operational section: [common]",
        )
        # The extra sentence moved into the exception; it is not logged twice.
        sentence = matching(records, _DEFAULTS_SENTENCE, level=None)
        self.assertEqual(len(sentence), 1, sentence)
        self.assertIn(_DEFAULTS_SENTENCE, lead)

    def test_required_config_error_printed_once(self):
        config = copy.deepcopy(_CONFIG)
        del config["testing_farm"]["api_key"]
        code, records = self._run_main(
            config, ["enge", "test", "-s", "9.7", "-T", "tier0", "--arch", "x86_64"]
        )
        self._assert_printed_once(
            code,
            records,
            exit_code=99,
            prefix="Configuration error:",
            needle="[testing_farm].api_key",
        )
        hint = "Set in config or export TESTING_FARM_API_TOKEN"
        self.assertEqual(len(matching(records, hint, level=None)), 1)

    def test_static_config_error_printed_once(self):
        config = copy.deepcopy(_CONFIG)
        code, records = self._run_main(
            config, ["enge", "test", "-s", "9.7", "-T", "tier0"]
        )
        self._assert_printed_once(
            code,
            records,
            exit_code=99,
            prefix="Configuration error:",
            needle="No architectures configured",
        )

    def test_option_dependency_error_printed_once(self):
        config = copy.deepcopy(_CONFIG)
        code, records = self._run_main(
            config,
            ["enge", "test", "-s", "9.7", "-T", "tier99", "--arch", "x86_64"],
        )
        self._assert_printed_once(
            code,
            records,
            exit_code=2,
            prefix="Validation error:",
            needle="Tier 'tier99' not found",
        )


if __name__ == "__main__":
    unittest.main()
