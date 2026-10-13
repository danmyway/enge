"""Characterization of the `enge test` subparser before its dispatch
arguments move into a shared builder (R6-3d).

The pins below were measured on the parser as it stood before the move.
They must hold unchanged at every commit of the extraction: the move is
behavior-preserving, so any change to an option string, default, help
text, type, choice list, nargs, const, metavar, required flag, action
class or argument order is a defect, not a pin to update.
"""

import argparse
import hashlib
import unittest

from enge.utils.arg_parser import build_parser


def _subparser(name):
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
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


def _digest(rows):
    return hashlib.md5(repr(rows).encode("utf-8")).hexdigest()


class TestTestSubparserCharacterization(unittest.TestCase):
    def test_every_action_of_the_test_subparser_is_unchanged(self):
        rows = [_canonical(a) for a in _subparser("test")._actions]

        self.assertEqual(len(rows), 35)
        self.assertEqual(_digest(rows), "c6a76d3156b27547ddeb4b2532469eaf")

    def test_copr_and_brew_stay_mutually_exclusive(self):
        groups = _subparser("test")._mutually_exclusive_groups
        rows = [
            sorted(o for a in g._group_actions for o in a.option_strings)
            for g in groups
        ]

        self.assertEqual(rows, [["--brew", "--copr"]])
