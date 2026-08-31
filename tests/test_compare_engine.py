"""RED tests for the unified `enge compare` engine (enge.compare.engine).

Ratified redesign (compare-redesign-contract-v3 C1/C2/C3/R1/R3/R5, fired
2026-07-22 -- see CLAUDE.md "Compare consolidation policy" pending
rewrite): the old two-mode split (`--flakiness` flag selecting flakiness
vs consolidation tables) is gone. There is now ONE view: a multi-column
comparison table plus an always-present consolidated column.

- Grouping (C2): `set` is never a grouping coordinate. Tier is always a
  hard partition. `--splitarch`/`--splitpath` add arch/path to the table
  key; neither flag folds every (arch, path) coordinate into one
  per-tier table.
- Consolidation (R1): TWO-STAGE. Stage 1, within each (arch, source,
  target) coordinate over its chronological columns: any PASSED wins;
  otherwise the latest REAL result wins. SKIPPED, CANCELED (fire-time
  ruling, 2026-07-22: grouped with SKIPPED as an absence-class verdict
  for consolidation purposes, not a real result), and absent are all
  excluded from this scan. Stage 2, across the coordinates present in a
  row: severity-max ERROR > FAILED > PASSED. A coordinate with no real
  result contributes nothing to stage 2. This is the behavioral delta
  from the old single-stage policy: a PASS on one coordinate no longer
  masks a FAIL/ERROR on another -- only PASS-wins *within* a coordinate's
  own rerun history.
- All-excluded rows (R3, generalized): if NO coordinate produces a real
  result, the row falls back to SKIPPED when >=1 cell is literally
  SKIPPED, else CANCELED when >=1 cell is literally CANCELED (never
  fabricates a PASSED/FAILED/ERROR that did not occur).
- Flakiness (R5): `flaky` stays computed on every row (present cells
  only, AMENDMENT-2 semantics), regardless of split flags; never
  rendered as a column (see test_compare_main.py).
- Absent marker (C3): unified to the em dash `—` everywhere (the old
  consolidation-only `-` marker is gone).

Fixtures are inline builder functions in this module (not files under
tests/fixtures/compare/) -- these are test inputs, not pinned contracts,
matching the precedent set by tests/test_results_cache.py.
"""

import unittest

from enge.utils.results_parser import PlanEntry, TestEntry


def _plan(name, verdict, tests=None):
    return PlanEntry(name=name, verdict=verdict, tests=tests or [])


def _test(name, verdict, duration=1.0):
    return TestEntry(name=name, verdict=verdict, duration_seconds=duration)


def _column(
    *,
    task_id="task-1",
    run_id="01RUN0000000000000000000A",
    set_name="setA",
    tier="tier1",
    arch="x86_64",
    source="9.9",
    target="10.3",
    source_compose="RHEL-9.9.0-1",
    target_compose=None,
    dispatched_at="2026-07-01T00:00:00Z",
    run_created_at="2026-07-01T00:00:00Z",
    plans=None,
    artifacts_url=None,
):
    from enge.compare.engine import ExecutionColumn

    return ExecutionColumn(
        task_id=task_id,
        run_id=run_id,
        set=set_name,
        tier=tier,
        arch=arch,
        source=source,
        target=target,
        source_compose=source_compose,
        target_compose=target_compose,
        dispatched_at=dispatched_at,
        run_created_at=run_created_at,
        plans=plans or [],
        artifacts_url=artifacts_url,
    )


