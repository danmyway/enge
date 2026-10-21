"""`enge.run.__main__.RunLoop`: the generation loop over injected phases.

`FakePhases` scripts what dispatch, Testing Farm, the cache and rerun do;
the clock is fake, so nothing sleeps. Lineage, candidates, the final tables
and the exit code are the real ones (`enge.lineage`, `enge.run.candidates`,
`compare.engine.build_lineage_tables`). Progress lines are pinned verbatim
on the loop's own logger.
"""

import contextlib
import dataclasses
import io
import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from enge.compare import engine as E
from enge.dispatch.__main__ import DispatchSubmission
from enge.rerun.__main__ import RerunSubmission
from enge.utils.errors import NetworkError, ValidationError
from enge.utils.globals import ExitCode
from enge.utils.results_parser import PlanEntry
from enge.utils.results_parser import TestEntry as _TestEntry

from tests._helpers import captured_logs

LOGGER_NAME = "enge.run.__main__"
A = "/plans/a"


def T(name, verdict):
    return _TestEntry(name=name, verdict=verdict, duration_seconds=1.0)


def task(task_id, run_id, at, verdict, *, rerun_of=None, unparseable=False):
    plans = [] if unparseable else [P(verdict)]
    return E.ExecutionColumn(
        task_id=task_id,
        run_id=run_id,
        set="s",
        tier="tier0",
        arch="x86_64",
        source="8.10",
        target="9.6",
        source_compose="RHEL-8.10.0-Nightly",
        target_compose="RHEL-9.6.0-Nightly",
        dispatched_at=at,
        run_created_at=at,
        plans=plans,
        artifacts_url=f"https://a/{task_id}",
        rerun_of=rerun_of,
        verdict="ERROR" if unparseable else verdict,
        plan=A if rerun_of else None,
        tests=["t1"] if rerun_of and not unparseable else [],
    )


def P(verdict):
    return PlanEntry(name=A, verdict=verdict, tests=[T("/t/x.py::t1", verdict)])


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class FakePhases:
    """Runs and their tasks as the loop would find them.

    `runs[run_id]` lists the run's task columns as harvest will cache them;
    `refreshed[run_id]` replaces them on a refresh harvest (D6b); `states`
    gives a task's polled TF states (default: complete); `reruns` is the
    queue of (RerunSubmission, child run's columns) that each rerun returns.
    """

    def __init__(self, gen0, *, total=None, submitted=None):
        self.runs = {"RUN0": gen0}
        self.refreshed = {}
        self.states = {}
        self.reruns = []
        self.harvest_error = {}
        self.cached = {}
        self.calls = []
        self.chain_result = None
        count = len(gen0)
        self.submission = DispatchSubmission(
            run_id="RUN0" if (submitted if submitted is not None else count) else None,
            total=total if total is not None else count,
            submitted=submitted if submitted is not None else count,
            results=(),
        )

    def dispatch(self):
        self.calls.append(("dispatch",))
        return self.submission

    def task_ids(self, run_id):
        return [c.task_id for c in self.runs[run_id]]

    def fetch_state(self, task_id):
        seq = self.states.get(task_id, ["complete"])
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def harvest(self, run_id, *, refresh=False):
        self.calls.append(("harvest", run_id, refresh))
        if run_id in self.harvest_error:
            raise self.harvest_error[run_id]
        columns = self.refreshed.get(run_id) if refresh else None
        columns = columns or self.runs[run_id]
        landed = [c for c in columns if self._finished(c.task_id)]
        self.cached[run_id] = landed

    def _finished(self, task_id):
        return self.states.get(task_id, ["complete"])[-1] == "complete"

    def cache(self, run_id):
        if run_id not in self.cached:
            return None
        landed = self.cached[run_id]
        done = len(landed) == len(self.runs[run_id])
        return SimpleNamespace(verdict="FAILED" if done else None, results=landed)

    def lineage(self, root):
        columns = [c for landed in self.cached.values() for c in landed]
        selected = [c.task_id for c in self.cached.get(root, [])]
        return columns, selected

    def final_tables(self, root, retry_count):
        columns, selected = self.lineage(root)
        if not columns:
            return []
        return [
            t
            for show_tests in (False, True)
            for t in E.build_lineage_tables(
                columns,
                selected,
                show_tests=show_tests,
                splitarch=False,
                splitpath=False,
                retry_count=retry_count,
            )
        ]

    def rerun(self, candidates, parent_run_id):
        self.calls.append(("rerun", parent_run_id, tuple(candidates)))
        submission, columns = self.reruns.pop(0)
        if submission.run_id:
            self.runs[submission.run_id] = columns
        return submission

    def render(self, root, retry_count):
        self.calls.append(("render", root, retry_count))

    def chain(self, root):
        if isinstance(self.chain_result, Exception):
            raise self.chain_result
        return self.chain_result


