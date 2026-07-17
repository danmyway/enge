"""RED tests for the report --compare deprecation alias and the removal of
--unify from the CLI surface.

Ratified design (fire-time maintainer ruling, Q7, verbatim in
SESSION_LOG.md / DEBRIEF.md): `enge report --compare` becomes a
deprecation alias for ONE release. It emits a WARNING pointing at
`enge compare` and delegates to it -- read-only, meaning it must NOT
write/gap-fill results.json caches during the deprecation window (unlike
every other manifest-backed report invocation). `--unify` is removed
outright: its only consumer was the legacy compare table, which this
branch deletes.

The existing tests/test_report_exit_codes.py::TestReportResultsCacheWiring
class (compare=False in its fixture) is the survival proof that the
normal report path's cache-writer wiring is untouched by this
restructuring -- it is not duplicated here.
"""

import unittest
from unittest.mock import patch

from tests._helpers import make_app_context
from enge.utils.globals import ExitCode


class TestCompareDeprecationAlias(unittest.TestCase):
    def _make_ctx(self, **cli_overrides):
        cli = {
            "list": False,
            "show_ids": False,
            "compare": True,
            "jira": False,
            "short": False,
            "skip_pass": False,
            "show_tests": False,
            "run": None,
            "filter_set": None,
            "filter_tier": None,
            "filter_arch": None,
            "filter_tag": None,
        }
        cli.update(cli_overrides)
        return make_app_context(action="report", extra_cli=cli)

    def test_compare_flag_emits_deprecation_warning(self):
        import enge.report.__main__ as rm

        ctx = self._make_ctx()

        with (
            patch("enge.compare.__main__.main", return_value=ExitCode.SUCCESS),
            self.assertLogs("enge.report.__main__", level="WARNING") as cm,
        ):
            rm.main(ctx)

        self.assertTrue(
            any("enge compare" in msg for msg in cm.output),
            f"expected a deprecation warning pointing at 'enge compare', got: {cm.output}",
        )

    def test_compare_flag_delegates_to_enge_compare(self):
        import enge.report.__main__ as rm

        ctx = self._make_ctx()

        with patch(
            "enge.compare.__main__.main", return_value=ExitCode.TEST_FAILURE
        ) as mock_compare_main:
            code = rm.main(ctx)

        mock_compare_main.assert_called_once_with(ctx)
        self.assertEqual(code, ExitCode.TEST_FAILURE)

    def test_compare_alias_never_writes_the_results_cache(self):
        """Q7 ruling: the alias is read-only during its one-release
        deprecation window -- it must not trigger cache_report_results."""
        import enge.report.__main__ as rm

        ctx = self._make_ctx()

        with (
            patch("enge.compare.__main__.main", return_value=ExitCode.SUCCESS),
            patch("enge.report.results_cache.cache_report_results") as mock_cache,
        ):
            rm.main(ctx)

        mock_cache.assert_not_called()

    def test_build_table_comparison_is_gone(self):
        import enge.report.__main__ as rm

        self.assertFalse(
            hasattr(rm, "build_table_comparison"),
            "build_table_comparison must be deleted, not just unused",
        )


class TestUnifyFlagRemoved(unittest.TestCase):
    def test_unify_not_in_report_parser(self):
        from enge.utils.arg_parser import get_arguments

        with self.assertRaises(SystemExit):
            get_arguments(["report", "--unify", "a=b"])

    def test_compare_not_in_report_epilog_as_a_supported_flag_example(self):
        from enge.utils.arg_parser import build_parser

        parser = build_parser()
        report_parser = next(
            action.choices["report"]
            for action in parser._subparsers._group_actions
            if hasattr(action, "choices") and "report" in action.choices
        )
        option_strings = {
            opt for action in report_parser._actions for opt in action.option_strings
        }
        self.assertNotIn("--unify", option_strings)
        # --compare survives (it is the alias trigger), just deprecated.
        self.assertIn("--compare", option_strings)


class TestCompareSubcommandRegistered(unittest.TestCase):
    def test_compare_subcommand_exists(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(["compare"])
        self.assertEqual(args.action, "compare")

    def test_compare_rejects_skip_pass(self):
        """--skip-pass is a ratified exclusion: consolidation must see
        every PASS, so parse-time pass-stripping cannot exist on this
        subcommand."""
        from enge.utils.arg_parser import get_arguments

        with self.assertRaises(SystemExit):
            get_arguments(["compare", "--skip-pass"])

    def test_compare_rejects_unify(self):
        from enge.utils.arg_parser import get_arguments

        with self.assertRaises(SystemExit):
            get_arguments(["compare", "--unify", "a=b"])

    def test_compare_flakiness_flag_exists(self):
        from enge.utils.arg_parser import get_arguments

        args = get_arguments(["compare", "--flakiness"])
        self.assertTrue(args.flakiness)

    def test_compare_dispatches_from_top_level_main(self):
        import enge.__main__ as enge_main

        with (
            patch("enge.__main__.get_arguments") as mock_get_args,
            patch("enge.utils.opt_manager.ParsedOpts") as mock_po_cls,
            patch(
                "enge.compare.__main__.main", return_value=ExitCode.SUCCESS
            ) as mock_compare_main,
        ):
            from types import SimpleNamespace

            mock_get_args.return_value = SimpleNamespace(
                debug=False, verbose=0, output_format="terminal"
            )
            ctx = make_app_context(action="compare")
            mock_po_cls.return_value = SimpleNamespace(
                cli_args=ctx.cli_args,
                config=ctx.config,
                testing_farm_endpoint=ctx.testing_farm_endpoint,
                archive_tasks_latest=ctx.archive_tasks_latest,
                archive_tasks_default=ctx.archive_tasks_default,
            )

            code = enge_main.main()

        mock_compare_main.assert_called_once()
        self.assertEqual(code, ExitCode.SUCCESS)


if __name__ == "__main__":
    unittest.main()
