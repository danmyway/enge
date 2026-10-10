"""
DX-3: global flags (-c/-v/-d) and reportportal-level flags given BEFORE a
subcommand must take effect.

argparse parses each subcommand into a fresh namespace and then copies every
key onto the parent namespace, so a subparser's own defaults used to overwrite
values set at an outer level. The fix gives every subparser copy of an
ancestor's optional dest ``default=argparse.SUPPRESS`` and keeps the real
default on the outermost parser that declares it.

Rulings: Q-DX3-1 (when a flag repeats across levels the inner level wins) and
Q-DX3-2 (reportportal-level flags reach the leaf subcommand).
"""

import argparse
import importlib.util
import logging
import pathlib
import sys
import unittest
from unittest import mock

import enge.utils.console as console_module
from enge.utils.arg_parser import build_parser, get_arguments

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_cli_docs.py"

SUBCOMMAND_PATHS = [
    "test",
    "report",
    "compare",
    "rerun",
    "cancel",
    "reportportal",
    "reportportal finish",
    "reportportal enrich",
    "reportportal delete-logs",
    "reportportal delete-stale",
    "reportportal check",
]

# Captured at the base commit (origin/devel ce67e1c1) with no flags given.
BASE_NAMESPACES = {
    "cancel": {
        "action": "cancel",
        "config": None,
        "debug": False,
        "dryrun": False,
        "file": None,
        "filter_arch": None,
        "filter_set": None,
        "filter_tag": None,
        "filter_tier": None,
        "input": None,
        "run": None,
        "since": None,
        "until": None,
        "verbose": 0,
    },
    "compare": {
        "action": "compare",
        "config": None,
        "debug": False,
        "filter_arch": None,
        "filter_set": None,
        "filter_tag": None,
        "filter_tier": None,
        "jira": False,
        "long": False,
        "output_format": "terminal",
        "run": None,
        "short": False,
        "show_tests": False,
        "since": None,
        "splitarch": False,
        "splitpath": False,
        "until": None,
        "verbose": 0,
    },
    "report": {
        "action": "report",
        "compare": False,
        "config": None,
        "debug": False,
        "download": False,
        "file": None,
        "filter_arch": None,
        "filter_set": None,
        "filter_tag": None,
        "filter_tier": None,
        "input": None,
        "jira": False,
        "list": False,
        "long": False,
        "output_format": "terminal",
        "refresh": False,
        "run": None,
        "short": False,
        "show_ids": False,
        "show_tests": False,
        "since": None,
        "skip_pass": False,
        "until": None,
        "verbose": 0,
        "wait": False,
    },
    "reportportal": {
        "action": "reportportal",
        "all_launches": False,
        "config": None,
        "debug": False,
        "delete_logs": False,
        "delete_stale": False,
        "dryrun": False,
        "enrich_logs": False,
        "file": None,
        "finish": False,
        "input": None,
        "rp_subcommand": None,
        "since": None,
        "test": False,
        "until": None,
        "verbose": 0,
    },
    "reportportal check": {
        "action": "reportportal",
        "all_launches": False,
        "config": None,
        "debug": False,
        "delete_logs": False,
        "delete_stale": False,
        "dryrun": False,
        "enrich_logs": False,
        "file": None,
        "finish": False,
        "input": None,
        "rp_subcommand": "check",
        "since": None,
        "test": False,
        "until": None,
        "verbose": 0,
    },
    "reportportal delete-logs": {
        "action": "reportportal",
        "all_launches": False,
        "config": None,
        "debug": False,
        "delete_logs": False,
        "delete_stale": False,
        "dryrun": False,
        "enrich_logs": False,
        "file": None,
        "finish": False,
        "input": None,
        "rp_subcommand": "delete-logs",
        "since": None,
        "test": False,
        "until": None,
        "verbose": 0,
    },
    "reportportal delete-stale": {
        "action": "reportportal",
        "all_launches": False,
        "config": None,
        "debug": False,
        "delete_logs": False,
        "delete_stale": False,
        "dryrun": False,
        "enrich_logs": False,
        "file": None,
        "finish": False,
        "input": None,
        "rp_subcommand": "delete-stale",
        "since": None,
        "test": False,
        "until": None,
        "verbose": 0,
    },
    "reportportal enrich": {
        "action": "reportportal",
        "all_launches": False,
        "config": None,
        "debug": False,
        "delete_logs": False,
        "delete_stale": False,
        "dryrun": False,
        "enrich_logs": False,
        "file": None,
        "finish": False,
        "input": None,
        "rp_subcommand": "enrich",
        "since": None,
        "test": False,
        "until": None,
        "verbose": 0,
    },
    "reportportal finish": {
        "action": "reportportal",
        "all_launches": False,
        "config": None,
        "debug": False,
        "delete_logs": False,
        "delete_stale": False,
        "dryrun": False,
        "enrich": False,
        "enrich_logs": False,
        "file": None,
        "finish": False,
        "input": None,
        "rp_subcommand": "finish",
        "since": None,
        "test": False,
        "until": None,
        "verbose": 0,
    },
    "rerun": {
        "action": "rerun",
        "auto_tag": False,
        "config": None,
        "debug": False,
        "dryrun": False,
        "error": False,
        "fail": False,
        "file": None,
        "filter_arch": None,
        "filter_set": None,
        "filter_tag": None,
        "filter_tier": None,
        "input": None,
        "output_format": "terminal",
        "run": None,
        "set_tag": None,
        "since": None,
        "until": None,
        "verbose": 0,
    },
    "test": {
        "action": "test",
        "architectures": None,
        "auto_tag": False,
        "brew": None,
        "config": None,
        "context": None,
        "copr": None,
        "debug": False,
        "dryrun": False,
        "environment": None,
        "event": None,
        "git_ref": None,
        "git_url": None,
        "list_sets": False,
        "list_sets_detail": False,
        "no_rhsm": False,
        "only_rhsm_mock_cdn": False,
        "only_rhsm_stage_cdn": False,
        "output_format": "terminal",
        "parallel_limit": None,
        "plan": None,
        "planfilter": None,
        "pool": None,
        "rp_description": None,
        "rp_launch": None,
        "set": None,
        "set_regex": None,
        "set_tag": None,
        "source": None,
        "target": None,
        "test": None,
        "testfilter": None,
        "tier": None,
        "verbose": 0,
        "wait": False,
    },
}


