"""RED tests for the enge compare consolidation engine
(enge.compare.engine).

Pure, I/O-free contract tests for the ratified design (see CLAUDE.md
"Results.json format" and the fire-time compare-subcommand session log):

- Grouping: `set` is never a grouping coordinate; tier is a hard partition
  (group_by_coordinate's key always includes tier, so no group ever spans
  two tiers); consolidation-mode groups are keyed by
  (tier, arch, source, target).
- Consolidation policy: any PASSED among a row's present columns wins;
  otherwise the chronologically latest present column's verdict reports;
  a `-` (absent) cell never participates; l0 (plan) rows consolidate on
  plan verdicts directly, never derived from test-level consolidation.
- Flakiness mode: one group per tier only (arch/source/target fold into
  columns); rows never carry a consolidated value.
- Exit-code derivation: worst mapped ExitCode across all consolidated
  values, using ExitCode-domain `worst_exit_code` -- NOT the
  results_parser/results_cache Verdict severity-rank table.

Fixtures are inline builder functions in this module (not files under
tests/fixtures/compare/) -- these are test inputs, not pinned contracts,
matching the precedent set by tests/test_results_cache.py.
"""

import unittest

from enge.utils.globals import ExitCode
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


class TestGroupByCoordinate(unittest.TestCase):
    """Consolidation-mode grouping contract."""

    def test_set_is_excluded_from_the_coordinate_key(self):
        from enge.compare.engine import group_by_coordinate

        col_a = _column(
            task_id="t1", set_name="setA", dispatched_at="2026-07-01T00:00:00Z"
        )
        col_b = _column(
            task_id="t2", set_name="setB", dispatched_at="2026-07-02T00:00:00Z"
        )

        groups = group_by_coordinate([col_a, col_b])

        # Same (tier, arch, source, target) but different `set` names must
        # land in the SAME group -- set is never part of the key.
        self.assertEqual(len(groups), 1)
        (only_group,) = groups.values()
        self.assertEqual({c.task_id for c in only_group}, {"t1", "t2"})

    def test_tier_is_a_hard_partition(self):
        from enge.compare.engine import group_by_coordinate

        col_tier0 = _column(task_id="t1", tier="tier0")
        col_tier1 = _column(task_id="t2", tier="tier1")

        groups = group_by_coordinate([col_tier0, col_tier1])

        self.assertEqual(len(groups), 2)
        for key, cols in groups.items():
            tiers_in_group = {c.tier for c in cols}
            self.assertEqual(
                len(tiers_in_group),
                1,
                "a single coordinate group must never span two tiers",
            )

    def test_distinct_arch_source_target_produce_distinct_groups(self):
        from enge.compare.engine import group_by_coordinate

        col_a = _column(task_id="t1", arch="x86_64")
        col_b = _column(task_id="t2", arch="s390x")
        col_c = _column(task_id="t3", source="10.3", target="10.4")

        groups = group_by_coordinate([col_a, col_b, col_c])

        self.assertEqual(len(groups), 3)

    def test_columns_within_a_group_are_sorted_chronologically(self):
        from enge.compare.engine import group_by_coordinate

        later = _column(task_id="later", dispatched_at="2026-07-05T00:00:00Z")
        earlier = _column(task_id="earlier", dispatched_at="2026-07-01T00:00:00Z")

        groups = group_by_coordinate([later, earlier])
        (cols,) = groups.values()

        self.assertEqual([c.task_id for c in cols], ["earlier", "later"])


class TestGroupByTier(unittest.TestCase):
    """Flakiness-mode grouping contract: tier only."""

    def test_arch_and_upgrade_path_fold_into_one_table_per_tier(self):
        from enge.compare.engine import group_by_tier

        col_a = _column(task_id="t1", tier="tier1", arch="x86_64")
        col_b = _column(task_id="t2", tier="tier1", arch="s390x")
        col_c = _column(task_id="t3", tier="tier1", source="10.3", target="10.4")

        groups = group_by_tier([col_a, col_b, col_c])

        self.assertEqual(len(groups), 1)
        (cols,) = groups.values()
        self.assertEqual({c.task_id for c in cols}, {"t1", "t2", "t3"})

    def test_still_partitions_across_tiers(self):
        from enge.compare.engine import group_by_tier

        col_tier0 = _column(task_id="t1", tier="tier0")
        col_tier1 = _column(task_id="t2", tier="tier1")

        groups = group_by_tier([col_tier0, col_tier1])

        self.assertEqual(set(groups.keys()), {"tier0", "tier1"})


