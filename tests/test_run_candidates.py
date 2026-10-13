"""`enge.run.candidates`: what the loop reruns after a generation, and the
exit code it ends with (contract v3 §1.4-§1.8; Q-R6-C, Q-R6-F, Q-R6-K,
Q-R6-L).

Columns are built in memory the way `compare.loader` builds them; one test
goes through the real loader over cache files on disk. Each test imports
`enge.run.candidates` itself, so each fails on its own until it exists.
"""

import json
import tempfile
import unittest
from pathlib import Path

from enge.compare import engine as E
from enge.rerun.__main__ import RerunCandidate
from enge.utils.globals import ExitCode
from enge.utils.results_parser import PlanEntry
from enge.utils.results_parser import TestEntry as _TestEntry

from tests._helpers import make_app_context

A = "/plans/a"
B = "/plans/b"
U = "/plans/undef"
T1 = "/t/x.py::t1"
T2 = "/t/x.py::t2"


def T(name, verdict):
    return _TestEntry(name=name, verdict=verdict, duration_seconds=1.0)


def P(name, verdict, *tests):
    return PlanEntry(name=name, verdict=verdict, tests=list(tests))


def col(task_id, at, plans=(), *, verdict=None, rerun_of=None, plan=None, tests=None):
    if verdict is None:
        order = {"PASSED": 0, "SKIPPED": 0, "FAILED": 1, "ERROR": 2}
        verdict = (
            max((p.verdict for p in plans), key=lambda v: order.get(v, 0))
            if plans
            else "ERROR"
        )
    return E.ExecutionColumn(
        task_id=task_id,
        run_id="RUN",
        set="s",
        tier="tier0",
        arch="x86_64",
        source="8.10",
        target="9.6",
        source_compose="RHEL-8.10.0-Nightly",
        target_compose="RHEL-9.6.0-Nightly",
        dispatched_at=at,
        run_created_at=at,
        plans=list(plans),
        artifacts_url=f"https://a/{task_id}",
        rerun_of=rerun_of,
        verdict=verdict,
        plan=plan,
        tests=tests,
    )


def _select(columns, task_ids, retry_count=2):
    from enge.run.candidates import select_candidates

    return select_candidates(columns, task_ids, retry_count)


def _root_failing_t1():
    return col(
        "r",
        "2026-10-01T00",
        [
            P(A, "FAILED", T("/t/x.py::t1", "FAILED"), T("/t/x.py::t2", "PASSED")),
            P(B, "PASSED", T("/t/y.py::t3", "PASSED")),
        ],
        tests=[],
    )


def _rerun_of(parent, task_id, at, verdict="FAILED"):
    return col(
        task_id,
        at,
        [P(A, verdict, T("/t/x.py::t1", verdict))],
        rerun_of=parent,
        plan=A,
        tests=["t1"],
    )