class TestGroupColumns(unittest.TestCase):
    """Table partitioning contract (C2)."""

    def test_set_is_excluded_from_every_grouping_key(self):
        from enge.compare.engine import group_columns

        col_a = _column(task_id="t1", set_name="setA")
        col_b = _column(task_id="t2", set_name="setB")

        groups = group_columns([col_a, col_b], splitarch=True, splitpath=True)

        self.assertEqual(len(groups), 1)
        (only_group,) = groups.values()
        self.assertEqual({c.task_id for c in only_group}, {"t1", "t2"})

    def test_default_folds_arch_and_path_into_one_table_per_tier(self):
        from enge.compare.engine import group_columns

        col_a = _column(task_id="t1", arch="x86_64")
        col_b = _column(task_id="t2", arch="s390x")
        col_c = _column(task_id="t3", source="10.3", target="10.4")

        groups = group_columns([col_a, col_b, col_c], splitarch=False, splitpath=False)

        self.assertEqual(len(groups), 1)
        (cols,) = groups.values()
        self.assertEqual({c.task_id for c in cols}, {"t1", "t2", "t3"})

    def test_splitarch_partitions_by_tier_and_arch(self):
        from enge.compare.engine import group_columns

        col_a = _column(task_id="t1", arch="x86_64")
        col_b = _column(task_id="t2", arch="s390x")
        col_c = _column(task_id="t3", arch="x86_64", source="10.3", target="10.4")

        groups = group_columns([col_a, col_b, col_c], splitarch=True, splitpath=False)

        # x86_64 group folds the two paths together; s390x is separate.
        self.assertEqual(len(groups), 2)
        sizes = sorted(len(cols) for cols in groups.values())
        self.assertEqual(sizes, [1, 2])

    def test_splitpath_partitions_by_tier_and_path(self):
        from enge.compare.engine import group_columns

        col_a = _column(task_id="t1", source="9.9", target="10.3", arch="x86_64")
        col_b = _column(task_id="t2", source="9.9", target="10.3", arch="s390x")
        col_c = _column(task_id="t3", source="10.3", target="10.4", arch="x86_64")

        groups = group_columns([col_a, col_b, col_c], splitarch=False, splitpath=True)

        self.assertEqual(len(groups), 2)
        sizes = sorted(len(cols) for cols in groups.values())
        self.assertEqual(sizes, [1, 2])

    def test_both_flags_partition_by_tier_arch_and_path(self):
        from enge.compare.engine import group_columns

        col_a = _column(task_id="t1", arch="x86_64", source="9.9", target="10.3")
        col_b = _column(task_id="t2", arch="s390x", source="9.9", target="10.3")
        col_c = _column(task_id="t3", arch="x86_64", source="10.3", target="10.4")

        groups = group_columns([col_a, col_b, col_c], splitarch=True, splitpath=True)

        self.assertEqual(len(groups), 3)

    def test_tier_is_always_a_hard_partition_regardless_of_flags(self):
        from enge.compare.engine import group_columns

        col_tier0 = _column(task_id="t1", tier="tier0")
        col_tier1 = _column(task_id="t2", tier="tier1")

        for splitarch in (False, True):
            for splitpath in (False, True):
                groups = group_columns(
                    [col_tier0, col_tier1], splitarch=splitarch, splitpath=splitpath
                )
                self.assertEqual(len(groups), 2)
                for cols in groups.values():
                    self.assertEqual(len({c.tier for c in cols}), 1)

    def test_columns_within_a_group_are_sorted_chronologically(self):
        from enge.compare.engine import group_columns

        later = _column(task_id="later", dispatched_at="2026-07-05T00:00:00Z")
        earlier = _column(task_id="earlier", dispatched_at="2026-07-01T00:00:00Z")

        groups = group_columns([later, earlier], splitarch=True, splitpath=True)
        (cols,) = groups.values()

        self.assertEqual([c.task_id for c in cols], ["earlier", "later"])


