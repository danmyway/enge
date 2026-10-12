"""5.2 expected-value table: `engine.build_lineage_tables` and the lineage
footer, checked against the coordinator's prototype output (contract v3 §5a).

The fixtures, the plain-text printer and every expected block are
coordinator-supplied and verbatim; do not edit them. Cell grammar: per-
generation initials in lineage order, then the terminal marker; "." is a
covered key without a result; "—" is a lineage that never covered the row.
"""

import unittest

from enge.compare import engine as E
from enge.utils.results_parser import PlanEntry, TestEntry


def T(name, v):
    return TestEntry(name=name, verdict=v, duration_seconds=1.0)


def P(name, v, *tests):
    return PlanEntry(name=name, verdict=v, tests=list(tests))


def col(
    tid,
    at,
    plans=(),
    *,
    verdict=None,
    rerun_of=None,
    plan=None,
    tests=None,
    arch="x86_64",
    tier="tier0",
    src="8.10",
    tgt="9.6",
    compose="RHEL-8.10.0-Nightly",
    run="RUN0",
):
    if verdict is None:
        order = {"PASSED": 0, "SKIPPED": 0, "FAILED": 1, "ERROR": 2}
        verdict = (
            max((p.verdict for p in plans), key=lambda v: order.get(v, 0))
            if plans
            else "ERROR"
        )
    return E.ExecutionColumn(
        task_id=tid,
        run_id=run,
        set="s",
        tier=tier,
        arch=arch,
        source=src,
        target=tgt,
        source_compose=compose,
        target_compose="RHEL-9.6.0-Nightly",
        dispatched_at=at,
        run_created_at=at,
        plans=list(plans),
        artifacts_url=f"https://a/{tid}",
        rerun_of=rerun_of,
        verdict=verdict,
        plan=plan,
        tests=tests,
    )


A1 = "/plans/a"
A2 = "/plans/b"