class TestSelectCandidates(unittest.TestCase):
    def test_failed_tests_of_a_failed_plan_are_rerun(self):
        candidates = _select([_root_failing_t1()], ["r"])

        self.assertEqual(
            candidates,
            [RerunCandidate(task_id="r", plans=(A,), tests_by_plan={A: ("t1",)})],
        )

    def test_a_key_with_its_budget_spent_is_not_rerun(self):
        chain = [
            _root_failing_t1(),
            _rerun_of("r", "c1", "2026-10-02T00"),
            _rerun_of("c1", "c2", "2026-10-03T00"),
        ]

        self.assertEqual(_select(chain, ["c2"], retry_count=2), [])
        self.assertEqual(
            _select(chain, ["c2"], retry_count=3),
            [RerunCandidate(task_id="c2", plans=(A,), tests_by_plan={A: ("t1",)})],
        )

    def test_a_failing_plan_without_failed_tests_is_rerun_plan_only(self):
        root = col(
            "r",
            "2026-10-01T00",
            [P(A, "FAILED", T("/t/x.py::t1", "FAILED")), P(U, "ERROR")],
            tests=[],
        )

        self.assertEqual(
            _select([root], ["r"]),
            [
                RerunCandidate(
                    task_id="r",
                    plans=(A, U),
                    tests_by_plan={A: ("t1",)},
                    undefined_plans=(U,),
                )
            ],
        )

    def test_an_unparseable_root_gets_its_uncharged_rerun_even_at_zero(self):
        root = col("r", "2026-10-01T00", [], verdict="ERROR", tests=[])

        self.assertEqual(_select([root], ["r"], retry_count=0), [RerunCandidate("r")])

    def test_a_second_unparseable_result_in_a_row_ends_the_lineage(self):
        chain = [
            col("r", "2026-10-01T00", [], verdict="ERROR", tests=[]),
            col("c1", "2026-10-02T00", [], verdict="ERROR", rerun_of="r"),
        ]

        self.assertEqual(_select(chain, ["c1"]), [])

    def test_an_unparseable_rerun_of_a_parsed_task_is_rerun_as_it_was(self):
        chain = [
            _root_failing_t1(),
            col("c1", "2026-10-02T00", [], verdict="ERROR", rerun_of="r", plan=A),
        ]

        self.assertEqual(_select(chain, ["c1"]), [RerunCandidate("c1")])

    def test_canceled_tasks_and_skipped_plans_are_terminal(self):
        canceled = col("r1", "2026-10-01T00", [], verdict="CANCELED", tests=[])
        skipped = col("r2", "2026-10-01T01", [P(A, "SKIPPED")], tests=[])

        self.assertEqual(_select([canceled, skipped], ["r1", "r2"]), [])

    def test_a_key_already_rerun_outside_the_loop_is_left_alone(self):
        root = col(
            "r",
            "2026-10-01T00",
            [
                P(A, "FAILED", T("/t/x.py::t1", "FAILED")),
                P(B, "FAILED", T("/t/y.py::t3", "FAILED")),
            ],
            tests=[],
        )
        manual = _rerun_of("r", "m", "2026-10-02T00", verdict="PASSED")

        self.assertEqual(
            _select([root, manual], ["r"]),
            [RerunCandidate(task_id="r", plans=(B,), tests_by_plan={B: ("t3",)})],
        )

    def test_tasks_never_harvested_are_ignored(self):
        self.assertEqual(
            _select([_root_failing_t1()], ["r", "missing"]),
            [RerunCandidate(task_id="r", plans=(A,), tests_by_plan={A: ("t1",)})],
        )

    def test_no_plan_is_both_plan_only_and_test_filtered(self):
        # L68/L69: a plan in both undefined_plans and tests_by_plan would
        # send an empty plan name in the filtered rerun payload.
        root = col(
            "r",
            "2026-10-01T00",
            [
                P(A, "FAILED", T("/t/x.py::t1", "FAILED")),
                P(B, "ERROR", T("/t/y.py::t3", "PASSED")),
                P(U, "ERROR"),
            ],
            tests=[],
        )
        (candidate,) = _select([root], ["r"])

        self.assertEqual(candidate.undefined_plans, (B, U))
        self.assertFalse(set(candidate.undefined_plans) & set(candidate.tests_by_plan))

    def test_candidates_come_from_the_loaders_closure(self):
        from enge.compare.loader import load_lineage_columns

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "runs" / "RUN0.json", _manifest("RUN0", ["r"], None))
            _write(root / "runs" / "RUN1.json", _manifest("RUN1", ["c1"], "RUN0"))
            _write(root / "results" / "RUN0.json", _results("RUN0", [_entry("r")]))
            _write(
                root / "results" / "RUN1.json",
                _results("RUN1", [_entry("c1", rerun_of="r", plan=A, tests=["t1"])]),
            )
            ctx = make_app_context(
                action="compare",
                extra_cli={"run": ["RUN0"], "lineage": True},
                extra_config={"common": {"results_dir": str(root / "results")}},
                manifest_runs_dir=str(root / "runs"),
                manifest_latest=str(root / "latest"),
            )
            columns, selected, error = load_lineage_columns(ctx)

        self.assertIsNone(error)
        self.assertEqual(selected, ["r"])
        self.assertEqual(
            _select(columns, ["c1"], retry_count=2),
            [RerunCandidate(task_id="c1", plans=(A,), tests_by_plan={A: ("t1",)})],
        )
        self.assertEqual(_select(columns, ["c1"], retry_count=1), [])


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _manifest(run_id, task_ids, parent):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-10-01T00:00:00Z",
        "command": "test" if parent is None else "rerun",
        "argv": ["enge"],
        "tags": [],
        "parent_run_id": parent,
        "origin": "native",
        "context": {},
        "requests": [{"task_id": t} for t in task_ids],
    }


def _entry(task_id, **overrides):
    entry = {
        "task_id": task_id,
        "set": "s",
        "tier": "tier0",
        "arch": "x86_64",
        "source_compose": "RHEL-8.10.0-Nightly",
        "target_compose": "RHEL-9.6.0-Nightly",
        "dispatched_at": "2026-10-01T00:00:00Z",
        "verdict": "FAILED",
        "total_duration_seconds": 1.0,
        "plans": [
            {
                "name": A,
                "verdict": "FAILED",
                "tests": [
                    {"name": "/t/x.py::t1", "verdict": "FAILED", "duration_seconds": 1}
                ],
            }
        ],
        "source": "8.10",
        "target": "9.6",
        "git_ref": None,
        "event": None,
        "build_ids": [],
        "rerun_of": None,
        "artifacts_url": f"https://a/{task_id}",
        "plan": None,
        "plan_filter": None,
        "tests": [],
    }
    entry.update(overrides)
    return entry