class TestTwoStageConsolidation(unittest.TestCase):
    """R1: stage 1 (per coordinate, PASS-wins else latest-real-wins,
    SKIPPED/CANCELED/absent excluded) then stage 2 (severity-max across
    coordinates present in the row)."""

    def test_any_passed_within_a_coordinate_wins_stage1(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
            _column(
                task_id="t3",
                dispatched_at="2026-07-03T00:00:00Z",
                plans=[_plan("/plans/p1", "ERROR")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "PASSED")

    def test_skipped_excluded_from_stage1_scan_fail_then_skip_consolidates_to_fail(
        self,
    ):
        """The one ratified behavioral delta from the old single-stage
        policy: a rerun that came back SKIPPED must not erase a prior
        FAILED -- SKIPPED is excluded from the "latest real" scan
        entirely, so FAILED (the latest *real* result) still wins."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "SKIPPED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.per_column, ("FAILED", "SKIPPED"))
        self.assertEqual(row.consolidated, "FAILED")

    def test_canceled_excluded_from_stage1_scan_same_as_skipped(self):
        """Fire-time ruling (2026-07-22): CANCELED is grouped with
        SKIPPED as an absence-class verdict for consolidation, so it must
        not erase a prior FAILED either."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "CANCELED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "FAILED")

    def test_latest_real_wins_within_a_coordinate_when_no_passed(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "ERROR")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "FAILED")

    def test_absent_cell_never_participates_in_stage1(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "ERROR")],
            ),
            _column(task_id="t2", dispatched_at="2026-07-02T00:00:00Z", plans=[]),
            _column(
                task_id="t3",
                dispatched_at="2026-07-03T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.per_column, ("ERROR", "—", "FAILED"))
        self.assertEqual(row.consolidated, "FAILED")

    def test_stage2_severity_max_error_beats_failed_beats_passed(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                arch="x86_64",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
            _column(
                task_id="t2",
                arch="s390x",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t3",
                arch="aarch64",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "ERROR")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "ERROR")

    def test_pass_on_one_coordinate_does_not_mask_failure_on_another(self):
        """The core semantic fix R1 exists for: a PASS on x86_64 must not
        make a row look all-clear when s390x actually failed."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                arch="x86_64",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
            _column(
                task_id="t2",
                arch="s390x",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "FAILED")

    def test_a_coordinates_rerun_history_can_still_resolve_to_passed(self):
        """Within ONE coordinate's own time series, PASS-wins still
        applies -- a fail-then-pass rerun consolidates that coordinate to
        PASSED, which then correctly wins stage 2 if it's the only
        coordinate in the row."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "PASSED")

    def test_l0_consolidates_on_plan_verdicts_directly_not_from_tests(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED", [_test("/tests/x", "PASSED")])],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "FAILED")

    def test_show_tests_rows_are_keyed_by_plan_and_test_name(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[
                    _plan(
                        "/plans/p1",
                        "FAILED",
                        [_test("/tests/x", "PASSED"), _test("/tests/y", "FAILED")],
                    )
                ],
            ),
        ]

        (table,) = build_tables(cols, show_tests=True, splitarch=False, splitpath=False)
        rows_by_label = {(r.plan_label, r.label): r for r in table.rows}

        self.assertEqual(
            rows_by_label[("/plans/p1", "/tests/x")].consolidated, "PASSED"
        )
        self.assertEqual(
            rows_by_label[("/plans/p1", "/tests/y")].consolidated, "FAILED"
        )

    def test_unknown_path_columns_are_never_merged_into_one_coordinate(self):
        """Bug found via real usage (2026-07-23): a multi-set legacy
        manifest predating the dispatch-context-schema fields has no
        per-task source/target, and item 7's fallback deliberately does
        not apply to multi-set manifests -- so every column's source and
        target are None. Two such columns for the SAME arch (e.g. one
        set covering an 8to9 upgrade, another covering 9to10, both
        genuinely different upgrade paths) must NOT be treated as the
        same stage-1 coordinate just because they share (arch, None,
        None) -- that would let a PASS on one path mask an ERROR on a
        completely different path, exactly the bug this whole two-stage
        redesign exists to prevent. Without a known, matching source AND
        target, two columns are never assumed to be the same coordinate."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                arch="x86_64",
                source=None,
                target=None,
                dispatched_at="2026-07-13T06:13:06Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
            _column(
                task_id="t2",
                arch="x86_64",
                source=None,
                target=None,
                dispatched_at="2026-07-13T06:13:29Z",
                plans=[_plan("/plans/p1", "ERROR")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "ERROR")

    def test_known_matching_path_columns_still_group_as_one_coordinate(self):
        """Sanity companion to the above: when source/target ARE known
        and match, PASS-wins-within-a-coordinate must still apply --
        the fix only withholds the assumption when we lack positive
        evidence, it must not break the normal rerun-history case."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                arch="x86_64",
                source="9.9",
                target="10.3",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                arch="x86_64",
                source="9.9",
                target="10.3",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "PASSED")


class TestAllExcludedRowFallback(unittest.TestCase):
    """R3, generalized: when no coordinate produces a real result, the
    row falls back to SKIPPED (>=1 literal SKIPPED) else CANCELED (>=1
    literal CANCELED) -- never fabricates PASSED/FAILED/ERROR."""

    def test_all_skipped_or_absent_with_one_skipped_consolidates_to_skipped(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "SKIPPED")],
            ),
            _column(task_id="t2", dispatched_at="2026-07-02T00:00:00Z", plans=[]),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "SKIPPED")

    def test_skipped_and_canceled_mixed_with_no_real_result_prefers_skipped(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "SKIPPED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "CANCELED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "SKIPPED")

    def test_all_canceled_with_no_skipped_consolidates_to_canceled(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "CANCELED")],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertEqual(row.consolidated, "CANCELED")


