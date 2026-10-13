"""A plan-only plan is dispatched in exactly one rerun payload (Q-R6-J).

A task whose failing plans include an UNDEFINED plan without testcases is
rerun as two payloads: a filtered one (named plans plus the failed tests'
`test_name`) and a plan-only one for the UNDEFINED plans. The plan-only
plans must not ride in the filtered payload as well: there they would run
under another plan's test filter, and each payload's recorded `plan` would
cover them, so `enge.lineage` would charge them twice per generation.
"""

import unittest
from unittest.mock import MagicMock, patch

from enge.rerun.__main__ import RerunCandidate, RerunJobs

from tests._helpers import make_app_context

TASK = "cccccccc-0000-0000-0000-00000000000c"


def _tf_details():
    return {
        "id": TASK,
        "state": "complete",
        "result": {"overall": "failed"},
        "environments_requested": [
            {"os": {"compose": "RHEL-9.0-nightly"}, "arch": "x86_64"}
        ],
        "test": {"fmf": {"name": "/orig/plan", "plan_filter": "tier:0"}},
    }


def _payloads(candidate):
    ctx = make_app_context(action="rerun", extra_cli={"dryrun": False})
    reply = MagicMock()
    reply.json.return_value = _tf_details()
    repin = patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u, **kw: c)
    with patch("enge.rerun.__main__.http_get", return_value=reply):
        with repin:
            jobs = RerunJobs(ctx, candidates=[candidate])
            jobs.qualify_candidates()
            return jobs.build_rerun_payloads(jobs.rerun_uuids)


def _mixed():
    return RerunCandidate(
        task_id=TASK,
        plans=("/plan/a", "/plan/undef", "/plan/b"),
        tests_by_plan={"/plan/a": ("t1",), "/plan/b": ("t2",)},
        undefined_plans=("/plan/undef",),
    )


class TestPlanOnlyPlansRunOnce(unittest.TestCase):
    def test_filtered_payload_omits_the_plan_only_plans(self):
        payloads = _payloads(_mixed())
        # Premise: the split happened (two payloads, a test filter on the
        # first), so the name below is the filtered payload's.
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["test"]["fmf"]["test_name"], "t1$|t2$")

        self.assertEqual(payloads[0]["test"]["fmf"]["name"], "/plan/a$|/plan/b$")
        self.assertEqual(payloads[1]["test"]["fmf"]["name"], "/plan/undef$")
        self.assertNotIn("test_name", payloads[1]["test"]["fmf"])

    def test_each_plan_is_in_exactly_one_payload(self):
        payloads = _payloads(_mixed())
        self.assertEqual(len(payloads), 2)

        names = [
            plan
            for payload in payloads
            for plan in payload["test"]["fmf"]["name"].split("|")
        ]
        self.assertEqual(sorted(names), ["/plan/a$", "/plan/b$", "/plan/undef$"])

    def test_without_a_test_filter_one_payload_keeps_every_plan(self):
        payloads = _payloads(
            RerunCandidate(
                task_id=TASK,
                plans=("/plan/u1", "/plan/u2"),
                undefined_plans=("/plan/u1", "/plan/u2"),
            )
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["test"]["fmf"]["name"], "/plan/u1$|/plan/u2$")
        self.assertNotIn("test_name", payloads[0]["test"]["fmf"])
