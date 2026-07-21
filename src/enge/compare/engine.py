"""Pure consolidation/flakiness engine for `enge compare`.

No I/O here -- this module only groups and consolidates already-loaded
`ExecutionColumn` records (see `enge.compare.loader` for how those get
built from results.json caches). Kept import-free of manifest/results.json
I/O so the ratified grouping and consolidation-policy contracts can be
pinned by direct unit tests (see CLAUDE.md "Results.json format" and the
fire-time compare-subcommand session log for the full ratified design).

Grouping contract:
- `set` is NEVER a grouping coordinate (sets are invocation wrappers).
- Tier is a hard partition: both `group_by_coordinate` and `group_by_tier`
  always include tier in their key, so no group can ever span two tiers.
- Consolidation mode groups by (tier, arch, source, target) -- `source`/
  `target` are the run-envelope upgrade-path values (e.g. "9.9"/"10.3"),
  not the per-task `source_compose`/`target_compose` (which can change
  run-to-run on a respin; those stay column/footer metadata only).
- Flakiness mode groups by tier only; arch/source/target fold into columns
  within that one table.

Consolidation policy (fence-critical, do not "align" with the
results_parser/results_cache Verdict severity-rank table -- that table
ranks ERROR>FAILED>CANCELED>PASSED>SKIPPED for a different purpose
(deriving ONE representative verdict for an entire run) and is explicitly
off-limits here):
- Per row, over its PRESENT columns only (`-` never participates): any
  PASSED wins; otherwise the chronologically latest present column's
  verdict reports.
- l0 (plan) rows consolidate on plan verdicts directly, never derived from
  rolled-up test verdicts.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from enge.utils.globals import ExitCode, worst_exit_code

ABSENT = "-"
FLAKINESS_ABSENT = "—"  # em dash -- flakiness-mode absence marker
# (AMENDMENT-2, 2026-07-21): arch-exclusive/excluded plans are structurally
# absent, not SKIPPED/ERROR; maintainer-ratified for visual prominence,
# distinct from consolidation mode's `-` ABSENT (unchanged, off-limits).

_VERDICT_TO_EXIT_CODE: Dict[str, ExitCode] = {
    "PASSED": ExitCode.SUCCESS,
    "SKIPPED": ExitCode.SUCCESS,
    "FAILED": ExitCode.TEST_FAILURE,
    "ERROR": ExitCode.TEST_ERROR,
    "CANCELED": ExitCode.MISSING_RESULTS,
}


@dataclass(frozen=True)
class ExecutionColumn:
    """One results.json task entry, destined to render as one comparison
    table column."""

    task_id: str
    run_id: str
    set: str
    tier: str
    arch: str
    source: str
    target: str
    source_compose: Optional[str]
    target_compose: Optional[str]
    dispatched_at: str
    run_created_at: str
    plans: List[Any] = field(default_factory=list)
    artifacts_url: Optional[str] = None


@dataclass(frozen=True)
class RowResult:
    """One plan (l0) or test (l1) row of a comparison table."""

    label: str
    plan_label: Optional[str]
    per_column: Tuple[str, ...]
    consolidated: Optional[str]
    flaky: Optional[bool] = None


@dataclass(frozen=True)
class ComparisonTable:
    tier: str
    mode: str  # "consolidation" | "flakiness"
    columns: Tuple[ExecutionColumn, ...]
    rows: Tuple[RowResult, ...]
    arch: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None


def _chrono_key(col: ExecutionColumn) -> Tuple[str, str]:
    return (col.dispatched_at, col.run_created_at)


def group_by_coordinate(
    columns: List[ExecutionColumn],
) -> Dict[Tuple[str, str, str, str], List[ExecutionColumn]]:
    """Consolidation-mode grouping: (tier, arch, source, target), `set`
    excluded. Each group's columns are sorted chronologically (ascending)
    so `consolidate_row`'s "latest present wins" fallback can simply take
    the last present entry."""
    groups: Dict[Tuple[str, str, str, str], List[ExecutionColumn]] = {}
    for col in columns:
        key = (col.tier, col.arch, col.source, col.target)
        groups.setdefault(key, []).append(col)
    for cols in groups.values():
        cols.sort(key=_chrono_key)
    return groups


def group_by_tier(columns: List[ExecutionColumn]) -> Dict[str, List[ExecutionColumn]]:
    """Flakiness-mode grouping: tier only. Columns are ordered by
    (arch, source, target, dispatched_at, run_created_at) so
    same-coordinate executions stay visually adjacent."""
    groups: Dict[str, List[ExecutionColumn]] = {}
    for col in columns:
        groups.setdefault(col.tier, []).append(col)
    for cols in groups.values():
        cols.sort(
            key=lambda c: (
                c.arch,
                c.source,
                c.target,
                c.dispatched_at,
                c.run_created_at,
            )
        )
    return groups


def consolidate_row(per_column: Tuple[str, ...]) -> Optional[str]:
    """PASS-wins, else latest-present-wins. `per_column` must already be in
    chronological column order (as produced by group_by_coordinate).
    Returns None if the row has no present column at all (should not
    happen structurally -- a row only exists because it appeared
    somewhere)."""
    present = [v for v in per_column if v != ABSENT]
    if not present:
        return None
    if "PASSED" in present:
        return "PASSED"
    return present[-1]


def _plan_names(columns: List[ExecutionColumn]) -> List[str]:
    return sorted({plan.name for col in columns for plan in col.plans})


def _plan_test_keys(columns: List[ExecutionColumn]) -> List[Tuple[str, str]]:
    keys = set()
    for col in columns:
        for plan in col.plans:
            for test in plan.tests:
                keys.add((plan.name, test.name))
    return sorted(keys)


def _row_is_flaky(per_column: Tuple[str, ...], absent: str) -> bool:
    """Flakiness-mode flag (AMENDMENT-2, 2026-07-21): flagged iff >=2
    present (non-absent) outcomes differ. A single present outcome is
    never flagged; absence never contributes to nor suppresses a flag --
    arch-exclusivity exempts nothing, only cell presence matters."""
    present = {v for v in per_column if v != absent}
    return len(present) > 1


def build_rows(
    columns: List[ExecutionColumn], *, show_tests: bool, consolidate: bool
) -> List[RowResult]:
    absent = ABSENT if consolidate else FLAKINESS_ABSENT
    if not show_tests:
        return _build_plan_rows(columns, consolidate=consolidate, absent=absent)
    return _build_test_rows(columns, consolidate=consolidate, absent=absent)


def _build_plan_rows(
    columns: List[ExecutionColumn], *, consolidate: bool, absent: str
) -> List[RowResult]:
    rows = []
    for name in _plan_names(columns):
        per_column = tuple(
            next((p.verdict for p in col.plans if p.name == name), absent)
            for col in columns
        )
        consolidated = consolidate_row(per_column) if consolidate else None
        flaky = None if consolidate else _row_is_flaky(per_column, absent)
        rows.append(
            RowResult(
                label=name,
                plan_label=None,
                per_column=per_column,
                consolidated=consolidated,
                flaky=flaky,
            )
        )
    return rows


def _build_test_rows(
    columns: List[ExecutionColumn], *, consolidate: bool, absent: str
) -> List[RowResult]:
    rows = []
    for plan_name, test_name in _plan_test_keys(columns):
        per_column_list = []
        for col in columns:
            verdict = absent
            for plan in col.plans:
                if plan.name != plan_name:
                    continue
                for test in plan.tests:
                    if test.name == test_name:
                        verdict = test.verdict
                break
            per_column_list.append(verdict)
        per_column = tuple(per_column_list)
        consolidated = consolidate_row(per_column) if consolidate else None
        flaky = None if consolidate else _row_is_flaky(per_column, absent)
        rows.append(
            RowResult(
                label=test_name,
                plan_label=plan_name,
                per_column=per_column,
                consolidated=consolidated,
                flaky=flaky,
            )
        )
    return rows


def build_consolidation_tables(
    columns: List[ExecutionColumn], *, show_tests: bool
) -> List[ComparisonTable]:
    groups = group_by_coordinate(columns)
    tables = []
    for (tier, arch, source, target), cols in sorted(groups.items()):
        rows = build_rows(cols, show_tests=show_tests, consolidate=True)
        tables.append(
            ComparisonTable(
                tier=tier,
                mode="consolidation",
                arch=arch,
                source=source,
                target=target,
                columns=tuple(cols),
                rows=tuple(rows),
            )
        )
    return tables


def build_flakiness_tables(
    columns: List[ExecutionColumn], *, show_tests: bool
) -> List[ComparisonTable]:
    groups = group_by_tier(columns)
    tables = []
    for tier, cols in sorted(groups.items()):
        rows = build_rows(cols, show_tests=show_tests, consolidate=False)
        tables.append(
            ComparisonTable(
                tier=tier,
                mode="flakiness",
                columns=tuple(cols),
                rows=tuple(rows),
            )
        )
    return tables


def derive_exit_code(tables: List[ComparisonTable]) -> ExitCode:
    """Consolidation-mode only: worst mapped ExitCode across every table's
    every row's consolidated verdict, reduced via the existing
    ExitCode-domain `worst_exit_code` (TEST_ERROR > TEST_FAILURE >
    MISSING_RESULTS > SUCCESS) -- not the Verdict-domain severity table."""
    worst: Optional[ExitCode] = None
    for table in tables:
        for row in table.rows:
            if not row.consolidated:
                continue
            code = _VERDICT_TO_EXIT_CODE[row.consolidated]
            worst = worst_exit_code(worst, code)
    return worst if worst is not None else ExitCode.SUCCESS
