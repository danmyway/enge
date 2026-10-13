"""The real phases behind `enge run`: each call into dispatch, report, rerun
and compare, with its own context and its output held back.

Every phase gets a context built the way its own subcommand builds one, so
every attribute it reads exists and every validation it runs still runs:
gen 0 from an `enge test` namespace carrying the loop's dispatch flags, the
other phases from real argv (`report --run <id>`, `rerun --run <id>`,
`compare --lineage --run <root>`) prefixed with the loop's global flags.

Phases run quiet (Q-R6-B): the shared console prints nothing, anything a
phase `print()`s is discarded, and the phase loggers are held at WARNING
(compare's at ERROR between generations, so its per-invocation WARNINGs
print once, under the final table) unless `-v` or `-d` was given. Only the
final compare writes to stdout.
"""

import argparse
import contextlib
import io
import logging
import os
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from requests import RequestException

from enge.compare.engine import ExecutionColumn, LineageTable
from enge.dispatch.__main__ import DispatchSubmission
from enge.rerun.__main__ import RerunCandidate, RerunSubmission
from enge.utils.app_context import AppContext
from enge.utils.arg_parser import get_arguments
from enge.utils.console import quiet_console
from enge.utils.errors import ValidationError
from enge.utils.globals import REQUEST_TIMEOUT_DEFAULT
from enge.utils.http_client import http_get
from enge.utils.manifest import ManifestReader
from enge.utils.opt_manager import ParsedOpts
from enge.utils.results_parser import ResultsJsonSchema, read_raw_results_json
from enge.utils.state_paths import results_dir

LOGGER = logging.getLogger(__name__)

_PHASE_LOGGERS = (
    "enge.dispatch",
    "enge.rerun",
    "enge.report",
    "enge.utils",
    "enge.compare",
)
_NOT_COPIED_TO_TEST = ("action",)