def _parse(*argv):
    return get_arguments(args=list(argv))


class TestNoFlagNamespacesAreUnchanged(unittest.TestCase):
    def test_no_flag_namespaces_are_unchanged(self):
        self.assertEqual(sorted(BASE_NAMESPACES), sorted(SUBCOMMAND_PATHS))
        for path in SUBCOMMAND_PATHS:
            with self.subTest(path=path):
                self.assertEqual(
                    vars(_parse(*path.split())),
                    BASE_NAMESPACES[path],
                )


class TestFlagsAfterTheSubcommandStillApply(unittest.TestCase):
    def test_flags_after_the_subcommand_still_apply(self):
        with self.subTest(case="globals after report"):
            ns = _parse("report", "--list", "-vv", "-c", "/x", "-d")
            self.assertEqual(ns.verbose, 2)
            self.assertEqual(ns.config, "/x")
            self.assertTrue(ns.debug)

        with self.subTest(case="reportportal finish options"):
            ns = _parse(
                "reportportal",
                "finish",
                "-n",
                "-i",
                "a",
                "-f",
                "f",
                "--since",
                "3d",
                "--until",
                "1d",
                "--all",
            )
            self.assertTrue(ns.dryrun)
            self.assertEqual(ns.input, ["a"])
            self.assertEqual(ns.file, ["f"])
            self.assertEqual(ns.since, "3d")
            self.assertEqual(ns.until, "1d")
            self.assertTrue(ns.all_launches)


class TestInnerLevelWinsWhenAFlagRepeats(unittest.TestCase):
    """Q-DX3-1: the later (inner) level wins; count/list flags keep only the
    inner level's occurrences."""

    def test_inner_level_wins_when_a_flag_repeats(self):
        with self.subTest(case="count"):
            self.assertEqual(_parse("-v", "report", "-vv").verbose, 2)
        with self.subTest(case="scalar"):
            self.assertEqual(_parse("-c", "a", "report", "-c", "b").config, "b")
        with self.subTest(case="list"):
            ns = _parse("reportportal", "-i", "a", "finish", "-i", "b")
            self.assertEqual(ns.input, ["b"])
        with self.subTest(case="date"):
            ns = _parse(
                "reportportal", "--since", "3d", "delete-stale", "--since", "1d"
            )
            self.assertEqual(ns.since, "1d")


class TestGlobalFlagsBeforeTheSubcommand(unittest.TestCase):
    def test_verbose_before_any_subcommand_applies(self):
        for path in SUBCOMMAND_PATHS:
            with self.subTest(path=path):
                self.assertEqual(_parse("-vv", *path.split()).verbose, 2)

    def test_config_before_any_subcommand_applies(self):
        for path in SUBCOMMAND_PATHS:
            with self.subTest(path=path):
                self.assertEqual(_parse("-c", "/x", *path.split()).config, "/x")

    def test_debug_before_any_subcommand_applies(self):
        for path in SUBCOMMAND_PATHS:
            with self.subTest(path=path):
                self.assertTrue(_parse("-d", *path.split()).debug)

    def test_global_flags_between_reportportal_and_a_leaf_apply(self):
        self.assertEqual(_parse("reportportal", "-v", "finish").verbose, 1)
        self.assertEqual(_parse("reportportal", "-c", "/y", "check").config, "/y")


