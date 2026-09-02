"""Pure engine for the unified `enge compare` view.

No I/O here -- this module only groups and consolidates already-loaded
`ExecutionColumn` records (see `enge.compare.loader` for how those get
built from results.json caches). Kept import-free of manifest/results.json
I/O so the ratified grouping and consolidation-policy contracts can be
pinned by direct unit tests (see docs/compare-consolidation.md
and the fire-time compare-redesign session log for the full ratified
design).

Unified view (C1): there is no more mode split. Every invocation produces
a multi-column comparison table plus an always-present consolidated
column; `enge.compare.__main__` always returns SUCCESS from the table
content (R4) -- CONFIG_ERROR is reserved for the loader's floor failure,
which is not a comparison result.

Grouping contract (C2):
- `set` is NEVER a grouping coordinate (sets are invocation wrappers).
- Tier is a hard partition: every grouping key includes tier, so no group
  ever spans two tiers.
- `--splitarch` adds arch to the key; `--splitpath` adds (source, target).
  Neither flag folds every (arch, path) coordinate into one per-tier
  table -- the default.

Consolidation policy (fence-critical, do not "align" with the
results_parser/results_cache Verdict severity-rank table -- that table
ranks ERROR>FAILED>CANCELED>PASSED>SKIPPED for a different purpose
(deriving ONE representative verdict for an entire run) and is explicitly
off-limits here):
- TWO-STAGE (R1). Stage 1, within each (arch, source, target) coordinate
  over its chronologically-ordered columns: any PASSED wins; otherwise
  the latest REAL result wins. SKIPPED, CANCELED, and absent are all
  excluded from this scan -- CANCELED is grouped with SKIPPED as an
  absence-class verdict for consolidation purposes (fire-time ruling,
  2026-07-22), not a real result. Stage 2, across the coordinates
  present in a row: severity-max ERROR > FAILED > PASSED. A coordinate
  with no real result contributes nothing to stage 2. This is the one
  behavioral delta from the old single-stage rule: a PASS on one
  coordinate no longer masks a FAIL/ERROR on another -- PASS-wins only
  applies *within* a coordinate's own rerun history.
- All-excluded rows (R3, generalized): if NO coordinate produces a real
  result, the row falls back to SKIPPED when >=1 cell is literally
  SKIPPED, else CANCELED when >=1 cell is literally CANCELED. The
  verdict enum is exhaustive over {PASSED, FAILED, SKIPPED, ERROR,
  CANCELED} and a row only exists because it appeared somewhere
  (non-absent), so this fallback is exhaustive -- never fabricates a
  PASSED/FAILED/ERROR that did not occur.
- l0 (plan) rows consolidate on plan verdicts directly, never derived
  from rolled-up test verdicts.
- `flaky` (R5, AMENDMENT-2 semantics unchanged): flagged iff >=2 present
  (non-absent) outcomes differ, over present cells only. Computed on
  every row regardless of split flags; never rendered as a column.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ABSENT = "—"  # em dash -- unified absence marker (C3)

_STAGE1_EXCLUDED = {"SKIPPED", "CANCELED"}
_STAGE2_RANK: Dict[str, int] = {"PASSED": 0, "FAILED": 1, "ERROR": 2}


@dataclass(frozen=True)
class ExecutionColumn:
    """One results.json task entry, destined to render as one comparison
    table column."""

    task_id: str
    run_id: str
    set: Optional[str]
    tier: Optional[str]
    arch: str
    source: Optional[str]
    target: Optional[str]
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
    consolidated: str
    flaky: bool


@dataclass(frozen=True)
class ComparisonTable:
    tier: Optional[str]
    columns: Tuple[ExecutionColumn, ...]
    rows: Tuple[RowResult, ...]
    arch: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None


def _sortable(value: Optional[str]) -> Tuple[bool, str]:
    """Comparable substitute for an Optional[str] sort key -- plain tuple
    comparison raises TypeError comparing None to str, and a per-task
    descriptor legitimately is None (multi-set legacy manifest, item 7)."""
    return (value is None, value or "")


def _column_sort_key(col: ExecutionColumn) -> Tuple[Any, ...]:
    return (
        col.arch,
        _sortable(col.source),
        _sortable(col.target),
        col.dispatched_at,
        col.run_created_at,
    )


def group_columns(
    columns: List[ExecutionColumn], *, splitarch: bool, splitpath: bool
) -> Dict[Tuple[Any, ...], List[ExecutionColumn]]:
    """Table partitioning (C2). Tier is always in the key. `splitarch`
    adds arch; `splitpath` adds (source, target). Neither flag means one
    table per tier, with every (arch, path) coordinate folding in as
    columns. `set` never participates. Columns within a group are sorted
    by (arch, source, target, dispatched_at, run_created_at) so same-
    coordinate reruns stay chronologically adjacent regardless of which
    axes are actually split."""
    groups: Dict[Tuple[Any, ...], List[ExecutionColumn]] = {}
    for col in columns:
        key: List[Any] = [col.tier]
        if splitarch:
            key.append(col.arch)
        if splitpath:
            key.append((col.source, col.target))
        groups.setdefault(tuple(key), []).append(col)
    for cols in groups.values():
        cols.sort(key=_column_sort_key)
    return groups


def _plan_names(columns: List[ExecutionColumn]) -> List[str]:
    return sorted({plan.name for col in columns for plan in col.plans})


def _plan_test_keys(columns: List[ExecutionColumn]) -> List[Tuple[str, str]]:
    keys = set()
    for col in columns:
        for plan in col.plans:
            for test in plan.tests:
                keys.add((plan.name, test.name))
    return sorted(keys)


def _row_is_flaky(per_column: Tuple[str, ...]) -> bool:
    """AMENDMENT-2, 2026-07-21 (R5: unchanged, kept on the unified view):
    flagged iff >=2 present (non-absent) outcomes differ. A single
    present outcome is never flagged; absence never contributes to nor
    suppresses a flag."""
    present = {v for v in per_column if v != ABSENT}
    return len(present) > 1


def _coordinate_key(col: ExecutionColumn, index: int) -> Tuple[Any, ...]:
    """Two columns are only ever the same stage-1 coordinate when source
    AND target are BOTH known and match. A None source/target means the
    coordinate is unknown (e.g. a multi-set legacy manifest predating the
    dispatch-context-schema fields, where item 7's fallback deliberately
    does not apply) -- and two unknown-path columns are never assumed to
    be the same coordinate just because they share (arch, None, None).
    `index` makes each such column its own singleton bucket instead."""
    if col.source is None or col.target is None:
        return (col.arch, col.source, col.target, index)
    return (col.arch, col.source, col.target)


def consolidate_row(
    cols: Tuple[ExecutionColumn, ...], per_column: Tuple[str, ...]
) -> str:
    """Two-stage consolidation (R1). `cols`/`per_column` must be the same
    length and in the same (already chronologically-sorted) order."""
    by_coordinate: Dict[Tuple[Any, ...], List[str]] = {}
    for index, (col, verdict) in enumerate(zip(cols, per_column)):
        by_coordinate.setdefault(_coordinate_key(col, index), []).append(verdict)

    stage1_results: List[str] = []
    any_skipped = False
    any_canceled = False
    for values in by_coordinate.values():
        if "SKIPPED" in values:
            any_skipped = True
        if "CANCELED" in values:
            any_canceled = True
        real = [v for v in values if v != ABSENT and v not in _STAGE1_EXCLUDED]
        if not real:
            continue
        stage1_results.append("PASSED" if "PASSED" in real else real[-1])

    if stage1_results:
        return max(stage1_results, key=lambda v: _STAGE2_RANK[v])
    if any_skipped:
        return "SKIPPED"
    if any_canceled:
        return "CANCELED"
    raise AssertionError(
        "unreachable: a row only exists because it appeared somewhere, and "
        "PASSED/FAILED/ERROR would have produced a stage1 result -- every "
        "other verdict (SKIPPED, CANCELED) is handled above"
    )


def _build_plan_rows(columns: List[ExecutionColumn]) -> List[RowResult]:
    rows = []
    for name in _plan_names(columns):
        per_column = tuple(
            next((p.verdict for p in col.plans if p.name == name), ABSENT)
            for col in columns
        )
        rows.append(
            RowResult(
                label=name,
                plan_label=None,
                per_column=per_column,
                consolidated=consolidate_row(tuple(columns), per_column),
                flaky=_row_is_flaky(per_column),
            )
        )
    return rows


def _build_test_rows(columns: List[ExecutionColumn]) -> List[RowResult]:
    rows = []
    for plan_name, test_name in _plan_test_keys(columns):
        per_column_list = []
        for col in columns:
            verdict = ABSENT
            for plan in col.plans:
                if plan.name != plan_name:
                    continue
                for test in plan.tests:
                    if test.name == test_name:
                        verdict = test.verdict
                break
            per_column_list.append(verdict)
        per_column = tuple(per_column_list)
        rows.append(
            RowResult(
                label=test_name,
                plan_label=plan_name,
                per_column=per_column,
                consolidated=consolidate_row(tuple(columns), per_column),
                flaky=_row_is_flaky(per_column),
            )
        )
    return rows


def build_rows(columns: List[ExecutionColumn], *, show_tests: bool) -> List[RowResult]:
    if not show_tests:
        return _build_plan_rows(columns)
    return _build_test_rows(columns)


def _safe(value: Optional[str]) -> Tuple[bool, str]:
    return _sortable(value)


def build_tables(
    columns: List[ExecutionColumn],
    *,
    show_tests: bool,
    splitarch: bool,
    splitpath: bool,
) -> List[ComparisonTable]:
    groups = group_columns(columns, splitarch=splitarch, splitpath=splitpath)
    tables = []
    for cols in groups.values():
        rows = build_rows(cols, show_tests=show_tests)
        tables.append(
            ComparisonTable(
                tier=cols[0].tier,
                arch=cols[0].arch if splitarch else None,
                source=cols[0].source if splitpath else None,
                target=cols[0].target if splitpath else None,
                columns=tuple(cols),
                rows=tuple(rows),
            )
        )
    tables.sort(
        key=lambda t: (_safe(t.tier), _safe(t.arch), _safe(t.source), _safe(t.target))
    )
    return tables
