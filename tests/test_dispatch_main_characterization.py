"""Characterization of `enge test`'s dispatch `main()` before R6-3c.

R6-3c moves everything `main()` does up to and including submission into
`submit_dispatch(ctx)`, which returns what happened instead of logging it,
so `enge run` can read generation 0's run_id. `main()` must behave exactly
as before: the same log lines at the same levels and in the same order, the
same summaries on stdout, the same exit codes. These tests pin that, and
pass both before and after the move.

Request expansion and submission are mocked (`expand_set_requests`,
`process_request_spec`, and on the legacy path `setup_submit_test`,
`validate_plan_filters`, `build_tier_plan_specs`); the manifest writer is
the real one `main()` builds, so an accepted request is flushed to disk as
in production. The shared rich console is replaced by a mock: other tests
reconfigure it (`-o json` makes it quiet), so its output is asserted as
calls, and stdout holds only what `print()` wrote.
"""

import contextlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import enge.dispatch.__main__ as dispatch_main
from enge.utils.manifest import ManifestReader

from tests._helpers import captured_logs

_MOD = "enge.dispatch.__main__"
_FAILED_CALL = call("FAILED  demo/tier0/x86_64: boom", style="error")


def _submitted(summary):
    def process(
        idx, total, spec, artifact_type, resolver=None, *, ctx, manifest_writer=None
    ):
        if not getattr(ctx.cli_args, "dryrun", False):
            manifest_writer.add_request(
                f"task-{idx}", set_name="demo", tier="tier0", arch="x86_64"
            )
            manifest_writer.flush(
                Path(ctx.manifest_runs_dir), Path(ctx.manifest_latest)
            )
        return {"status": "submitted", "summary": summary}

    return process


def _failed(
    idx, total, spec, artifact_type, resolver=None, *, ctx, manifest_writer=None
):
    return {
        "status": "failed",
        "error": "boom",
        "set_name": "demo",
        "tier": "tier0",
        "arch": "x86_64",
    }


def _sequence(*processes):
    calls = iter(processes)

    def process(*args, **kwargs):
        return next(calls)(*args, **kwargs)

    return process


