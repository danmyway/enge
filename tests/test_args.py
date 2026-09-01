"""
Unit tests for command-line options
"""

import unittest

from enge.utils.arg_parser import get_arguments


class TestArgs(unittest.TestCase):
    def test_input_args_are_appended(self):
        """enge report -i should aggregate multiple inputs when passed repeatedly"""
        args = get_arguments(
            [
                "report",
                "-i",
                "http://artifacts.osci.redhat.com/testing-farm/909143cf-89ca-4d62-8f81-959bf8ab4d03/",
                "-i",
                "http://artifacts.osci.redhat.com/testing-farm/14612a82-002d-4a8d-a5c4-613a0a75efeb/",
            ]
        )
        self.assertEqual(len(args.input), 2)

        # Passing multiple values to a single -i is not supported
        with self.assertRaises(SystemExit):
            get_arguments(
                [
                    "report",
                    "-i",
                    "http://artifacts.osci.redhat.com/testing-farm/909143cf-89ca-4d62-8f81-959bf8ab4d03/",
                    "http://artifacts.osci.redhat.com/testing-farm/14612a82-002d-4a8d-a5c4-613a0a75efeb/",
                ]
            )


class TestShortLongMutualExclusion(unittest.TestCase):
    """RULING F4 / Coordinator Refinement 2 (2026-07-27, restated v23
    §2.9): `-s/--short` and `-l/--long` are mutually exclusive on both
    `report` and `compare`."""

    def test_report_short_and_long_together_is_parse_error(self):
        with self.assertRaises(SystemExit):
            get_arguments(["report", "-s", "-l"])

    def test_compare_short_and_long_together_is_parse_error(self):
        with self.assertRaises(SystemExit):
            get_arguments(["compare", "-s", "-l"])

    def test_report_long_flag_parses(self):
        args = get_arguments(["report", "-l"])
        self.assertTrue(args.long)

    def test_compare_long_flag_parses(self):
        args = get_arguments(["compare", "-l"])
        self.assertTrue(args.long)


if __name__ == "__main__":
    unittest.main()