def _run(phases, *, retry_count=2, timeout_hours=24.0, resume=None):
    from enge.run.__main__ import RunLoop

    clock = FakeClock()
    loop = RunLoop(
        phases, retry_count=retry_count, timeout_hours=timeout_hours, clock=clock
    )
    with captured_logs(LOGGER_NAME, logging.INFO) as records:
        code = loop.run(resume=resume)
    lines = [(r.levelname, r.getMessage()) for r in records]
    return code, lines, clock


def _rerun_run(run_id, task_id, parent, verdict, at, **kwargs):
    child = task(task_id, run_id, at, verdict, rerun_of=parent, **kwargs)
    return (RerunSubmission(run_id=run_id, payloads=1, failed=0), [child])


ROOT_LINE = (
    "INFO",
    "Root run RUN0. Inspect: enge compare --lineage --run RUN0 --retry-count 2. "
    "Resume: enge run --resume RUN0",
)


class TestGenerations(unittest.TestCase):
    def test_a_failure_that_passes_on_retry(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.reruns.append(_rerun_run("RUN1", "c1", "r", "PASSED", "2026-10-02T00"))

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertEqual(
            lines,
            [
                ROOT_LINE,
                ("INFO", "gen 0: submitted 1 task(s) as run RUN0"),
                ("INFO", "gen 0: 1/1 finished, 0 running, 0m"),
                (
                    "INFO",
                    "gen 0: 0 passed, 1 failed, 0 error, 0 unparseable, "
                    "0 canceled, 0 skipped",
                ),
                (
                    "INFO",
                    "gen 1: rerunning 1 task(s): 1 test key(s), 0 plan-only "
                    "plan(s), 0 unparseable",
                ),
                ("INFO", "gen 1: submitted 1 task(s) as run RUN1"),
                ("INFO", "gen 1: 1/1 finished, 0 running, 0m"),
                (
                    "INFO",
                    "gen 1: 1 passed, 0 failed, 0 error, 0 unparseable, "
                    "0 canceled, 0 skipped",
                ),
            ],
        )
        self.assertEqual(phases.calls[-1], ("render", "RUN0", 2))

    def test_an_exhausted_failure_exits_2_after_retry_count_reruns(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.reruns.append(_rerun_run("RUN1", "c1", "r", "FAILED", "2026-10-02T00"))
        phases.reruns.append(_rerun_run("RUN2", "c2", "c1", "FAILED", "2026-10-03T00"))

        code, _, _ = _run(phases)

        self.assertEqual(code, ExitCode.TEST_FAILURE)
        reruns = [c[1] for c in phases.calls if c[0] == "rerun"]
        self.assertEqual(reruns, ["RUN0", "RUN1"])
        self.assertEqual(phases.reruns, [])

    def test_no_rerun_at_retry_count_zero(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])

        code, _, _ = _run(phases, retry_count=0)

        self.assertEqual(code, ExitCode.TEST_FAILURE)
        self.assertNotIn("rerun", [c[0] for c in phases.calls])


class TestRowlessLineages(unittest.TestCase):
    def test_a_lineage_with_no_row_is_graded_and_named(self):
        def tier1(column):
            return dataclasses.replace(column, tier="tier1")

        unparseable = tier1(task("u", "RUN0", "2026-10-01T00", "x", unparseable=True))
        phases = FakePhases([task("p", "RUN0", "2026-10-01T00", "PASSED"), unparseable])
        rerun = tier1(
            task("u2", "RUN1", "2026-10-02T00", "x", rerun_of="u", unparseable=True)
        )
        phases.reruns.append((RerunSubmission("RUN1", payloads=1, failed=0), [rerun]))

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.TEST_ERROR)
        self.assertIn(
            (
                "WARNING",
                "the lineage of task u (s, tier1, x86_64) ended ERROR without a "
                "plan result; the final table has no row for it",
            ),
            lines,
        )


