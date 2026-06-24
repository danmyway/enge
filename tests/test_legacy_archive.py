import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from enge.utils.legacy_archive import (
    extract_tags_from_filename,
    find_archive_files,
    read_task_ids,
)


class TestReadTaskIds(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_reads_ids_from_file(self):
        f = self.tmp / "jobs"
        f.write_text("aaa\nbbb\nccc\n")
        self.assertEqual(read_task_ids(f), ["aaa", "bbb", "ccc"])

    def test_skips_blank_lines(self):
        f = self.tmp / "jobs"
        f.write_text("aaa\n\nbbb\n")
        self.assertEqual(read_task_ids(f), ["aaa", "bbb"])

    def test_missing_file(self):
        self.assertEqual(read_task_ids(self.tmp / "nonexistent"), [])


class TestFindArchiveFiles(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.archive = Path(self._tmpdir.name) / "archive"
        self.archive.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _create(self, name, content="task-1\n"):
        (self.archive / name).write_text(content)

    def test_finds_all_without_filters(self):
        self._create("enge_jobs_archive_20260622140000")
        self._create("enge_jobs_archive_20260622150000.tier0")
        files = find_archive_files(self.archive)
        self.assertEqual(len(files), 2)

    def test_tag_pattern_matches_extension(self):
        self._create("enge_jobs_archive_20260622140000.tier0.x86_64")
        self._create("enge_jobs_archive_20260622150000.tier1")
        found = find_archive_files(self.archive, tag_patterns=["tier0"])
        self.assertEqual(len(found), 1)
        self.assertIn("tier0", found[0].name)

    def test_tag_regex_pattern(self):
        self._create("enge_jobs_archive_20260622140000.tier0")
        self._create("enge_jobs_archive_20260622150000.tier1")
        self._create("enge_jobs_archive_20260622160000.smoke")
        found = find_archive_files(self.archive, tag_patterns=[r"tier\d"])
        self.assertEqual(len(found), 2)

    def test_date_filter_since(self):
        self._create("enge_jobs_archive_20260620140000")
        self._create("enge_jobs_archive_20260622140000")
        since = datetime(2026, 6, 21, 0, 0, 0)
        found = find_archive_files(self.archive, since=since)
        self.assertEqual(len(found), 1)
        self.assertIn("20260622", found[0].name)

    def test_date_filter_until(self):
        self._create("enge_jobs_archive_20260620140000")
        self._create("enge_jobs_archive_20260622140000")
        until = datetime(2026, 6, 21, 0, 0, 0)
        found = find_archive_files(self.archive, until=until)
        self.assertEqual(len(found), 1)
        self.assertIn("20260620", found[0].name)

    def test_skips_migrated_files(self):
        self._create("enge_jobs_archive_20260622140000")
        (self.archive / "enge_jobs_archive_20260622140000.migrated").write_text("")
        self._create("enge_jobs_archive_20260622150000")
        files = find_archive_files(self.archive)
        self.assertEqual(len(files), 2)

    def test_nonexistent_dir(self):
        self.assertEqual(find_archive_files(Path("/nonexistent/dir")), [])


class TestExtractTags(unittest.TestCase):
    def test_extracts_tags(self):
        p = Path("enge_jobs_archive_20260622140000.tier0.x86_64")
        self.assertEqual(extract_tags_from_filename(p), ["tier0", "x86_64"])

    def test_no_tags(self):
        p = Path("enge_jobs_archive_20260622140000")
        self.assertEqual(extract_tags_from_filename(p), [])

    def test_rerun_tag(self):
        p = Path("enge_jobs_archive_20260622140000.regression.rerun")
        self.assertEqual(extract_tags_from_filename(p), ["regression", "rerun"])


if __name__ == "__main__":
    unittest.main()
