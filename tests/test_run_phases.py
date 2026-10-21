"""`enge.run.phases`: how `enge run` calls dispatch, report, rerun and compare.

Pins the per-phase contexts (gen 0 is exactly the `enge test` namespace the
same flags would give; the other phases parse real argv with the loop's
global flags first), the quiet output of every phase (Q-R6-B: nothing on
stdout, phase loggers held unless -v/-d), the manifest walk behind
`--resume` (Q-R6-D), and the `run` route in `main`.
"""

import contextlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from requests import RequestException

import enge.utils.console as console_module
from enge.utils.arg_parser import get_arguments
from enge.utils.errors import ValidationError

from tests._helpers import make_app_context


def _phases(argv=(), **ctx_overrides):
    from enge.run.phases import Phases

    cli = vars(get_arguments([*argv] if argv else ["run"]))
    return Phases(make_app_context(action="run", extra_cli=cli, **ctx_overrides))


class TestPhaseContexts(unittest.TestCase):
    def test_gen0_namespace_is_what_enge_test_parses_for_the_same_flags(self):
        flags = ["-s", "9.7", "-T", "tier0", "--set-tag", "t", "-o"]
        phases = _phases(["-c", "/x.toml", "-v", "run", *flags, "--retry-count", "1"])

        self.assertEqual(
            vars(phases.test_namespace()),
            vars(get_arguments(["-c", "/x.toml", "-v", "test", *flags])),
        )

    def test_other_phases_get_the_global_flags_first(self):
        phases = _phases(["-c", "/x.toml", "-vv", "run", "-d"])

        self.assertEqual(phases._global_argv(), ["-c", "/x.toml", "-v", "-v", "-d"])
        self.assertEqual(_phases()._global_argv(), [])

    def test_harvest_is_report_run_without_the_table(self):
        phases = _phases()
        built = []
        phases._context = lambda argv: built.append(list(argv)) or "REPORT_CTX"
        with (
            mock.patch(
                "enge.report.concurrent_parser.parse_request_xunit_concurrent",
                return_value=({}, None, ["TR"]),
            ) as parse,
            mock.patch("enge.report.results_cache.cache_report_results") as cache,
        ):
            phases.harvest("RUN1", refresh=True)

        self.assertEqual(built, [["report", "--run", "RUN1"]])
        parse.assert_called_once_with(
            "REPORT_CTX", None, None, False, raise_on_unparseable_input=True
        )
        cache.assert_called_once_with("REPORT_CTX", ["TR"], refresh=True)

    def test_render_is_the_final_compare_with_the_loops_policy(self):
        phases = _phases(["run", "-o"])
        built = []
        phases._context = lambda argv: built.append(list(argv)) or "COMPARE_CTX"
        with mock.patch("enge.compare.__main__.main") as compare_main:
            phases.render("RUN0", 3)

        self.assertEqual(
            built,
            [
                [
                    "compare",
                    "--lineage",
                    "--run",
                    "RUN0",
                    "--retry-count",
                    "3",
                    "-o",
                    "gitlab",
                ]
            ],
        )
        compare_main.assert_called_once_with("COMPARE_CTX")

    def test_final_tables_are_both_views_for_the_exit_code(self):
        from enge.compare import engine as E
        from enge.utils.results_parser import PlanEntry, TestEntry

        column = E.ExecutionColumn(
            task_id="r",
            run_id="RUN0",
            set="s",
            tier="tier0",
            arch="x86_64",
            source="8.10",
            target="9.6",
            source_compose=None,
            target_compose=None,
            dispatched_at="2026-10-01T00",
            run_created_at="2026-10-01T00",
            plans=[
                PlanEntry(
                    name="/plans/a",
                    verdict="FAILED",
                    tests=[TestEntry("/t::t1", "FAILED", 1.0)],
                )
            ],
            verdict="FAILED",
            tests=[],
        )
        phases = _phases()
        phases.lineage = lambda root: ([column], ["r"])

        tables = phases.final_tables("RUN0", 2)

        self.assertEqual(
            [[row.label for row in t.rows] for t in tables], [["/plans/a"], ["/t::t1"]]
        )

    def test_fetch_state_returns_none_on_a_failed_poll(self):
        phases = _phases()
        ok = SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"state": "running"}
        )
        with mock.patch("enge.run.phases.http_get", return_value=ok):
            self.assertEqual(phases.fetch_state("t1"), "running")
        with mock.patch("enge.run.phases.http_get", side_effect=RequestException("x")):
            self.assertIsNone(phases.fetch_state("t1"))


class TestOneConfigLoad(unittest.TestCase):
    """Every phase reuses the config the invocation already loaded: a loop
    that re-read it per phase repeated every load-time WARNING once per
    phase, and would pick up a config file edited mid-loop."""

    def test_phase_contexts_reuse_the_loops_config(self):
        # The fields ParsedOpts' static validation requires.
        phases = _phases(
            extra_config={"project": {"name": "p", "repo_url": "https://r"}}
        )
        with mock.patch(
            "enge.utils.opt_manager.load_config",
            side_effect=AssertionError("config reloaded"),
        ):
            report_ctx = phases._context(["report", "--run", "RUN1"])

        self.assertEqual(report_ctx.config["testing_farm"]["api_key"], "test-api-key")
        self.assertIsNot(report_ctx.config, phases.ctx.config)

    def test_gen0_reuses_the_loops_config(self):
        phases = _phases()
        with (
            mock.patch("enge.run.phases.ParsedOpts") as parsed_opts,
            mock.patch("enge.run.phases.AppContext"),
            mock.patch("enge.dispatch.__main__.submit_dispatch"),
        ):
            phases.dispatch()

        parsed_opts.assert_called_once_with(cli_args=mock.ANY, config=phases.ctx.config)