class TestStopRules(unittest.TestCase):
    def test_a_timeout_stops_the_loop_and_exits_with_4_folded_in(self):
        phases = FakePhases(
            [
                task("r1", "RUN0", "2026-10-01T00", "PASSED"),
                task("r2", "RUN0", "2026-10-01T01", "PASSED"),
            ]
        )
        phases.states["r2"] = ["running"]

        code, lines, clock = _run(phases, timeout_hours=1.0)

        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        self.assertEqual(clock.t, 3600.0)
        self.assertIn(
            (
                "WARNING",
                "1 task(s) of run RUN0 have no result yet; the loop stopped. "
                "Resume with: enge run --resume RUN0",
            ),
            lines,
        )
        self.assertEqual(
            [c[0] for c in phases.calls], ["dispatch", "harvest", "render"]
        )

    def test_an_unreachable_task_stops_the_loop(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.reruns.append(_rerun_run("RUN1", "c1", "r", "PASSED", "2026-10-02T00"))
        phases.states["c1"] = [None]

        code, lines, _ = _run(phases)

        # r's FAILED is still the key's last result: 2 outranks the 4.
        self.assertEqual(code, ExitCode.TEST_FAILURE)
        self.assertIn(
            (
                "WARNING",
                "1 task(s) of run RUN1 have no result yet; the loop stopped. "
                "Resume with: enge run --resume RUN0",
            ),
            lines,
        )
        self.assertEqual(len([c for c in phases.calls if c[0] == "rerun"]), 1)

    def test_a_harvest_network_error_stops_the_loop_with_4(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "PASSED")])
        phases.harvest_error["RUN0"] = NetworkError("VPN down")

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        self.assertIn(("WARNING", "could not harvest run RUN0: VPN down"), lines)
        self.assertIn(
            (
                "WARNING",
                "1 task(s) of run RUN0 have no result yet; the loop stopped. "
                "Resume with: enge run --resume RUN0",
            ),
            lines,
        )
        self.assertNotIn("render", [c[0] for c in phases.calls])

    def test_a_harvest_that_caches_nothing_stops_the_loop_with_4(self):
        # Report never fails on a caching error (it logs and returns), so a
        # harvest can return normally and leave no cache behind.
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.harvest = lambda run_id, refresh=False: phases.calls.append(
            ("harvest", run_id, refresh)
        )

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        self.assertIn(
            (
                "WARNING",
                "1 task(s) of run RUN0 have no result yet; the loop stopped. "
                "Resume with: enge run --resume RUN0",
            ),
            lines,
        )
        self.assertNotIn("rerun", [c[0] for c in phases.calls])

    def test_a_harvest_that_caches_some_tasks_stops_the_loop_with_4(self):
        phases = FakePhases(
            [
                task("a", "RUN0", "2026-10-01T00", "PASSED"),
                task("b", "RUN0", "2026-10-01T00", "FAILED"),
            ]
        )
        harvest = phases.harvest

        def caches_only_the_first(run_id, refresh=False):
            harvest(run_id, refresh=refresh)
            phases.cached[run_id] = phases.cached[run_id][:1]

        phases.harvest = caches_only_the_first

        code, lines, _ = _run(phases)

        # b failed but was never cached: neither rerun nor graded 0.
        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        self.assertIn(
            (
                "WARNING",
                "1 task(s) of run RUN0 have no result yet; the loop stopped. "
                "Resume with: enge run --resume RUN0",
            ),
            lines,
        )
        self.assertNotIn("rerun", [c[0] for c in phases.calls])

    def test_a_complete_but_unfinalized_cache_is_graded_from_its_entries(self):
        # Every task entry is cached but the root verdict never got written
        # (report swallows a finalization failure). The task verdicts are
        # all there and grading reads only them, so the loop goes on: a stop
        # would turn complete results into "missing" (exit 4) and a resume
        # would hit the same failure again (re-review R1, declined).
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.reruns.append(_rerun_run("RUN1", "c1", "r", "PASSED", "2026-10-02T00"))
        cache = phases.cache

        def never_finalized(run_id):
            found = cache(run_id)
            return found and SimpleNamespace(verdict=None, results=found.results)

        phases.cache = never_finalized

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertEqual([c[1] for c in phases.calls if c[0] == "rerun"], ["RUN0"])
        self.assertFalse([m for _, m in lines if "the loop stopped" in m])

    def test_a_rejected_rerun_ends_its_keys_at_their_last_result(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.reruns.append((RerunSubmission(run_id=None, payloads=1, failed=1), []))

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.TEST_FAILURE)
        self.assertIn(
            (
                "WARNING",
                "gen 1: 1 of 1 rerun request(s) were rejected; their keys end "
                "at their last result",
            ),
            lines,
        )
        self.assertEqual(phases.calls[-1], ("render", "RUN0", 2))


