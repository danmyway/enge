"""Characterization of `enge rerun`'s submit path, and the callable it becomes.

Everything `rerun.main` does after qualification -- payload scrub, parent
lineage, the ReportPortal launch per payload, request-data extraction,
`SubmitTest.send_request`, the manifest write, the `Run ID:` lines and the
exit-code mapping -- is pinned here through the REAL `main` and the REAL
`SubmitTest`. Only the network boundary (`http_post`, `http_get`), the xunit
parse, task resolution, compose re-pinning and the ReportPortal launch are
stubbed.

Volatile values are normalised, never ignored, and each place says which:
  * `run_id` (a fresh ULID per run) -> "<RUN_ID>", after asserting that every
    place it appears (manifest, launch calls, log lines, file name) agrees;
  * `created_at` / `dispatched_at` (wall-clock) -> "<TS>";
  * `argv` is made deterministic by patching `sys.argv`, not masked.
"""

import copy
import itertools
import json
import logging
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from enge.utils.globals import ExitCode
from tests._helpers import captured_logs, make_app_context

UUID_ONE = "aaaaaaaa-0000-0000-0000-00000000000a"
UUID_TWO = "bbbbbbbb-0000-0000-0000-00000000000b"
API = "https://tf.example.com/api"
HTTP_POST = "enge.dispatch.tf_send_request.http_post"
FAKE_ARGV = ["enge", "rerun"]


class _Reply:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _accept(task_id):
    return _Reply(200, {"id": task_id})


def _reject():
    return _Reply(400, {"message": "bad"})


def _tf_details(task_uuid, plan="/plan/tier0"):
    """Testing Farm request details as `http_get(...).json()` returns them.

    Carries a PACKIT_* and a CI_* variable and a tmt `uniq_id`, which the
    rerun scrub must drop, and a trigger/initiator it must overwrite.
    """
    return {
        "id": task_uuid,
        "state": "complete",
        "user_id": "user-1",
        "token_id": "token-1",
        "notes": ["a note"],
        "result": {"overall": "failed"},
        "run": {"artifacts": "https://artifacts.example.com"},
        "user": {"name": "tester"},
        "queued_time": 1,
        "run_time": 2,
        "created": "2026-01-01",
        "updated": "2026-01-02",
        "environments_requested": [
            {
                "os": {"compose": "RHEL-9.0-nightly"},
                "arch": "x86_64",
                "variables": {
                    "PACKIT_X": "1",
                    "CI_Y": "2",
                    "SOURCE_RELEASE": "9.6",
                    "TARGET_RELEASE": "10.0",
                    "KEEP": "k",
                },
                "artifacts": [{"type": "fedora-copr-build", "id": "123:rhel-9-x86_64"}],
                "tmt": {
                    "context": {
                        "uniq_id": "old-launch",
                        "initiator": "packit",
                        "trigger": "commit",
                        "event": "nightly",
                        "distro": "rhel-9",
                    }
                },
            }
        ],
        "test": {
            "fmf": {
                "url": "https://git.example.com/tests.git",
                "ref": "main",
                "name": plan,
                "plan_filter": "tier:0",
            }
        },
    }


def _suite(name, result, tests=()):
    return {
        "testsuite_name": name,
        "testsuite_result": result,
        "testsuite_arch": "x86_64",
        "testcases": [{"testcase_name": t, "testcase_result": "FAILED"} for t in tests],
    }


def _parsed(*suites):
    return {"source_compose": "RHEL-9.0-nightly", "testsuites": list(suites)}


def two_failed_tasks():
    """Two tasks, each with one FAILED plan and one failed test."""
    return {
        UUID_ONE: _parsed(_suite("/plan/tier0", "FAILED", ["test::one"])),
        UUID_TWO: _parsed(_suite("/plan/tier0", "FAILED", ["test::two"])),
    }


def undefined_task():
    """One task: a FAILED plan with a failed test, and an UNDEFINED plan
    without testcases (the plan-only second payload)."""
    return {
        UUID_ONE: _parsed(
            _suite("/plan/failing", "FAILED", ["test::broken"]),
            _suite("/plan/undef", "UNDEFINED"),
        )
    }


