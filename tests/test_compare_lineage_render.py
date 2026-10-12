"""Item 5.2: rendering of `enge compare --lineage` (contract v3 §5a).

Output is captured with `rich.console.Console(record=True, width=200)`
patched in for the module's `console` -- the one named exception to the
"import only from tests/_helpers.py and the code under test" rule for this
item. Captions are compared with whitespace normalized: rich wraps a table
caption to the table's own width.

The normal-mode block at the bottom was captured from `origin/devel`'s
renderer at the RED commit and is committed verbatim: lineage mode must
leave normal `enge compare` output byte-identical.
"""

import io
import unittest
from unittest.mock import patch

from rich.console import Console

from tests._helpers import make_app_context
from enge.utils.results_parser import PlanEntry, TestEntry

A1 = "/plans/a"
A2 = "/plans/b"

LEGEND = (
    "Legend: P passed, F failed, E error or unparseable, S skipped, "
    "C canceled, . covered without a result, — not covered; [X] exhausted, "
    "[?] unparseable twice, [-] canceled (terminal)"
)


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
        "long": True,
        "jira": False,
    }
    cli.update(cli_overrides)
    return make_app_context(action="compare", extra_cli=cli)


def _plan(name, verdict, *tests):
    return PlanEntry(
        name=name,
        verdict=verdict,
        tests=[TestEntry(name=n, verdict=v, duration_seconds=1.0) for n, v in tests],
    )


def _col(task_id, at, plans, *, arch="x86_64", **lineage):
    from enge.compare.engine import ExecutionColumn

    if plans and "verdict" not in lineage:
        lineage["verdict"] = plans[0].verdict
    return ExecutionColumn(
        task_id=task_id,
        run_id=f"RUN-{task_id}",
        set="s",
        tier="tier0",
        arch=arch,
        source="8.10",
        target="9.6",
        source_compose="RHEL-8.10.0-Nightly",
        target_compose="RHEL-9.6.0-Nightly",
        dispatched_at=at,
        run_created_at=at,
        plans=list(plans),
        artifacts_url=f"https://a/{task_id}",
        **lineage,
    )


def _rerun(task_id, parent, at, plans, *, arch="x86_64", tests=("t1",), **kw):
    return _col(
        task_id,
        at,
        plans,
        arch=arch,
        rerun_of=parent,
        plan=A1,
        tests=tests,
        **kw,
    )


def _three_markers():
    """Three roots, one per marker: x86_64 F F F, aarch64 F E E, s390x F C."""
    failed = [_plan(A1, "FAILED", ("/t/x.py::t1", "FAILED"))]
    return [
        _col("rx", "2026-10-01T00", failed),
        _rerun("cx1", "rx", "2026-10-02T00", failed),
        _rerun("cx2", "cx1", "2026-10-03T00", failed),
        _col("ra", "2026-10-01T00", failed, arch="aarch64"),
        _rerun("ca1", "ra", "2026-10-02T00", [], arch="aarch64", verdict="ERROR"),
        _rerun("ca2", "ca1", "2026-10-03T00", [], arch="aarch64", verdict="ERROR"),
        _col("rs", "2026-10-01T00", failed, arch="s390x"),
        _rerun("cs1", "rs", "2026-10-02T00", [], arch="s390x", verdict="CANCELED"),
    ]


def _capture(ctx, *, columns=None, lineage=None):
    """Run compare.main with a recording console; return (exit, text)."""
    from enge.compare import __main__ as compare

    recorder = Console(record=True, width=200, file=io.StringIO())
    patches = [patch.object(compare, "console", recorder)]
    if columns is not None:
        patches.append(
            patch.object(compare, "load_columns", return_value=(columns, None))
        )
    if lineage is not None:
        patches.append(
            patch.object(compare, "load_lineage_columns", return_value=lineage)
        )
    for p in patches:
        p.start()
    try:
        code = compare.main(ctx)
    finally:
        for p in reversed(patches):
            p.stop()
    return code, recorder.export_text()


def _flat(text):
    return " ".join(text.split())


