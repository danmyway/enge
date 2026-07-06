"""Tests for artifact log-level mapping in reportportal/utils.py.

All artifacts currently upload at INFO (the default level). The
per-artifact level machinery is retained for a future curated mapping;
these tests lock the current all-INFO behaviour and the structural
invariant that any future entries must be tuples.
"""

import unittest

from enge.reportportal.utils import (
    ARTIFACT_LOG_LEVELS,
    _ARTIFACT_LEVEL_LOOKUP,
    get_artifact_log_level,
)


class TestAllArtifactsUploadAtInfo(unittest.TestCase):
    """Every artifact name must resolve to INFO while the mapping is
    intentionally empty."""

    def test_tmt_verbose_log(self):
        self.assertEqual(get_artifact_log_level("tmt-verbose-log"), "INFO")

    def test_testout_log(self):
        self.assertEqual(get_artifact_log_level("testout.log"), "INFO")

    def test_test_debug_log(self):
        self.assertEqual(get_artifact_log_level("test_debug.log"), "INFO")

    def test_leapp_preupgrade_log(self):
        self.assertEqual(get_artifact_log_level("leapp-preupgrade.log"), "INFO")

    def test_leapp_out(self):
        self.assertEqual(get_artifact_log_level("leapp.out"), "INFO")

    def test_leapp_report_txt(self):
        self.assertEqual(get_artifact_log_level("leapp-report.txt"), "INFO")

    def test_leapp_report_json(self):
        self.assertEqual(get_artifact_log_level("leapp-report.json"), "INFO")

    def test_tmt_log(self):
        self.assertEqual(get_artifact_log_level("tmt-log"), "INFO")

    def test_unknown_artifact(self):
        self.assertEqual(get_artifact_log_level("random-file.txt"), "INFO")


class TestArtifactLogLevelStructure(unittest.TestCase):
    """Structural invariants on the mapping constants."""

    def test_mapping_is_currently_empty(self):
        self.assertEqual(len(ARTIFACT_LOG_LEVELS), 0)

    def test_lookup_is_currently_empty(self):
        self.assertEqual(len(_ARTIFACT_LEVEL_LOOKUP), 0)

    def test_all_values_are_tuples(self):
        for level, names in ARTIFACT_LOG_LEVELS.items():
            with self.subTest(level=level):
                self.assertIsInstance(
                    names,
                    tuple,
                    f"ARTIFACT_LOG_LEVELS[{level!r}] must be a tuple, "
                    f"got {type(names).__name__}",
                )


if __name__ == "__main__":
    unittest.main()
