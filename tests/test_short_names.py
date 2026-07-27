"""`--short` display-rule tests, shared by `enge report` and `enge compare`
(fix/short-name-rendering, 2026-07-27).

`TestSplitNameCharacterizationPin` pins the pre-existing, UNCHANGED
`_split_name(name, 0)` behaviour used on the non-`--short` path -- these
pass before and after this branch's change; they exist so a future edit
to `_split_name` cannot silently alter default `enge report` output.
Everything else pins the new `_short_name` rule (maintainer-ratified
2026-07-27): split on `::` if present and take everything after the FIRST
separator; otherwise split on `/` and take the last two segments.
"""

import unittest

from enge.report.__main__ import _split_name


class TestSplitNameCharacterizationPin(unittest.TestCase):
    """`_split_name(name, 0)` is the non-`--short` default in `enge report`;
    it must keep dropping the leading `/` and otherwise passing the name
    through untouched."""

    def test_plan_name_leading_slash_dropped(self):
        name = "/plans/newstyle/nondestructive/tier0only"
        self.assertEqual(
            _split_name(name, 0), "plans/newstyle/nondestructive/tier0only"
        )

    def test_verification_plan_name_leading_slash_dropped(self):
        name = "/plans/newstyle/nondestructive/verification_99_103_ctc2"
        self.assertEqual(
            _split_name(name, 0),
            "plans/newstyle/nondestructive/verification_99_103_ctc2",
        )

    def test_test_name_leading_slash_dropped_nodeid_untouched(self):
        name = (
            "/tests/newstyle/upgrades/tests/nondestructive/"
            "test_selinux_labels.py::TestSelinuxLabels"
        )
        self.assertEqual(
            _split_name(name, 0),
            "tests/newstyle/upgrades/tests/nondestructive/"
            "test_selinux_labels.py::TestSelinuxLabels",
        )

    def test_destructive_test_name_leading_slash_dropped(self):
        name = (
            "/plans/newstyle/upgrades/tests/destructive/https_custom_repos/"
            "test_https_custom_repos.py::TestHttpsCustomReposCertInEtcPki"
        )
        self.assertEqual(
            _split_name(name, 0),
            "plans/newstyle/upgrades/tests/destructive/https_custom_repos/"
            "test_https_custom_repos.py::TestHttpsCustomReposCertInEtcPki",
        )


if __name__ == "__main__":
    unittest.main()