class TestMarkersCaptionLegend(unittest.TestCase):
    def _render(self, retry_count=2, **cli):
        columns = _three_markers()
        selected = ["rx", "ra", "rs"]
        ctx_cli = {"lineage": True, **cli}
        if retry_count is not None:
            ctx_cli["retry_count"] = retry_count
        return _capture(_ctx(**ctx_cli), lineage=(columns, selected, None))

    def test_literal_markers_are_rendered(self):
        code, text = self._render()

        self.assertEqual(code, 0)
        self.assertIn("F F F [X]", text)
        self.assertIn("F E E [?]", text)
        self.assertIn("F C [-]", text)

    def test_caption_names_the_retry_policy(self):
        _, text = self._render(retry_count=2)

        self.assertIn("lineage view; retry policy: --retry-count 2", _flat(text))

    def test_caption_without_a_retry_policy(self):
        _, text = self._render(retry_count=None)

        flat = _flat(text)
        self.assertIn(
            "lineage view; no retry policy given, so [X] (exhausted) is not evaluated",
            flat,
        )
        self.assertNotIn("F F F [X]", text)

    def test_legend_follows_every_table(self):
        _, text = self._render(splitarch=True)

        self.assertEqual(text.count("~~~ COMPARE RESULT"), 3)
        self.assertEqual(text.count(LEGEND), 3)
        result_at = text.index("~~~ COMPARE RESULT")
        self.assertLess(result_at, text.index(LEGEND))
        self.assertLess(text.index(LEGEND), text.index("~~~ TASK REFERENCE"))

    def test_no_run_field_in_the_printed_footer(self):
        _, text = self._render()

        self.assertNotIn("run=", text)


class TestFooter(unittest.TestCase):
    def test_footer_lines_and_their_printed_form(self):
        from enge.compare import engine
        from enge.compare.__main__ import _lineage_footer_lines

        columns = _three_markers()
        tables = engine.build_lineage_tables(
            columns,
            ["rx", "ra", "rs"],
            show_tests=False,
            splitarch=False,
            splitpath=False,
            retry_count=None,
        )
        expected = [
            "(1) aarch64 8.10→9.6 gen0=https://a/ra gen1=https://a/ca1 "
            "gen2=https://a/ca2",
            "(2) s390x 8.10→9.6 gen0=https://a/rs gen1=https://a/cs1",
            "(3) x86_64 8.10→9.6 gen0=https://a/rx gen1=https://a/cx1 "
            "gen2=https://a/cx2",
        ]
        self.assertEqual(_lineage_footer_lines(tables[0]), expected)

        _, text = _capture(
            _ctx(lineage=True), lineage=(columns, ["rx", "ra", "rs"], None)
        )
        footer = text[text.index("~~~ TASK REFERENCE") :].splitlines()[1:4]
        self.assertEqual([line.rstrip() for line in footer], expected)

    def test_footer_lists_composes_only_when_they_drift(self):
        from enge.compare import engine
        from enge.compare.__main__ import _lineage_footer_lines

        failed = [_plan(A1, "FAILED")]
        columns = [
            _col("r", "2026-10-01T00", failed),
            _rerun("c1", "r", "2026-10-02T00", failed, tests=[]),
        ]
        tables = engine.build_lineage_tables(
            columns,
            ["r"],
            show_tests=False,
            splitarch=False,
            splitpath=False,
            retry_count=None,
        )
        self.assertNotIn("composes:", _lineage_footer_lines(tables[0])[0])


