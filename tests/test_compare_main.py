"""RED tests for enge.compare.__main__: the two things that exist only at
the process entrypoint, not in the pure engine or the I/O loader.

`enge.compare.engine`/`enge.compare.loader` own the pure grouping/
consolidation/descriptor-sourcing contract tests (see
test_compare_engine.py / test_compare_loader.py); this module is new for
the compare-redesign branch (compare-redesign-contract-v3, fired
2026-07-22) because neither R4 (always-SUCCESS exit regardless of table
content) nor item 7's None -> em-dash render fallback has a home in the
pure-engine or I/O-loader test files -- both are entrypoint-level
concerns. No test file previously existed for `compare/__main__.py`.
"""

import unittest
from unittest.mock import patch

from tests._helpers import make_app_context
from enge.compare.engine import ComparisonTable, ExecutionColumn, RowResult
from enge.utils.globals import ExitCode
from enge.utils.results_parser import PlanEntry


def _column(**overrides):
    base = dict(
        task_id="t1",
        run_id="run1",
        set="setA",
        tier="tier1",
        arch="x86_64",
        source="9.9",
        target="10.3",
        source_compose="RHEL-9.9.0-1",
        target_compose=None,
        dispatched_at="2026-07-01T00:00:00Z",
        run_created_at="2026-07-01T00:00:00Z",
        plans=[],
        artifacts_url=None,
    )
    base.update(overrides)
    return ExecutionColumn(**base)


def _ctx(**cli_overrides):
    cli = {
        "run": None,
        "filter_set": None,
        "filter_tier": None,
        "filter_arch": None,
        "filter_tag": None,
        "show_tests": False,
        "splitarch": False,
        "splitpath": False,
        "short": False,
        "jira": False,
    }
    cli.update(cli_overrides)
    return make_app_context(action="compare", extra_cli=cli)


class TestAlwaysSuccessExit(unittest.TestCase):
    """R4: DELETE derive_exit_code -- the unified view always returns
    SUCCESS regardless of table content. CONFIG_ERROR for the floor
    failure is not a comparison result and is unaffected."""

    def test_table_with_failed_and_error_rows_still_exits_success(self):
        import enge.compare.__main__ as cm

        cols = [
            _column(
                task_id="t1",
                plans=[PlanEntry(name="/plans/a", verdict="FAILED", tests=[])],
            ),
            _column(
                task_id="t2",
                arch="s390x",
                plans=[PlanEntry(name="/plans/b", verdict="ERROR", tests=[])],
            ),
        ]

        with patch("enge.compare.__main__.load_columns", return_value=(cols, None)):
            code = cm.main(_ctx())

        self.assertEqual(code, ExitCode.SUCCESS)

    def test_floor_failure_still_returns_config_error(self):
        import enge.compare.__main__ as cm

        with patch(
            "enge.compare.__main__.load_columns",
            return_value=([], ExitCode.CONFIG_ERROR),
        ):
            code = cm.main(_ctx())

        self.assertEqual(code, ExitCode.CONFIG_ERROR)


class TestDescriptorRenderFallback(unittest.TestCase):
    """Item 7: a None source/target (multi-set legacy manifest, no
    per-task descriptor) renders as the em dash, never the literal string
    'None'."""

    def test_none_source_and_target_render_as_em_dash_in_footer(self):
        from enge.compare.__main__ import _footer_entries

        col = _column(source=None, target=None)
        table = ComparisonTable(tier="tier1", columns=(col,), rows=())

        (entry,) = list(_footer_entries(table))

        self.assertNotIn("None", entry["path"])
        self.assertIn("—", entry["path"])


class TestUntieredTableTitle(unittest.TestCase):
    """RULING Q-T1 (maintainer, 2026-08-31): tier is nullable, not a
    sentinel. _table_title must not render the bare word 'None' for a
    null-tier (untiered) table."""

    def test_null_tier_table_title_renders_ratified_label(self):
        from enge.compare.__main__ import _table_title

        col = _column()
        table = ComparisonTable(tier=None, columns=(col,), rows=())

        self.assertEqual(_table_title(table), "tier: untiered")