class TestUnparseable(unittest.TestCase):
    def test_d6b_refreshes_once_before_calling_a_task_unparseable(self):
        phases = FakePhases(
            [task("r", "RUN0", "2026-10-01T00", "PASSED", unparseable=True)]
        )
        phases.refreshed["RUN0"] = [task("r", "RUN0", "2026-10-01T00", "PASSED")]

        code, _, clock = _run(phases)

        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertIn(60.0, clock.sleeps)
        self.assertEqual(
            [c for c in phases.calls if c[0] == "harvest"],
            [("harvest", "RUN0", False), ("harvest", "RUN0", True)],
        )
        self.assertNotIn("rerun", [c[0] for c in phases.calls])

    def test_gen0_with_every_task_unparseable_aborts_with_3(self):
        phases = FakePhases(
            [
                task("r1", "RUN0", "2026-10-01T00", "PASSED", unparseable=True),
                task("r2", "RUN0", "2026-10-01T01", "PASSED", unparseable=True),
            ]
        )

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.TEST_ERROR)
        self.assertIn(
            (
                "CRITICAL",
                "every task of run RUN0 is unparseable (no xunit); the loop "
                "stopped before rerunning anything",
            ),
            lines,
        )
        self.assertNotIn("rerun", [c[0] for c in phases.calls])
        self.assertNotIn("render", [c[0] for c in phases.calls])

    def test_an_unparseable_task_gets_one_uncharged_rerun(self):
        phases = FakePhases(
            [
                task("r1", "RUN0", "2026-10-01T00", "PASSED", unparseable=True),
                task("r2", "RUN0", "2026-10-01T01", "PASSED"),
            ]
        )
        phases.reruns.append(_rerun_run("RUN1", "c1", "r1", "PASSED", "2026-10-02T00"))

        code, lines, _ = _run(phases, retry_count=0)

        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertIn(
            (
                "INFO",
                "gen 1: rerunning 1 task(s): 0 test key(s), 0 plan-only "
                "plan(s), 1 unparseable",
            ),
            lines,
        )


class TestGenerationZero(unittest.TestCase):
    def test_nothing_resolved_exits_1(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "PASSED")])
        phases.submission = None

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.EXCEPTION)
        self.assertEqual(lines, [])

    def test_nothing_accepted_exits_1(self):
        phases = FakePhases(
            [task("r", "RUN0", "2026-10-01T00", "PASSED")], total=2, submitted=0
        )

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.EXCEPTION)
        self.assertEqual(
            lines, [("CRITICAL", "No requests were successfully submitted!")]
        )

    def test_a_partial_rejection_folds_in_4(self):
        phases = FakePhases(
            [task("r", "RUN0", "2026-10-01T00", "PASSED")], total=3, submitted=1
        )

        code, lines, _ = _run(phases)

        self.assertEqual(code, ExitCode.MISSING_RESULTS)
        self.assertIn(
            ("WARNING", "gen 0: 2 of 3 request(s) failed to submit and have no result"),
            lines,
        )


