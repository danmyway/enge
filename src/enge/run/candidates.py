"""What `enge run` reruns after a generation, and the exit code it ends with.

Both read the same lineage the final `enge compare --lineage` table shows:
root, coverage, attempts spent and trajectory all come from `enge.lineage`
(Q-L5-3), so the loop and the table can never disagree.

Candidates (contract v3 §1.4-§1.8), one per task of the generation just
harvested:
  * an unparseable task (ERROR with no plans) whose parent is parsed, or
    that is a root, gets its one uncharged rerun with the original filter;
    a second unparseable result in a row ends the lineage (§1.6);
  * a parsed task reruns, per FAILED/ERROR plan, its failed tests whose key
    still has budget (``spent < retry_count``); a failing plan with no
    failed test is rerun plan-only, against the plan key's own budget;
  * CANCELED and SKIPPED are terminal: never rerun (§1.5);
  * a key whose latest covering task is not this task (a rerun made
    outside the loop) is left alone, so a rerun is never planned twice.

Exit code (Q-R6-F, Q-R6-K, Q-R6-L), over every cell of both table views:
the last value E -> 3, F -> 2, C or "." (no result) -> 4, P or S -> 0,
combined by precedence 3 > 2 > 4 > 0. Tasks with no result at all (timed
out, unreachable, or rejected at gen 0) contribute 4. A lineage the table
has no row for (no task of it ever had a plan result, and it is alone in
its table) is graded by its last task's verdict instead (`rowless_lineages`).
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple, cast

from enge.compare.engine import ExecutionColumn, LineageTable
from enge.lineage import (
    LineageIndex,
    LineageTask,
    is_unparseable,
    last_segment,
)
from enge.rerun.__main__ import RerunCandidate
from enge.utils.globals import ExitCode, worst_exit_code

_RERUN_PLAN_VERDICTS = ("FAILED", "ERROR")
_FAILED_TEST_VERDICTS = ("FAILED", "ERROR")

_CODE_OF_LAST_VALUE: Dict[str, ExitCode] = {
    "E": ExitCode.TEST_ERROR,
    "F": ExitCode.TEST_FAILURE,
    "C": ExitCode.MISSING_RESULTS,
    ".": ExitCode.MISSING_RESULTS,
    "P": ExitCode.SUCCESS,
    "S": ExitCode.SUCCESS,
}


_CODE_OF_LAST_VERDICT: Dict[Optional[str], ExitCode] = {
    "ERROR": ExitCode.TEST_ERROR,
    "FAILED": ExitCode.TEST_FAILURE,
    "CANCELED": ExitCode.MISSING_RESULTS,
    None: ExitCode.MISSING_RESULTS,
    "PASSED": ExitCode.SUCCESS,
    "SKIPPED": ExitCode.SUCCESS,
}


def _no_verdict(_task: object) -> Optional[str]:
    return None


def _unparseable(column: ExecutionColumn) -> bool:
    return is_unparseable(cast(LineageTask, column))


def _due(
    index: LineageIndex,
    task_id: str,
    plan: str,
    segments: Optional[frozenset],
    retry_count: int,
) -> bool:
    """The key has budget left and `task_id` is its newest covering task."""
    root = index.root_of(task_id)
    if index.spent(root, plan, segments) >= retry_count:
        return False
    entries = index.trajectory(root, plan, segments, _no_verdict)
    return bool(entries) and entries[-1].task_id == task_id


def _failed_tests(plan) -> List[str]:
    seen: List[str] = []
    for test in plan.tests:
        segment = last_segment(test.name)
        if test.verdict in _FAILED_TEST_VERDICTS and segment not in seen:
            seen.append(segment)
    return seen


def _parsed_candidate(
    index: LineageIndex, task: ExecutionColumn, retry_count: int
) -> Optional[RerunCandidate]:
    plans: List[str] = []
    tests_by_plan: Dict[str, Tuple[str, ...]] = {}
    plan_only: List[str] = []
    for plan in task.plans:
        if plan.verdict not in _RERUN_PLAN_VERDICTS:
            continue
        failed = _failed_tests(plan)
        if not failed:
            if _due(index, task.task_id, plan.name, None, retry_count):
                plans.append(plan.name)
                plan_only.append(plan.name)
            continue
        kept = tuple(
            segment
            for segment in failed
            if _due(index, task.task_id, plan.name, frozenset({segment}), retry_count)
        )
        if kept:
            plans.append(plan.name)
            tests_by_plan[plan.name] = kept
    if not plans:
        return None
    return RerunCandidate(
        task_id=task.task_id,
        plans=tuple(plans),
        tests_by_plan=tests_by_plan,
        undefined_plans=tuple(plan_only),
    )


def _unparseable_candidate(
    index: LineageIndex, by_id: Dict[str, ExecutionColumn], task: ExecutionColumn
) -> Optional[RerunCandidate]:
    if index.children(task.task_id):
        return None  # already rerun outside the loop
    parent = index.parent_of(task.task_id)
    if parent is not None and _unparseable(by_id[parent]):
        return None  # second unparseable result in a row: the lineage ends [?]
    return RerunCandidate(task_id=task.task_id)


def select_candidates(
    columns: Sequence[ExecutionColumn],
    task_ids: Iterable[str],
    retry_count: int,
) -> List[RerunCandidate]:
    """Rerun candidates among `task_ids` (the generation just harvested).

    `columns` is the lineage closure the loader returns for the loop's root
    run. A task id absent from it (never harvested) yields nothing.
    """
    index = LineageIndex(cast(List[LineageTask], list(columns)))
    by_id = {column.task_id: column for column in columns}
    tasks = sorted(
        (by_id[t] for t in dict.fromkeys(task_ids) if t in by_id),
        key=lambda c: (c.dispatched_at, c.task_id),
    )
    candidates = []
    for task in tasks:
        if _unparseable(task):
            candidate = _unparseable_candidate(index, by_id, task)
        else:
            candidate = _parsed_candidate(index, task, retry_count)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def rowless_lineages(
    tables: Iterable[LineageTable],
) -> List[Tuple[ExecutionColumn, ExecutionColumn]]:
    """(root, last task) of every lineage in a table without rows, once.

    A table has no rows when none of its tasks has a plan result; its
    lineages are then invisible to the cells, in both views.
    """
    found: Dict[str, Tuple[ExecutionColumn, ExecutionColumn]] = {}
    for table in tables:
        if table.rows:
            continue
        for root, lineage in zip(table.columns, table.generations):
            found.setdefault(root.task_id, (root, lineage[-1][1]))
    return list(found.values())


def final_exit_code(
    tables: Iterable[LineageTable], *, without_result: int = 0
) -> ExitCode:
    """The loop's exit code from the final tables (both views)."""
    tables = list(tables)
    code: Optional[ExitCode] = ExitCode.SUCCESS
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                if cell is not None and cell.values:
                    code = worst_exit_code(code, _CODE_OF_LAST_VALUE[cell.values[-1]])
    for _, last in rowless_lineages(tables):
        verdict = _CODE_OF_LAST_VERDICT.get(last.verdict, ExitCode.TEST_ERROR)
        code = worst_exit_code(code, verdict)
    if without_result:
        code = worst_exit_code(code, ExitCode.MISSING_RESULTS)
    return cast(ExitCode, code)