class TestFlakyNeverRendered(unittest.TestCase):
    """R5: flaky is computed but must never surface as a rendered
    column."""

    def test_flaky_field_is_never_rendered_as_a_column(self):
        from enge.compare.__main__ import _render_table

        col = _column()
        row = RowResult(
            label="/plans/a",
            plan_label=None,
            per_column=("PASSED",),
            consolidated="PASSED",
            flaky=True,
        )
        table = ComparisonTable(tier="tier1", columns=(col,), rows=(row,))

        rich_table = _render_table(table, short=False)

        headers = [str(c.header).lower() for c in rich_table.columns]
        self.assertNotIn("flaky", headers)


class TestShortFlagRendering(unittest.TestCase):
    """`--short` (fix/short-name-rendering, 2026-07-27): render-time only,
    threaded from `main()` into `_render_table`. Never mutates row/plan
    identity used for grouping -- only the displayed label text."""

    def test_short_shortens_plan_header_and_row_label(self):
        from enge.compare.__main__ import _render_table

        col = _column()
        plan_row = RowResult(
            label="/plans/newstyle/nondestructive/tier0only",
            plan_label=None,
            per_column=("PASSED",),
            consolidated="PASSED",
            flaky=False,
        )
        test_row = RowResult(
            label=(
                "/tests/newstyle/upgrades/tests/nondestructive/"
                "test_selinux_labels.py::TestSelinuxLabels"
            ),
            plan_label="/plans/newstyle/nondestructive/tier0only",
            per_column=("PASSED",),
            consolidated="PASSED",
            flaky=False,
        )
        table = ComparisonTable(tier="tier1", columns=(col,), rows=(plan_row, test_row))

        rich_table = _render_table(table, short=True)

        name_cells = rich_table.columns[0]._cells
        self.assertEqual(name_cells[0], "nondestructive/tier0only")
        self.assertEqual(name_cells[1], "nondestructive/tier0only")
        self.assertEqual(name_cells[-1], "**** TestSelinuxLabels")

    def test_without_short_renders_raw_verbatim_name_with_leading_slash(self):
        from enge.compare.__main__ import _render_table

        col = _column()
        row = RowResult(
            label="/plans/newstyle/nondestructive/tier0only",
            plan_label=None,
            per_column=("PASSED",),
            consolidated="PASSED",
            flaky=False,
        )
        table = ComparisonTable(tier="tier1", columns=(col,), rows=(row,))

        rich_table = _render_table(table, short=False)

        self.assertIn(
            "/plans/newstyle/nondestructive/tier0only",
            rich_table.columns[0]._cells[0],
        )

    def test_main_threads_short_flag_from_cli_args_into_render_table(self):
        """`main()` must forward `ctx.cli_args.short` into `_render_table`,
        not hardcode it -- caught the wrapped real implementation so the
        assertion is on the actual keyword value passed, not on a mock's
        own behavior."""
        import enge.compare.__main__ as cm

        cols = [
            _column(
                task_id="t1",
                plans=[
                    PlanEntry(
                        name="/plans/newstyle/nondestructive/tier0only",
                        verdict="PASSED",
                        tests=[],
                    )
                ],
            ),
        ]

        with (
            patch("enge.compare.__main__.load_columns", return_value=(cols, None)),
            patch.object(cm, "_render_table", wraps=cm._render_table) as mock_render,
        ):
            cm.main(_ctx(short=True))

        mock_render.assert_called_once()
        _args, kwargs = mock_render.call_args
        self.assertTrue(kwargs["short"])

    def test_plan_header_dedup_keyed_on_raw_label_not_shortened(self):
        """Two different raw plan labels that happen to shorten to the same
        string (no real collision exists in practice, ruling S-3) must
        still render as two distinct header rows -- dedup compares the
        RAW plan_label, never `_short_name(plan_label)`."""
        from enge.compare.__main__ import _render_table

        col = _column()
        row_a = RowResult(
            label="test-a",
            plan_label="/plans/setA/nondestructive/tier0only",
            per_column=("PASSED",),
            consolidated="PASSED",
            flaky=False,
        )
        row_b = RowResult(
            label="test-b",
            plan_label="/plans/setB/nondestructive/tier0only",
            per_column=("PASSED",),
            consolidated="PASSED",
            flaky=False,
        )
        table = ComparisonTable(tier="tier1", columns=(col,), rows=(row_a, row_b))

        rich_table = _render_table(table, short=True)

        self.assertEqual(rich_table.row_count, 4)  # 2 headers + 2 rows

    def test_main_defaults_to_shortened_rendering_with_neither_flag(self):
        """RULING F4 (maintainer, 2026-07-27, restated v23 §2.9): `enge
        compare` shortens names by default now, with neither `-s` nor
        `-l` passed."""
        import enge.compare.__main__ as cm

        cols = [
            _column(
                task_id="t1",
                plans=[
                    PlanEntry(
                        name="/plans/newstyle/nondestructive/tier0only",
                        verdict="PASSED",
                        tests=[],
                    )
                ],
            ),
        ]

        with (
            patch("enge.compare.__main__.load_columns", return_value=(cols, None)),
            patch.object(cm, "_render_table", wraps=cm._render_table) as mock_render,
        ):
            cm.main(_ctx())

        mock_render.assert_called_once()
        _args, kwargs = mock_render.call_args
        self.assertTrue(kwargs["short"])

    def test_main_long_flag_renders_full_verbatim_name(self):
        """`-l/--long` opts back into the full verbatim rendering that
        used to be the default."""
        import enge.compare.__main__ as cm

        cols = [
            _column(
                task_id="t1",
                plans=[
                    PlanEntry(
                        name="/plans/newstyle/nondestructive/tier0only",
                        verdict="PASSED",
                        tests=[],
                    )
                ],
            ),
        ]

        with (
            patch("enge.compare.__main__.load_columns", return_value=(cols, None)),
            patch.object(cm, "_render_table", wraps=cm._render_table) as mock_render,
        ):
            cm.main(_ctx(long=True))

        mock_render.assert_called_once()
        _args, kwargs = mock_render.call_args
        self.assertFalse(kwargs["short"])