class TestResume(unittest.TestCase):
    def test_resume_harvests_unfinished_runs_and_continues(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        child = task("c1", "RUN1", "2026-10-02T00", "FAILED", rerun_of="r")
        phases.runs["RUN1"] = [child]
        phases.cached["RUN0"] = phases.runs["RUN0"]
        phases.chain_result = ["RUN0", "RUN1"]
        phases.reruns.append(_rerun_run("RUN2", "c2", "c1", "PASSED", "2026-10-03T00"))

        code, lines, _ = _run(phases, resume="RUN0")

        self.assertEqual(code, ExitCode.SUCCESS)
        self.assertNotIn("dispatch", [c[0] for c in phases.calls])
        self.assertEqual(
            [c[1] for c in phases.calls if c[0] == "harvest"], ["RUN1", "RUN2"]
        )
        self.assertEqual(lines[0], ROOT_LINE)
        self.assertIn(
            (
                "INFO",
                "gen 2: rerunning 1 task(s): 1 test key(s), 0 plan-only "
                "plan(s), 0 unparseable",
            ),
            lines,
        )

    def test_resuming_a_settled_loop_dispatches_nothing(self):
        # The loop under its root's policy (--retry-count 1): r failed, its
        # one rerun failed too, so the lineage is exhausted and resume must
        # go straight to FINAL.
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.runs["RUN1"] = [
            task("c1", "RUN1", "2026-10-02T00", "FAILED", rerun_of="r")
        ]
        phases.cached["RUN0"] = phases.runs["RUN0"]
        phases.cached["RUN1"] = phases.runs["RUN1"]
        phases.chain_result = ["RUN0", "RUN1"]

        code, _, _ = _run(phases, retry_count=1, resume="RUN0")

        self.assertEqual(code, ExitCode.TEST_FAILURE)
        self.assertEqual([c[0] for c in phases.calls], ["render"])

    def test_resume_says_what_it_waits_on(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.runs["RUN1"] = [
            task("c1", "RUN1", "2026-10-02T00", "FAILED", rerun_of="r")
        ]
        phases.cached["RUN0"] = phases.runs["RUN0"]
        phases.chain_result = ["RUN0", "RUN1"]
        phases.reruns.append(_rerun_run("RUN2", "c2", "c1", "PASSED", "2026-10-03T00"))

        _, lines, _ = _run(phases, resume="RUN0")

        waiting = [m for _, m in lines if m.startswith("resume: waiting")]
        # RUN0 is finalized (nothing to wait for); RUN2 is the loop's own
        # generation, reported by the usual gen lines.
        self.assertEqual(waiting, ["resume: waiting on gen 1 run RUN1 (1 task(s))"])
        self.assertLess(
            lines.index(("INFO", waiting[0])),
            lines.index(("INFO", "gen 1: 1/1 finished, 0 running, 0m")),
        )

    def test_a_chain_error_propagates(self):
        phases = FakePhases([task("r", "RUN0", "2026-10-01T00", "FAILED")])
        phases.chain_result = ValidationError("run RUN0 has 2 reruns")

        with self.assertRaises(ValidationError):
            _run(phases, resume="RUN0")


ENGE = "/home/u/.local/bin/enge"


def _policy(argv, root_argv=None, root="RUN0"):
    """(retry_count, timeout_hours), log lines, stderr of resolve_policy."""
    from enge.run.__main__ import resolve_policy
    from enge.utils.arg_parser import get_arguments

    cli_args = get_arguments(["run", *argv])
    err = io.StringIO()
    with captured_logs(LOGGER_NAME, logging.INFO) as records:
        with contextlib.redirect_stderr(err):
            policy = resolve_policy(cli_args, root, root_argv)
    lines = [(r.levelname, r.getMessage()) for r in records]
    return (policy.retry_count, policy.timeout_hours), lines, err.getvalue()


class TestResumePolicy(unittest.TestCase):
    """Q-R6-D': `--resume` keeps the loop's policy. A loop started with
    `--retry-count 1` must not get the default 2 on resume: that dispatched
    reruns of keys its own table had already marked exhausted."""

    def test_a_fresh_loop_uses_its_flags_or_the_defaults(self):
        self.assertEqual(
            _policy(["--retry-count", "1"], root=None)[:2], ((1, 24.0), [])
        )
        self.assertEqual(_policy([], root=None)[:2], ((2, 24.0), []))

    def test_resume_inherits_the_roots_policy(self):
        root = [ENGE, "run", "-S", "s", "--retry-count", "1", "--timeout", "6"]
        self.assertEqual(_policy(["--resume", "RUN0"], root)[:2], ((1, 6.0), []))

    def test_a_root_that_set_nothing_had_the_defaults(self):
        root = [ENGE, "run", "-S", "s"]
        self.assertEqual(_policy(["--resume", "RUN0"], root)[:2], ((2, 24.0), []))

    def test_global_flags_before_the_roots_subcommand(self):
        root = [ENGE, "-c", "/c.toml", "-v", "run", "-S", "s", "--retry-count", "1"]
        self.assertEqual(_policy(["--resume", "RUN0"], root)[0], (1, 24.0))

    def test_an_explicit_flag_overrides_and_is_announced(self):
        root = [ENGE, "run", "-S", "s", "--retry-count", "1", "--timeout", "6"]

        values, lines, _ = _policy(["--resume", "RUN0", "--retry-count", "3"], root)

        self.assertEqual(values, (3, 6.0))
        self.assertEqual(
            lines, [("INFO", "resume: --retry-count 3 overrides root run RUN0's 1")]
        )
        # Restating the root's own value is not an override.
        self.assertEqual(
            _policy(["--resume", "RUN0", "--timeout", "6"], root)[:2], ((1, 6.0), [])
        )

    def test_a_root_dispatched_by_enge_test_uses_the_flags_or_defaults(self):
        root = [ENGE, "test", "-S", "s"]

        values, lines, _ = _policy(["--resume", "RUN0", "--retry-count", "1"], root)

        self.assertEqual(values, (1, 24.0))
        self.assertEqual(
            lines,
            [
                (
                    "INFO",
                    "resume: root run RUN0 was dispatched by `enge test`, not "
                    "`enge run`; using --retry-count 1 --timeout 24",
                )
            ],
        )

    def test_an_unreadable_root_falls_back_with_a_warning(self):
        expected = [
            (
                "WARNING",
                "resume: cannot read the loop policy from root run RUN0's "
                "manifest; using --retry-count 2 --timeout 24",
            )
        ]
        for root in (
            None,
            [ENGE],
            [ENGE, "run", "--no-such-flag"],
            [ENGE, "run", "--retry-count", "-1"],
        ):
            with self.subTest(root=root):
                values, lines, stderr = _policy(["--resume", "RUN0"], root)
                self.assertEqual((values, lines), ((2, 24.0), expected))
                self.assertNotIn("usage:", stderr, "argparse's usage text leaked")


class TestMainResolvesThePolicy(unittest.TestCase):
    def test_main_hands_the_roots_policy_to_the_loop(self):
        from enge.run import __main__ as run_main
        from enge.utils.arg_parser import get_arguments

        ctx = SimpleNamespace(cli_args=get_arguments(["run", "--resume", "RUN0"]))
        phases = mock.Mock()
        phases.root.return_value = (
            "RUN0FULL",
            [ENGE, "run", "--retry-count", "1", "--timeout", "6"],
        )
        with (
            mock.patch("enge.run.phases.Phases", return_value=phases),
            mock.patch.object(run_main, "RunLoop") as loop,
        ):
            loop.return_value.run.return_value = 2
            self.assertEqual(run_main.main(ctx), 2)

        phases.root.assert_called_once_with("RUN0")
        loop.assert_called_once_with(
            phases, retry_count=1, timeout_hours=6.0, clock=mock.ANY
        )
        loop.return_value.run.assert_called_once_with(resume="RUN0FULL")


class TestValidateRunArgs(unittest.TestCase):
    def _parse(self, *argv):
        from enge.utils.arg_parser import get_arguments

        return get_arguments(["run", *argv])

    def test_resume_refuses_dispatch_and_tagging_flags(self):
        from enge.run.__main__ import validate_run_args

        for argv in (("-s", "9.7"), ("-T", "tier0"), ("--set-tag", "x")):
            with self.subTest(argv=argv):
                with self.assertRaises(ValidationError):
                    validate_run_args(self._parse("--resume", "RUN0", *argv))
        validate_run_args(self._parse("--resume", "RUN0", "--timeout", "2"))

    def test_retry_count_and_timeout_bounds(self):
        from enge.run.__main__ import validate_run_args

        with self.assertRaisesRegex(ValidationError, "--retry-count"):
            validate_run_args(self._parse("--retry-count", "-1"))
        with self.assertRaisesRegex(ValidationError, "--timeout"):
            validate_run_args(self._parse("--timeout", "0"))
        validate_run_args(self._parse("--retry-count", "0"))

    def test_loop_flags_default_in_code_not_in_the_namespace(self):
        from enge.run.__main__ import DEFAULT_RETRY_COUNT, DEFAULT_TIMEOUT_HOURS

        namespace = self._parse()
        for dest in ("retry_count", "timeout", "resume"):
            self.assertFalse(hasattr(namespace, dest), dest)
        self.assertEqual((DEFAULT_RETRY_COUNT, DEFAULT_TIMEOUT_HOURS), (2, 24.0))
