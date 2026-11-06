"""Distro family as a compare coordinate (Q-CMP-1).

results.json records release paths without a distro ("9.7" -> "10.1"), so
before this change Alma and Rocky on the same arch and path were one
stage-1 coordinate and a PASS on one hid a FAIL on the other. The family
is derived from `source_compose` and joins the stage-1 coordinate and the
`--splitpath` key, in normal and lineage mode alike.
"""

import unittest

from enge.utils.results_parser import PlanEntry

ALMA = "AlmaLinux OS 9.7.20251118 x86_64"
ALMA_LATER = "AlmaLinux OS 9.7.20251201 x86_64"
ROCKY = "Rocky-9-EC2-Base-9.7-20251123.2.x86_64"
PLAN = "/plans/upgrade"


def _col(task_id, compose, verdict, *, at="2026-11-01T00", rerun_of=None, set_="s"):
    from enge.compare.engine import ExecutionColumn

    return ExecutionColumn(
        task_id=task_id,
        run_id=f"RUN-{task_id}",
        set=set_,
        tier="tier0",
        arch="x86_64",
        source="9.7",
        target="10.1",
        source_compose=compose,
        target_compose=None,
        dispatched_at=at,
        run_created_at=at,
        plans=[PlanEntry(name=PLAN, verdict=verdict, tests=[])],
        artifacts_url=f"https://a/{task_id}",
        rerun_of=rerun_of,
        verdict=verdict,
        plan=PLAN if rerun_of else None,
        tests=[] if rerun_of else None,
    )


class TestDistroFamily(unittest.TestCase):
    def test_family_from_compose_prefix(self):
        from enge.compare.engine import distro_family

        cases = {
            "RHEL-9.10.0-20261101.n.0": "rhel",
            "RHEL-8-rhui": "rhel",
            "CentOS-Stream-9": "centos",
            ALMA: "alma",
            "AlmaLinux OS 9.7.20251118": "alma",
            ROCKY: "rocky",
            "Oracle:Oracle-Linux:ol98-lvm-gen2:9.8.2": "oracle",
            "Fedora-41": None,
            "": None,
            None: None,
        }
        for compose, family in cases.items():
            with self.subTest(compose=compose):
                self.assertEqual(distro_family(compose), family)

    def test_column_exposes_its_family(self):
        self.assertEqual(_col("a", ALMA, "PASSED").distro, "alma")
        self.assertIsNone(_col("n", None, "PASSED").distro)


class TestDistroCoordinate(unittest.TestCase):
    def test_a_pass_on_one_distro_does_not_hide_a_fail_on_another(self):
        from enge.compare.engine import consolidate_row

        cols = (_col("a", ALMA, "FAILED"), _col("r", ROCKY, "PASSED"))
        self.assertEqual(consolidate_row(cols, ("FAILED", "PASSED")), "FAILED")

    def test_a_rerun_of_one_distro_still_passes_on_retry(self):
        from enge.compare.engine import consolidate_row

        cols = (
            _col("a1", ALMA, "FAILED", at="2026-11-01T00"),
            _col("a2", ALMA_LATER, "PASSED", at="2026-11-02T00"),
        )
        self.assertEqual(consolidate_row(cols, ("FAILED", "PASSED")), "PASSED")

    def test_columns_without_a_compose_keep_the_release_coordinate(self):
        from enge.compare.engine import consolidate_row

        cols = (
            _col("n1", None, "FAILED", at="2026-11-01T00"),
            _col("n2", None, "PASSED", at="2026-11-02T00"),
        )
        self.assertEqual(consolidate_row(cols, ("FAILED", "PASSED")), "PASSED")

    def test_splitpath_puts_each_distro_in_its_own_table(self):
        from enge.compare.engine import build_tables

        tables = build_tables(
            [_col("r", ROCKY, "PASSED"), _col("a", ALMA, "FAILED")],
            show_tests=False,
            splitarch=False,
            splitpath=True,
        )
        self.assertEqual(
            [(t.distro, t.source, t.target) for t in tables],
            [("alma", "9.7", "10.1"), ("rocky", "9.7", "10.1")],
        )
        self.assertEqual([t.rows[0].consolidated for t in tables], ["FAILED", "PASSED"])

    def test_columns_of_one_distro_stay_adjacent(self):
        from enge.compare.engine import build_tables

        (table,) = build_tables(
            [
                _col("a1", ALMA, "FAILED", at="2026-11-01T00"),
                _col("r", ROCKY, "PASSED", at="2026-11-02T00"),
                _col("a2", ALMA_LATER, "PASSED", at="2026-11-03T00"),
            ],
            show_tests=False,
            splitarch=False,
            splitpath=False,
        )
        self.assertEqual([c.task_id for c in table.columns], ["a1", "a2", "r"])

    def test_lineage_consolidated_keeps_distros_apart(self):
        from enge.compare.engine import build_lineage_tables

        columns = [_col("a", ALMA, "FAILED"), _col("r", ROCKY, "PASSED")]
        (table,) = build_lineage_tables(
            columns,
            ["a", "r"],
            show_tests=False,
            splitarch=False,
            splitpath=False,
            retry_count=None,
        )
        self.assertEqual(table.rows[0].consolidated, "FAILED")


