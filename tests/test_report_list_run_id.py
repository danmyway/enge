"""`enge report --list` must keep the full Run ID when stdout is not a TTY.

Piped or redirected, Rich falls back to an 80-column console and ellipsizes
every cell of the nine-column table, including the Run ID that --run,
--refresh, rerun and cancel take as input (DX-6).
"""

import contextlib
import io
import unittest
from unittest.mock import patch

from rich.console import Console

from enge.report.__main__ import _handle_list
from tests._helpers import make_app_context

RUN_IDS = (
    "01KVWEGV5ZT0CVWAPWP53YZ9GD",
    "01KVWEGV5ZT0CVWAPWP53YZ9GE",
    "01KVWEGV5ZT0CVWAPWP53YZ9GF",
)


def _summaries():
    return [
        {
            "run_id": run_id,
            "created_at": f"2026-10-0{n}T12:34:56Z",
            "command": "test",
            "sets": ["rhel9to10"],
            "context": {
                "set": "rhel9to10",
                "tiers": ["tier0", "tier1"],
                "architectures": ["x86_64", "aarch64"],
            },
            "tags": ["nightly"],
            "request_count": 4,
            "origin": "native",
        }
        for n, run_id in enumerate(RUN_IDS, start=1)
    ]


class ReportListRunIdTests(unittest.TestCase):
    def _render_terminal(self, width):
        buf = io.StringIO()
        test_console = Console(
            file=buf, width=width, force_terminal=False, color_system=None
        )
        ctx = make_app_context(action="report", extra_cli={"output_format": "terminal"})
        with (
            patch(
                "enge.report.__main__.ManifestReader.list_runs",
                return_value=_summaries(),
            ),
            patch("enge.report.__main__.console", test_console),
        ):
            _handle_list(ctx)
        return buf.getvalue()

    def test_l1_narrow_non_tty_console_keeps_full_run_ids(self):
        output = self._render_terminal(80)
        self.assertIn("Manifest Store", output)
        self.assertIn("Run ID", output)
        for run_id in RUN_IDS:
            self.assertIn(run_id, output)

    def test_l2_wide_console_keeps_full_run_ids(self):
        output = self._render_terminal(200)
        self.assertIn("Manifest Store", output)
        for run_id in RUN_IDS:
            self.assertIn(run_id, output)

    def test_l3_gitlab_output_keeps_full_run_ids(self):
        ctx = make_app_context(action="report", extra_cli={"output_format": "gitlab"})
        out = io.StringIO()
        with (
            patch(
                "enge.report.__main__.ManifestReader.list_runs",
                return_value=_summaries(),
            ),
            contextlib.redirect_stdout(out),
        ):
            _handle_list(ctx)
        output = out.getvalue()
        self.assertIn("| Run ID |", output)
        for run_id in RUN_IDS:
            self.assertIn(run_id, output)


if __name__ == "__main__":
    unittest.main()
