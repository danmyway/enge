"""The run loop's own wait for one generation (contract R7-1, Q-R6-E', Q-R6-I).

Every unfinished task is polled concurrently, one Testing Farm GET per task
per interval; no batch state query exists. A failed poll is retried after
``RETRY_INTERVAL``; a task whose poll fails ``MAX_POLL_FAILURES`` times in a
row is *unreachable*. The wait ends when every task is terminal or
unreachable, or when the generation's timeout passes; the tasks still
running then are *timed out*. Nothing is cancelled and no verdict is
invented for a task that is not terminal.

The clock and the state fetch are injected, so tests never sleep or touch
the network. Workers only fetch; every log line comes from the caller's
thread through ``on_progress``.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from enge.report.results_cache import _is_task_terminal

POLL_INTERVAL = 30.0
RETRY_INTERVAL = 60.0
MAX_POLL_FAILURES = 3
HEARTBEAT_INTERVAL = 15 * 60.0
_MAX_WORKERS = 10


class Clock(Protocol):
    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


# A task's TF state, or None when the poll failed.
FetchState = Callable[[str], Optional[str]]
# (finished, total, running, elapsed seconds); called whenever `finished`
# changes, and as a heartbeat after HEARTBEAT_INTERVAL without a change.
OnProgress = Callable[[int, int, int, float], None]


@dataclass(frozen=True)
class WaitOutcome:
    terminal: Tuple[str, ...]
    unreachable: Tuple[str, ...]
    timed_out: Tuple[str, ...]

    @property
    def unharvested(self) -> Tuple[str, ...]:
        """Tasks with no result yet: the loop must stop (Q-R6-E', Q-R6-I)."""
        return self.unreachable + self.timed_out


class _Wait:
    def __init__(
        self,
        task_ids: Sequence[str],
        fetch_state: FetchState,
        clock: Clock,
        timeout: float,
        on_progress: OnProgress,
    ) -> None:
        self.task_ids = tuple(task_ids)
        self.fetch_state = fetch_state
        self.clock = clock
        self.on_progress = on_progress
        self.start = clock.now()
        self.deadline = self.start + timeout
        self.pending: List[str] = list(self.task_ids)
        self.next_poll: Dict[str, float] = {t: self.start for t in self.task_ids}
        self.failures: Dict[str, int] = {t: 0 for t in self.task_ids}
        self.terminal: List[str] = []
        self.unreachable: List[str] = []
        self.reported = 0
        self.last_report = self.start

    def _record(self, task_id: str, state: Optional[str], now: float) -> None:
        if state is None:
            self.failures[task_id] += 1
            if self.failures[task_id] >= MAX_POLL_FAILURES:
                self.pending.remove(task_id)
                self.unreachable.append(task_id)
            else:
                self.next_poll[task_id] = now + RETRY_INTERVAL
            return
        self.failures[task_id] = 0
        if _is_task_terminal(state):
            self.pending.remove(task_id)
            self.terminal.append(task_id)
        else:
            self.next_poll[task_id] = now + POLL_INTERVAL

    def _poll(self, pool: ThreadPoolExecutor) -> None:
        now = self.clock.now()
        due = [t for t in self.pending if self.next_poll[t] <= now]
        for task_id, state in zip(due, pool.map(self.fetch_state, due)):
            self._record(task_id, state, now)

    def _progress(self) -> None:
        now = self.clock.now()
        finished = len(self.terminal)
        changed = finished != self.reported
        if changed or now - self.last_report >= HEARTBEAT_INTERVAL:
            self.on_progress(
                finished, len(self.task_ids), len(self.pending), now - self.start
            )
            self.reported = finished
            self.last_report = now

    def run(self) -> WaitOutcome:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            while True:
                self._poll(pool)
                self._progress()
                if not self.pending or self.clock.now() >= self.deadline:
                    break
                wake = min(min(self.next_poll[t] for t in self.pending), self.deadline)
                self.clock.sleep(max(0.0, wake - self.clock.now()))
        return WaitOutcome(
            terminal=tuple(self.terminal),
            unreachable=tuple(self.unreachable),
            timed_out=tuple(self.pending),
        )


def wait_for_tasks(
    task_ids: Sequence[str],
    fetch_state: FetchState,
    *,
    clock: Clock,
    timeout: float,
    on_progress: OnProgress,
) -> WaitOutcome:
    """Poll until every task is terminal or unreachable, or `timeout` passes."""
    return _Wait(task_ids, fetch_state, clock, timeout, on_progress).run()