class Phases:
    """Everything the loop does outside itself; see the module docstring."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.args = ctx.cli_args
        self.loud = bool(getattr(self.args, "verbose", 0)) or bool(
            getattr(self.args, "debug", False)
        )
        self.runs_dir = Path(ctx.manifest_runs_dir)
        self.output_format = getattr(self.args, "output_format", "terminal")

    # --- contexts -------------------------------------------------------

    def _global_argv(self) -> List[str]:
        argv: List[str] = []
        if getattr(self.args, "config", None):
            argv += ["-c", self.args.config]
        argv += ["-v"] * int(getattr(self.args, "verbose", 0) or 0)
        if getattr(self.args, "debug", False):
            argv.append("-d")
        return argv

    def _context(self, argv: Sequence[str]) -> AppContext:
        namespace = get_arguments([*self._global_argv(), *argv])
        return AppContext.from_parsed_opts(ParsedOpts(cli_args=namespace))

    def test_namespace(self) -> argparse.Namespace:
        """`enge test`'s namespace with the loop's dispatch and tagging flags.

        Starts from `enge test`'s own defaults, so `--wait`, `-n` and
        `--list-sets` keep their off values, and copies every value the loop
        was given for an argument `enge test` also has.
        """
        namespace = get_arguments(["test"])
        for dest in vars(namespace):
            if dest not in _NOT_COPIED_TO_TEST and hasattr(self.args, dest):
                setattr(namespace, dest, getattr(self.args, dest))
        return namespace

    # --- output ---------------------------------------------------------

    @contextlib.contextmanager
    def _held_loggers(self, compare_level: int) -> Iterator[None]:
        if self.loud:
            yield
            return
        saved: Dict[str, int] = {}
        for name in _PHASE_LOGGERS:
            phase_logger = logging.getLogger(name)
            saved[name] = phase_logger.level
            level = compare_level if name == "enge.compare" else logging.WARNING
            phase_logger.setLevel(max(level, phase_logger.level))
        try:
            yield
        finally:
            for name, level in saved.items():
                logging.getLogger(name).setLevel(level)

    @contextlib.contextmanager
    def quiet(self, compare_level: int = logging.WARNING) -> Iterator[None]:
        with quiet_console(), contextlib.redirect_stdout(io.StringIO()):
            with self._held_loggers(compare_level):
                yield

    # --- phases ---------------------------------------------------------

    def dispatch(self) -> Optional[DispatchSubmission]:
        from enge.dispatch.__main__ import submit_dispatch

        test_ctx = AppContext.from_parsed_opts(
            ParsedOpts(cli_args=self.test_namespace())
        )
        with self.quiet():
            return submit_dispatch(test_ctx)

    def task_ids(self, run_id: str) -> List[str]:
        manifest = ManifestReader.get_run(self.runs_dir, run_id)
        return ManifestReader.get_task_ids(manifest)

    def fetch_state(self, task_id: str) -> Optional[str]:
        endpoint = str(self.ctx.testing_farm_endpoint.api_endpoint_url)
        url = os.path.join(endpoint, task_id)
        try:
            response = http_get(url, timeout=REQUEST_TIMEOUT_DEFAULT)
            response.raise_for_status()
            return str(response.json()["state"])
        except (RequestException, ValueError, KeyError, TypeError) as e:
            LOGGER.debug("state poll for %s failed: %s", task_id, e)
            return None

    def harvest(self, run_id: str, *, refresh: bool = False) -> None:
        """`enge report --run <id>` without the table; NetworkError propagates."""
        from enge.report import results_cache
        from enge.report.concurrent_parser import parse_request_xunit_concurrent

        report_ctx = self._context(["report", "--run", run_id])
        with self.quiet():
            _, _, task_results = parse_request_xunit_concurrent(
                report_ctx, None, None, False, raise_on_unparseable_input=True
            )
            results_cache.cache_report_results(
                report_ctx, task_results, refresh=refresh
            )

    def cache(self, run_id: str) -> Optional[ResultsJsonSchema]:
        path = results_dir(self.ctx.config) / f"{run_id}.json"
        if not path.exists():
            return None
        return ResultsJsonSchema.from_dict(read_raw_results_json(path))

    def lineage(self, root_run_id: str) -> Tuple[List[ExecutionColumn], List[str]]:
        from enge.compare.loader import load_lineage_columns

        compare_ctx = self._context(["compare", "--lineage", "--run", root_run_id])
        with self.quiet(compare_level=logging.ERROR):
            columns, selected, _ = load_lineage_columns(compare_ctx)
        return columns, selected

    def final_tables(self, root_run_id: str, retry_count: int) -> List[LineageTable]:
        """Both views of the final table, for the exit code."""
        from enge.compare.engine import build_lineage_tables

        columns, selected = self.lineage(root_run_id)
        if not columns:
            return []
        return [
            table
            for show_tests in (False, True)
            for table in build_lineage_tables(
                columns,
                selected,
                show_tests=show_tests,
                splitarch=False,
                splitpath=False,
                retry_count=retry_count,
            )
        ]

    def rerun(
        self, candidates: Sequence[RerunCandidate], parent_run_id: str
    ) -> RerunSubmission:
        from enge.rerun.__main__ import RerunJobs, submit_rerun

        rerun_ctx = self._context(["rerun", "--run", parent_run_id])
        jobs = RerunJobs(
            rerun_ctx, candidates=candidates, task_source=f"manifest:{parent_run_id}"
        )
        with self.quiet():
            jobs.qualify_candidates()
            return submit_rerun(jobs, rerun_ctx)

    def render(self, root_run_id: str, retry_count: int) -> None:
        """The final `enge compare --lineage` table: the loop's only stdout."""
        from enge.compare.__main__ import main as compare_main

        argv = ["compare", "--lineage", "--run", root_run_id]
        argv += ["--retry-count", str(retry_count), "-o", self.output_format]
        compare_main(self._context(argv))

    def chain(self, root_run_id: str) -> List[str]:
        """The loop's runs from the root down `parent_run_id` (Q-R6-D)."""
        root = ManifestReader.get_run(self.runs_dir, root_run_id)
        root_id = root["run_id"]
        if root.get("parent_run_id"):
            raise ValidationError(
                f"run {root_id} is a rerun (parent {root['parent_run_id']}); "
                "--resume takes the root run the loop dispatched"
            )
        children: Dict[str, List[str]] = {}
        for summary in ManifestReader.list_runs(self.runs_dir):
            parent = summary.get("parent_run_id")
            if parent:
                children.setdefault(parent, []).append(summary["run_id"])
        chain = [root_id]
        while children.get(chain[-1]):
            kids = sorted(children[chain[-1]])
            if len(kids) > 1:
                raise ValidationError(
                    f"run {chain[-1]} has {len(kids)} reruns ({', '.join(kids)}); "
                    "--resume cannot tell which one the loop made"
                )
            chain.append(kids[0])
        return chain
