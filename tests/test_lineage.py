"""Tests for ``enge.lineage`` (run-loop item 5.1).

The module under test is pure: it links results.json task entries by
``rerun_of`` and derives generations, filter coverage, attempts spent, the
per-generation trajectory and the lineage outcome. Names from
``enge.lineage`` are imported inside the test methods so that a missing
module fails each test individually rather than the whole collection.
"""

import ast
import dataclasses
import itertools
import unittest
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

from enge.utils.results_parser import PlanEntry, TaskEntry
from enge.utils.results_parser import TestEntry as ResultTestEntry

A = "/plans/tier0/a"
B = "/plans/tier0/b"
T1 = "/t/x.py::t1"
T2 = "/t/y.py::t2"
S1 = {"t1"}
S2 = {"t2"}
ROOT_PLAN = "/plans/tier0"

LINEAGE_SRC = Path(__file__).resolve().parents[1] / "src" / "enge" / "lineage.py"

_clock = itertools.count()


def _stamp() -> str:
    """Distinct, increasing ISO timestamps in construction order."""
    n = next(_clock)
    return f"2026-01-01T{n // 60:02d}:{n % 60:02d}:00Z"


def P(name: str, verdict: str, *tests: Tuple[str, str]) -> PlanEntry:
    return PlanEntry(
        name=name,
        verdict=verdict,
        tests=[
            ResultTestEntry(name=n, verdict=v, duration_seconds=0.0) for n, v in tests
        ],
    )


def mk(
    task_id: str,
    verdict: str,
    plans: Sequence[PlanEntry] = (),
    rerun_of: Optional[str] = None,
    plan: Optional[str] = None,
    tests: Optional[List[str]] = None,
) -> TaskEntry:
    return TaskEntry(
        task_id=task_id,
        set=None,
        tier="tier0",
        arch="x86_64",
        source_compose=None,
        target_compose=None,
        dispatched_at=_stamp(),
        verdict=verdict,
        total_duration_seconds=0.0,
        plans=list(plans),
        rerun_of=rerun_of,
        plan=plan,
        tests=tests,
    )


def tv(plan: str, raw_test: str) -> Callable[[Any], Optional[str]]:
    """verdict_of for a test row: the verdict found in the task, else None."""

    def verdict_of(task: Any) -> Optional[str]:
        for p in task.plans:
            if p.name == plan:
                for t in p.tests:
                    if t.name == raw_test:
                        return t.verdict
        return None

    return verdict_of


def pv(plan: str) -> Callable[[Any], Optional[str]]:
    """verdict_of for a plan row: the verdict found in the task, else None."""

    def verdict_of(task: Any) -> Optional[str]:
        for p in task.plans:
            if p.name == plan:
                return p.verdict
        return None

    return verdict_of


def ids(tasks: Sequence[Any]) -> Tuple[str, ...]:
    return tuple(t.task_id for t in tasks)


def values(entries: Sequence[Any]) -> str:
    return " ".join(e.value for e in entries)


def at(task: TaskEntry, stamp: str) -> TaskEntry:
    """``task`` with a specific ``dispatched_at`` (``mk`` stamps from a clock)."""
    return dataclasses.replace(task, dispatched_at=stamp)


# --- shared shapes -----------------------------------------------------------


def chain_k1() -> List[TaskEntry]:
    r = mk(
        "r",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED")), P(B, "PASSED", (T2, "PASSED"))],
        plan=ROOT_PLAN,
        tests=[],
    )
    c1 = mk(
        "c1",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED"))],
        rerun_of="r",
        plan=A,
        tests=["t1"],
    )
    c2 = mk(
        "c2",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED"))],
        rerun_of="c1",
        plan=A,
        tests=["t1"],
    )
    return [r, c1, c2]


def chain_f1() -> List[TaskEntry]:
    r = mk("r", "ERROR", [], plan=ROOT_PLAN, tests=[])
    c1 = mk(
        "c1",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED"))],
        rerun_of="r",
        plan=ROOT_PLAN,
        tests=[],
    )
    return [r, c1]