class TestFlakiness(unittest.TestCase):
    """R5: `flaky` stays computed on every row regardless of split flags;
    AMENDMENT-2 semantics (present cells only) are unchanged."""

    def test_flagged_when_two_present_outcomes_differ(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(task_id="t1", arch="x86_64", plans=[_plan("/plans/p1", "PASSED")]),
            _column(task_id="t2", arch="s390x", plans=[_plan("/plans/p1", "FAILED")]),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertTrue(row.flaky)

    def test_not_flagged_with_a_single_present_outcome(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(task_id="t1", arch="x86_64", plans=[_plan("/plans/p1", "PASSED")]),
            _column(task_id="t2", arch="s390x", plans=[]),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )
        (row,) = table.rows

        self.assertFalse(row.flaky)

    def test_flaky_uses_the_same_formula_under_a_split_table(self):
        """Flakiness is inherently about the columns present WITHIN a
        table, so it is not invariant to table partitioning -- splitting
        by arch isolates each arch into its own single-column table,
        where a row can never be flagged (a single present outcome is
        never flaky). What must stay constant across split flags is the
        formula itself (>=2 differing present outcomes), not the
        resulting value. This pins flakiness working correctly within a
        split table: two reruns of the SAME arch that disagree are still
        flagged once splitarch isolates that arch's own table."""
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1",
                run_id="run-a",
                arch="x86_64",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                run_id="run-b",
                arch="x86_64",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
            _column(task_id="t3", arch="s390x", plans=[_plan("/plans/p1", "PASSED")]),
        ]

        split = build_tables(cols, show_tests=False, splitarch=True, splitpath=False)

        x86_table = next(t for t in split if t.arch == "x86_64")
        s390_table = next(t for t in split if t.arch == "s390x")
        (x86_row,) = x86_table.rows
        (s390_row,) = s390_table.rows

        self.assertTrue(x86_row.flaky)
        self.assertFalse(s390_row.flaky)

    def test_arch_exclusive_plan_row_renders_others_as_em_dash(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(
                task_id="t1", arch="x86_64", plans=[_plan("/plans/common", "PASSED")]
            ),
            _column(
                task_id="t2",
                arch="s390x",
                plans=[
                    _plan("/plans/common", "PASSED"),
                    _plan("/plans/x86-only", "FAILED"),
                ],
            ),
        ]

        (table,) = build_tables(
            cols, show_tests=False, splitarch=False, splitpath=False
        )

        row = next(r for r in table.rows if r.label == "/plans/x86-only")
        self.assertIn("—", row.per_column)
        self.assertIn("FAILED", row.per_column)


class TestMixedTieredUntieredSort(unittest.TestCase):
    """RED pin for fix/results-tier-nullable: a null tier must not crash
    build_tables' final sort, and forms its own hard partition (RULING
    Q-T3, maintainer 2026-08-31) rather than folding into a tiered
    table."""

    def test_mixed_tiered_and_untiered_columns_sort_without_raising(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(task_id="t1", tier="tier0"),
            _column(task_id="t2", tier=None),
        ]

        tables = build_tables(cols, show_tests=False, splitarch=False, splitpath=False)

        self.assertEqual(len(tables), 2)
        self.assertEqual([t.tier for t in tables], ["tier0", None])


class TestUnifiedViewShape(unittest.TestCase):
    """C1: no more mode split -- every row always carries both a
    consolidated verdict and a flaky flag."""

    def test_every_row_has_a_non_none_consolidated_value(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(task_id="t1", plans=[_plan("/plans/p1", "PASSED")]),
            _column(task_id="t2", arch="s390x", plans=[_plan("/plans/p1", "FAILED")]),
        ]

        tables = build_tables(cols, show_tests=False, splitarch=False, splitpath=False)

        for table in tables:
            for row in table.rows:
                self.assertIsNotNone(row.consolidated)

    def test_every_row_has_a_bool_flaky_value(self):
        from enge.compare.engine import build_tables

        cols = [
            _column(task_id="t1", plans=[_plan("/plans/p1", "PASSED")]),
        ]

        tables = build_tables(cols, show_tests=False, splitarch=False, splitpath=False)

        for table in tables:
            for row in table.rows:
                self.assertIsInstance(row.flaky, bool)

    def test_comparison_table_no_longer_carries_a_mode_field(self):
        from enge.compare.engine import ComparisonTable

        self.assertNotIn("mode", ComparisonTable.__dataclass_fields__)

    def test_absent_marker_is_the_em_dash(self):
        from enge.compare.engine import ABSENT

        self.assertEqual(ABSENT, "—")


if __name__ == "__main__":
    unittest.main()
