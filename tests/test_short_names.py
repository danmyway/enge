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
from unittest.mock import patch

from tests._helpers import make_app_context
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


class TestShortName(unittest.TestCase):
    """New `--short` rule (maintainer-ratified 2026-07-27): split on `::`
    and keep everything after the FIRST separator (refinement C-1 -- this
    preserves a pytest node ID's class name, e.g. `TestX::test_y`, rather
    than dropping it); if no `::` is present, split on `/` and keep the
    last two segments (a single trailing segment is not enough to
    disambiguate destructive/nondestructive plan names)."""

    CASES = [
        ("/plans/newstyle/nondestructive/tier0only", "nondestructive/tier0only"),
        (
            "/plans/newstyle/nondestructive/verification_99_103_ctc2",
            "nondestructive/verification_99_103_ctc2",
        ),
        (
            "/plans/newstyle/nondestructive/verification_99_103_ctc2_optional",
            "nondestructive/verification_99_103_ctc2_optional",
        ),
        (
            "/tests/newstyle/upgrades/tests/nondestructive/"
            "test_selinux_labels.py::TestSelinuxLabels",
            "TestSelinuxLabels",
        ),
        (
            "/plans/newstyle/upgrades/tests/destructive/https_custom_repos/"
            "test_https_custom_repos.py::TestHttpsCustomReposCertInEtcPki",
            "TestHttpsCustomReposCertInEtcPki",
        ),
        ("/tests/foo/bar/baz.py::TestX::test_y", "TestX::test_y"),
        ("/plans/tier0", "plans/tier0"),
        ("/", "/"),
    ]

    def test_short_name_acceptance_table(self):
        from enge.report.__main__ import _short_name

        for name, expected in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(_short_name(name), expected)


class TestReportShortIntegration(unittest.TestCase):
    """`enge report --short` must render plan/test names via the new
    `_short_name` rule, not the old last-segment-only split."""

    def _ctx(self, **cli_overrides):
        cli = {
            "list": False,
            "show_ids": False,
            "compare": False,
            "jira": False,
            "short": True,
            "skip_pass": False,
            "show_tests": True,
            "output_format": "terminal",
        }
        cli.update(cli_overrides)
        return make_app_context(action="report", extra_cli=cli)

    def test_short_flag_uses_new_shortening_rule(self):
        import enge.report.__main__ as rm

        parsed_dict = {
            "task-uuid-1": {
                "testsuites": [
                    {
                        "testsuite_name": "/plans/newstyle/nondestructive/tier0only",
                        "testsuite_result": "PASSED",
                        "testsuite_arch": "x86_64",
                        "testcases": [
                            {
                                "testcase_name": (
                                    "/tests/newstyle/upgrades/tests/"
                                    "nondestructive/test_selinux_labels.py"
                                    "::TestSelinuxLabels"
                                ),
                                "testcase_result": "PASSED",
                            }
                        ],
                    }
                ],
            }
        }

        with patch.object(
            rm,
            "_parse_request_xunit_with_retval",
            return_value=(parsed_dict, 0, []),
        ):
            tables_list, _retval, _task_results = rm.build_table(self._ctx())

        (result_table, _metadata) = tables_list[0]
        # The plan name and the test case land in separate rows (build_table
        # adds one add_row() call per testsuite, then one per testcase) --
        # the plan cell is row 0, the test-case cell is row 1.
        plan_cell = result_table.columns[0]._cells[0]
        test_cell = result_table.columns[2]._cells[1]

        self.assertEqual(plan_cell, rm.colorize("PASSED", "nondestructive/tier0only"))
        self.assertEqual(test_cell, rm.colorize("PASSED", "TestSelinuxLabels"))


class TestReportDefaultShortFlip(unittest.TestCase):
    """RULING F4 (maintainer, 2026-07-27, restated v23 §2.9): `--short`
    becomes the default for `enge report`; `-l/--long` opts back into the
    full verbatim name that used to be the default."""

    PARSED_DICT = {
        "task-uuid-1": {
            "testsuites": [
                {
                    "testsuite_name": "/plans/newstyle/nondestructive/tier0only",
                    "testsuite_result": "PASSED",
                    "testsuite_arch": "x86_64",
                    "testcases": [
                        {
                            "testcase_name": (
                                "/tests/newstyle/upgrades/tests/"
                                "nondestructive/test_selinux_labels.py"
                                "::TestSelinuxLabels"
                            ),
                            "testcase_result": "PASSED",
                        }
                    ],
                }
            ],
        }
    }

    def _ctx(self, **cli_overrides):
        cli = {
            "list": False,
            "show_ids": False,
            "compare": False,
            "jira": False,
            "skip_pass": False,
            "show_tests": True,
            "output_format": "terminal",
        }
        cli.update(cli_overrides)
        return make_app_context(action="report", extra_cli=cli)

    def test_default_renders_shortened_name_with_neither_flag(self):
        import enge.report.__main__ as rm

        with patch.object(
            rm,
            "_parse_request_xunit_with_retval",
            return_value=(self.PARSED_DICT, 0, []),
        ):
            tables_list, _retval, _task_results = rm.build_table(self._ctx())

        (result_table, _metadata) = tables_list[0]
        plan_cell = result_table.columns[0]._cells[0]
        test_cell = result_table.columns[2]._cells[1]

        self.assertEqual(plan_cell, rm.colorize("PASSED", "nondestructive/tier0only"))
        self.assertEqual(test_cell, rm.colorize("PASSED", "TestSelinuxLabels"))

    def test_long_flag_renders_full_verbatim_name(self):
        import enge.report.__main__ as rm

        with patch.object(
            rm,
            "_parse_request_xunit_with_retval",
            return_value=(self.PARSED_DICT, 0, []),
        ):
            tables_list, _retval, _task_results = rm.build_table(self._ctx(long=True))

        (result_table, _metadata) = tables_list[0]
        plan_cell = result_table.columns[0]._cells[0]
        test_cell = result_table.columns[2]._cells[1]

        self.assertEqual(
            plan_cell,
            rm.colorize("PASSED", "plans/newstyle/nondestructive/tier0only"),
        )
        self.assertEqual(
            test_cell,
            rm.colorize(
                "PASSED",
                "tests/newstyle/upgrades/tests/nondestructive/"
                "test_selinux_labels.py::TestSelinuxLabels",
            ),
        )


if __name__ == "__main__":
    unittest.main()
