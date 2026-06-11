"""Regression tests for the latest-jobs file clear-once-per-run fix.

Before the fix, SubmitTest.record_task_ids() called os.unlink() on the latest
file on every invocation, so in a multi-request dispatch only the last task ID
survived.  After the fix, clear_latest_jobs_file() is called once before the
dispatch loop and record_task_ids() only appends.  Dry-run invocations must
never clear the file.
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from enge.dispatch import tf_send_request
from enge.dispatch.tf_send_request import SubmitTest, clear_latest_jobs_file


def _make_opts(archive_dir, latest_path):
    return SimpleNamespace(
        archive_tasks_latest=latest_path,
        archive_tasks_default=archive_dir,
        testing_farm_endpoint=SimpleNamespace(
            log_artifact_baseurl="http://logs.example.com",
            api_endpoint_url="http://api.example.com",
        ),
        cli_args=SimpleNamespace(
            dryrun=False,
            action="test",
            wait=False,
            set_tag=None,
            auto_tag=False,
        ),
    )


class TestRecordTaskIds(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.archive_dir = self.tmp / "archive"
        self.archive_dir.mkdir()
        self.latest_path = str(self.tmp / "latest_jobs")
        self.opts = _make_opts(str(self.archive_dir), self.latest_path)
        self._patcher = patch.object(tf_send_request, "parsed_opts", self.opts)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _submit(self, archive_filename="enge_jobs_archive_test"):
        return SubmitTest(shared_archive_filename=archive_filename)

    def test_all_task_ids_present_in_latest_file(self):
        """Three record_task_ids calls across separate SubmitTest instances must
        all appear in the latest file after one clear_latest_jobs_file() call."""
        clear_latest_jobs_file()
        ids = ["task-aaa", "task-bbb", "task-ccc"]
        for task_id in ids:
            self._submit().record_task_ids(task_id)
        latest = Path(self.latest_path).read_text().splitlines()
        self.assertEqual(latest, ids)

    def test_archive_file_also_contains_all_task_ids(self):
        """Archive file must contain all IDs (unchanged append semantics)."""
        clear_latest_jobs_file()
        ids = ["task-111", "task-222", "task-333"]
        for task_id in ids:
            self._submit().record_task_ids(task_id)
        archive_files = list(self.archive_dir.iterdir())
        self.assertEqual(len(archive_files), 1)
        archive_lines = archive_files[0].read_text().splitlines()
        self.assertEqual(archive_lines, ids)

    def test_second_run_does_not_contain_first_run_ids(self):
        """Two separate runs (two clear calls) must produce independent latest files."""
        # First run: two requests
        clear_latest_jobs_file()
        for task_id in ["run1-aaa", "run1-bbb"]:
            self._submit("enge_jobs_archive_run1").record_task_ids(task_id)

        # Second run: one request
        clear_latest_jobs_file()
        self._submit("enge_jobs_archive_run2").record_task_ids("run2-zzz")

        latest = Path(self.latest_path).read_text().splitlines()
        self.assertEqual(latest, ["run2-zzz"])
        self.assertNotIn("run1-aaa", latest)
        self.assertNotIn("run1-bbb", latest)

    def test_clear_is_noop_when_file_absent(self):
        """clear_latest_jobs_file() must not raise when the file does not exist."""
        self.assertFalse(Path(self.latest_path).exists())
        clear_latest_jobs_file()  # must not raise

    def test_dryrun_does_not_clear_latest_file(self):
        """When dryrun=True the helper must not clear the file."""
        from enge.dispatch.tf_send_request import maybe_clear_latest_jobs_file

        sentinel = "previous-run-id"
        Path(self.latest_path).write_text(f"{sentinel}\n")

        self.opts.cli_args.dryrun = True
        maybe_clear_latest_jobs_file()  # calls REAL production helper

        self.assertTrue(Path(self.latest_path).exists())
        self.assertIn(sentinel, Path(self.latest_path).read_text())

    def test_non_dryrun_clears_latest_file(self):
        """When dryrun=False the helper must remove the file."""
        from enge.dispatch.tf_send_request import maybe_clear_latest_jobs_file

        Path(self.latest_path).write_text("old-run-id\n")

        self.opts.cli_args.dryrun = False
        maybe_clear_latest_jobs_file()  # calls REAL production helper

        self.assertFalse(Path(self.latest_path).exists())


if __name__ == "__main__":
    unittest.main()
