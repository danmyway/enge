"""`enge report` with raw task input holding no parseable task ID (DX-14).

Ruling Q-DX14 (maintainer, 2026-09-29): `report -i <garbage>` / `-f` with
zero parseable task IDs is a ValidationError (exit 2) -- nothing ran at
all -- instead of a CRITICAL log line and exit 0. The scope is `report`
only and zero parseable IDs only: manifest-backed empties, partial garbage,
the `rerun` path and `--show-ids` keep today's behavior, pinned here as
characterization tests.
"""

import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._helpers import captured_logs, make_app_context, matching
from enge.report.concurrent_parser import ConcurrentRequestParser
from enge.utils.errors import ValidationError
from enge.utils.globals import EXIT_PARTIAL_FAILURE, ExitCode

_VALID_UUID = "aaaaaaaa-0000-0000-0000-000000000001"
_API_URL = "https://tf.example.com/api"
_VALID_URL = f"{_API_URL}/{_VALID_UUID}"

_PARSER_LOGGER = "enge.report.concurrent_parser"
_REPORT_LOGGER = "enge.report.__main__"
_NO_TASKS = "There are no tasks to report for"


def _ctx(**cli):
    extra = {
        "download": False,
        "skip_pass": False,
        "long": False,
        "show_ids": False,
        "refresh": False,
        "list": False,
        "compare": False,
    }
    extra.update(cli)
    return make_app_context(action="report", api_endpoint_url=_API_URL, extra_cli=extra)


class TestReportUnparseableInput(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, name, content):
        path = os.path.join(self._tmpdir.name, name)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def _build_table(self, ctx):
        from enge.report.__main__ import build_table

        return build_table(ctx)

    # ── RED at base: zero parseable IDs from -i/-f raise ────────────

    def test_input_with_no_task_id_raises(self):
        """R1."""
        # Premise: a parseable -i value does not raise, even when its
        # fetch drops (graded MISSING_RESULTS instead).
        with patch.object(
            ConcurrentRequestParser, "_fetch_task_info", return_value=None
        ):
            self._build_table(_ctx(input=[_VALID_UUID]))

        with self.assertRaises(ValidationError) as cm:
            self._build_table(_ctx(input=["not-a-uuid"]))
        self.assertIn("-i/--input", str(cm.exception))

    def test_file_of_garbage_lines_raises(self):
        """R2."""
        path = self._write("garbage.txt", "not-a-uuid\nalso garbage\n")
        with self.assertRaises(ValidationError) as cm:
            self._build_table(_ctx(file=[path]))
        self.assertIn("-f/--file", str(cm.exception))

    def test_empty_file_raises(self):
        """R3."""
        path = self._write("empty.txt", "")
        with self.assertRaises(ValidationError) as cm:
            self._build_table(_ctx(file=[path]))
        self.assertIn("-f/--file", str(cm.exception))

    def test_blank_input_raises(self):
        """R4."""
        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self._build_table(_ctx(input=[value]))

    def test_message_names_both_flags(self):
        """R5."""
        path = self._write("garbage.txt", "not-a-uuid\n")
        with self.assertRaises(ValidationError) as cm:
            self._build_table(_ctx(input=["x"], file=[path]))
        message = str(cm.exception)
        self.assertIn("-i/--input", message)
        self.assertIn("-f/--file", message)

    def test_real_entry_point_exits_2(self):
        """R6: through enge.__main__.main() with report's main unpatched."""
        import enge.__main__ as enge_main

        ctx = _ctx(input=["not-a-uuid"])
        stub_po = SimpleNamespace(cli_args=ctx.cli_args, config=ctx.config)
        with (
            patch.object(
                enge_main, "get_arguments", return_value=SimpleNamespace(debug=False)
            ),
            patch("enge.utils.opt_manager.ParsedOpts", return_value=stub_po),
            patch(
                "enge.utils.app_context.AppContext.from_parsed_opts",
                return_value=ctx,
            ),
        ):
            code = enge_main.main()
        self.assertEqual(code, EXIT_PARTIAL_FAILURE)

    def test_raise_replaces_critical_line(self):
        """R12 (P5): main() already logs the ValidationError at CRITICAL;
        the old line must not repeat it."""
        with captured_logs(_PARSER_LOGGER) as records:
            with self.assertRaises(ValidationError):
                self._build_table(_ctx(input=["not-a-uuid"]))
        self.assertEqual(matching(records, _NO_TASKS, level=None), [])

    # ── GREEN at base: characterization pins ────────────────────────

    def test_partial_garbage_still_proceeds(self):
        """R7 (P2): garbage next to a valid ID is skipped silently."""
        with patch.object(
            ConcurrentRequestParser, "_fetch_task_info", return_value=None
        ) as mock_fetch:
            self._build_table(_ctx(input=["not-a-uuid", _VALID_UUID]))
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(mock_fetch.call_args[0][0], _VALID_URL)

    def test_manifest_backed_empty_returns(self):
        """R8 (P3): a manifest invocation resolving to no tasks returns."""
        with patch(
            "enge.utils.task_resolver.parse_tasks",
            return_value=([], "manifest:R1"),
        ) as mock_parse:
            self._build_table(_ctx())
        mock_parse.assert_called_once()

    def test_manifest_backed_empty_logs_critical(self):
        """R9 (P3): ... and still logs the CRITICAL line."""
        with (
            patch(
                "enge.utils.task_resolver.parse_tasks",
                return_value=([], "manifest:R1"),
            ),
            captured_logs(_PARSER_LOGGER) as records,
        ):
            self._build_table(_ctx())
        self.assertTrue(matching(records, _NO_TASKS, level=logging.CRITICAL))

    def test_rerun_path_unchanged(self):
        """R10 (P1): parse_request_xunit (the rerun path) does not raise."""
        from enge.report.__main__ import parse_request_xunit

        self.assertEqual(parse_request_xunit(ctx=_ctx(input=["not-a-uuid"])), {})

    def test_show_ids_unchanged(self):
        """R11 (P1): --show-ids with no parseable ID still succeeds."""
        from enge.report.__main__ import main

        with captured_logs(_REPORT_LOGGER) as records:
            code = main(_ctx(show_ids=True, input=["not-a-uuid"]))
        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertTrue(matching(records, "No UUIDs found!", level=logging.INFO))


if __name__ == "__main__":
    unittest.main()
