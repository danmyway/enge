"""Tests for rich markup escaping in report output."""

import unittest

from enge.report.__main__ import colorize


class TestColorize(unittest.TestCase):
    def test_passed_gets_green(self):
        result = colorize("PASSED")
        self.assertIn("[", result)
        self.assertIn("PASSED", result)

    def test_brackets_in_label_escaped(self):
        result = colorize("passed", label="test [bold]injection[/]")
        self.assertNotIn("[bold]injection[/]", result)
        self.assertIn("\\[bold]", result)

    def test_plain_label_no_style(self):
        result = colorize("unknown_result", label="safe text")
        self.assertEqual(result, "safe text")

    def test_brackets_in_result_escaped(self):
        result = colorize("[error]bad[/]")
        self.assertIn("\\[error]", result)


if __name__ == "__main__":
    unittest.main()
