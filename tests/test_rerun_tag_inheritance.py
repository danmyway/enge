"""Tests for the live tag-inheritance path in rerun/__main__.py."""

import unittest
from pathlib import Path

from enge.rerun.__main__ import (
    _extract_tags_from_filename,
    _get_next_rerun_tag,
    _unique_preserve,
)


class TestRerunTagInheritance(unittest.TestCase):
    def test_extract_tags_from_filename(self):
        path = Path("enge_jobs_archive_20260611.rerun.tier0")
        self.assertEqual(_extract_tags_from_filename(path), ["rerun", "tier0"])

    def test_get_next_rerun_tag_progression(self):
        self.assertEqual(_get_next_rerun_tag([]), "rerun")
        self.assertEqual(_get_next_rerun_tag(["rerun"]), "rerun1")
        self.assertEqual(_get_next_rerun_tag(["rerun1"]), "rerun2")

    def test_unique_preserve_base_and_inherited_ordering(self):
        base_tags = ["enge", "automated", "rerun"]
        current_tags = ["rerun", "tier0"]
        result = _unique_preserve([*base_tags, *current_tags])
        self.assertEqual(result, ["enge", "automated", "rerun", "tier0"])


if __name__ == "__main__":
    unittest.main()