def _results(run_id, entries):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-10-01T00:00:00Z",
        "event": None,
        "source": "8.10",
        "target": "9.6",
        "verdict": "FAILED",
        "results": entries,
    }


def _tables(columns, selected=None, retry_count=2):
    selected = selected or [c.task_id for c in columns if c.rerun_of is None]
    return [
        table
        for show_tests in (False, True)
        for table in E.build_lineage_tables(
            columns,
            selected,
            show_tests=show_tests,
            splitarch=False,
            splitpath=False,
            retry_count=retry_count,
        )
    ]


def _code(columns, **kwargs):
    from enge.run.candidates import final_exit_code

    return final_exit_code(_tables(columns), **kwargs)


def _single(plan_verdict, test_verdict):
    plan = P(A, plan_verdict, T("/t/x.py::t1", test_verdict))
    return [col("r", "2026-10-01T00", [plan])]


class TestFinalExitCode(unittest.TestCase):
    def test_every_key_passed_or_skipped(self):
        self.assertEqual(_code(_single("PASSED", "PASSED")), ExitCode.SUCCESS)
        self.assertEqual(_code(_single("SKIPPED", "SKIPPED")), ExitCode.SUCCESS)

    def test_last_failed_is_2(self):
        self.assertEqual(_code(_single("FAILED", "FAILED")), ExitCode.TEST_FAILURE)

    def test_last_error_is_3(self):
        self.assertEqual(_code(_single("ERROR", "ERROR")), ExitCode.TEST_ERROR)

    def test_two_unparseable_in_a_row_is_3(self):
        chain = [
            col("r", "2026-10-01T00", [], verdict="ERROR", tests=[]),
            col("c1", "2026-10-02T00", [], verdict="ERROR", rerun_of="r"),
        ]
        chain.append(
            col("x", "2026-10-01T01", [P(A, "PASSED", T("/t/x.py::t1", "PASSED"))])
        )
        self.assertEqual(_code(chain), ExitCode.TEST_ERROR)

    def test_canceled_is_4(self):
        chain = [
            _root_failing_t1(),
            col("c1", "2026-10-02T00", [], verdict="CANCELED", rerun_of="r", plan=A),
        ]
        # Premise: the rerun covers only plan A; plan B passed at gen 0.
        self.assertEqual(_code(chain), ExitCode.MISSING_RESULTS)

    def test_a_covered_key_without_a_result_is_4(self):
        chain = [
            _root_failing_t1(),
            col(
                "c1",
                "2026-10-02T00",
                [P(A, "PASSED", T("/t/x.py::other", "PASSED"))],
                rerun_of="r",
                plan=A,
                tests=["t1"],
            ),
        ]
        self.assertEqual(_code(chain), ExitCode.MISSING_RESULTS)

    def test_tasks_without_result_add_4_below_a_failure(self):
        self.assertEqual(
            _code(_single("PASSED", "PASSED"), without_result=1),
            ExitCode.MISSING_RESULTS,
        )
        self.assertEqual(
            _code(_single("FAILED", "FAILED"), without_result=1),
            ExitCode.TEST_FAILURE,
        )

    def test_error_outranks_failure(self):
        columns = _single("FAILED", "FAILED") + [
            col("r2", "2026-10-01T01", [P(B, "ERROR", T("/t/y.py::t3", "ERROR"))])
        ]
        self.assertEqual(_code(columns), ExitCode.TEST_ERROR)

    def test_an_exhausted_test_counts_when_its_plan_later_passed(self):
        # t2 is exhausted at gen 1 (retry 1); gen 2 reran t1 only and passed,
        # so the plan's last verdict is P while t2's is F: the test view
        # carries the 2.
        chain = [
            col(
                "r",
                "2026-10-01T00",
                [P(A, "FAILED", T(T1, "FAILED"), T(T2, "FAILED"))],
                tests=[],
            ),
            col(
                "c1",
                "2026-10-02T00",
                [P(A, "FAILED", T(T1, "FAILED"), T(T2, "FAILED"))],
                rerun_of="r",
                plan=A,
                tests=["t1", "t2"],
            ),
            col(
                "c2",
                "2026-10-03T00",
                [P(A, "PASSED", T("/t/x.py::t1", "PASSED"))],
                rerun_of="c1",
                plan=A,
                tests=["t1"],
            ),
        ]
        from enge.run.candidates import final_exit_code

        plan_view = _tables(chain, retry_count=1)[0]
        self.assertEqual(final_exit_code([plan_view]), ExitCode.SUCCESS)
        self.assertEqual(_code(chain), ExitCode.TEST_FAILURE)
