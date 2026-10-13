"""`_add_dispatch_args(parser)`: the dispatch arguments of `enge test`
as one reusable builder (R6-3d), so `enge run` can declare the same
flags without copying them.

Each test imports the builder itself, so each fails on its own until the
builder exists.
"""

import argparse
import unittest
from contextlib import redirect_stderr
from io import StringIO

from enge.utils.arg_parser import build_parser

DISPATCH_DESTS = [
    "source",
    "target",
    "copr",
    "brew",
    "tier",
    "set",
    "set_regex",
    "plan",
    "test",
    "testfilter",
    "planfilter",
    "only_rhsm_mock_cdn",
    "no_rhsm",
    "only_rhsm_stage_cdn",
    "event",
    "git_url",
    "git_ref",
    "architectures",
    "pool",
    "parallel_limit",
    "environment",
    "context",
    "rp_launch",
    "rp_description",
]


def _test_subparser():
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["test"]
    raise AssertionError("parser has no subcommands")


def _canonical(action):
    type_ = action.type
    return (
        type(action).__name__,
        tuple(action.option_strings),
        action.dest,
        repr(action.nargs),
        repr(action.const),
        repr(action.default),
        getattr(type_, "__name__", repr(type_)),
        repr(action.choices),
        action.required,
        action.help,
        repr(action.metavar),
    )


def _bare_parser_with_dispatch_args():
    from enge.utils.arg_parser import _add_dispatch_args

    parser = argparse.ArgumentParser(add_help=False)
    _add_dispatch_args(parser)
    return parser


class TestAddDispatchArgs(unittest.TestCase):
    def test_adds_exactly_the_dispatch_arguments_in_order(self):
        parser = _bare_parser_with_dispatch_args()

        self.assertEqual([a.dest for a in parser._actions], DISPATCH_DESTS)

    def test_arguments_are_identical_to_the_test_subparsers(self):
        parser = _bare_parser_with_dispatch_args()
        expected = [
            _canonical(a)
            for a in _test_subparser()._actions
            if a.dest in DISPATCH_DESTS
        ]

        self.assertEqual(len(expected), len(DISPATCH_DESTS))
        self.assertEqual([_canonical(a) for a in parser._actions], expected)

    def test_copr_and_brew_are_mutually_exclusive(self):
        parser = _bare_parser_with_dispatch_args()
        # Premise: each flag parses on its own, so the SystemExit below
        # can only come from the exclusion.
        self.assertEqual(len(parser.parse_args(["--copr", "x"]).copr), 1)
        self.assertEqual(len(parser.parse_args(["--brew", "y"]).brew), 1)

        with self.assertRaises(SystemExit) as caught, redirect_stderr(StringIO()):
            parser.parse_args(["--copr", "x", "--brew", "y"])

        self.assertEqual(caught.exception.code, 2)