class TestDistroLabels(unittest.TestCase):
    """Q-CMP-2: every path label names the family; L71: TASK REFERENCE is
    a header per column with its tasks indented under it."""

    def test_column_header_names_the_distro(self):
        from enge.compare.__main__ import _column_header
        from enge.compare.engine import ComparisonTable

        table = ComparisonTable(
            tier="tier0", columns=(_col("a", ALMA, "PASSED"),), rows=()
        )
        self.assertEqual(_column_header(table, 0), "x86_64\nalma 9.7→10.1\n(1)")

    def test_unknown_compose_keeps_the_bare_path(self):
        from enge.compare.__main__ import _column_header
        from enge.compare.engine import ComparisonTable

        table = ComparisonTable(
            tier="tier0", columns=(_col("n", None, "PASSED"),), rows=()
        )
        self.assertEqual(_column_header(table, 0), "x86_64\n9.7→10.1\n(1)")

    def test_splitpath_title_names_the_distro(self):
        from enge.compare.__main__ import _table_title
        from enge.compare.engine import build_tables

        tables = build_tables(
            [_col("r", ROCKY, "PASSED"), _col("a", ALMA, "FAILED")],
            show_tests=False,
            splitarch=False,
            splitpath=True,
        )
        self.assertEqual(
            [_table_title(t) for t in tables],
            ["alma 9.7→10.1 | tier: tier0", "rocky 9.7→10.1 | tier: tier0"],
        )

    def test_lineage_splitpath_titles_name_the_distro(self):
        from enge.compare.__main__ import _table_title
        from enge.compare.engine import build_lineage_tables

        tables = build_lineage_tables(
            [_col("r", ROCKY, "PASSED"), _col("a", ALMA, "FAILED")],
            ["r", "a"],
            show_tests=False,
            splitarch=False,
            splitpath=True,
            retry_count=None,
        )
        self.assertEqual(
            [_table_title(t) for t in tables],
            ["alma 9.7→10.1 | tier: tier0", "rocky 9.7→10.1 | tier: tier0"],
        )

    def test_normal_task_reference_is_a_header_and_a_run_line(self):
        from enge.compare.__main__ import _footer_lines
        from enge.compare.engine import ComparisonTable

        table = ComparisonTable(
            tier="tier0", columns=(_col("a", ALMA, "PASSED", set_="alma9"),), rows=()
        )
        self.assertEqual(
            _footer_lines(table),
            ["(1) x86_64 alma 9.7→10.1  set alma9", "    run RUN-a  https://a/a"],
        )

    def test_task_reference_without_set_or_url(self):
        from dataclasses import replace

        from enge.compare.__main__ import _footer_lines
        from enge.compare.engine import ComparisonTable

        col = replace(_col("n", None, "PASSED"), set=None, artifacts_url=None)
        table = ComparisonTable(tier="tier0", columns=(col,), rows=())
        self.assertEqual(
            _footer_lines(table), ["(1) x86_64 9.7→10.1", "    run RUN-n  —"]
        )

    def test_mixed_distro_compare_output(self):
        import io
        from unittest.mock import patch

        from rich.console import Console

        from tests._helpers import make_app_context
        from enge.compare import __main__ as compare

        columns = [
            _col("a", ALMA, "FAILED", set_="alma9"),
            _col("r", ROCKY, "PASSED", set_="rocky9"),
        ]
        ctx = make_app_context(
            action="compare",
            extra_cli={
                "run": None,
                "filter_set": None,
                "filter_tier": None,
                "filter_arch": None,
                "filter_tag": None,
                "show_tests": False,
                "splitarch": False,
                "splitpath": False,
                "long": True,
                "jira": False,
            },
        )
        recorder = Console(record=True, width=200, file=io.StringIO())
        with (
            patch.object(compare, "console", recorder),
            patch.object(compare, "load_columns", return_value=(columns, None)),
        ):
            self.assertEqual(compare.main(ctx), 0)
        text = recorder.export_text()
        rows = [
            [cell.strip() for cell in line.split("│")[1:-1]]
            for line in text.splitlines()
            if line.startswith("│")
        ]
        self.assertIn(["", "alma 9.7→10.1", "rocky 9.7→10.1", ""], rows)
        self.assertIn([PLAN, "FAILED", "PASSED", "FAILED"], rows)
        reference = text[text.index("~~~ TASK REFERENCE") :].splitlines()[1:5]
        self.assertEqual(
            [line.rstrip() for line in reference],
            [
                "(1) x86_64 alma 9.7→10.1  set alma9",
                "    run RUN-a  https://a/a",
                "(2) x86_64 rocky 9.7→10.1  set rocky9",
                "    run RUN-r  https://a/r",
            ],
        )


if __name__ == "__main__":
    unittest.main()
