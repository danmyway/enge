"""Rerun lineage over results.json task entries.

A *lineage* is the tree of tasks linked by ``rerun_of``: a root (a task that
is not a rerun, or whose parent is absent from the input -- an *orphan*) and
every rerun descended from it. This module is pure: it reads task entries
(duck-typed, ``enge.utils.results_parser.TaskEntry`` has every attribute it
needs), does no I/O and logs nothing. It is shared by ``enge compare
--lineage`` (markers) and ``enge run`` (rerun accounting), so both always
agree on what a lineage has spent and how it stands.

Coverage
    Whether a task's dispatched filter covers a ``(plan[, test])`` key is
    *parent-relative*, because native dispatch records ``spec.plan`` -- a tmt
    name REGEX, not exact plan names. A root covers its whole lineage. A
    child whose recorded plans (joined by ``", "``) are all plans its parent
    parsed is a narrowed rerun and covers exactly those. Any other child (the
    fallback rerun of an unparseable task, a rerun that re-records the
    original filter, nothing recorded) inherits its parent's coverage. The
    child's own ``tests`` filter then applies; ``[]`` and ``None`` (written
    by an enge that predates the field) both mean "no test filter".
    ``test_segments`` is ``None`` (a plan key) or a non-empty collection of
    last ``::`` segments; a bare ``str`` is rejected.

Charging
    Attempts spent on a key is the number of child edges in the lineage
    whose dispatched filter covers the key. It is derived, never stored, and
    independent of verdicts -- except that the rerun of an *unparseable*
    parent (``ERROR`` with no plans: terminal, no xunit) is uncharged.

Outcome
    ``exhausted`` (the last result is FAILED/ERROR and the attempts spent
    reached a given ``retry_count``; never reported when ``retry_count`` is
    ``None``), ``unparseable`` (two consecutive unparseable results; a parsed
    result in between resets the streak) or ``terminal-canceled`` (the last
    result is CANCELED). Markers describe how the lineage stands
    NOW: a later generation supersedes an earlier terminal state.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Collection,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

VERDICT_INITIALS = {
    "PASSED": "P",
    "FAILED": "F",
    "ERROR": "E",
    "SKIPPED": "S",
    "CANCELED": "C",
}
NO_RESULT = "."
EXHAUSTED = "exhausted"
UNPARSEABLE = "unparseable"
TERMINAL_CANCELED = "terminal-canceled"
MARKERS = {
    EXHAUSTED: "[X]",
    UNPARSEABLE: "[?]",
    TERMINAL_CANCELED: "[-]",
}


class LineageTask(Protocol):
    """The attributes of a results.json task entry this module reads."""

    @property
    def task_id(self) -> str: ...

    @property
    def rerun_of(self) -> Optional[str]: ...

    @property
    def dispatched_at(self) -> str: ...

    @property
    def verdict(self) -> str: ...

    @property
    def plans(self) -> Sequence[Any]: ...

    @property
    def plan(self) -> Optional[str]: ...

    @property
    def tests(self) -> Optional[Sequence[str]]: ...


VerdictOf = Callable[[Any], Optional[str]]


def is_unparseable(task: LineageTask) -> bool:
    """Terminal with no xunit: the cache state ``ERROR`` with no plans."""
    return task.verdict == "ERROR" and not task.plans


def last_segment(test_name: str) -> str:
    """The last ``::`` segment; a name without ``::`` is returned whole."""
    return test_name.split("::")[-1]


def dispatched_plans(task: LineageTask) -> Optional[FrozenSet[str]]:
    """Plan names recorded on the task, or ``None`` when none were."""
    if not task.plan:
        return None
    return frozenset(task.plan.split(", "))


@dataclass(frozen=True)
class Entry:
    """One generation's result for a key."""

    task_id: str
    generation: int
    value: str
    unparseable: bool


def _order(task: LineageTask) -> Tuple[str, str]:
    return (task.dispatched_at, task.task_id)