class TestFetchStateValidation(unittest.TestCase):
    def test_a_malformed_state_is_a_failed_poll(self):
        # str(None) == "None" would read as a live task, so a TF answer with
        # no usable state ran to the generation timeout instead of making the
        # task unreachable after three failed polls (audit B3).
        phases = _phases()
        for payload in ({"state": None}, {"state": ""}, {"state": 3}, {}):
            with self.subTest(payload=payload):
                response = SimpleNamespace(
                    raise_for_status=lambda: None, json=lambda p=payload: p
                )
                with mock.patch("enge.run.phases.http_get", return_value=response):
                    self.assertIsNone(phases.fetch_state("t1"))

    def test_an_unknown_state_name_is_kept(self):
        # A state TF adds later is a live task, not a failed poll; the
        # generation timeout bounds the wait.
        phases = _phases()
        response = SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"state": "provisioning"}
        )
        with mock.patch("enge.run.phases.http_get", return_value=response):
            self.assertEqual(phases.fetch_state("t1"), "provisioning")


class TestQuietPhases(unittest.TestCase):
    def test_nothing_reaches_stdout_and_the_console_is_restored(self):
        phases = _phases()
        before = console_module._current
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with phases.quiet():
                print("printed by a phase")
                console_module.console.print("printed on the shared console")
                self.assertIsNot(console_module._current, before)

        self.assertEqual(out.getvalue(), "")
        self.assertIs(console_module._current, before)

    def test_phase_loggers_are_held_and_restored(self):
        phases = _phases()
        for name in ("enge.dispatch", "enge.report", "enge.compare"):
            # Premise: the production state each test starts from.
            logging.getLogger(name).setLevel(logging.NOTSET)
        with phases.quiet(compare_level=logging.ERROR):
            self.assertEqual(logging.getLogger("enge.dispatch").level, logging.WARNING)
            self.assertEqual(logging.getLogger("enge.report").level, logging.WARNING)
            self.assertEqual(logging.getLogger("enge.compare").level, logging.ERROR)

        for name in ("enge.dispatch", "enge.report", "enge.compare"):
            self.assertEqual(logging.getLogger(name).level, logging.NOTSET)

    def test_verbose_leaves_the_phase_loggers_alone(self):
        phases = _phases(["run", "-v"])
        logging.getLogger("enge.dispatch").setLevel(logging.NOTSET)
        with phases.quiet():
            self.assertEqual(logging.getLogger("enge.dispatch").level, logging.NOTSET)


def _manifest(runs_dir, run_id, parent, **extra):
    payload = {"run_id": run_id, "parent_run_id": parent, "requests": [], **extra}
    (Path(runs_dir) / f"{run_id}.json").write_text(json.dumps(payload))


class TestResumeChain(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs_dir = tmp.name

    def _chain(self, root):
        return _phases(manifest_runs_dir=self.runs_dir).chain(root)

    def test_the_chain_follows_parent_run_id_down_from_the_root(self):
        _manifest(self.runs_dir, "RUN0", None)
        _manifest(self.runs_dir, "RUN1", "RUN0")
        _manifest(self.runs_dir, "RUN2", "RUN1")
        _manifest(self.runs_dir, "OTHER", None)

        self.assertEqual(self._chain("RUN0"), ["RUN0", "RUN1", "RUN2"])

    def test_a_fork_is_refused(self):
        _manifest(self.runs_dir, "RUN0", None)
        _manifest(self.runs_dir, "RUN1", "RUN0")
        _manifest(self.runs_dir, "RUN1B", "RUN0")

        with self.assertRaisesRegex(ValidationError, "RUN0 has 2 reruns"):
            self._chain("RUN0")

    def test_a_rerun_run_is_not_a_root(self):
        _manifest(self.runs_dir, "RUN0", None)
        _manifest(self.runs_dir, "RUN1", "RUN0")

        with self.assertRaisesRegex(ValidationError, "RUN1 is a rerun"):
            self._chain("RUN1")


class TestResumeRoot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs_dir = tmp.name

    def test_root_resolves_the_id_and_reads_the_argv(self):
        argv = ["/x/enge", "run", "--retry-count", "1"]
        _manifest(self.runs_dir, "01ROOTFULL", None, argv=argv)
        _manifest(self.runs_dir, "02NOARGV", None)

        phases = _phases(manifest_runs_dir=self.runs_dir)

        self.assertEqual(phases.root("01ROOT"), ("01ROOTFULL", argv))
        self.assertEqual(phases.root("02NOARGV"), ("02NOARGV", None))
        with self.assertRaisesRegex(ValidationError, "No run matching"):
            phases.root("NOPE")


class TestRoute(unittest.TestCase):
    def test_main_routes_run_to_the_loop(self):
        from enge.__main__ import _dispatch_action

        with mock.patch("enge.run.__main__.main", return_value=7) as run_main:
            self.assertEqual(_dispatch_action("run", "CTX"), 7)
        run_main.assert_called_once_with("CTX")
