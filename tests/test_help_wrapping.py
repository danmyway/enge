"""``--help`` output must wrap to the terminal width (DX-7).

``RawTextHelpFormatter`` keeps every help string verbatim, so a long ``help=``
or ``description=`` rendered as one unwrapped line (811 characters for
``enge test``). Argparse reads the width from the ``COLUMNS`` environment
variable, so these tests pin ``COLUMNS`` and check the rendered text. A
test that forgot to set it would measure the developer's terminal instead,
which is why every width test first asserts a premise about the rendered
help before it checks the limit.
"""

import argparse
import os
import unittest
from unittest import mock

from enge.utils.arg_parser import build_parser

WIDE_COLUMNS = 100
WIDE_LIMIT = WIDE_COLUMNS - 2
NARROW_COLUMNS = 80
NARROW_LIMIT = NARROW_COLUMNS - 2


def _walk(parser, name="enge"):
    """Yield ``(name, parser)`` for ``parser`` and every nested subparser."""
    yield name, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub_name, sub in action.choices.items():
                yield from _walk(sub, f"{name} {sub_name}")


def _env(columns):
    patcher = mock.patch.dict(os.environ, {"COLUMNS": str(columns)})
    patcher.start()
    os.environ.pop("FORCE_COLOR", None)
    return patcher


def _longest(text):
    return max(len(line) for line in text.splitlines())


class TestHelpWrapping(unittest.TestCase):
    def setUp(self):
        patcher = _env(WIDE_COLUMNS)
        self.addCleanup(patcher.stop)
        self.parsers = dict(_walk(build_parser()))

    def _assert_fits(self, name):
        text = self.parsers[name].format_help()
        longest = max(text.splitlines(), key=len)
        self.assertLessEqual(
            len(longest),
            WIDE_LIMIT,
            f"`{name} --help` has a {len(longest)}-column line at "
            f"COLUMNS={WIDE_COLUMNS}: {longest[:70]}...",
        )
        return text

    def test_test_help_fits_100_columns(self):
        text = self.parsers["enge test"].format_help()
        self.assertIn("--source", text)
        self._assert_fits("enge test")

    def test_report_help_fits_100_columns(self):
        text = self.parsers["enge report"].format_help()
        self.assertIn("--list", text)
        self._assert_fits("enge report")

    def test_compare_help_fits_100_columns(self):
        text = self.parsers["enge compare"].format_help()
        self.assertIn("--show-tests", text)
        self._assert_fits("enge compare")

    def test_other_parsers_fit_100_columns(self):
        covered = {"enge test", "enge report", "enge compare"}
        others = [n for n in self.parsers if n not in covered]
        self.assertGreaterEqual(len(others), 9)
        for name in others:
            with self.subTest(parser=name):
                self._assert_fits(name)

    def test_description_lines_fit_80_columns(self):
        # Rendered through the parser's own formatter so wrapping (default
        # formatter) and verbatim lines (raw formatter) are both measured.
        with mock.patch.dict(os.environ, {"COLUMNS": str(NARROW_COLUMNS)}):
            with_description = [
                (n, p) for n, p in self.parsers.items() if p.description
            ]
            self.assertGreaterEqual(len(with_description), 7)
            for name, parser in with_description:
                with self.subTest(parser=name):
                    formatter = parser._get_formatter()
                    formatter.add_text(parser.description)
                    rendered = formatter.format_help()
                    self.assertTrue(rendered.strip())
                    self.assertLessEqual(_longest(rendered), NARROW_LIMIT)

    def test_epilog_examples_render_verbatim(self):
        for name in ("enge test", "enge report", "enge compare"):
            with self.subTest(parser=name):
                parser = self.parsers[name]
                self.assertTrue(parser.epilog)
                self.assertIn("examples:", parser.epilog)
                self.assertIn(parser.epilog.rstrip("\n"), parser.format_help())


if __name__ == "__main__":
    unittest.main()
