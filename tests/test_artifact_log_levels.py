"""Tests for artifact log-level mapping in reportportal/utils.py.

Regression tests for missing-comma string concatenation in the ERROR
tuple and the bare-string (not 1-tuple) WARN value.
"""

import unittest

from enge.reportportal.utils import (
    ARTIFACT_LOG_LEVELS,
    _ARTIFACT_LEVEL_LOOKUP,
    get_artifact_log_level,
)


class TestArtifactLogLevelMapping(unittest.TestCase):
    """get_artifact_log_level must return the correct level for every
    artifact name listed in ARTIFACT_LOG_LEVELS."""

    # -- Bug #1: three adjacent strings missing commas in the ERROR tuple
    #    Python concatenates them into one key that no real filename matches.

    def test_test_debug_log_is_error(self):
        self.assertEqual(get_artifact_log_level("test_debug.log"), "ERROR")

    def test_leapp_preupgrade_log_is_error(self):
        self.assertEqual(get_artifact_log_level("leapp-preupgrade.log"), "ERROR")

    def test_leapp_out_is_error(self):
        self.assertEqual(get_artifact_log_level("leapp.out"), "ERROR")

    # -- Bug #2: ("tmt-log") is a str, not a 1-tuple; iteration yields chars.

    def test_tmt_log_is_warn(self):
        self.assertEqual(get_artifact_log_level("tmt-log"), "WARN")

    # -- Unaffected entries that must stay correct after the fix.

    def test_tmt_verbose_log_is_error(self):
        self.assertEqual(get_artifact_log_level("tmt-verbose-log"), "ERROR")

    def test_testout_log_is_error(self):
        self.assertEqual(get_artifact_log_level("testout.log"), "ERROR")

    def test_leapp_report_txt_is_error(self):
        self.assertEqual(get_artifact_log_level("leapp-report.txt"), "ERROR")

    def test_leapp_report_json_is_error(self):
        self.assertEqual(get_artifact_log_level("leapp-report.json"), "ERROR")

    # -- Default level for unlisted names.

    def test_unknown_artifact_gets_default(self):
        self.assertEqual(get_artifact_log_level("random-file.txt"), "INFO")


class TestArtifactLogLevelStructure(unittest.TestCase):
    """Structural invariants on the mapping constants."""

    def test_all_values_are_tuples(self):
        for level, names in ARTIFACT_LOG_LEVELS.items():
            with self.subTest(level=level):
                self.assertIsInstance(
                    names,
                    tuple,
                    f"ARTIFACT_LOG_LEVELS[{level!r}] must be a tuple, "
                    f"got {type(names).__name__}",
                )

    def test_lookup_contains_all_intended_filenames(self):
        expected = {
            "tmt-verbose-log",
            "testout.log",
            "test_debug.log",
            "leapp-preupgrade.log",
            "leapp.out",
            "leapp-report.txt",
            "leapp-report.json",
            "tmt-log",
        }
        self.assertEqual(set(_ARTIFACT_LEVEL_LOOKUP.keys()), expected)


if __name__ == "__main__":
    unittest.main()
