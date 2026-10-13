"""`enge run`: dispatch, wait, harvest and rerun until every key settles.

The loop of contract v3 §4 under the rulings of 2026-10-12/13:

    gen 0:  dispatch -> wait -> harvest -> [every task unparseable? -> exit 3]
    gen G:  index -> candidates -> [none? -> FINAL] -> rerun -> wait -> harvest
    FINAL:  enge compare --lineage --run <root> --retry-count N -> exit code

* Waiting is the loop's own concurrent poll (`enge.run.poller`).
* A generation with any task still unharvested at its end (timed out,
  unreachable, or a harvest that hit a network error) stops the loop:
  what landed is rendered, one WARNING names the resume command, and the
  exit code includes 4 (Q-R6-E', Q-R6-I, Q-R6-M). Nothing is cancelled.
* Before a task counts as unparseable, its run is re-harvested once with
  `--refresh` after RETRY_INTERVAL (D6b).
* A rejected rerun create is never retried; its keys end at their last
  result (Q-R6-C).
* Progress goes to stderr on this module's logger (Q-R6-H); the final
  table is the only stdout (Q-R6-B).
* `--resume <root>` walks the manifests down from the root, harvests every
  run without a finalized cache, and continues from the newest (Q-R6-D),
  under the root's own --retry-count and --timeout unless given again
  (Q-R6-D'; `resolve_policy`). It logs each run it has to wait on.
* The exit code mirrors every key's final state (Q-R6-F, Q-R6-K, Q-R6-L;
  `enge.run.candidates.final_exit_code`).
"""

import argparse
import contextlib
import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set

from enge.dispatch.__main__ import DispatchSubmission
from enge.rerun.__main__ import RerunCandidate, RerunSubmission
from enge.run import poller
from enge.run.candidates import final_exit_code, select_candidates
from enge.utils.errors import NetworkError, ValidationError
from enge.utils.globals import ExitCode

LOGGER = logging.getLogger(__name__)

DEFAULT_RETRY_COUNT = 2
DEFAULT_TIMEOUT_HOURS = 24.0
_COUNTED_VERDICTS = ("PASSED", "FAILED", "ERROR", "CANCELED", "SKIPPED")


class _Phases(Protocol):
    def dispatch(self) -> Optional[DispatchSubmission]: ...

    def task_ids(self, run_id: str) -> List[str]: ...

    def fetch_state(self, task_id: str) -> Optional[str]: ...

    def harvest(self, run_id: str, *, refresh: bool = False) -> None: ...

    def cache(self, run_id: str) -> Any: ...

    def lineage(self, root_run_id: str) -> Any: ...

    def final_tables(self, root_run_id: str, retry_count: int) -> List[Any]: ...

    def rerun(
        self, candidates: Sequence[RerunCandidate], parent_run_id: str
    ) -> RerunSubmission: ...

    def render(self, root_run_id: str, retry_count: int) -> None: ...

    def chain(self, root_run_id: str) -> List[str]: ...


def _is_unparseable(entry: Any) -> bool:
    """A cached task with no xunit: ERROR and no plans (contract §1.6)."""
    return entry.verdict == "ERROR" and not entry.plans


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def format_elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


@dataclass
class _Generation:
    number: int
    run_id: str


class _Stop(Exception):
    """The loop stops early; `without_result` tasks have no result yet."""

    def __init__(self, without_result: int) -> None:
        super().__init__(without_result)
        self.without_result = without_result