class LineageIndex:
    """Lineage structure over a set of tasks; see the module docstring."""

    def __init__(self, tasks: Iterable[LineageTask]) -> None:
        self._tasks: Dict[str, LineageTask] = {}
        for task in tasks:
            if task.task_id in self._tasks:
                raise ValueError(f"duplicate task_id {task.task_id!r}")
            self._tasks[task.task_id] = task

        self._parent: Dict[str, Optional[str]] = {}
        orphans: List[str] = []
        for task_id, task in self._tasks.items():
            parent = task.rerun_of
            if parent and parent in self._tasks:
                self._parent[task_id] = parent
            else:
                self._parent[task_id] = None
                if parent:
                    orphans.append(task_id)
        self.orphans: Tuple[str, ...] = tuple(sorted(orphans))

        self._generation: Dict[str, int] = {}
        self._root: Dict[str, str] = {}
        for task_id in self._tasks:
            self._resolve(task_id)

        self._children: Dict[str, List[LineageTask]] = {t: [] for t in self._tasks}
        self._members: Dict[str, List[LineageTask]] = {}
        for task_id, task in self._tasks.items():
            parent_id = self._parent[task_id]
            if parent_id is not None:
                self._children[parent_id].append(task)
            self._members.setdefault(self._root[task_id], []).append(task)
        for kids in self._children.values():
            kids.sort(key=_order)
        for root_id, members in self._members.items():
            members.sort(key=lambda t: (self._generation[t.task_id], *_order(t)))

    def _resolve(self, task_id: str) -> None:
        """Fill generation/root for ``task_id``; reject a ``rerun_of`` cycle."""
        path: List[str] = []
        seen = set()
        current: Optional[str] = task_id
        while current is not None and current not in self._root:
            if current in seen:
                raise ValueError(f"rerun_of cycle through {current!r}")
            seen.add(current)
            path.append(current)
            current = self._parent[current]
        if current is None:
            root, base = path[-1], 1
            self._root[root] = root
            self._generation[root] = 0
            path.pop()
        else:
            root, base = self._root[current], self._generation[current] + 1
        for offset, tid in enumerate(reversed(path)):
            self._root[tid] = root
            self._generation[tid] = base + offset

    def parent_of(self, task_id: str) -> Optional[str]:
        return self._parent[task_id]

    def root_of(self, task_id: str) -> str:
        return self._root[task_id]

    def generation(self, task_id: str) -> int:
        return self._generation[task_id]

    def roots(self) -> Tuple[str, ...]:
        roots = [self._tasks[t] for t, p in self._parent.items() if p is None]
        return tuple(t.task_id for t in sorted(roots, key=_order))

    def children(self, task_id: str) -> Tuple[LineageTask, ...]:
        return tuple(self._children[task_id])

    def lineage(self, root_id: str) -> Tuple[LineageTask, ...]:
        """Every task whose root is ``root_id``, by generation then time.

        Raises ``ValueError`` when ``root_id`` is not a lineage root; resolve
        a task to its root with ``root_of`` first.
        """
        self._require_root(root_id)
        return tuple(self._members[root_id])

    def _require_root(self, root_id: str) -> None:
        if self._root[root_id] != root_id:
            raise ValueError(f"{root_id!r} is not a lineage root")

    @staticmethod
    def _segments(
        test_segments: Optional[Collection[str]],
    ) -> Optional[Collection[str]]:
        if isinstance(test_segments, str):
            raise ValueError(
                "test_segments must be a collection of str, not a bare str: "
                f"{test_segments!r}"
            )
        if test_segments is not None and len(test_segments) == 0:
            raise ValueError("test_segments must be None or non-empty")
        return test_segments

    def _plan_covered(self, task_id: str, plan: str) -> bool:
        while True:
            parent_id = self._parent[task_id]
            if parent_id is None:
                return True  # a root/orphan is its lineage's origin
            names = dispatched_plans(self._tasks[task_id])
            parent_plans = {p.name for p in self._tasks[parent_id].plans}
            if names and names <= parent_plans:
                return plan in names  # narrowed rerun
            task_id = parent_id  # inherits the parent's coverage

    def covers(
        self,
        task_id: str,
        plan: str,
        test_segments: Optional[Collection[str]] = None,
    ) -> bool:
        """Whether the task's dispatched filter covers the key."""
        segments = self._segments(test_segments)
        if not self._plan_covered(task_id, plan):
            return False
        task = self._tasks[task_id]
        if segments is None or not task.tests:
            return True
        return any(s in task.tests for s in segments)

    def spent(
        self,
        root_id: str,
        plan: str,
        test_segments: Optional[Collection[str]] = None,
    ) -> int:
        """Attempts charged to the key; reruns of unparseable parents are free."""
        self._require_root(root_id)
        self._segments(test_segments)
        count = 0
        for task in self.lineage(root_id):
            parent_id = self._parent[task.task_id]
            if parent_id is None or is_unparseable(self._tasks[parent_id]):
                continue
            if self.covers(task.task_id, plan, test_segments):
                count += 1
        return count

    def trajectory(
        self,
        root_id: str,
        plan: str,
        test_segments: Optional[Collection[str]],
        verdict_of: VerdictOf,
    ) -> Tuple[Entry, ...]:
        """Per-generation results of the key over the tasks that cover it."""
        self._require_root(root_id)
        entries: List[Entry] = []
        for task in self.lineage(root_id):
            if not self.covers(task.task_id, plan, test_segments):
                continue
            generation = self._generation[task.task_id]
            if is_unparseable(task):
                entries.append(Entry(task.task_id, generation, "E", True))
                continue
            verdict = verdict_of(task)
            if verdict is None:
                value = NO_RESULT
            elif verdict in VERDICT_INITIALS:
                value = VERDICT_INITIALS[verdict]
            else:
                raise ValueError(f"unknown verdict {verdict!r} on {task.task_id!r}")
            entries.append(Entry(task.task_id, generation, value, False))
        return tuple(entries)

    def outcome(
        self,
        root_id: str,
        plan: str,
        test_segments: Optional[Collection[str]],
        verdict_of: VerdictOf,
        retry_count: Optional[int],
    ) -> Optional[str]:
        """How the key's lineage stands now, or ``None`` when it is open."""
        if retry_count is not None and (
            isinstance(retry_count, bool)
            or not isinstance(retry_count, int)
            or retry_count < 0
        ):
            raise ValueError(
                f"retry_count must be None or an int >= 0: {retry_count!r}"
            )
        entries = self.trajectory(root_id, plan, test_segments, verdict_of)
        if not entries:
            return None
        streak = 0
        for entry in reversed(entries):
            if not entry.unparseable:
                break
            streak += 1
        if streak >= 2:
            return UNPARSEABLE
        if streak == 1:
            return None  # an uncharged rerun is still due
        last = entries[-1].value
        if last == "C":
            return TERMINAL_CANCELED
        if (
            last in ("F", "E")
            and retry_count is not None
            and self.spent(root_id, plan, test_segments) >= retry_count
        ):
            return EXHAUSTED
        return None