def chain_f2() -> List[TaskEntry]:
    base = chain_f1()
    c2 = mk(
        "c2",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED"))],
        rerun_of="c1",
        plan=A,
        tests=["t1"],
    )
    return base + [c2]


def chain_r1() -> List[TaskEntry]:
    base = chain_f1()
    c2u = mk("c2u", "ERROR", [], rerun_of="c1", plan=A, tests=["t1"])
    return base + [c2u]


def chain_d1() -> List[TaskEntry]:
    r = mk(
        "r",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED"), (T2, "FAILED"))],
        plan=None,
        tests=[],
    )
    c1 = mk(
        "c1",
        "PASSED",
        [P(A, "PASSED", (T2, "PASSED"))],
        rerun_of="r",
        plan=A,
        tests=["t1", "t2"],
    )
    return [r, c1]


def chain_w1() -> List[TaskEntry]:
    r = mk(
        "r",
        "ERROR",
        [P(A, "FAILED", (T1, "FAILED")), P(B, "ERROR")],
        plan=None,
        tests=[],
    )
    ct = mk(
        "ct",
        "FAILED",
        [P(A, "FAILED", (T1, "FAILED"))],
        rerun_of="r",
        plan=A,
        tests=["t1"],
    )
    cp = mk("cp", "ERROR", [P(B, "ERROR")], rerun_of="r", plan=B, tests=[])
    return [r, ct, cp]


# --- K: the canonical multi-generation lineage -------------------------------


class TestCanonicalLineage(unittest.TestCase):
    def setUp(self):
        from enge.lineage import LineageIndex

        self.idx = LineageIndex(chain_k1())

    def test_k1_generations_and_structure(self):
        self.assertEqual([self.idx.generation(t) for t in ("r", "c1", "c2")], [0, 1, 2])
        self.assertEqual(self.idx.root_of("c2"), "r")
        self.assertEqual(self.idx.roots(), ("r",))
        self.assertEqual(ids(self.idx.lineage("r")), ("r", "c1", "c2"))
        self.assertEqual(self.idx.parent_of("c2"), "c1")
        self.assertIsNone(self.idx.parent_of("r"))

    def test_k2_root_with_regex_plan_covers_its_rows(self):
        self.assertTrue(self.idx.covers("r", A, S1))
        self.assertTrue(self.idx.covers("r", B, S2))

    def test_k3_narrowed_child_covers_exactly_its_filter(self):
        self.assertTrue(self.idx.covers("c1", A, S1))
        self.assertFalse(self.idx.covers("c1", A, S2))
        self.assertFalse(self.idx.covers("c1", B, None))

    def test_k4_trajectory(self):
        self.assertEqual(values(self.idx.trajectory("r", A, S1, tv(A, T1))), "F F F")
        self.assertEqual(values(self.idx.trajectory("r", B, S2, tv(B, T2))), "P")

    def test_k5_spent(self):
        self.assertEqual(self.idx.spent("r", A, S1), 2)
        self.assertEqual(self.idx.spent("r", B, S2), 0)
        self.assertEqual(self.idx.spent("r", A, None), 2)

    def test_k6_exhausted_depends_on_retry_count(self):
        self.assertEqual(self.idx.outcome("r", A, S1, tv(A, T1), 2), "exhausted")
        self.assertIsNone(self.idx.outcome("r", A, S1, tv(A, T1), 3))
        self.assertIsNone(self.idx.outcome("r", A, S1, tv(A, T1), None))

    def test_k7_passed_row_has_no_outcome(self):
        self.assertIsNone(self.idx.outcome("r", B, S2, tv(B, T2), 0))

    def test_k8_lineage_orders_by_generation_before_time(self):
        from enge.lineage import LineageIndex

        r = mk(
            "r",
            "FAILED",
            [P(A, "FAILED", (T1, "FAILED")), P(B, "FAILED", (T2, "FAILED"))],
            plan=ROOT_PLAN,
            tests=[],
        )
        a = mk(
            "a",
            "FAILED",
            [P(A, "FAILED", (T1, "FAILED"))],
            rerun_of="r",
            plan=A,
            tests=["t1"],
        )
        ac = mk(
            "ac",
            "FAILED",
            [P(A, "FAILED", (T1, "FAILED"))],
            rerun_of="a",
            plan=A,
            tests=["t1"],
        )
        b = mk(
            "b",
            "FAILED",
            [P(B, "FAILED", (T2, "FAILED"))],
            rerun_of="r",
            plan=B,
            tests=["t2"],
        )
        # dispatched_at order: r < a < ac < b, but b is generation 1.
        idx = LineageIndex([ac, b, r, a])
        self.assertEqual(ids(idx.lineage("r")), ("r", "a", "b", "ac"))