class Run:
    """What one scripted `main` / `submit_rerun` invocation left behind."""

    def __init__(self):
        self.value = None
        self.records = []
        self.post = None
        self.launch_calls = []
        self.ctx = None
        self.print = None
        self.jobs = None

    def posted(self):
        """Every POSTed body, in order."""
        return [c.kwargs["json"] for c in self.post.call_args_list]

    def info_plus(self):
        """(level name, message) for every INFO+ record from `enge.rerun`."""
        return [
            (r.levelname, r.getMessage())
            for r in self.records
            if r.levelno >= logging.INFO
        ]


class SubmitHarness:
    """Shared fixture: temp state dirs plus a scripted run of the real flow."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def ctx(self, dryrun=False, extra_cli=None):
        return make_app_context(
            action="rerun",
            extra_cli={
                "error": False,
                "fail": False,
                "dryrun": dryrun,
                **(extra_cli or {}),
            },
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

    def manifests(self):
        return sorted(self.runs_dir.glob("*.json")) if self.runs_dir.exists() else []

    def script(
        self,
        parsed,
        replies,
        *,
        dryrun=False,
        task_source="latest",
        launches=True,
        launch_value=None,
        extra_cli=None,
        call=None,
        plans=None,
    ):
        """Run `call(ctx)` (default `rerun.main`) over canned Testing Farm data.

        `parsed` is what the xunit parse returns, keyed by task id; the
        tasks are resolved in that order. `replies` feed the create POSTs in
        order. With `launches` the recording stand-in for the ReportPortal
        launch returns "launch-1", "launch-2", ...; otherwise None. A
        `launch_value` overrides that with one fixed return value.
        """
        from enge.rerun.__main__ import main

        run = Run()
        run.ctx = ctx = self.ctx(dryrun, extra_cli)
        uuids = list(parsed)
        urls = [f"{API}/{u}" for u in uuids]
        details = {
            f"{API}/{u}": _tf_details(u, plans[u] if plans else "/plan/tier0")
            for u in uuids
        }
        counter = itertools.count(1)

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.json.side_effect = lambda: copy.deepcopy(details[url.rstrip("/")])
            return resp

        def fake_launch(
            payload,
            is_dryrun,
            ctx_arg,
            run_id=None,
            parent_run_id=None,
            original_uuid=None,
        ):
            run.launch_calls.append(
                {
                    "payload": copy.deepcopy(payload),
                    "is_dryrun": is_dryrun,
                    "ctx_is_ctx": ctx_arg is ctx,
                    "run_id": run_id,
                    "parent_run_id": parent_run_id,
                    "original_uuid": original_uuid,
                }
            )
            if launch_value is not None:
                return launch_value
            return f"launch-{next(counter)}" if launches else None

        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", list(FAKE_ARGV)))
            run.records = stack.enter_context(captured_logs("enge.rerun"))
            stack.enter_context(
                patch(
                    "enge.rerun.__main__._create_rerun_launch_for_payload",
                    side_effect=fake_launch,
                )
            )
            stack.enter_context(
                patch(
                    "enge.rerun.__main__.repin_compose",
                    side_effect=lambda c, u, **kw: c,
                )
            )
            stack.enter_context(
                patch("enge.rerun.__main__.http_get", side_effect=fake_get)
            )
            stack.enter_context(
                patch("enge.rerun.__main__.parse_request_xunit", return_value=parsed)
            )
            stack.enter_context(
                patch(
                    "enge.rerun.__main__.parse_tasks_with_map",
                    return_value=(
                        urls,
                        task_source,
                        {**{u: None for u in uuids}, **{u: None for u in urls}},
                    ),
                )
            )
            run.print = stack.enter_context(patch("builtins.print"))
            run.post = stack.enter_context(patch(HTTP_POST, side_effect=replies))
            if call is None:
                run.value = main(ctx)
            else:
                run.value = call(run, ctx)
        return run


TS_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def _post_body(rerun_of, test_name, *, plan="/plan/tier0$", launch=None):
    """The full POST body for one accepted `_tf_details` rerun.

    Spelled out field by field so that a drift in any scrubbed, overwritten
    or re-added key shows up as a diff against this literal.
    """
    tmt = {
        "context": {
            "distro": "rhel-9",
            "event": "nightly",
            "initiator": "enge",
            "rerun_of": rerun_of,
            "trigger": "rerun",
        }
    }
    if launch:
        tmt["context"]["uniq_id"] = launch
        tmt["environment"] = {"TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH": launch}
    fmf = {
        "url": "https://git.example.com/tests.git",
        "ref": "main",
        "name": plan,
        "plan_filter": None,
    }
    if test_name:
        fmf["test_name"] = test_name
    return {
        "environments": [
            {
                "os": {"compose": "RHEL-9.0-nightly"},
                "arch": "x86_64",
                "variables": {
                    "SOURCE_RELEASE": "9.6",
                    "TARGET_RELEASE": "10.0",
                    "KEEP": "k",
                },
                "artifacts": [{"type": "fedora-copr-build", "id": "123:rhel-9-x86_64"}],
                "tmt": tmt,
            }
        ],
        "test": {"fmf": fmf},
    }


def _manifest_request(task_id, rerun_of, plan, tests, **overrides):
    entry = {
        "task_id": task_id,
        "set": None,
        "tier": None,
        "arch": "x86_64",
        "plan": plan,
        "source_compose": "RHEL-9.0-nightly",
        "target_compose": None,
        "artifacts_url": f"https://tf.example.com/artifacts/{task_id}",
        "dispatched_at": "<TS>",
        "launch_uuid": None,
        "rerun_of": rerun_of,
        "source": "9.6",
        "target": "10.0",
        "git_ref": "main",
        "event": "nightly",
        "build_ids": ["123:rhel-9-x86_64"],
        "tests": tests,
    }
    entry.update(overrides)
    return entry


class TestRerunMainSubmitPath(SubmitHarness, unittest.TestCase):
    """C1-C3, C5-C9: `rerun.main` through the real SubmitTest."""

    def masked_manifest(self):
        """The single manifest on disk with the volatile values normalised.

        Returns (run_id, manifest). Masked: run_id -> "<RUN_ID>" (after
        checking it names the file), created_at and every dispatched_at ->
        "<TS>" (after checking they are UTC `...Z` timestamps).
        """
        paths = self.manifests()
        self.assertEqual(len(paths), 1, paths)
        data = json.loads(paths[0].read_text())
        run_id = data["run_id"]
        self.assertEqual(paths[0].stem, run_id)
        self.assertRegex(data["created_at"], TS_RE)
        data["run_id"] = "<RUN_ID>"
        data["created_at"] = "<TS>"
        for req in data["requests"]:
            self.assertRegex(req["dispatched_at"], TS_RE)
            req["dispatched_at"] = "<TS>"
        return run_id, data

    def test_main_accepted_run_pins_posts_launches_manifest_and_logs(self):
        run = self.script(
            two_failed_tasks(), [_accept("new-1"), _accept("new-2")], launches=True
        )

        self.assertIsNone(run.value)
        self.assertEqual(
            run.posted(),
            [
                _post_body(UUID_ONE, "one$", launch="launch-1"),
                _post_body(UUID_TWO, "two$", launch="launch-2"),
            ],
        )

        run_id, manifest = self.masked_manifest()

        # One launch call per payload, in order. The payload is snapshotted at
        # call time: it still carries _enge_source_path, has rerun_of set and
        # the old uniq_id scrubbed, and has no RP env yet.
        def launch_payload(rerun_of, test_name):
            body = _post_body(rerun_of, test_name)
            body["_enge_source_path"] = None
            return body

        self.assertEqual(
            [dict(c, run_id="<RUN_ID>") for c in run.launch_calls],
            [
                {
                    "payload": launch_payload(UUID_ONE, "one$"),
                    "is_dryrun": False,
                    "ctx_is_ctx": True,
                    "run_id": "<RUN_ID>",
                    "parent_run_id": None,
                    "original_uuid": UUID_ONE,
                },
                {
                    "payload": launch_payload(UUID_TWO, "two$"),
                    "is_dryrun": False,
                    "ctx_is_ctx": True,
                    "run_id": "<RUN_ID>",
                    "parent_run_id": None,
                    "original_uuid": UUID_TWO,
                },
            ],
        )
        self.assertEqual({c["run_id"] for c in run.launch_calls}, {run_id})

        self.assertEqual(
            manifest,
            {
                "schema_version": 1,
                "run_id": "<RUN_ID>",
                "created_at": "<TS>",
                "command": "rerun",
                "argv": FAKE_ARGV,
                "tags": ["rerun"],
                "parent_run_id": None,
                "origin": "native",
                "context": {},
                "requests": [
                    _manifest_request(
                        "new-1",
                        UUID_ONE,
                        "/plan/tier0",
                        ["one"],
                        launch_uuid="launch-1",
                    ),
                    _manifest_request(
                        "new-2",
                        UUID_TWO,
                        "/plan/tier0",
                        ["two"],
                        launch_uuid="launch-2",
                    ),
                ],
            },
        )
        self.assertEqual(Path(self.latest.read_text()).stem, run_id)

        self.assertEqual(
            run.info_plus(),
            [
                (
                    "INFO",
                    "Looking for tasks from the requested sources, this may "
                    "take a while.",
                ),
                ("INFO", "The following plans qualify for a re-run:"),
                ("INFO", f"Run ID: {run_id}"),
                ("INFO", f"Report with: enge report --run {run_id}"),
            ],
        )

    def test_main_dry_run_writes_nothing_and_logs_no_run_id(self):
        run = self.script(two_failed_tasks(), [], dryrun=True, launches=False)

        self.assertIsNone(run.value)
        self.assertEqual(run.post.call_count, 0)
        self.assertEqual(self.manifests(), [])
        self.assertFalse(self.latest.exists())
        self.assertEqual([m for _, m in run.info_plus() if "Run ID:" in m], [])
        self.assertEqual([m for _, m in run.info_plus() if "Report with:" in m], [])
        self.assertEqual(len(run.launch_calls), 2)
        self.assertEqual([c["is_dryrun"] for c in run.launch_calls], [True, True])
        # Masked nothing: a dry run has no run id to pass on.
        self.assertEqual([c["run_id"] for c in run.launch_calls], [None, None])
        self.assertEqual(
            run.info_plus(),
            [
                (
                    "INFO",
                    "Looking for tasks from the requested sources, this may "
                    "take a while.",
                ),
                ("INFO", "The following plans qualify for a re-run:"),
            ],
        )

    def test_main_undefined_plan_sends_plan_only_rerun(self):
        """L18, CLI path: a FAILED plan plus an UNDEFINED plan with no
        testcases is rerun as a filtered payload, then a plan-only one."""
        run = self.script(
            undefined_task(), [_accept("new-1"), _accept("new-2")], launches=False
        )

        self.assertIsNone(run.value)
        self.assertEqual(
            run.posted(),
            [
                _post_body(
                    UUID_ONE,
                    "broken$",
                    plan="/plan/failing$|/plan/undef$",
                ),
                _post_body(UUID_ONE, None, plan="/plan/undef$"),
            ],
        )

        run_id, manifest = self.masked_manifest()
        self.assertEqual(
            manifest["requests"],
            [
                _manifest_request(
                    "new-1",
                    UUID_ONE,
                    "/plan/failing, /plan/undef",
                    ["broken"],
                ),
                _manifest_request("new-2", UUID_ONE, "/plan/undef", []),
            ],
        )
        self.assertIn(
            (
                "INFO",
                f"Request {UUID_ONE} includes 1 UNDEFINED plan(s) with no "
                "testcase information; submitting an additional plan-only rerun.",
            ),
            run.info_plus(),
        )
        self.assertIn(("INFO", f"Run ID: {run_id}"), run.info_plus())

    def test_main_failure_mapping_and_lines(self):
        cases = {
            "all-rejected": (
                [_reject(), _reject()],
                ExitCode.EXCEPTION,
                [("CRITICAL", "No requests were successfully submitted!")],
                False,
            ),
            "partial": (
                [_accept("new-1"), _reject()],
                ExitCode.TEST_FAILURE,
                [("WARNING", "1 requests failed")],
                True,
            ),
        }
        for label, (replies, expected, loud_expected, has_run_id) in cases.items():
            with self.subTest(label):
                self.runs_dir = self.tmp / f"runs-{label}"
                self.latest = self.tmp / f"latest-{label}"
                run = self.script(two_failed_tasks(), replies, launches=False)

                self.assertEqual(run.value, expected)
                self.assertEqual(run.post.call_count, 2)
                loud = [
                    (lvl, msg)
                    for lvl, msg in run.info_plus()
                    if lvl in ("WARNING", "CRITICAL")
                ]
                self.assertEqual(loud, loud_expected)
                run_id_lines = [m for _, m in run.info_plus() if "Run ID:" in m]
                if has_run_id:
                    self.assertEqual(len(self.manifests()), 1)
                    run_id, _ = self.masked_manifest()
                    self.assertEqual(run_id_lines, [f"Run ID: {run_id}"])
                    self.assertIn(
                        ("INFO", f"Report with: enge report --run {run_id}"),
                        run.info_plus(),
                    )
                else:
                    self.assertEqual(self.manifests(), [])
                    self.assertFalse(self.latest.exists())
                    self.assertEqual(run_id_lines, [])
                    self.assertEqual(
                        [m for _, m in run.info_plus() if "Report with:" in m], []
                    )

    def _write_parent(self, requests, context, tags=("nightly",)):
        from enge.utils.manifest import ManifestWriter
        from enge.utils.ulid import generate_ulid

        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id,
            command="test",
            argv=["enge", "test"],
            tags=list(tags),
            context=context,
        )
        for task_id, set_name, tier, target in requests:
            parent.add_request(
                task_id,
                set_name=set_name,
                tier=tier,
                arch="x86_64",
                target_compose=target,
            )
        parent.flush(self.runs_dir, self.latest)
        return parent_id

    def test_main_single_parent_lineage_through_submit(self):
        parent_id = self._write_parent(
            [
                (UUID_ONE, "smoke", "tier0", "CentOS-Stream-10"),
                (UUID_TWO, "smoke", "tier1", "CentOS-Stream-10-b"),
            ],
            {"set": "smoke", "event": "nightly", "tiers": ["tier0", "tier1"]},
        )

        run = self.script(
            two_failed_tasks(),
            [_accept("new-1"), _accept("new-2")],
            task_source=f"manifest:{parent_id}",
            launches=False,
        )

        self.assertIsNone(run.value)
        manifests = [p for p in self.manifests() if p.stem != parent_id]
        self.assertEqual(len(manifests), 1)
        child = json.loads(manifests[0].read_text())
        self.assertEqual(child["parent_run_id"], parent_id)
        self.assertEqual(child["tags"], ["nightly", "rerun"])
        self.assertEqual(
            child["context"],
            {"set": "smoke", "event": "nightly", "tiers": ["tier0", "tier1"]},
        )
        self.assertEqual(
            [
                (r["task_id"], r["set"], r["tier"], r["target_compose"], r["rerun_of"])
                for r in child["requests"]
            ],
            [
                ("new-1", "smoke", "tier0", "CentOS-Stream-10", UUID_ONE),
                ("new-2", "smoke", "tier1", "CentOS-Stream-10-b", UUID_TWO),
            ],
        )
        self.assertEqual(
            [c["parent_run_id"] for c in run.launch_calls], [parent_id, parent_id]
        )

    def test_main_posts_to_endpoint_with_bearer_header_and_timeout(self):
        """C7: the api key, auth header, URL and timeout (body step 3)."""
        from enge.utils.globals import REQUEST_TIMEOUT_DEFAULT

        run = self.script(
            two_failed_tasks(), [_accept("new-1"), _accept("new-2")], launches=False
        )

        self.assertEqual(run.post.call_count, 2)
        for call in run.post.call_args_list:
            self.assertEqual(call.args, (API,))
            self.assertEqual(
                call.kwargs["headers"], {"Authorization": "Bearer test-api-key"}
            )
            self.assertEqual(call.kwargs["timeout"], REQUEST_TIMEOUT_DEFAULT)

    def test_main_set_tag_is_merged_between_parent_tags_and_rerun(self):
        """C8: tags are parent's, then the CLI --set-tag, then "rerun",
        de-duplicated in that order (body steps 3 and 6)."""
        parent_id = self._write_parent(
            [(UUID_ONE, "smoke", "tier0", None), (UUID_TWO, "smoke", "tier0", None)],
            {"set": "smoke"},
            tags=("nightly", "extra"),
        )

        self.script(
            two_failed_tasks(),
            [_accept("new-1"), _accept("new-2")],
            task_source=f"manifest:{parent_id}",
            launches=False,
            extra_cli={"set_tag": ["extra", "cli-tag"]},
        )

        child = [p for p in self.manifests() if p.stem != parent_id]
        self.assertEqual(len(child), 1)
        self.assertEqual(
            json.loads(child[0].read_text())["tags"],
            ["nightly", "extra", "cli-tag", "rerun"],
        )

    def test_main_dry_run_placeholder_launch_sets_zero_uniq_id_and_env(self):
        """C9: the `dryrun_placeholder` launch value is turned into the
        all-zero uuid, both as tmt uniq_id and as the RP upload target; it is
        visible only in the payload SubmitTest prints on a dry run."""
        zero = "00000000-0000-0000-0000-000000000000"
        run = self.script(
            {UUID_ONE: two_failed_tasks()[UUID_ONE]},
            [],
            dryrun=True,
            launch_value="dryrun_placeholder",
        )

        self.assertEqual(run.post.call_count, 0)
        printed = []
        for call in run.print.call_args_list:
            try:
                printed.append(json.loads(call.args[0]))
            except (ValueError, TypeError, IndexError):
                continue
        printed = [p for p in printed if "environments" in p]
        self.assertEqual(len(printed), 1)
        tmt = printed[0]["environments"][0]["tmt"]
        self.assertEqual(tmt["context"]["uniq_id"], zero)
        self.assertEqual(
            tmt["environment"],
            {"TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH": zero},
        )
        self.assertEqual(self.manifests(), [])


class TestUndefinedOnBothIntakePaths(SubmitHarness, unittest.TestCase):
    """C4 (L18): an UNDEFINED plan is registered and built the same way
    whether it came from parsed xunit or from a hand-written candidate."""

    def _qualified(self, via):
        from enge.rerun.__main__ import RerunCandidate, RerunJobs

        ctx = self.ctx()
        details = _tf_details(UUID_ONE)

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.json.side_effect = lambda: copy.deepcopy(details)
            return resp

        with (
            patch(
                "enge.rerun.__main__.repin_compose", side_effect=lambda c, u, **kw: c
            ),
            patch("enge.rerun.__main__.http_get", side_effect=fake_get),
            patch(
                "enge.rerun.__main__.parse_request_xunit",
                return_value=undefined_task(),
            ),
            patch(
                "enge.rerun.__main__.parse_tasks_with_map",
                return_value=([f"{API}/{UUID_ONE}"], "latest", {UUID_ONE: None}),
            ),
            patch("enge.rerun.__main__.console"),
        ):
            if via == "cli":
                jobs = RerunJobs(ctx)
                jobs.qualify_results()
            else:
                jobs = RerunJobs(
                    ctx,
                    candidates=[
                        RerunCandidate(
                            task_id=UUID_ONE,
                            plans=("/plan/failing", "/plan/undef"),
                            tests_by_plan={"/plan/failing": ("broken",)},
                            undefined_plans=("/plan/undef",),
                            source_compose="RHEL-9.0-nightly",
                            source_path=None,
                        )
                    ],
                )
                jobs.qualify_candidates()
            payloads = jobs.build_rerun_payloads(jobs.rerun_uuids)
        return jobs, payloads

    def test_candidate_path_undefined_matches_cli_path(self):
        cli_jobs, cli_payloads = self._qualified("cli")
        cand_jobs, cand_payloads = self._qualified("candidates")

        expected_processed = {
            UUID_ONE: (
                "/plan/failing$|/plan/undef$",
                "RHEL-9.0-nightly",
                {"/plan/failing": ["broken$"], "/plan/undef": []},
                ["/plan/undef$"],
                None,
            )
        }
        self.assertEqual(cli_jobs.processed_data, expected_processed)
        self.assertEqual(cand_jobs.processed_data, cli_jobs.processed_data)
        self.assertEqual(cli_jobs.rerun_uuids, [UUID_ONE])
        self.assertEqual(cand_jobs.rerun_uuids, cli_jobs.rerun_uuids)

        self.assertEqual(len(cli_payloads), 2)
        self.assertEqual(
            [p["test"]["fmf"].get("test_name") for p in cli_payloads],
            ["broken$", None],
        )
        self.assertEqual(
            [p["test"]["fmf"]["name"] for p in cli_payloads],
            ["/plan/failing$|/plan/undef$", "/plan/undef$"],
        )
        self.assertEqual(cand_payloads, cli_payloads)


class TestRerunCandidateValidation(unittest.TestCase):
    """V1-V8 (L19): RerunCandidate refuses malformed input and is hashable."""

    def _make(self, **kwargs):
        from enge.rerun.__main__ import RerunCandidate

        kwargs.setdefault("task_id", "uuid-a")
        return RerunCandidate(**kwargs)

    def test_v1_empty_task_id_is_refused(self):
        with self.assertRaisesRegex(ValueError, "task_id"):
            self._make(task_id="")

    def test_v2_plan_ending_in_dollar_is_refused(self):
        with self.assertRaisesRegex(ValueError, "plans"):
            self._make(plans=("/plan/a$",))

    def test_v3_tests_by_plan_key_must_be_a_plan(self):
        with self.assertRaisesRegex(ValueError, "tests_by_plan"):
            self._make(plans=("/plan/a",), tests_by_plan={"/plan/other": ("t",)})

    def test_v4_undefined_plan_must_be_a_plan(self):
        with self.assertRaisesRegex(ValueError, "undefined_plans"):
            self._make(plans=("/plan/a",), undefined_plans=("/plan/other",))

    def test_v5_fallback_shape_takes_no_tests(self):
        # Rule 3 (keys must be plans) also rejects this input, so the message
        # must name the fallback shape for the test to guard rule 5 itself.
        with self.assertRaisesRegex(ValueError, "tests_by_plan.*fallback"):
            self._make(plans=(), tests_by_plan={"/plan/a": ("t",)})

    def test_v6_duplicate_plan_is_refused(self):
        with self.assertRaisesRegex(ValueError, "plans"):
            self._make(plans=("/plan/a", "/plan/a"))

    def test_v7_equal_candidates_hash_equal(self):
        def build():
            return self._make(
                plans=("/plan/a", "/plan/b"),
                tests_by_plan={"/plan/a": ("t1", "t2")},
                undefined_plans=("/plan/b",),
                source_compose="RHEL-9.0",
                source_path="/archive/a.json",
            )

        first, second = build(), build()
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(len({first, second}), 1)

    def test_v8_lists_are_normalised_to_tuples(self):
        candidate = self._make(
            plans=["/plan/a", "/plan/b"],
            tests_by_plan={"/plan/a": ["t1", "t2"]},
            undefined_plans=["/plan/b"],
        )

        self.assertEqual(candidate.plans, ("/plan/a", "/plan/b"))
        self.assertEqual(candidate.undefined_plans, ("/plan/b",))
        self.assertEqual(candidate.tests_by_plan, {"/plan/a": ("t1", "t2")})
        self.assertIsInstance(candidate.tests_by_plan["/plan/a"], tuple)
        self.assertIsInstance(hash(candidate), int)


class TestSubmitRerunCallable(SubmitHarness, unittest.TestCase):
    """S1-S6: `submit_rerun(jobs, ctx) -> RerunSubmission`, and `main` over it."""

    def _cli_submit(self, parsed, replies, **kwargs):
        """Qualify via the CLI path, then call `submit_rerun` and nothing else."""
        from enge.rerun.__main__ import RerunJobs, submit_rerun

        def call(run, ctx):
            run.jobs = RerunJobs(ctx)
            run.jobs.qualify_results()
            return submit_rerun(run.jobs, ctx)

        kwargs.setdefault("launches", False)
        return self.script(parsed, replies, call=call, **kwargs)

    def _only_manifest_id(self):
        paths = self.manifests()
        self.assertEqual(len(paths), 1, paths)
        return paths[0].stem

    def test_s1_accepted_returns_run_id_and_counts(self):
        from enge.rerun.__main__ import RerunSubmission

        run = self._cli_submit(two_failed_tasks(), [_accept("new-1"), _accept("new-2")])

        # The run id is generated, so it is read back from the file it names.
        run_id = self._only_manifest_id()
        self.assertEqual(
            run.value, RerunSubmission(run_id=run_id, payloads=2, failed=0)
        )

    def test_s2_dry_run_returns_no_run_id(self):
        from enge.rerun.__main__ import RerunSubmission

        run = self._cli_submit(two_failed_tasks(), [], dryrun=True)

        self.assertEqual(run.value, RerunSubmission(None, 2, 0))
        self.assertEqual(self.manifests(), [])
        self.assertEqual(run.post.call_count, 0)

    def test_s3_partial_failure_keeps_run_id_and_counts_failed(self):
        from enge.rerun.__main__ import RerunSubmission

        run = self._cli_submit(two_failed_tasks(), [_accept("new-1"), _reject()])

        run_id = self._only_manifest_id()
        self.assertEqual(
            run.value, RerunSubmission(run_id=run_id, payloads=2, failed=1)
        )

    def test_s4_all_rejected_returns_no_run_id(self):
        from enge.rerun.__main__ import RerunSubmission

        run = self._cli_submit(two_failed_tasks(), [_reject(), _reject()])

        self.assertEqual(run.value, RerunSubmission(None, 2, 2))
        self.assertEqual(self.manifests(), [])

    def test_s5_candidate_path_end_to_end(self):
        from enge.rerun.__main__ import RerunCandidate, RerunJobs, submit_rerun
        from enge.utils.manifest import ManifestWriter
        from enge.utils.ulid import generate_ulid

        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id,
            command="test",
            argv=["enge", "test"],
            tags=["nightly"],
            context={"set": "smoke"},
        )
        parent.add_request(UUID_ONE, set_name="smoke", tier="tier0", arch="x86_64")
        parent.flush(self.runs_dir, self.latest)

        candidate = RerunCandidate(
            task_id=UUID_ONE,
            plans=("/plan/failing", "/plan/undef"),
            tests_by_plan={"/plan/failing": ("broken",)},
            undefined_plans=("/plan/undef",),
            source_compose="RHEL-9.0-nightly",
        )

        def call(run, ctx):
            run.jobs = RerunJobs(
                ctx, candidates=[candidate], task_source=f"manifest:{parent_id}"
            )
            run.jobs.qualify_candidates()
            return submit_rerun(run.jobs, ctx)

        run = self.script(
            undefined_task(),
            [_accept("new-1"), _accept("new-2")],
            call=call,
            launches=False,
        )

        # The same two bodies the CLI path sends for this task (C3).
        self.assertEqual(
            run.posted(),
            [
                _post_body(UUID_ONE, "broken$", plan="/plan/failing$|/plan/undef$"),
                _post_body(UUID_ONE, None, plan="/plan/undef$"),
            ],
        )
        children = [p for p in self.manifests() if p.stem != parent_id]
        self.assertEqual(len(children), 1)
        child = json.loads(children[0].read_text())
        self.assertEqual(child["parent_run_id"], parent_id)
        self.assertEqual(run.value.run_id, children[0].stem)
        self.assertEqual((run.value.payloads, run.value.failed), (2, 0))
        self.assertEqual([r["set"] for r in child["requests"]], ["smoke", "smoke"])

    def test_s6_main_delegates_and_maps_the_result(self):
        from enge.rerun.__main__ import RerunSubmission, main

        cases = [
            (
                RerunSubmission("01ABC", 2, 0),
                None,
                [
                    ("INFO", "Run ID: 01ABC"),
                    ("INFO", "Report with: enge report --run 01ABC"),
                ],
            ),
            (
                RerunSubmission(None, 2, 2),
                ExitCode.EXCEPTION,
                [("CRITICAL", "No requests were successfully submitted!")],
            ),
            (
                RerunSubmission("01ABC", 2, 1),
                ExitCode.TEST_FAILURE,
                [
                    ("INFO", "Run ID: 01ABC"),
                    ("INFO", "Report with: enge report --run 01ABC"),
                    ("WARNING", "1 requests failed"),
                ],
            ),
        ]
        for result, expected, lines in cases:
            with self.subTest(result=result):
                ctx = self.ctx()
                with (
                    captured_logs("enge.rerun") as records,
                    patch("enge.rerun.__main__.RerunJobs") as jobs_cls,
                    patch(
                        "enge.rerun.__main__.submit_rerun", return_value=result
                    ) as submit,
                ):
                    value = main(ctx)

                self.assertEqual(value, expected)
                jobs_cls.assert_called_once_with(ctx)
                jobs_cls.return_value.qualify_results.assert_called_once_with()
                submit.assert_called_once_with(jobs_cls.return_value, ctx)
                self.assertEqual(
                    [
                        (r.levelname, r.getMessage())
                        for r in records
                        if r.levelno >= logging.INFO
                    ],
                    lines,
                )


if __name__ == "__main__":
    unittest.main()
