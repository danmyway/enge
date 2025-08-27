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


if __name__ == "__main__":
    unittest.main()
