"""RED/GREEN pins for dispatch completion run_id visibility (L4).

Exercises enge.dispatch.__main__.main() with process_request_spec and
expand_set_requests mocked (network/artifact resolution is out of scope
here -- see test_dispatch_golden.py for that), but manifest_writer is the
REAL ManifestWriter instance main() constructs, so a fake process function
that calls manifest_writer.add_request()/flush() exercises the real
manifest write path. This lets the "logged ULID equals the flushed
manifest's run_id" assertion read the manifest back off disk instead of
comparing against a literal.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from enge.dispatch.__main__ import main
from enge.utils.manifest import ManifestReader


class TestDispatchRunIdLogging(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _ctx(self, *, output_format="terminal", dryrun=False):
        cli = SimpleNamespace(
            output_format=output_format,
            dryrun=dryrun,
            set=["demo-set"],
            tier=None,
            plan=None,
            event=None,
            auto_tag=False,
            set_tag=None,
            copr=None,
        )
        return SimpleNamespace(
            cli_args=cli,
            individual_test_sets=[{"effective_values": {"event": None}}],
            plans=[],
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

    @staticmethod
    def _fake_process_flushing(
        idx, total, spec, artifact_type, resolver=None, *, ctx, manifest_writer=None
    ):
        manifest_writer.add_request(
            f"task-{idx}", set_name="demo", tier="tier0", arch="x86_64"
        )
        manifest_writer.flush(Path(ctx.manifest_runs_dir), Path(ctx.manifest_latest))
        return {"status": "submitted", "summary": "ok", "idx": idx}

    @staticmethod
    def _fake_process_no_flush(
        idx, total, spec, artifact_type, resolver=None, *, ctx, manifest_writer=None
    ):
        # Mirrors dryrun's real behavior: submit_test "succeeds" but the
        # manifest is never touched.
        return {"status": "submitted", "summary": "dry", "idx": idx}

    @staticmethod
    def _fake_process_failed(
        idx, total, spec, artifact_type, resolver=None, *, ctx, manifest_writer=None
    ):
        return {
            "status": "failed",
            "error": "boom",
            "set_name": "demo",
            "tier": "tier0",
            "arch": "x86_64",
        }

    @patch("enge.dispatch.__main__.process_request_spec")
    @patch("enge.dispatch.__main__.expand_set_requests")
    def test_run_id_logged_once_and_matches_flushed_manifest(
        self, mock_expand, mock_process
    ):
        mock_expand.return_value = [MagicMock()]
        mock_process.side_effect = self._fake_process_flushing
        ctx = self._ctx()

        with self.assertLogs("enge.dispatch.__main__", level="INFO") as cm:
            main(ctx)

        manifest_data = ManifestReader.load_latest(self.latest)
        self.assertIsNotNone(manifest_data, "expected a manifest to be flushed")
        run_id = manifest_data["run_id"]

        run_id_lines = [m for m in cm.output if "Run ID:" in m]
        self.assertEqual(
            len(run_id_lines), 1, f"expected exactly one Run ID line, got: {cm.output}"
        )
        self.assertIn(run_id, run_id_lines[0])

        hint_lines = [m for m in cm.output if "Report with:" in m]
        self.assertEqual(len(hint_lines), 1)
        self.assertIn(f"enge report --run {run_id}", hint_lines[0])

    @patch("enge.dispatch.__main__.process_request_spec")
    @patch("enge.dispatch.__main__.expand_set_requests")
    def test_run_id_logged_exactly_once_across_multiple_specs(
        self, mock_expand, mock_process
    ):
        mock_expand.return_value = [MagicMock(), MagicMock()]
        mock_process.side_effect = self._fake_process_flushing
        ctx = self._ctx()

        with self.assertLogs("enge.dispatch.__main__", level="INFO") as cm:
            main(ctx)

        run_id_lines = [m for m in cm.output if "Run ID:" in m]
        self.assertEqual(
            len(run_id_lines),
            1,
            f"expected the Run ID line once per invocation, not once per "
            f"request, got: {cm.output}",
        )

    @patch("enge.dispatch.__main__.process_request_spec")
    @patch("enge.dispatch.__main__.expand_set_requests")
    def test_json_output_suppresses_run_id_line(self, mock_expand, mock_process):
        mock_expand.return_value = [MagicMock()]
        mock_process.side_effect = self._fake_process_flushing
        ctx = self._ctx(output_format="json")

        with self.assertLogs("enge.dispatch.__main__", level="INFO") as cm:
            main(ctx)

        self.assertFalse(any("Run ID:" in m for m in cm.output))
        self.assertFalse(any("Report with:" in m for m in cm.output))

    @patch("enge.dispatch.__main__.process_request_spec")
    @patch("enge.dispatch.__main__.expand_set_requests")
    def test_dryrun_suppresses_run_id_line_despite_full_success(
        self, mock_expand, mock_process
    ):
        # Deviation from the literal prompt text: this pins the
        # dryrun/no-manifest guard found during the STOP-gate investigation
        # (dryrun never flushes, so an unguarded log would advertise a
        # run_id with nothing on disk behind it).
        mock_expand.return_value = [MagicMock()]
        mock_process.side_effect = self._fake_process_no_flush
        ctx = self._ctx(dryrun=True)

        with self.assertLogs("enge.dispatch.__main__", level="INFO") as cm:
            main(ctx)

        self.assertFalse(any("Run ID:" in m for m in cm.output))
        self.assertFalse(any("Report with:" in m for m in cm.output))
        self.assertIsNone(ManifestReader.load_latest(self.latest))

    @patch("enge.dispatch.__main__.process_request_spec")
    @patch("enge.dispatch.__main__.expand_set_requests")
    def test_all_requests_failed_suppresses_run_id_line(
        self, mock_expand, mock_process
    ):
        # Deviation from the literal prompt text: pins the other half of
        # the guard -- zero successful (non-dryrun) requests also never
        # flush a manifest.
        mock_expand.return_value = [MagicMock()]
        mock_process.side_effect = self._fake_process_failed
        ctx = self._ctx()

        with self.assertLogs("enge.dispatch.__main__", level="INFO") as cm:
            main(ctx)

        self.assertFalse(any("Run ID:" in m for m in cm.output))
        self.assertFalse(any("Report with:" in m for m in cm.output))
        self.assertIsNone(ManifestReader.load_latest(self.latest))


if __name__ == "__main__":
    unittest.main()