class TestColumnHeaderNewlineJoin(unittest.TestCase):
    """RULING F5 (maintainer, 2026-07-29): column headers join their parts
    with a plain newline in every output mode -- no format-aware
    branching."""

    def test_header_joins_arch_and_path_parts_with_newline(self):
        from enge.compare.__main__ import _column_header

        col = _column(arch="x86_64", source="9.9", target="10.3")
        table = ComparisonTable(
            tier="tier1", arch=None, source=None, target=None, columns=(col,), rows=()
        )

        header = _column_header(table, 0)

        self.assertEqual(header, "x86_64\n9.9→10.3\n(1)")

    def test_header_is_index_only_when_arch_and_path_are_table_level(self):
        from enge.compare.__main__ import _column_header

        col = _column(arch="x86_64", source="9.9", target="10.3")
        table = ComparisonTable(
            tier="tier1",
            arch="x86_64",
            source="9.9",
            target="10.3",
            columns=(col,),
            rows=(),
        )

        header = _column_header(table, 0)

        self.assertEqual(header, "(1)")


class TestFooterDropsSetAndTask(unittest.TestCase):
    """RULING F3 (maintainer, 2026-07-29): the TASK REFERENCE footer drops
    `set=` and `task=`; `run=` stays unconditional -- the task is already
    in results.json, run_id is not otherwise discoverable without grepping
    the caches."""

    def test_footer_line_omits_set_and_task_keeps_run_unconditionally(self):
        import enge.compare.__main__ as cm

        cols = [
            _column(
                task_id="t1",
                run_id="run1",
                set="setA",
                arch="x86_64",
                source="9.9",
                target="10.3",
                artifacts_url="https://example.test/artifacts",
                plans=[PlanEntry(name="/plans/a", verdict="PASSED", tests=[])],
            ),
        ]

        with (
            patch("enge.compare.__main__.load_columns", return_value=(cols, None)),
            patch("enge.compare.__main__.console.print") as mock_print,
        ):
            cm.main(_ctx())

        footer_lines = [
            call.args[0]
            for call in mock_print.call_args_list
            if call.args
            and isinstance(call.args[0], str)
            and call.args[0].startswith("(1)")
        ]
        self.assertEqual(len(footer_lines), 1)
        footer_line = footer_lines[0]

        self.assertEqual(
            footer_line,
            "(1) x86_64 9.9→10.3 run=run1 https://example.test/artifacts",
        )
        self.assertNotIn("set=", footer_line)
        self.assertNotIn("task=", footer_line)


if __name__ == "__main__":
    unittest.main()
