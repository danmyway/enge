"""Tests for dispatch output formatting and failure tracking."""

import json
import unittest
from io import StringIO
from unittest.mock import patch

from enge.dispatch.__main__ import _print_dispatch_summaries


class TestPrintDispatchSummaries(unittest.TestCase):
    def _capture_json(self, results):
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _print_dispatch_summaries(results, "json")
            return json.loads(mock_out.getvalue())

    def test_json_all_successful(self):
        results = [
            {"status": "submitted", "set_name": "a", "summary": "s"},
            {"status": "submitted", "set_name": "b", "summary": "s"},
        ]
        out = self._capture_json(results)
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["successful"], 2)
        self.assertEqual(out["failed"], 0)

    def test_json_mixed_results(self):
        results = [
            {"status": "submitted", "set_name": "a", "summary": "s"},
            {"status": "failed", "set_name": "b", "error": "oops"},
        ]
        out = self._capture_json(results)
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["successful"], 1)
        self.assertEqual(out["failed"], 1)

    def test_json_all_failed(self):
        results = [
            {"status": "failed", "set_name": "a", "error": "e1"},
        ]
        out = self._capture_json(results)
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["successful"], 0)
        self.assertEqual(out["failed"], 1)

    def test_json_excludes_summary_key(self):
        results = [{"status": "submitted", "summary": "text", "set_name": "a"}]
        out = self._capture_json(results)
        self.assertNotIn("summary", out["requests"][0])

    def test_gitlab_uses_backtick_fences(self):
        results = [{"summary": "line1", "set_name": "a", "status": "submitted"}]
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _print_dispatch_summaries(results, "gitlab")
            output = mock_out.getvalue()
        self.assertIn("```", output)
        self.assertNotIn("{noformat}", output)

    def test_terminal_prints_summaries(self):
        results = [{"summary": "REQUEST SUMMARY", "status": "submitted"}]
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _print_dispatch_summaries(results, "terminal")
            output = mock_out.getvalue()
        self.assertIn("REQUEST SUMMARY", output)

    def test_gitlab_failed_shows_error_not_dryrun(self):
        results = [
            {
                "status": "submitted",
                "set_name": "a",
                "tier": "t0",
                "arch": "x86_64",
                "results_url": "http://example.com/1",
                "summary": "s",
            },
            {
                "status": "failed",
                "set_name": "b",
                "tier": "t1",
                "arch": "aarch64",
                "error": "no artifact",
            },
        ]
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            _print_dispatch_summaries(results, "gitlab")
            output = mock_out.getvalue()
        self.assertIn("FAILED: no artifact", output)
        lines = output.splitlines()
        failed_row = [line for line in lines if "aarch64" in line][0]
        self.assertNotIn("dry run", failed_row)

    def test_terminal_failed_emits_error_line(self):
        import enge.utils.console as console_mod
        from rich.console import Console
        from enge.utils.console import ENGE_THEME

        results = [
            {"status": "submitted", "summary": "REQUEST SUMMARY"},
            {
                "status": "failed",
                "set_name": "s1",
                "tier": "t0",
                "arch": "x86_64",
                "error": "boom",
            },
        ]
        buf = StringIO()
        with (
            patch("sys.stdout", new_callable=StringIO) as mock_out,
            patch.object(
                console_mod,
                "_current",
                Console(file=buf, theme=ENGE_THEME, no_color=True),
            ),
        ):
            _print_dispatch_summaries(results, "terminal")
            stdout = mock_out.getvalue()
        self.assertIn("REQUEST SUMMARY", stdout)
        console_output = buf.getvalue()
        self.assertIn("FAILED", console_output)
        self.assertIn("boom", console_output)


if __name__ == "__main__":
    unittest.main()
