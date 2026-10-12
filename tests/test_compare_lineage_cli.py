"""Item 5.2: `enge compare --lineage` / `--retry-count` argument handling
(contract v3 §5a, ruling Q-L5-2').

Both flags use `default=argparse.SUPPRESS`, so a plain `compare` namespace
never carries them. Validation runs before any loading and raises
`ValidationError`, which the process entrypoint maps to exit 2. Normal mode
and lineage mode never share a loader.
"""

import unittest
from unittest.mock import patch

from tests._helpers import make_app_context
from enge.utils.arg_parser import get_arguments
from enge.utils.errors import ValidationError
from enge.utils.globals import ExitCode
from enge.utils.results_parser import PlanEntry


def _ctx(**cli_overrides):
    cli = {
        "run": None,
        "filter_set": None,
        "filter_tier": None,
        "filter_arch": None,
        "filter_tag": None,
        "since": None,
        "until": None,
        "show_tests": False,
        "splitarch": False,
        "splitpath": False,
        "long": False,
        "jira": False,
    }
    cli.update(cli_overrides)
    return make_app_context(action="compare", extra_cli=cli)


def _root_column():
    from enge.compare.engine import ExecutionColumn

    return ExecutionColumn(
        task_id="r",
        run_id="RUN0",
        set="s",
        tier="tier0",
        arch="x86_64",
        source="8.10",
        target="9.6",
        source_compose="RHEL-8.10.0-Nightly",
        target_compose=None,
        dispatched_at="2026-10-01T00:00:00Z",
        run_created_at="2026-10-01T00:00:00Z",
        plans=[PlanEntry(name="/plans/a", verdict="FAILED", tests=[])],
        artifacts_url="https://a/r",
        verdict="FAILED",
    )


class TestArgparse(unittest.TestCase):
    def test_flags_are_absent_from_a_plain_compare_namespace(self):
        ns = get_arguments(args=["compare"])

        self.assertFalse(hasattr(ns, "lineage"))
        self.assertFalse(hasattr(ns, "retry_count"))

    def test_flags_parse_when_given(self):
        ns = get_arguments(args=["compare", "--lineage", "--retry-count", "2"])

        self.assertIs(ns.lineage, True)
        self.assertEqual(ns.retry_count, 2)

    def test_retry_count_must_be_an_integer(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            get_arguments(args=["compare", "--lineage", "--retry-count", "two"])


class TestValidation(unittest.TestCase):
    def _assert_rejected(self, message, **cli):
        from enge.compare import __main__ as compare

        with (
            patch.object(compare, "load_columns") as normal,
            patch.object(compare, "load_lineage_columns") as lineage,
        ):
            with self.assertRaises(ValidationError) as cm:
                compare.main(_ctx(**cli))
        self.assertEqual(str(cm.exception), message)
        normal.assert_not_called()
        lineage.assert_not_called()

    def test_retry_count_without_lineage(self):
        self._assert_rejected(
            "--retry-count is only valid with --lineage", retry_count=2
        )

    def test_negative_retry_count(self):
        self._assert_rejected(
            "--retry-count must be an integer >= 0", lineage=True, retry_count=-1
        )

    def test_each_forbidden_selector(self):
        for dest, flag, value in (
            ("filter_set", "--set", ["s"]),
            ("filter_tier", "--tier", ["tier0"]),
            ("filter_arch", "--arch", ["x86_64"]),
            ("filter_tag", "--tag", ["nightly"]),
            ("since", "--since", "2026-10-01"),
            ("until", "--until", "2026-10-02"),
        ):
            with self.subTest(flag=flag):
                self._assert_rejected(
                    f"--lineage selects runs with --run only; drop {flag}",
                    lineage=True,
                    **{dest: value},
                )

    def test_two_offending_flags_are_listed_in_order(self):
        self._assert_rejected(
            "--lineage selects runs with --run only; drop --tier, --until",
            lineage=True,
            until="2026-10-02",
            filter_tier=["tier0"],
        )

    def test_validation_error_exits_2_through_the_entrypoint(self):
        import enge.__main__ as entry

        ctx = _ctx(retry_count=1)
        with (
            patch.object(entry, "setup_logging"),
            patch.object(entry, "_handle_list_sets", return_value=False),
            patch("enge.utils.opt_manager.ParsedOpts"),
            patch(
                "enge.utils.app_context.AppContext.from_parsed_opts", return_value=ctx
            ),
            patch("enge.compare.__main__.load_columns") as normal,
        ):
            code = entry.main()

        self.assertEqual(code, 2)
        normal.assert_not_called()


class TestLoaderRouting(unittest.TestCase):
    def test_retry_count_zero_is_accepted(self):
        from enge.compare import __main__ as compare

        with (
            patch.object(
                compare,
                "load_lineage_columns",
                return_value=([_root_column()], ["r"], None),
            ),
            patch.object(compare, "console"),
        ):
            code = compare.main(_ctx(lineage=True, retry_count=0))

        self.assertEqual(code, ExitCode.SUCCESS)

    def test_run_is_allowed_with_lineage(self):
        from enge.compare import __main__ as compare

        with (
            patch.object(
                compare,
                "load_lineage_columns",
                return_value=([_root_column()], ["r"], None),
            ),
            patch.object(compare, "console"),
        ):
            code = compare.main(_ctx(lineage=True, run=["RUN0"]))

        self.assertEqual(code, ExitCode.SUCCESS)

    def test_normal_mode_never_calls_the_lineage_loader(self):
        from enge.compare import __main__ as compare

        with (
            patch.object(
                compare, "load_columns", return_value=([_root_column()], None)
            ) as normal,
            patch.object(compare, "load_lineage_columns") as lineage,
            patch.object(compare, "console"),
        ):
            compare.main(_ctx())

        normal.assert_called_once()
        lineage.assert_not_called()

    def test_lineage_mode_never_calls_the_normal_loader(self):
        from enge.compare import __main__ as compare

        with (
            patch.object(compare, "load_columns") as normal,
            patch.object(
                compare,
                "load_lineage_columns",
                return_value=([_root_column()], ["r"], None),
            ) as lineage,
            patch.object(compare, "console"),
        ):
            compare.main(_ctx(lineage=True))

        lineage.assert_called_once()
        normal.assert_not_called()

    def test_lineage_loader_floor_is_returned(self):
        from enge.compare import __main__ as compare

        with (
            patch.object(
                compare,
                "load_lineage_columns",
                return_value=([], [], ExitCode.CONFIG_ERROR),
            ),
            patch.object(compare, "console"),
        ):
            code = compare.main(_ctx(lineage=True))

        self.assertEqual(code, ExitCode.CONFIG_ERROR)


if __name__ == "__main__":
    unittest.main()