class RunLoop:
    def __init__(
        self,
        phases: _Phases,
        *,
        retry_count: int,
        timeout_hours: float,
        clock: poller.Clock,
    ) -> None:
        self.phases = phases
        self.retry_count = retry_count
        self.timeout = timeout_hours * 3600.0
        self.clock = clock
        self.root: Optional[str] = None
        self.without_result = 0
        self._resumed: Set[str] = set()  # the runs --resume found

    def _root(self) -> str:
        assert self.root is not None, "the root run is known once gen 0 exists"
        return self.root

    # --- start ------------------------------------------------------------

    def _dispatch(self) -> Optional[_Generation]:
        """Generation 0; None means nothing was dispatched (exit 1)."""
        result = self.phases.dispatch()
        if result is None:
            return None
        if result.submitted == 0 or result.run_id is None:
            LOGGER.critical("No requests were successfully submitted!")
            return None
        if result.submitted < result.total:
            rejected = result.total - result.submitted
            self.without_result += rejected
            LOGGER.warning(
                "gen 0: %d of %d request(s) failed to submit and have no result",
                rejected,
                result.total,
            )
        self.root = result.run_id
        self._log_start()
        LOGGER.info(
            "gen 0: submitted %d task(s) as run %s", result.submitted, result.run_id
        )
        return _Generation(0, result.run_id)

    def _resume(self, root_run_id: str) -> _Generation:
        chain = self.phases.chain(root_run_id)
        self.root = chain[0]
        self._resumed = set(chain)
        self._log_start()
        for number, run_id in enumerate(chain[:-1]):
            self._wait_and_harvest(_Generation(number, run_id))
        return _Generation(len(chain) - 1, chain[-1])

    def _log_start(self) -> None:
        LOGGER.info(
            "Root run %s. Inspect: enge compare --lineage --run %s --retry-count %d. "
            "Resume: enge run --resume %s",
            self.root,
            self.root,
            self.retry_count,
            self.root,
        )

    # --- one generation -----------------------------------------------------

    def _progress(self, number: int):
        def report(finished: int, total: int, running: int, elapsed: float) -> None:
            LOGGER.info(
                "gen %d: %d/%d finished, %d running, %s",
                number,
                finished,
                total,
                running,
                format_elapsed(elapsed),
            )

        return report

    def _stop(self, count: int, run_id: str) -> _Stop:
        LOGGER.warning(
            "%d task(s) of run %s have no result yet; the loop stopped. "
            "Resume with: enge run --resume %s",
            count,
            run_id,
            self.root,
        )
        return _Stop(count)

    def _harvest(self, run_id: str, *, refresh: bool) -> None:
        try:
            self.phases.harvest(run_id, refresh=refresh)
        except NetworkError as e:
            LOGGER.warning("could not harvest run %s: %s", run_id, e)
            raise self._stop(len(self.phases.task_ids(run_id)), run_id) from e

    def _unparseable(self, run_id: str) -> List[str]:
        cache = self.phases.cache(run_id)
        if cache is None:
            return []
        return [t.task_id for t in cache.results if _is_unparseable(t)]

    def _wait_and_harvest(self, generation: _Generation) -> None:
        cache = self.phases.cache(generation.run_id)
        if cache is not None and cache.verdict is not None:
            return  # finalized: every task is terminal and harvested
        task_ids = self.phases.task_ids(generation.run_id)
        if generation.run_id in self._resumed:
            LOGGER.info(
                "resume: waiting on gen %d run %s (%d task(s))",
                generation.number,
                generation.run_id,
                len(task_ids),
            )
        outcome = poller.wait_for_tasks(
            task_ids,
            self.phases.fetch_state,
            clock=self.clock,
            timeout=self.timeout,
            on_progress=self._progress(generation.number),
        )
        self._harvest(generation.run_id, refresh=False)
        if outcome.unharvested:
            raise self._stop(len(outcome.unharvested), generation.run_id)
        if self._unparseable(generation.run_id):
            self.clock.sleep(poller.RETRY_INTERVAL)
            self._harvest(generation.run_id, refresh=True)  # D6b
        self._log_harvest(generation)

    def _log_harvest(self, generation: _Generation) -> None:
        cache = self.phases.cache(generation.run_id)
        results = cache.results if cache is not None else []
        unparseable = sum(1 for t in results if _is_unparseable(t))
        counts = {v: sum(t.verdict == v for t in results) for v in _COUNTED_VERDICTS}
        LOGGER.info(
            "gen %d: %d passed, %d failed, %d error, %d unparseable, "
            "%d canceled, %d skipped",
            generation.number,
            counts["PASSED"],
            counts["FAILED"],
            counts["ERROR"] - unparseable,
            unparseable,
            counts["CANCELED"],
            counts["SKIPPED"],
        )

    def _rerun(self, generation: _Generation) -> Optional[_Generation]:
        columns, _ = self.phases.lineage(self._root())
        candidates = select_candidates(
            columns, self.phases.task_ids(generation.run_id), self.retry_count
        )
        if not candidates:
            return None
        nxt = generation.number + 1
        tests = sum(len(t) for c in candidates for t in c.tests_by_plan.values())
        plan_only = sum(len(c.undefined_plans) for c in candidates)
        fallback = sum(1 for c in candidates if not c.plans)
        LOGGER.info(
            "gen %d: rerunning %d task(s): %d test key(s), %d plan-only plan(s), "
            "%d unparseable",
            nxt,
            len(candidates),
            tests,
            plan_only,
            fallback,
        )
        result = self.phases.rerun(candidates, generation.run_id)
        if result.failed:
            LOGGER.warning(
                "gen %d: %d of %d rerun request(s) were rejected; their keys end "
                "at their last result",
                nxt,
                result.failed,
                result.payloads,
            )
        if result.run_id is None:
            return None
        LOGGER.info(
            "gen %d: submitted %d task(s) as run %s",
            nxt,
            result.payloads - result.failed,
            result.run_id,
        )
        return _Generation(nxt, result.run_id)

    # --- the loop -------------------------------------------------------------

    def _final(self) -> int:
        root = self._root()
        if self.phases.cache(root) is not None:
            self.phases.render(root, self.retry_count)
        tables = self.phases.final_tables(root, self.retry_count)
        return int(final_exit_code(tables, without_result=self.without_result))

    def _generations(self, generation: Optional[_Generation]) -> None:
        while generation is not None:
            self._wait_and_harvest(generation)
            if generation.number == 0:
                task_ids = self.phases.task_ids(generation.run_id)
                if task_ids and len(self._unparseable(generation.run_id)) == len(
                    task_ids
                ):
                    raise _Abort(generation.run_id)
            generation = self._rerun(generation)

    def run(self, resume: Optional[str] = None) -> int:
        try:
            if resume:
                generation: Optional[_Generation] = self._resume(resume)
            else:
                generation = self._dispatch()
                if generation is None:
                    return int(ExitCode.EXCEPTION)
            self._generations(generation)
        except _Stop as stop:
            self.without_result += stop.without_result
        except _Abort as abort:
            LOGGER.critical(
                "every task of run %s is unparseable (no xunit); the loop "
                "stopped before rerunning anything",
                abort.run_id,
            )
            return int(ExitCode.TEST_ERROR)
        return self._final()