# --- U: unparseable streaks --------------------------------------------------


def chain_u1(with_c2: bool = True) -> List[TaskEntry]:
    r = mk("r", "ERROR", [], plan=ROOT_PLAN, tests=[])
    c1 = mk("c1", "ERROR", [], rerun_of="r", plan=ROOT_PLAN, tests=[])
    out = [r, c1]
    if with_c2:
        out.append(
            mk(
                "c2",
                "PASSED",
                [P(A, "PASSED", (T1, "PASSED"))],
                rerun_of="c1",
                plan=ROOT_PLAN,
                tests=[],
            )
        )
    return out


class TestUnparseable(unittest.TestCase):
    def test_u1_trajectory_and_uncharged_reruns(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_u1())
        entries = idx.trajectory("r", A, S1, tv(A, T1))
        self.assertEqual(values(entries), "E E P")
        self.assertEqual([e.unparseable for e in entries], [True, True, False])
        self.assertEqual(idx.spent("r", A, S1), 0)

    def test_u3_parsed_result_supersedes_the_streak(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_u1())
        self.assertIsNone(idx.outcome("r", A, S1, tv(A, T1), 2))

    def test_u4_two_consecutive_unparseable_terminate(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_u1(with_c2=False))
        self.assertEqual(idx.outcome("r", A, S1, tv(A, T1), 2), "unparseable")


# --- F: the fallback rerun of an unparseable parent --------------------------


class TestFallbackRerun(unittest.TestCase):
    def test_f1_rerun_of_unparseable_parent_is_uncharged(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_f1())
        self.assertEqual(idx.spent("r", A, S1), 0)

    def test_f2_charging_resumes_after_the_fallback(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_f2())
        self.assertEqual(idx.spent("r", A, S1), 1)
        self.assertEqual(values(idx.trajectory("r", A, S1, tv(A, T1))), "E F F")
        self.assertEqual(idx.outcome("r", A, S1, tv(A, T1), 1), "exhausted")


# --- R: a trailing unparseable result ----------------------------------------


class TestTrailingUnparseable(unittest.TestCase):
    def test_r1_single_trailing_unparseable_is_not_terminal(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_r1())
        self.assertEqual(values(idx.trajectory("r", A, S1, tv(A, T1))), "E F E")
        self.assertIsNone(idx.outcome("r", A, S1, tv(A, T1), 9))

    def test_r2_trailing_unparseable_beats_exhausted(self):
        # spent == retry_count here, so only the "an uncharged rerun is
        # still due" rule keeps the lineage open instead of exhausted.
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_r1())
        self.assertEqual(idx.spent("r", A, S1), 1)
        self.assertIsNone(idx.outcome("r", A, S1, tv(A, T1), 1))

    def test_r3_child_of_parsed_parent_with_narrow_filter_covers(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_r1())
        self.assertTrue(idx.covers("c2u", A, S1))


# --- N: a child of an unparseable (plan-less) parent inherits coverage -------


class TestInheritedCoverage(unittest.TestCase):
    def setUp(self):
        from enge.lineage import LineageIndex

        base = chain_r1()
        g = mk(
            "g",
            "PASSED",
            [P(A, "PASSED", (T1, "PASSED"))],
            rerun_of="c2u",
            plan=A,
            tests=["t1"],
        )
        self.idx = LineageIndex(base + [g])

    def test_n1_child_of_unparseable_inherits_parent_coverage(self):
        self.assertTrue(self.idx.covers("g", A, S1))
        self.assertFalse(self.idx.covers("g", A, S2))
        self.assertFalse(self.idx.covers("g", B, None))

    def test_n3_trajectory_and_spent_across_unparseable_hop(self):
        self.assertEqual(values(self.idx.trajectory("r", A, S1, tv(A, T1))), "E F E P")
        self.assertEqual(self.idx.spent("r", A, S1), 1)