class TestCells(unittest.TestCase):
    def test_no_result_cell_is_uncolored(self):
        from enge.compare.__main__ import _lineage_cell_markup
        from enge.compare.engine import LineageCell

        self.assertEqual(_lineage_cell_markup(LineageCell(("F", "."), None)), "F .")

    def test_cell_is_colored_by_its_last_verdict(self):
        from enge.compare.__main__ import _lineage_cell_markup
        from enge.compare.engine import LineageCell

        self.assertEqual(
            _lineage_cell_markup(LineageCell(("F", "P"), None)),
            "[bold green]F P[/]",
        )
        self.assertEqual(
            _lineage_cell_markup(LineageCell(("F", "F"), "exhausted")),
            "[bold red]F F [X][/]",
        )

    def test_uncovered_cell_renders_the_em_dash(self):
        from enge.compare import __main__ as compare
        from enge.compare import engine

        root_a = _col("ra", "2026-10-01T00", [_plan(A1, "PASSED")], arch="aarch64")
        root_x = _col("rx", "2026-10-01T00", [_plan(A1, "FAILED")])
        table = engine.LineageTable(
            tier="tier0",
            columns=(root_a, root_x),
            generations=(((0, root_a),), ((0, root_x),)),
            rows=(
                engine.LineageRow(
                    label=A1,
                    plan_label=None,
                    cells=(None, engine.LineageCell(("F",), None)),
                    consolidated="FAILED",
                ),
            ),
        )
        recorder = Console(record=True, width=200, file=io.StringIO())
        recorder.print(
            compare._render_lineage_table(table, short=False, retry_count=None)
        )
        row = next(line for line in recorder.export_text().splitlines() if A1 in line)
        self.assertEqual(
            [cell.strip() for cell in row.split("│")[1:-1]],
            [A1, "—", "F", "FAILED"],
        )

    def test_test_view_prints_a_header_row_before_each_plans_tests(self):
        failed = [
            _plan(A1, "FAILED", ("/t/x.py::t1", "FAILED")),
            _plan(A2, "PASSED"),
        ]
        columns = [
            _col("r", "2026-10-01T00", failed),
            _rerun(
                "c1",
                "r",
                "2026-10-02T00",
                [_plan(A1, "PASSED", ("/t/x.py::t1", "PASSED"))],
            ),
        ]
        _, text = _capture(
            _ctx(lineage=True, show_tests=True), lineage=(columns, ["r"], None)
        )
        rows = [
            [cell.strip() for cell in line.split("│")[1:-1]]
            for line in text.splitlines()
            if line.startswith("│")
        ]
        body = [r for r in rows if r and r[0] not in ("", "Name")]
        self.assertEqual(
            body,
            [
                [A1, "", ""],
                ["/t/x.py::t1", "F P", "PASSED"],
                [A2, "P", "PASSED"],
            ],
        )


# Captured from origin/devel's renderer (tree 4cf3004) at the RED commit.
_NORMAL_MODE_EXPECTED = {
    False: (
        "\n"
        "~~~ COMPARE RESULT ~~~~~~~~~~~~~~~~\n"
        "                        tier: tier0                        \n"
        "╭─────────┬──────────┬──────────┬──────────┬──────────────╮\n"
        "│         │ aarch64  │ x86_64   │ x86_64   │              │\n"
        "│         │ 8.10→9.6 │ 8.10→9.6 │ 8.10→9.6 │              │\n"
        "│ Name    │ (1)      │ (2)      │ (3)      │ Consolidated │\n"
        "├─────────┼──────────┼──────────┼──────────┼──────────────┤\n"
        "│ plans/a │ ERROR    │ FAILED   │ PASSED   │ ERROR        │\n"
        "│ plans/b │ —        │ PASSED   │ —        │ PASSED       │\n"
        "╰─────────┴──────────┴──────────┴──────────┴──────────────╯\n"
        "\n"
        "~~~ TASK REFERENCE ~~~~~~~~~~~~~~~~\n"
        "(1) aarch64 8.10→9.6 run=RUN-t3 https://a/t3\n"
        "(2) x86_64 8.10→9.6 run=RUN-t1 https://a/t1\n"
        "(3) x86_64 8.10→9.6 run=RUN-t2 https://a/t2\n"
    ),
    True: (
        "\n"
        "~~~ COMPARE RESULT ~~~~~~~~~~~~~~~~\n"
        "                           tier: tier0                           \n"
        "╭───────────────┬──────────┬──────────┬──────────┬──────────────╮\n"
        "│               │ aarch64  │ x86_64   │ x86_64   │              │\n"
        "│               │ 8.10→9.6 │ 8.10→9.6 │ 8.10→9.6 │              │\n"
        "│ Name          │ (1)      │ (2)      │ (3)      │ Consolidated │\n"
        "├───────────────┼──────────┼──────────┼──────────┼──────────────┤\n"
        "│ plans/a       │          │          │          │              │\n"
        "│     [bracket] │ ERROR    │ —        │ —        │ ERROR        │\n"
        "│     t1        │ —        │ FAILED   │ PASSED   │ PASSED       │\n"
        "│     t2        │ —        │ PASSED   │ —        │ PASSED       │\n"
        "│ plans/b       │          │          │          │              │\n"
        "│     t3        │ —        │ PASSED   │ —        │ PASSED       │\n"
        "╰───────────────┴──────────┴──────────┴──────────┴──────────────╯\n"
        "\n"
        "~~~ TASK REFERENCE ~~~~~~~~~~~~~~~~\n"
        "(1) aarch64 8.10→9.6 run=RUN-t3 https://a/t3\n"
        "(2) x86_64 8.10→9.6 run=RUN-t1 https://a/t1\n"
        "(3) x86_64 8.10→9.6 run=RUN-t2 https://a/t2\n"
    ),
}