class _Abort(Exception):
    """Gen 0 produced no parseable result at all (contract §1.6 run guard)."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


# --- entry point ----------------------------------------------------------------


def _dispatch_dests() -> List[str]:
    from enge.utils.arg_parser import _add_dispatch_args, _add_tagging_args

    scratch = argparse.ArgumentParser(add_help=False)
    _add_dispatch_args(scratch)
    _add_tagging_args(scratch)
    return [action.dest for action in scratch._actions]


def _check_policy(retry_count: int, timeout: float) -> None:
    if retry_count < 0:
        raise ValidationError("--retry-count must be an integer >= 0")
    if not timeout > 0:
        raise ValidationError("--timeout must be a number of hours > 0")


def validate_run_args(cli_args: Any) -> None:
    """`enge run`'s own flags; dispatch flags are validated by gen 0's
    `enge test` context."""
    _check_policy(
        getattr(cli_args, "retry_count", DEFAULT_RETRY_COUNT),
        getattr(cli_args, "timeout", DEFAULT_TIMEOUT_HOURS),
    )
    if getattr(cli_args, "resume", None):
        from enge.utils.arg_parser import get_arguments

        defaults = get_arguments(["run"])
        given = [
            dest
            for dest in _dispatch_dests()
            if getattr(cli_args, dest, None) != getattr(defaults, dest, None)
        ]
        if given:
            raise ValidationError(
                "--resume continues an existing run; drop the dispatch flags ("
                + ", ".join(given)
                + ")"
            )


@dataclass(frozen=True)
class LoopPolicy:
    retry_count: int
    timeout_hours: float

    def describe(self) -> str:
        return f"--retry-count {self.retry_count} --timeout {self.timeout_hours:g}"


# (namespace dest, flag, default); the dests default to SUPPRESS, so a flag
# that was not given is absent from the namespace.
_POLICY_FLAGS = (
    ("retry_count", "--retry-count", DEFAULT_RETRY_COUNT),
    ("timeout", "--timeout", DEFAULT_TIMEOUT_HOURS),
)


def _with_defaults(values: Dict[str, Any]) -> Dict[str, Any]:
    return {dest: values.get(dest, default) for dest, _, default in _POLICY_FLAGS}


def _policy(values: Dict[str, Any]) -> LoopPolicy:
    merged = _with_defaults(values)
    return LoopPolicy(merged["retry_count"], merged["timeout"])


def _given(namespace: Any) -> Dict[str, Any]:
    return {
        d: getattr(namespace, d) for d, _, _ in _POLICY_FLAGS if hasattr(namespace, d)
    }


def _root_namespace(root_argv: Optional[Sequence[str]]) -> Optional[Any]:
    """The root invocation's namespace; None when its argv does not parse."""
    from enge.utils.arg_parser import get_arguments

    if not root_argv or len(root_argv) < 2:
        return None
    try:
        # argparse prints its usage text before exiting; that is not ours.
        with contextlib.redirect_stderr(io.StringIO()):
            namespace = get_arguments([str(a) for a in root_argv[1:]])
    except SystemExit:
        return None
    return namespace if getattr(namespace, "action", None) else None


