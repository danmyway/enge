"""Rendering of failed dispatch requests: terminal label and gitlab cells."""

import unittest
from io import StringIO
from unittest.mock import patch

from enge.dispatch.__main__ import _print_dispatch_summaries


def _terminal_lines(results):
    import enge.utils.console as console_mod
    from rich.console import Console
    from enge.utils.console import ENGE_THEME

    buf = StringIO()
    with (
        patch("sys.stdout", new_callable=StringIO),
        patch.object(
            console_mod,
            "_current",
            Console(file=buf, theme=ENGE_THEME, no_color=True),
        ),
    ):
        _print_dispatch_summaries(results, "terminal")
    return [line.strip() for line in buf.getvalue().splitlines()]


def _gitlab_lines(results):
    with patch("sys.stdout", new_callable=StringIO) as mock_out:
        _print_dispatch_summaries(results, "gitlab")
    return mock_out.getvalue().splitlines()


def _failed(set_name, tier, arch, error):
    return {
        "status": "failed",
        "set_name": set_name,
        "tier": tier,
        "arch": arch,
        "error": error,
    }


class TestTerminalFailedLabel(unittest.TestCase):
    def test_no_set_drops_set_segment(self):
        lines = _terminal_lines([_failed(None, "t0", "x86_64", "boom")])
        self.assertIn("FAILED  t0/x86_64: boom", lines)

    def test_no_set_no_tier_keeps_arch_only(self):
        lines = _terminal_lines([_failed(None, None, "x86_64", "boom")])
        self.assertIn("FAILED  x86_64: boom", lines)

    def test_full_label_unchanged(self):
        lines = _terminal_lines([_failed("s1", "t0", "x86_64", "boom")])
        self.assertIn("FAILED  s1/t0/x86_64: boom", lines)


class TestGitlabCells(unittest.TestCase):
    def test_pipe_in_error_is_escaped(self):
        lines = _gitlab_lines([_failed("s", "t0", "x86_64", "a | b")])
        self.assertIn(r"| s | t0 | x86_64 | FAILED: a \| b |", lines)

    def test_newline_in_error_joined_onto_one_row(self):
        lines = _gitlab_lines([_failed("s", "t0", "x86_64", "line1\nline2")])
        self.assertIn("| s | t0 | x86_64 | FAILED: line1 line2 |", lines)
        self.assertNotIn("line2 |", lines)

    def test_pipe_in_set_name_is_escaped(self):
        lines = _gitlab_lines(
            [{"status": "submitted", "set_name": "a|b", "tier": "t0", "arch": "x86_64"}]
        )
        self.assertIn(r"| a\|b | t0 | x86_64 | dry run |", lines)

    def test_missing_set_renders_dash(self):
        lines = _gitlab_lines([_failed(None, "t0", "x86_64", "e")])
        self.assertIn("| - | t0 | x86_64 | FAILED: e |", lines)