class _DispatchHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.runs_dir = tmp / "runs"
        self.latest = tmp / "latest"

    def _ctx(self, *, output_format="terminal", dryrun=False, sets=True):
        cli = SimpleNamespace(
            output_format=output_format,
            dryrun=dryrun,
            set=["demo-set"] if sets else None,
            tier=None,
            plan=None,
            event=None,
            auto_tag=False,
            set_tag=None,
            copr=None,
            brew=None,
        )
        return SimpleNamespace(
            cli_args=cli,
            individual_test_sets=(
                [{"effective_values": {"event": None}}] if sets else []
            ),
            plans=[],
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

    def _run_sets(self, process, n_specs, **ctx_kwargs):
        """main() on the test-set path; returns (exit code, records, stdout)."""
        ctx = self._ctx(**ctx_kwargs)
        out = io.StringIO()
        with (
            patch(f"{_MOD}.expand_set_requests", return_value=[MagicMock()] * n_specs),
            patch(f"{_MOD}.process_request_spec", side_effect=process),
            patch(f"{_MOD}.console") as console,
            captured_logs(_MOD) as records,
            contextlib.redirect_stdout(out),
        ):
            code = dispatch_main.main(ctx)
        self.console_calls = console.print.call_args_list
        return code, records, out.getvalue()

    def _run_legacy(self, specs, process=None):
        """main() on the legacy tiers/plans path."""
        ctx = self._ctx(sets=False)
        out = io.StringIO()
        with (
            patch(f"{_MOD}.setup_submit_test"),
            patch(f"{_MOD}.validate_plan_filters"),
            patch(f"{_MOD}.build_tier_plan_specs", return_value=specs),
            patch(f"{_MOD}.process_request_spec", side_effect=process),
            patch(f"{_MOD}.console") as console,
            captured_logs(_MOD) as records,
            contextlib.redirect_stdout(out),
        ):
            code = dispatch_main.main(ctx)
        self.console_calls = console.print.call_args_list
        return code, records, out.getvalue()

    def _info_plus(self, records):
        return [
            (r.levelname, r.getMessage()) for r in records if r.levelno >= logging.INFO
        ]

    def _run_id(self):
        manifest = ManifestReader.load_latest(self.latest)
        self.assertIsNotNone(manifest)
        return manifest["run_id"]


class TestDispatchMainCharacterization(_DispatchHarness):
    def test_all_submitted(self):
        code, records, out = self._run_sets(
            _sequence(_submitted("summary one"), _submitted("summary two")), 2
        )
        run_id = self._run_id()
        self.assertEqual(code, 0)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 2 request(s)"),
                ("INFO", "Done: 2/2 submitted"),
                ("INFO", f"Run ID: {run_id}"),
                ("INFO", f"Report with: enge report --run {run_id}"),
            ],
        )
        self.assertEqual(out, "summary one\nsummary two\n")
        self.assertEqual(self.console_calls, [])

    def test_done_line_style(self):
        _, records, _ = self._run_sets(_submitted("s"), 1)
        done = [r for r in records if r.getMessage().startswith("Done:")]
        self.assertEqual([r.style for r in done], ["bold green"])
        _, records, _ = self._run_sets(_sequence(_submitted("s"), _failed), 2)
        done = [r for r in records if r.getMessage().startswith("Done:")]
        self.assertEqual([r.style for r in done], ["bold yellow"])

    def test_partial_failure(self):
        code, records, out = self._run_sets(
            _sequence(_submitted("summary one"), _failed), 2
        )
        run_id = self._run_id()
        self.assertEqual(code, 2)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 2 request(s)"),
                ("INFO", "Done: 1/2 submitted"),
                ("INFO", f"Run ID: {run_id}"),
                ("INFO", f"Report with: enge report --run {run_id}"),
                ("WARNING", "1 requests failed"),
            ],
        )
        self.assertEqual(out, "summary one\n")
        self.assertEqual(self.console_calls, [_FAILED_CALL])

    def test_all_failed(self):
        code, records, out = self._run_sets(_failed, 1)
        self.assertEqual(code, 1)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 1 request(s)"),
                ("INFO", "Done: 0/1 submitted"),
                ("CRITICAL", "No requests were successfully submitted!"),
            ],
        )
        self.assertEqual(out, "")
        self.assertEqual(self.console_calls, [_FAILED_CALL])
        self.assertIsNone(ManifestReader.load_latest(self.latest))

    def test_dryrun(self):
        code, records, out = self._run_sets(_submitted("dry"), 1, dryrun=True)
        self.assertEqual(code, 0)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 1 request(s)"),
                ("INFO", "Done: 1/1 submitted"),
            ],
        )
        self.assertEqual(out, "dry\n")

    def test_json_output(self):
        code, records, out = self._run_sets(
            _sequence(_submitted("summary one"), _failed), 2, output_format="json"
        )
        self.assertEqual(code, 2)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 2 request(s)"),
                ("WARNING", "1 requests failed"),
            ],
        )
        self.assertEqual(
            json.loads(out),
            {
                "requests": [
                    {"status": "submitted", "idx": 1},
                    {
                        "status": "failed",
                        "error": "boom",
                        "set_name": "demo",
                        "tier": "tier0",
                        "arch": "x86_64",
                        "idx": 2,
                    },
                ],
                "total": 2,
                "successful": 1,
                "failed": 1,
            },
        )

    def test_set_path_with_no_requests(self):
        code, records, out = self._run_sets(_failed, 0)
        self.assertEqual(code, 1)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 0 request(s)"),
                ("INFO", "Done: 0/0 submitted"),
                ("CRITICAL", "No requests were successfully submitted!"),
            ],
        )
        self.assertEqual(out, "")

    def test_legacy_path_with_no_requests(self):
        code, records, out = self._run_legacy([])
        self.assertEqual(code, 1)
        self.assertEqual(
            self._info_plus(records),
            [
                (
                    "WARNING",
                    "No requests to process: no tiers/plans resolved into "
                    "concrete requests.",
                ),
            ],
        )
        self.assertEqual(out, "")

    def test_legacy_path_submits(self):
        code, records, out = self._run_legacy([MagicMock()], _submitted("legacy"))
        run_id = self._run_id()
        self.assertEqual(code, 0)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 1 request(s)"),
                ("INFO", "Done: 1/1 submitted"),
                ("INFO", f"Run ID: {run_id}"),
                ("INFO", f"Report with: enge report --run {run_id}"),
            ],
        )
        self.assertEqual(out, "legacy\n")

    def test_unexpected_exception_maps_to_1(self):
        def explode(*args, **kwargs):
            raise RuntimeError("kaboom")

        code, records, out = self._run_sets(explode, 1)
        self.assertEqual(code, 1)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 1 request(s)"),
                ("CRITICAL", "Unexpected error in dispatch: kaboom"),
            ],
        )
        self.assertEqual(out, "")

    def test_keyboard_interrupt_maps_to_130(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        code, records, out = self._run_sets(interrupt, 1)
        self.assertEqual(code, 130)
        self.assertEqual(
            self._info_plus(records),
            [
                ("INFO", "Dispatching 1 request(s)"),
                ("INFO", "Operation cancelled by user"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