class TestConsolidationPolicy(unittest.TestCase):
    """Per-row consolidation policy: PASS-wins, else latest-wins; `-` never
    participates; l0 uses plan verdicts directly."""

    def test_any_passed_among_present_columns_wins(self):
        from enge.compare.engine import build_consolidation_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED", [_test("/tests/x", "FAILED")])],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED", [_test("/tests/x", "PASSED")])],
            ),
            _column(
                task_id="t3",
                dispatched_at="2026-07-03T00:00:00Z",
                plans=[_plan("/plans/p1", "ERROR", [_test("/tests/x", "ERROR")])],
            ),
        ]

        (table,) = build_consolidation_tables(cols, show_tests=False)
        (row,) = table.rows

        self.assertEqual(row.consolidated, "PASSED")

    def test_latest_wins_when_no_passed_present(self):
        from enge.compare.engine import build_consolidation_tables

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

        (table,) = build_consolidation_tables(cols, show_tests=False)
        (row,) = table.rows

        self.assertEqual(row.consolidated, "FAILED")

    def test_latest_present_wins_when_entity_absent_from_the_newest_column(self):
        from enge.compare.engine import build_consolidation_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[],  # plan filter changed; /plans/p1 absent here
            ),
        ]

        (table,) = build_consolidation_tables(cols, show_tests=False)
        (row,) = table.rows

        self.assertEqual(row.per_column[1], "-")
        self.assertEqual(row.consolidated, "FAILED")

    def test_absent_cell_never_participates_in_pass_wins_check(self):
        from enge.compare.engine import build_consolidation_tables

        # Column 2 has no /plans/p1 at all ("-"); if "-" ever leaked into
        # the PASS-wins check as a truthy/comparable verdict this would
        # corrupt the row's consolidated value.
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

        (table,) = build_consolidation_tables(cols, show_tests=False)
        (row,) = table.rows

        self.assertEqual(row.per_column, ("ERROR", "-", "FAILED"))
        self.assertEqual(row.consolidated, "FAILED")

    def test_l0_consolidates_on_plan_verdicts_directly_not_from_tests(self):
        from enge.compare.engine import build_consolidation_tables

        # Plan-level verdict is FAILED even though the (only) test under it
        # PASSED -- e.g. a plan-level infra hiccup after tests completed.
        # l0 must report the plan's own verdict, not something derived by
        # rolling up test verdicts.
        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED", [_test("/tests/x", "PASSED")])],
            ),
        ]

        (table,) = build_consolidation_tables(cols, show_tests=False)
        (row,) = table.rows

        self.assertEqual(row.consolidated, "FAILED")

    def test_show_tests_rows_are_keyed_by_plan_and_test_name(self):
        from enge.compare.engine import build_consolidation_tables

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

        (table,) = build_consolidation_tables(cols, show_tests=True)
        rows_by_label = {(r.plan_label, r.label): r for r in table.rows}

        self.assertEqual(
            rows_by_label[("/plans/p1", "/tests/x")].consolidated, "PASSED"
        )
        self.assertEqual(
            rows_by_label[("/plans/p1", "/tests/y")].consolidated, "FAILED"
        )


class TestFlakinessMode(unittest.TestCase):
    def test_flakiness_rows_never_carry_a_consolidated_value(self):
        from enge.compare.engine import build_flakiness_tables

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
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "FAILED")],
            ),
        ]

        (table,) = build_flakiness_tables(cols, show_tests=False)

        self.assertEqual(table.mode, "flakiness")
        for row in table.rows:
            self.assertIsNone(row.consolidated)

    def test_flakiness_produces_one_table_per_tier_only(self):
        from enge.compare.engine import build_flakiness_tables

        cols = [
            _column(task_id="t1", tier="tier0", arch="x86_64"),
            _column(task_id="t2", tier="tier0", arch="s390x"),
            _column(task_id="t3", tier="tier1", arch="x86_64"),
        ]

        tables = build_flakiness_tables(cols, show_tests=False)

        self.assertEqual({t.tier for t in tables}, {"tier0", "tier1"})
        for t in tables:
            self.assertEqual(len(t.columns), 2 if t.tier == "tier0" else 1)


class TestExitCodeDerivation(unittest.TestCase):
    def test_all_passed_yields_success(self):
        from enge.compare.engine import build_consolidation_tables, derive_exit_code

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
            _column(
                task_id="t2",
                dispatched_at="2026-07-02T00:00:00Z",
                plans=[_plan("/plans/p1", "PASSED")],
            ),
        ]
        tables = build_consolidation_tables(cols, show_tests=False)

        self.assertEqual(derive_exit_code(tables), ExitCode.SUCCESS)

    def test_fail_then_pass_rerun_history_exits_success(self):
        """A row that failed and then passed later must consolidate to
        PASSED (PASS-wins policy) and therefore exit SUCCESS overall."""
        from enge.compare.engine import build_consolidation_tables, derive_exit_code

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
        tables = build_consolidation_tables(cols, show_tests=False)

        self.assertEqual(derive_exit_code(tables), ExitCode.SUCCESS)

    def test_error_outranks_failed(self):
        from enge.compare.engine import build_consolidation_tables, derive_exit_code

        cols = [
            _column(
                task_id="t1",
                arch="x86_64",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/failing", "FAILED")],
            ),
            _column(
                task_id="t2",
                arch="s390x",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/erroring", "ERROR")],
            ),
        ]
        tables = build_consolidation_tables(cols, show_tests=False)

        self.assertEqual(derive_exit_code(tables), ExitCode.TEST_ERROR)

    def test_canceled_only_yields_missing_results(self):
        from enge.compare.engine import build_consolidation_tables, derive_exit_code

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "CANCELED")],
            ),
        ]
        tables = build_consolidation_tables(cols, show_tests=False)

        self.assertEqual(derive_exit_code(tables), ExitCode.MISSING_RESULTS)

    def test_flakiness_mode_always_reports_success_at_the_engine_level(self):
        """Flakiness mode's SUCCESS-unless-usage/data-error default is
        applied by the caller (no consolidated verdicts to derive from at
        all here); the engine simply never computes retval for it."""
        from enge.compare.engine import build_flakiness_tables

        cols = [
            _column(
                task_id="t1",
                dispatched_at="2026-07-01T00:00:00Z",
                plans=[_plan("/plans/p1", "ERROR")],
            ),
        ]
        tables = build_flakiness_tables(cols, show_tests=False)
        # No API to derive an exit code from flakiness tables exists --
        # asserting the shape instead: no row carries a consolidated verdict.
        for t in tables:
            for row in t.rows:
                self.assertIsNone(row.consolidated)


if __name__ == "__main__":
    unittest.main()