def _scenarios():
    """Built per call: the fixtures need the ExecutionColumn lineage fields,
    so at the RED commit each test fails on its own instead of the module."""
    SCEN = {}
    # S1: narrowed chain, pass on retry
    SCEN["S1"] = [
        col(
            "r",
            "2026-10-01T00",
            [
                P(A1, "FAILED", T("/t/x.py::t1", "FAILED"), T("/t/x.py::t2", "PASSED")),
                P(A2, "PASSED", T("/t/y.py::t3", "PASSED")),
            ],
        ),
        col(
            "c1",
            "2026-10-02T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
        col(
            "c2",
            "2026-10-03T00",
            [P(A1, "PASSED", T("/t/x.py::t1", "PASSED"))],
            rerun_of="c1",
            plan=A1,
            tests=("t1",),
            run="RUN2",
        ),
    ]
    # S2: exhausted
    SCEN["S2"] = [
        col("r", "2026-10-01T00", [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))]),
        col(
            "c1",
            "2026-10-02T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
        col(
            "c2",
            "2026-10-03T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="c1",
            plan=A1,
            tests=("t1",),
            run="RUN2",
        ),
    ]
    # S3: unparseable twice (fallback child re-records the original filter)
    SCEN["S3"] = [
        col("r", "2026-10-01T00", [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))]),
        col(
            "c1", "2026-10-02T00", [], rerun_of="r", plan=A1, tests=("t1",), run="RUN1"
        ),
        col(
            "c2", "2026-10-03T00", [], rerun_of="c1", plan=A1, tests=("t1",), run="RUN2"
        ),
    ]
    # S4: canceled rerun
    SCEN["S4"] = [
        col("r", "2026-10-01T00", [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))]),
        col(
            "c1",
            "2026-10-02T00",
            [],
            verdict="CANCELED",
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]
    # S5: covered but no result for one plan
    SCEN["S5"] = [
        col(
            "r",
            "2026-10-01T00",
            [
                P(A1, "FAILED", T("/t/x.py::t1", "FAILED")),
                P(A2, "FAILED", T("/t/y.py::t3", "FAILED")),
            ],
        ),
        col(
            "c1",
            "2026-10-02T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="r",
            plan=f"{A1}, {A2}",
            tests=[],
            run="RUN1",
        ),
    ]
    # S8: two children in one generation (test-filtered + plan-only), plus compose drift
    SCEN["S8"] = [
        col(
            "r",
            "2026-10-01T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED")), P(A2, "ERROR")],
        ),
        col(
            "c1b",
            "2026-10-02T00",
            [P(A2, "PASSED")],
            rerun_of="r",
            plan=A2,
            tests=[],
            run="RUN1",
            compose="RHEL-8.10.0-20261002.n.0",
        ),
        col(
            "c1a",
            "2026-10-02T00",
            [P(A1, "PASSED", T("/t/x.py::t1", "PASSED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
            compose="RHEL-8.10.0-20261002.n.0",
        ),
    ]
    # S9: two roots in one run (two arches), one rerun
    SCEN["S9"] = [
        col("rx", "2026-10-01T00", [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))]),
        col(
            "ra",
            "2026-10-01T01",
            [P(A1, "PASSED", T("/t/x.py::t1", "PASSED"))],
            arch="aarch64",
        ),
        col(
            "cx",
            "2026-10-02T00",
            [P(A1, "PASSED", T("/t/x.py::t1", "PASSED"))],
            rerun_of="rx",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]
    # S11: counter-key granularity: two rows share a last segment
    SCEN["S11"] = [
        col(
            "r",
            "2026-10-01T00",
            [
                P(
                    A1,
                    "FAILED",
                    T("/default-0/x.py::test_x", "FAILED"),
                    T("/tests/y.py::test_x", "FAILED"),
                )
            ],
        ),
        col(
            "c1",
            "2026-10-02T00",
            [
                P(
                    A1,
                    "FAILED",
                    T("/default-0/x.py::test_x", "FAILED"),
                    T("/tests/y.py::test_x", "FAILED"),
                )
            ],
            rerun_of="r",
            plan=A1,
            tests=("test_x",),
            run="RUN1",
        ),
    ]
    # S12: G1 normalization across generations (phase differs between gens)
    SCEN["S12"] = [
        col(
            "r", "2026-10-01T00", [P(A1, "FAILED", T("/default-0/x.py::t1", "FAILED"))]
        ),
        col(
            "c1",
            "2026-10-02T00",
            [P(A1, "PASSED", T("/default-1/x.py::t1", "PASSED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]
    # S14: plan marker severity: one test exhausted, other unparseable? (worst wins)
    SCEN["S14"] = [
        col(
            "r",
            "2026-10-01T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"), T("/t/x.py::t2", "FAILED"))],
        ),
        col(
            "c1",
            "2026-10-02T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
        col(
            "c2",
            "2026-10-02T01",
            [],
            verdict="CANCELED",
            rerun_of="r",
            plan=A1,
            tests=("t2",),
            run="RUN1",
        ),
    ]
    # S15: orphan (parent missing)
    SCEN["S15"] = [
        col(
            "o",
            "2026-10-02T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="gone",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]
    # S13: G1 guard over the union of generations: the root runs t1 under two
    # concurrent positional phases (collision -> plan keeps verbatim names); the
    # child runs one phase. A per-generation guard would normalize the child alone.
    SCEN["S13"] = [
        col(
            "r",
            "2026-10-01T00",
            [
                P(
                    A1,
                    "FAILED",
                    T("/default-0/x.py::t1", "FAILED"),
                    T("/default-1/x.py::t1", "FAILED"),
                )
            ],
        ),
        col(
            "c1",
            "2026-10-02T00",
            [P(A1, "PASSED", T("/default-0/x.py::t1", "PASSED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]
    # S16: marker severity inside one plan: t1 exhausted, t2 unparseable twice.
    SCEN["S16"] = [
        col(
            "r",
            "2026-10-01T00",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"), T("/t/x.py::t2", "FAILED"))],
        ),
        col(
            "c1", "2026-10-02T00", [], rerun_of="r", plan=A1, tests=("t2",), run="RUN1"
        ),
        col(
            "c2", "2026-10-03T00", [], rerun_of="c1", plan=A1, tests=("t2",), run="RUN2"
        ),
        col(
            "c3",
            "2026-10-02T01",
            [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))],
            rerun_of="r",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]
    # S17: consolidation order: with no PASSED, the chronologically latest real
    # result wins within a coordinate (child task id sorts before the root's).
    SCEN["S17"] = [
        col("r9", "2026-10-01T00", [P(A1, "FAILED", T("/t/x.py::t1", "FAILED"))]),
        col(
            "c0",
            "2026-10-02T00",
            [P(A1, "ERROR", T("/t/x.py::t1", "ERROR"))],
            rerun_of="r9",
            plan=A1,
            tests=("t1",),
            run="RUN1",
        ),
    ]

    return SCEN


def render_text(tables):
    """Plain-text form of lineage tables (cells, consolidated, footer)."""
    from enge.compare.__main__ import _lineage_footer_lines

    lines = []
    for table in tables:
        lines.append(
            "columns: "
            + ", ".join(f"({i}) {c.arch}" for i, c in enumerate(table.columns, start=1))
        )
        for row in table.rows:
            label = f"    {row.label}" if row.plan_label is not None else row.label
            cells = " | ".join(c.text if c is not None else "—" for c in row.cells)
            lines.append(f"{label} | {cells} | {row.consolidated}")
        lines.extend(f"F {line}" for line in _lineage_footer_lines(table))
    return "\n".join(lines)


class TestLineageExpectedValues(unittest.TestCase):
    maxDiff = None

    def _check(self, scen, selected, show_tests, retry_count, expected):
        tables = E.build_lineage_tables(
            _scenarios()[scen],
            selected,
            show_tests=show_tests,
            splitarch=False,
            splitpath=False,
            retry_count=retry_count,
        )
        self.assertEqual(render_text(tables), expected)

    def test_s1_r_plans_rcnone(self):
        self._check(
            "S1",
            ["r"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F P | PASSED\n"
                "/plans/b | P | PASSED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s1_r_tests_rc1(self):
        self._check(
            "S1",
            ["r"],
            True,
            1,
            (
                "columns: (1) x86_64\n"
                "    /t/x.py::t1 | F F P | PASSED\n"
                "    /t/x.py::t2 | P | PASSED\n"
                "    /t/y.py::t3 | P | PASSED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s1_c2_plans_rcnone(self):
        self._check(
            "S1",
            ["c2"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F P | PASSED\n"
                "/plans/b | P | PASSED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s2_r_plans_rcnone(self):
        self._check(
            "S2",
            ["r"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F F | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s2_r_plans_rc2(self):
        self._check(
            "S2",
            ["r"],
            False,
            2,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F F [X] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s2_r_plans_rc3(self):
        self._check(
            "S2",
            ["r"],
            False,
            3,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F F | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s2_r_tests_rc2(self):
        self._check(
            "S2",
            ["r"],
            True,
            2,
            (
                "columns: (1) x86_64\n"
                "    /t/x.py::t1 | F F F [X] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s3_r_plans_rcnone(self):
        self._check(
            "S3",
            ["r"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F E E [?] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen2=https://a/c2"
            ),
        )

    def test_s4_r_plans_rcnone(self):
        self._check(
            "S4",
            ["r"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F C [-] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1"
            ),
        )

    def test_s5_r_plans_rcnone(self):
        self._check(
            "S5",
            ["r"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F | FAILED\n"
                "/plans/b | F . | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1"
            ),
        )

    def test_s8_r_plans_rcnone(self):
        self._check(
            "S8",
            ["r"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F P | PASSED\n"
                "/plans/b | E P | PASSED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1a gen1=https://a/c1b composes: gen0=RHEL-8.10.0-Nightly gen1=RHEL-8.10.0-20261002.n.0 gen1=RHEL-8.10.0-20261002.n.0"
            ),
        )

    def test_s8_r_tests_rcnone(self):
        self._check(
            "S8",
            ["r"],
            True,
            None,
            (
                "columns: (1) x86_64\n"
                "    /t/x.py::t1 | F P | PASSED\n"
                "/plans/b | E P | PASSED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1a gen1=https://a/c1b composes: gen0=RHEL-8.10.0-Nightly gen1=RHEL-8.10.0-20261002.n.0 gen1=RHEL-8.10.0-20261002.n.0"
            ),
        )

    def test_s9_rx_ra_plans_rc1(self):
        self._check(
            "S9",
            ["rx", "ra"],
            False,
            1,
            (
                "columns: (1) aarch64, (2) x86_64\n"
                "/plans/a | P | F P | PASSED\n"
                "F (1) aarch64 8.10→9.6 gen0=https://a/ra\n"
                "F (2) x86_64 8.10→9.6 gen0=https://a/rx gen1=https://a/cx"
            ),
        )

    def test_s11_r_tests_rc1(self):
        self._check(
            "S11",
            ["r"],
            True,
            1,
            (
                "columns: (1) x86_64\n"
                "    /tests/y.py::test_x | F F [X] | FAILED\n"
                "    /x.py::test_x | F F [X] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1"
            ),
        )

    def test_s12_r_tests_rcnone(self):
        self._check(
            "S12",
            ["r"],
            True,
            None,
            (
                "columns: (1) x86_64\n"
                "    /x.py::t1 | F P | PASSED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1"
            ),
        )

    def test_s14_r_plans_rc1(self):
        self._check(
            "S14",
            ["r"],
            False,
            1,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F F C [X] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen1=https://a/c2"
            ),
        )

    def test_s14_r_tests_rc1(self):
        self._check(
            "S14",
            ["r"],
            True,
            1,
            (
                "columns: (1) x86_64\n"
                "    /t/x.py::t1 | F F [X] | FAILED\n"
                "    /t/x.py::t2 | F C [-] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen1=https://a/c2"
            ),
        )

    def test_s15_o_plans_rc0(self):
        self._check(
            "S15",
            ["o"],
            False,
            0,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F [X] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/o"
            ),
        )

    def test_s13_r_tests_rcnone(self):
        self._check(
            "S13",
            ["r"],
            True,
            None,
            (
                "columns: (1) x86_64\n"
                "    /default-0/x.py::t1 | F P | PASSED\n"
                "    /default-1/x.py::t1 | F . | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1"
            ),
        )

    def test_s16_r_plans_rc1(self):
        self._check(
            "S16",
            ["r"],
            False,
            1,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F E F E [?] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen1=https://a/c3 gen2=https://a/c2"
            ),
        )

    def test_s16_r_tests_rc1(self):
        self._check(
            "S16",
            ["r"],
            True,
            1,
            (
                "columns: (1) x86_64\n"
                "    /t/x.py::t1 | F F [X] | FAILED\n"
                "    /t/x.py::t2 | F E E [?] | FAILED\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r gen1=https://a/c1 gen1=https://a/c3 gen2=https://a/c2"
            ),
        )

    def test_s17_r9_plans_rcnone(self):
        self._check(
            "S17",
            ["r9"],
            False,
            None,
            (
                "columns: (1) x86_64\n"
                "/plans/a | F E | ERROR\n"
                "F (1) x86_64 8.10→9.6 gen0=https://a/r9 gen1=https://a/c0"
            ),
        )


if __name__ == "__main__":
    unittest.main()