class TestNormalModeUnchanged(unittest.TestCase):
    def _columns(self):
        from enge.compare.engine import ExecutionColumn

        def column(task_id, arch, at, plans):
            return ExecutionColumn(
                task_id=task_id,
                run_id=f"RUN-{task_id}",
                set="s",
                tier="tier0",
                arch=arch,
                source="8.10",
                target="9.6",
                source_compose="RHEL-8.10.0-Nightly",
                target_compose=None,
                dispatched_at=at,
                run_created_at=at,
                plans=plans,
                artifacts_url=f"https://a/{task_id}",
            )

        return [
            column(
                "t1",
                "x86_64",
                "2026-10-01T00",
                [
                    _plan(
                        A1,
                        "FAILED",
                        ("/t/x.py::t1", "FAILED"),
                        ("/t/x.py::t2", "PASSED"),
                    ),
                    _plan(A2, "PASSED", ("/t/y.py::t3", "PASSED")),
                ],
            ),
            column(
                "t2",
                "x86_64",
                "2026-10-02T00",
                [_plan(A1, "PASSED", ("/t/x.py::t1", "PASSED"))],
            ),
            column(
                "t3",
                "aarch64",
                "2026-10-01T00",
                [_plan(A1, "ERROR", ("/t/x.py::[bracket]", "ERROR"))],
            ),
        ]

    def test_normal_mode_output_is_byte_identical(self):
        for show_tests in (False, True):
            with self.subTest(show_tests=show_tests):
                code, text = _capture(
                    _ctx(show_tests=show_tests, long=False), columns=self._columns()
                )
                self.assertEqual(code, 0)
                self.assertEqual(text, _NORMAL_MODE_EXPECTED[show_tests])

    def test_normal_mode_ignores_the_lineage_fields(self):
        """The loader fills the lineage fields in normal mode too; normal
        compare must still render origin/devel's bytes. Column t2 is a rerun
        of t1, so a normal-mode sort or row that read `rerun_of` would move
        or change it."""
        from dataclasses import replace

        lineage = {
            "t1": dict(rerun_of=None, verdict="FAILED", plan=None, tests=None),
            "t2": dict(rerun_of="t1", verdict="PASSED", plan=A1, tests=["t1"]),
            "t3": dict(rerun_of=None, verdict="ERROR", plan=None, tests=[]),
        }
        columns = [replace(c, **lineage[c.task_id]) for c in self._columns()]
        for show_tests in (False, True):
            with self.subTest(show_tests=show_tests):
                code, text = _capture(
                    _ctx(show_tests=show_tests, long=False), columns=columns
                )
                self.assertEqual(code, 0)
                self.assertEqual(text, _NORMAL_MODE_EXPECTED[show_tests])


if __name__ == "__main__":
    unittest.main()