# --- D: tests dropped from the rerun's xunit ---------------------------------


class TestDroppedTests(unittest.TestCase):
    def setUp(self):
        from enge.lineage import LineageIndex

        self.idx = LineageIndex(chain_d1())

    def test_d1_missing_test_row_is_no_result(self):
        self.assertEqual(values(self.idx.trajectory("r", A, S1, tv(A, T1))), "F .")
        self.assertEqual(values(self.idx.trajectory("r", A, S2, tv(A, T2))), "F P")

    def test_d2_no_result_is_not_exhausted(self):
        self.assertIsNone(self.idx.outcome("r", A, S1, tv(A, T1), 0))


# --- C: canceled and skipped -------------------------------------------------


class TestCanceledSkipped(unittest.TestCase):
    def test_c1_canceled_is_terminal(self):
        from enge.lineage import LineageIndex

        r = chain_d1()[0]
        cc = mk(
            "cc",
            "CANCELED",
            [P(A, "CANCELED", (T1, "CANCELED"))],
            rerun_of="r",
            plan=A,
            tests=["t1"],
        )
        idx = LineageIndex([r, cc])
        self.assertEqual(idx.outcome("r", A, S1, tv(A, T1), None), "terminal-canceled")

    def test_c2_skipped_is_not_a_marker(self):
        from enge.lineage import LineageIndex

        r = chain_d1()[0]
        cs = mk(
            "cs",
            "SKIPPED",
            [P(A, "SKIPPED", (T1, "SKIPPED"))],
            rerun_of="r",
            plan=A,
            tests=["t1"],
        )
        idx = LineageIndex([r, cs])
        self.assertIsNone(idx.outcome("r", A, S1, tv(A, T1), 0))


# --- G: test-name granularity ------------------------------------------------


class TestGranularity(unittest.TestCase):
    def test_g1_last_segment(self):
        from enge.lineage import last_segment

        self.assertEqual(last_segment("/a.py::test_x"), "test_x")
        self.assertEqual(last_segment("/b.py::test_x"), "test_x")
        self.assertEqual(last_segment("/default-0/foo"), "/default-0/foo")

    def test_g2_two_rows_one_counter(self):
        from enge.lineage import LineageIndex

        ra, rb = "/a.py::test_x", "/b.py::test_x"
        r = mk(
            "r", "FAILED", [P(A, "FAILED", (ra, "FAILED"), (rb, "FAILED"))], tests=[]
        )
        c1 = mk(
            "c1",
            "FAILED",
            [P(A, "FAILED", (ra, "FAILED"), (rb, "PASSED"))],
            rerun_of="r",
            plan=A,
            tests=["test_x"],
        )
        idx = LineageIndex([r, c1])
        seg = {"test_x"}
        self.assertEqual(values(idx.trajectory("r", A, seg, tv(A, ra))), "F F")
        self.assertEqual(values(idx.trajectory("r", A, seg, tv(A, rb))), "F P")
        self.assertEqual(idx.spent("r", A, seg), 1)


# --- W: plan-only keys -------------------------------------------------------


class TestPlanKeys(unittest.TestCase):
    def setUp(self):
        from enge.lineage import LineageIndex

        self.idx = LineageIndex(chain_w1())

    def test_w1_children_ordered_by_time(self):
        self.assertEqual(ids(self.idx.children("r")), ("ct", "cp"))

    def test_w2_error_with_xunit_counts_as_an_attempt(self):
        self.assertEqual(values(self.idx.trajectory("r", B, None, pv(B))), "E E")
        self.assertEqual(self.idx.spent("r", B, None), 1)
        self.assertEqual(self.idx.outcome("r", B, None, pv(B), 1), "exhausted")

    def test_w3_test_filtered_sibling_does_not_cover_other_plan(self):
        self.assertFalse(self.idx.covers("ct", B, None))


# --- L: legacy ---------------------------------------------------------------