def _fmt(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def resolve_policy(
    cli_args: Any, root_run_id: Optional[str], root_argv: Optional[Sequence[str]]
) -> LoopPolicy:
    """The loop's --retry-count and --timeout (Q-R6-D').

    A fresh loop uses its flags or the defaults. `--resume` keeps the
    policy of the root run's own invocation, read from its manifest argv:
    a flag given again wins, and is announced when it differs. A root that
    `enge test` dispatched has no policy, so the flags or defaults apply;
    a root argv that does not parse (or holds an out-of-bounds value)
    falls back to them with a WARNING.
    """
    given = _given(cli_args)
    if not getattr(cli_args, "resume", None):
        return _policy(given)

    root = _root_namespace(root_argv)
    root_values: Dict[str, Any] = {}
    if root is not None and root.action == "run":
        root_values = _with_defaults(_given(root))
        try:
            _check_policy(root_values["retry_count"], root_values["timeout"])
        except ValidationError:
            root = None

    if root is None:
        policy = _policy(given)
        LOGGER.warning(
            "resume: cannot read the loop policy from root run %s's manifest; "
            "using %s",
            root_run_id,
            policy.describe(),
        )
        return policy
    if root.action != "run":
        policy = _policy(given)
        LOGGER.info(
            "resume: root run %s was dispatched by `enge %s`, not `enge run`; "
            "using %s",
            root_run_id,
            root.action,
            policy.describe(),
        )
        return policy

    for dest, flag, _ in _POLICY_FLAGS:
        if dest in given and given[dest] != root_values[dest]:
            LOGGER.info(
                "resume: %s %s overrides root run %s's %s",
                flag,
                _fmt(given[dest]),
                root_run_id,
                _fmt(root_values[dest]),
            )
    return _policy({**root_values, **given})


def main(ctx) -> int:
    from enge.run.phases import Phases

    validate_run_args(ctx.cli_args)
    phases = Phases(ctx)
    resume = getattr(ctx.cli_args, "resume", None)
    root_argv = None
    if resume:
        resume, root_argv = phases.root(resume)
    policy = resolve_policy(ctx.cli_args, resume, root_argv)
    loop = RunLoop(
        phases,
        retry_count=policy.retry_count,
        timeout_hours=policy.timeout_hours,
        clock=SystemClock(),
    )
    return loop.run(resume=resume)
