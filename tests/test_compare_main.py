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

        rich_table = _render_table(table)

        headers = [str(c.header).lower() for c in rich_table.columns]
        self.assertNotIn("flaky", headers)


if __name__ == "__main__":
    unittest.main()