class TestReportportalLevelFlagsReachTheLeaf(unittest.TestCase):
    """Q-DX3-2."""

    def test_reportportal_level_flags_reach_the_leaf(self):
        with self.subTest(case="date filters on delete-stale"):
            ns = _parse(
                "reportportal", "--since", "3d", "--until", "1d", "delete-stale"
            )
            self.assertEqual(ns.since, "3d")
            self.assertEqual(ns.until, "1d")
        with self.subTest(case="dry-run on finish"):
            self.assertTrue(_parse("reportportal", "-n", "finish").dryrun)
        with self.subTest(case="dry-run on delete-stale"):
            self.assertTrue(_parse("reportportal", "-n", "delete-stale").dryrun)
        with self.subTest(case="input sources on enrich"):
            ns = _parse("reportportal", "-i", "a", "-f", "f", "enrich")
            self.assertEqual(ns.input, ["a"])
            self.assertEqual(ns.file, ["f"])
        with self.subTest(case="all-launches on delete-logs"):
            ns = _parse("reportportal", "--all-launches", "delete-logs")
            self.assertTrue(ns.all_launches)


class _ProcessStateMixin(unittest.TestCase):
    """Entry-point helpers mutate process-global logging and console state.
    Put it back so test order cannot matter."""

    def setUp(self):
        root = logging.getLogger()
        self._root_level = root.level
        self._root_handlers = list(root.handlers)
        self._enge_level = logging.getLogger("enge").level
        self._console = console_module._current
        self.addCleanup(self._restore)

    def _restore(self):
        root = logging.getLogger()
        root.setLevel(self._root_level)
        for handler in list(root.handlers):
            if handler not in self._root_handlers:
                root.removeHandler(handler)
        logging.getLogger("enge").setLevel(self._enge_level)
        console_module._current = self._console


class TestEntryPointsHonorGlobalFlagsBeforeTheSubcommand(_ProcessStateMixin):
    def test_setup_logging_honors_verbose_before_the_subcommand(self):
        from enge.__main__ import setup_logging

        with mock.patch.object(sys, "argv", ["enge", "-vv", "report", "--list"]):
            setup_logging()

        self.assertEqual(logging.getLogger("enge").level, logging.DEBUG)

    def test_config_before_the_subcommand_reaches_list_sets(self):
        from enge.__main__ import _handle_list_sets

        argv = ["enge", "-c", "/tmp/x.toml", "test", "--list-sets"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch("enge.utils.config_parser.load_config", return_value={}) as load,
            mock.patch("enge.__main__.console"),
        ):
            _handle_list_sets()

        load.assert_called_once()
        load.assert_called_once_with(paths=["/tmp/x.toml"])


def _walk(parser, ancestor_dests, visited):
    """Yield (parser, ancestor_dests) for every parser in the tree."""
    visited.append(parser)
    yield parser, ancestor_dests
    own = {a.dest for a in parser._actions if a.option_strings and a.dest != "help"}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _walk(child, ancestor_dests | own, visited)


class TestSubparserDefaultsNeverOverwriteOuterValues(unittest.TestCase):
    def test_no_subparser_default_overwrites_an_outer_value(self):
        visited = []
        checked = 0
        for parser, ancestor_dests in _walk(build_parser(), set(), visited):
            for action in parser._actions:
                if not action.option_strings or action.dest == "help":
                    continue
                if action.dest not in ancestor_dests:
                    continue
                checked += 1
                with self.subTest(parser=parser.prog, dest=action.dest):
                    self.assertIs(action.default, argparse.SUPPRESS)

        # Premise guards: an empty walk would pass vacuously.
        self.assertEqual(len(visited), 12)
        self.assertGreater(checked, 0)


class TestCliDocsGeneratorIgnoresSuppressedDefaults(unittest.TestCase):
    def test_cli_docs_generator_ignores_suppressed_defaults(self):
        spec = importlib.util.spec_from_file_location(
            "generate_cli_docs", GENERATOR_PATH
        )
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)

        action = argparse.ArgumentParser().add_argument(
            "-c", "--config", default=argparse.SUPPRESS
        )

        self.assertIsNone(generator._default_note(action))


if __name__ == "__main__":
    unittest.main()
