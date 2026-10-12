"""R6-3c: `submit_dispatch(ctx)` returns what dispatch did.

`enge run` dispatches generation 0 and must then know its run_id; `main()`
only logged it. `submit_dispatch` is everything `main()` did up to and
including submission. It returns a frozen `DispatchSubmission(run_id,
total, submitted, results)` and leaves the `Done:` / `Run ID:` lines, the
summaries and the exit-code mapping to `main()` (pinned unchanged by
tests/test_dispatch_main_characterization.py). It never swallows an
exception: mapping one to an exit code is the caller's job.

`submit_dispatch` and `DispatchSubmission` are looked up on the module at
call time, so before they exist each test fails on its own.
"""

import contextlib
import dataclasses
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import enge.dispatch.__main__ as dispatch_main
from enge.utils.manifest import ManifestReader

from tests._helpers import captured_logs

_MOD = "enge.dispatch.__main__"


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
    return {"status": "failed", "error": "boom", "set_name": "demo"}


def _sequence(*processes):
    calls = iter(processes)

    def process(*args, **kwargs):
        return next(calls)(*args, **kwargs)

    return process


class TestSubmitDispatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.runs_dir = tmp / "runs"
        self.latest = tmp / "latest"

    def _ctx(self, *, dryrun=False, sets=True):
        cli = SimpleNamespace(
            output_format="terminal",
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

    def _submit(self, process, n_specs, **ctx_kwargs):
        """submit_dispatch on the test-set path; (result, records, stdout, console)."""
        ctx = self._ctx(**ctx_kwargs)
        out = io.StringIO()
        with (
            patch(f"{_MOD}.expand_set_requests", return_value=[MagicMock()] * n_specs),
            patch(f"{_MOD}.process_request_spec", side_effect=process),
            patch(f"{_MOD}.console") as console,
            captured_logs(_MOD) as records,
            contextlib.redirect_stdout(out),
        ):
            result = dispatch_main.submit_dispatch(ctx)
        return result, records, out.getvalue(), console

    def _run_id(self):
        manifest = ManifestReader.load_latest(self.latest)
        self.assertIsNotNone(manifest)
        return manifest["run_id"]

    def test_result_all_submitted(self):
        result, _, _, _ = self._submit(
            _sequence(_submitted("one"), _submitted("two")), 2
        )
        self.assertEqual(
            result,
            dispatch_main.DispatchSubmission(
                run_id=self._run_id(),
                total=2,
                submitted=2,
                results=(
                    {"status": "submitted", "summary": "one", "idx": 1},
                    {"status": "submitted", "summary": "two", "idx": 2},
                ),
            ),
        )

    def test_result_partial_failure(self):
        result, _, _, _ = self._submit(_sequence(_failed, _submitted("two")), 2)
        self.assertEqual(
            result,
            dispatch_main.DispatchSubmission(
                run_id=self._run_id(),
                total=2,
                submitted=1,
                results=(
                    {"status": "failed", "error": "boom", "set_name": "demo", "idx": 1},
                    {"status": "submitted", "summary": "two", "idx": 2},
                ),
            ),
        )

    def test_all_failed_has_no_run_id(self):
        result, _, _, _ = self._submit(_failed, 1)
        # Premise: nothing was written to the manifest.
        self.assertIsNone(ManifestReader.load_latest(self.latest))
        self.assertEqual((result.run_id, result.total, result.submitted), (None, 1, 0))

    def test_dryrun_has_no_run_id(self):
        result, _, _, _ = self._submit(_submitted("dry"), 1, dryrun=True)
        # Premise: the request counts as submitted.
        self.assertEqual((result.total, result.submitted), (1, 1))
        self.assertIsNone(result.run_id)

    def test_reports_nothing_itself(self):
        _, records, out, console = self._submit(
            _sequence(_submitted("one"), _failed), 2
        )
        # Premise: the submission itself logged.
        self.assertIn("Dispatching 2 request(s)", [r.getMessage() for r in records])
        for prefix in (
            "Done:",
            "Run ID:",
            "Report with:",
            "No requests were",
            "1 requests",
        ):
            self.assertEqual(
                [r.getMessage() for r in records if r.getMessage().startswith(prefix)],
                [],
                prefix,
            )
        self.assertEqual(out, "")
        console.print.assert_not_called()

    def test_legacy_path_with_no_requests_returns_none(self):
        ctx = self._ctx(sets=False)
        with (
            patch(f"{_MOD}.setup_submit_test"),
            patch(f"{_MOD}.validate_plan_filters"),
            patch(f"{_MOD}.build_tier_plan_specs", return_value=[]),
            captured_logs(_MOD) as records,
        ):
            result = dispatch_main.submit_dispatch(ctx)
        self.assertIsNone(result)
        self.assertEqual(
            [r.getMessage() for r in records],
            [
                "No requests to process: no tiers/plans resolved into concrete "
                "requests."
            ],
        )

    def test_exception_propagates(self):
        def explode(*args, **kwargs):
            raise RuntimeError("kaboom")

        with self.assertRaisesRegex(RuntimeError, "^kaboom$"):
            self._submit(explode, 1)

    def test_keyboard_interrupt_propagates(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._submit(interrupt, 1)

    def test_result_is_frozen(self):
        result, _, _, _ = self._submit(_submitted("one"), 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.run_id = "other"


if __name__ == "__main__":
    unittest.main()