class TestLegacy(unittest.TestCase):
    def test_l1_null_tests_means_no_test_filter(self):
        from enge.lineage import LineageIndex

        r = mk(
            "r", "FAILED", [P(A, "FAILED", (T1, "FAILED"), (T2, "FAILED"))], tests=[]
        )
        c1 = mk(
            "c1",
            "FAILED",
            [P(A, "FAILED", (T1, "FAILED"))],
            rerun_of="r",
            plan=A,
            tests=None,
        )
        idx = LineageIndex([r, c1])
        self.assertTrue(idx.covers("c1", A, S2))


# --- E: errors and edges -----------------------------------------------------


class TestErrors(unittest.TestCase):
    def test_e1_orphan_is_its_own_root(self):
        from enge.lineage import LineageIndex

        o = mk("o", "FAILED", [P(A, "FAILED")], rerun_of="missing", plan=A)
        idx = LineageIndex([o])
        self.assertEqual(idx.orphans, ("o",))
        self.assertEqual(idx.root_of("o"), "o")
        self.assertEqual(idx.generation("o"), 0)
        self.assertIsNone(idx.parent_of("o"))

    def test_e2_duplicate_task_id(self):
        from enge.lineage import LineageIndex

        with self.assertRaises(ValueError):
            LineageIndex([mk("d", "FAILED"), mk("d", "PASSED")])

    def test_e3_rerun_of_cycle(self):
        from enge.lineage import LineageIndex

        with self.assertRaises(ValueError):
            LineageIndex(
                [mk("a", "FAILED", rerun_of="b"), mk("b", "FAILED", rerun_of="a")]
            )

    def test_e4_invalid_retry_count(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex([mk("r", "FAILED", [P(A, "FAILED", (T1, "FAILED"))])])
        for bad in (-1, True, 1.0):
            with self.subTest(retry_count=bad):
                with self.assertRaises(ValueError):
                    idx.outcome("r", A, S1, tv(A, T1), bad)

    def test_e5_unknown_verdict(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex([mk("r", "FAILED", [P(A, "UNDEFINED")])])
        with self.assertRaises(ValueError):
            idx.trajectory("r", A, None, pv(A))

    def test_e6_empty_test_segments(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_k1())
        with self.assertRaises(ValueError):
            idx.covers("c1", A, set())

    def test_e7_non_root_argument(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_k1())
        with self.assertRaises(ValueError):
            idx.spent("c1", A, S1)

    def test_e8_lineage_requires_a_root(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_k1())
        for tid in ("c1", "c2"):
            with self.subTest(task_id=tid):
                with self.assertRaisesRegex(ValueError, "not a lineage root"):
                    idx.lineage(tid)
        with self.assertRaises(KeyError):
            idx.lineage("missing")

    def test_e9_bare_str_test_segments(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(chain_k1())
        self.assertTrue(idx.covers("c1", A, ["t1"]))  # premise: a list works
        calls = {
            "covers": lambda: idx.covers("c1", A, "t1"),
            "spent": lambda: idx.spent("r", A, "t1"),
            "trajectory": lambda: idx.trajectory("r", A, "t1", tv(A, T1)),
            "outcome": lambda: idx.outcome("r", A, "t1", tv(A, T1), 2),
        }
        for name, call in calls.items():
            with self.subTest(method=name):
                with self.assertRaisesRegex(ValueError, "bare str"):
                    call()


# --- O: ordering -------------------------------------------------------------


class TestOrdering(unittest.TestCase):
    def test_o1_roots_ordered_by_time_then_id(self):
        from enge.lineage import LineageIndex

        idx = LineageIndex(
            [
                at(mk("z", "FAILED"), "2026-03-01T00:02:00Z"),
                at(mk("b", "FAILED"), "2026-03-01T00:00:00Z"),
                at(mk("a", "FAILED"), "2026-03-01T00:00:00Z"),
                at(mk("y", "FAILED"), "2026-03-01T00:01:00Z"),
            ]
        )
        self.assertEqual(idx.roots(), ("a", "b", "y", "z"))

    def test_o2_children_ordered_by_time_then_id(self):
        from enge.lineage import LineageIndex

        root = at(
            mk("r", "FAILED", [P(A, "FAILED", (T1, "FAILED"))]),
            "2026-03-01T00:00:00Z",
        )
        kids = [
            at(mk(tid, "FAILED", rerun_of="r", plan=A), stamp)
            for tid, stamp in (
                ("late", "2026-03-01T00:09:00Z"),
                ("tb", "2026-03-01T00:05:00Z"),
                ("ta", "2026-03-01T00:05:00Z"),
                ("early", "2026-03-01T00:01:00Z"),
            )
        ]
        idx = LineageIndex([root, *kids])
        self.assertEqual(ids(idx.children("r")), ("early", "ta", "tb", "late"))


# --- P: purity ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MiniPlan:
    name: str
    verdict: str
    tests: Tuple[Any, ...] = ()


@dataclasses.dataclass(frozen=True)
class MiniTest:
    name: str
    verdict: str


@dataclasses.dataclass(frozen=True)
class MiniTask:
    task_id: str
    rerun_of: Optional[str]
    dispatched_at: str
    verdict: str
    plans: Tuple[MiniPlan, ...]
    plan: Optional[str]
    tests: Optional[Tuple[str, ...]]


class TestPurity(unittest.TestCase):
    def test_p1_imports_only_stdlib_dataclasses_and_typing(self):
        tree = ast.parse(LINEAGE_SRC.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "relative import")
                modules.add((node.module or "").split(".")[0])
        self.assertLessEqual(modules, {"dataclasses", "typing", "__future__"})

    def test_p2_duck_typing_on_a_plain_dataclass(self):
        from enge.lineage import LineageIndex

        def mini(tid, parent, n, plans, plan, tests):
            return MiniTask(
                task_id=tid,
                rerun_of=parent,
                dispatched_at=f"2026-02-01T00:{n:02d}:00Z",
                verdict="FAILED",
                plans=tuple(plans),
                plan=plan,
                tests=None if tests is None else tuple(tests),
            )

        def mplan(name, verdict, *tests):
            return MiniPlan(name, verdict, tuple(MiniTest(n, v) for n, v in tests))

        tasks = [
            mini(
                "r",
                None,
                0,
                [
                    mplan(A, "FAILED", (T1, "FAILED")),
                    mplan(B, "PASSED", (T2, "PASSED")),
                ],
                ROOT_PLAN,
                [],
            ),
            mini("c1", "r", 1, [mplan(A, "FAILED", (T1, "FAILED"))], A, ["t1"]),
            mini("c2", "c1", 2, [mplan(A, "FAILED", (T1, "FAILED"))], A, ["t1"]),
        ]
        idx = LineageIndex(tasks)
        self.assertEqual(values(idx.trajectory("r", A, S1, tv(A, T1))), "F F F")
        self.assertEqual(values(idx.trajectory("r", B, S2, tv(B, T2))), "P")

    def test_p3_constants_are_exact_and_ascii(self):
        import enge.lineage as lin

        self.assertEqual(
            lin.VERDICT_INITIALS,
            {
                "PASSED": "P",
                "FAILED": "F",
                "ERROR": "E",
                "SKIPPED": "S",
                "CANCELED": "C",
            },
        )
        self.assertEqual(lin.NO_RESULT, ".")
        self.assertEqual(lin.EXHAUSTED, "exhausted")
        self.assertEqual(lin.UNPARSEABLE, "unparseable")
        self.assertEqual(lin.TERMINAL_CANCELED, "terminal-canceled")
        self.assertEqual(
            lin.MARKERS,
            {"exhausted": "[X]", "unparseable": "[?]", "terminal-canceled": "[-]"},
        )
        strings = [lin.NO_RESULT, lin.EXHAUSTED, lin.UNPARSEABLE, lin.TERMINAL_CANCELED]
        strings += list(lin.VERDICT_INITIALS) + list(lin.VERDICT_INITIALS.values())
        strings += list(lin.MARKERS) + list(lin.MARKERS.values())
        for s in strings:
            self.assertTrue(s.isascii(), s)


if __name__ == "__main__":
    unittest.main()
