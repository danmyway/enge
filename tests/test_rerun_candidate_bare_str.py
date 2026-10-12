"""RerunCandidate refuses a bare str where it expects a sequence of names (L52).

``tuple("plan")`` is ``('p', 'l', 'a', 'n')``, so without an explicit guard a
bare str is silently split into one-character names.
"""

import unittest

from enge.rerun.__main__ import RerunCandidate


def _refusal(field):
    """The full bare-str message for one field, anchored at its start, so a
    guard that names the wrong field cannot match (``plans`` is a suffix of
    ``undefined_plans``)."""
    return rf"^RerunCandidate\.{field} must be a sequence of names, not a bare str: "


class RerunCandidateBareStrTests(unittest.TestCase):
    def test_b1_plans_bare_str_refused(self):
        with self.assertRaisesRegex(ValueError, _refusal("plans")):
            RerunCandidate("t1", plans="plan")

    def test_b2_realistic_plan_path_refused_for_the_real_cause(self):
        # Already refused today, but by the duplicate-plan rule: the message
        # must name the bare str, not a "repeated plan".
        with self.assertRaisesRegex(ValueError, _refusal("plans")):
            RerunCandidate("t1", plans="/plan/a")

    def test_b3_undefined_plans_bare_str_refused(self):
        with self.assertRaisesRegex(ValueError, _refusal("undefined_plans")):
            RerunCandidate("t1", plans=("/",), undefined_plans="/")

    def test_b4_tests_by_plan_value_bare_str_refused(self):
        with self.assertRaisesRegex(ValueError, _refusal("tests_by_plan")):
            RerunCandidate("t1", plans=("/p",), tests_by_plan={"/p": "t1"})

    def test_c1_lists_still_normalize_to_tuples(self):
        cand = RerunCandidate(
            "t1",
            plans=["/p"],
            tests_by_plan={"/p": ["t1"]},
            undefined_plans=["/p"],
        )
        self.assertEqual(cand.plans, ("/p",))
        self.assertEqual(cand.tests_by_plan, {"/p": ("t1",)})
        self.assertEqual(cand.undefined_plans, ("/p",))


if __name__ == "__main__":
    unittest.main()
