"""Dispatch against create requests Testing Farm (TF) rejects.

A rejected or unconfirmed create is a failed result, never a manifest entry;
one failing request must not stop the others; the exit code counts failures.
"""

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from enge.dispatch.__main__ import main
from enge.dispatch.set_flow import RequestSpec, process_request_spec
from enge.utils.manifest import ManifestWriter
from enge.utils.ulid import generate_ulid
from tests._helpers import captured_logs

HTTP_POST = "enge.dispatch.tf_send_request.http_post"
PLAN_FILTER = "enge.dispatch.set_flow.generate_tier_plan_filter"


class _Reply:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class TestDispatchRejectedRequests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _make_ctx(self):
        po = MagicMock()
        po.testing_farm = {"api_key": "token"}
        po.testing_farm_endpoint = MagicMock(
            log_artifact_baseurl="https://artifacts.example.com",
            api_endpoint_url="https://api.tf.example/v0.1/requests",
        )
        po.tests = {
            "git_url": "https://git.example/repo",
            "git_ref": "main",
            "parallel_limit": 5,
            "tier": {},
        }
        po.project = {"repo_url": "https://git.example/repo", "name": "pkg"}
        po.cli_args = MagicMock()
        po.cli_args.dryrun = False
        po.cli_args.output_format = "terminal"
        po.cli_args.testfilter = None
        po.cli_args.test = None
        po.cli_args.planfilter = None
        po.cli_args.environment = None
        po.cli_args.context = None
        po.cli_args.git_url = None
        po.cli_args.git_ref = None
        po.cli_args.event = None
        po.cli_args.only_rhsm_mock_cdn = False
        po.cli_args.no_rhsm = False
        po.cli_args.only_rhsm_stage_cdn = False
        po.cli_args.auto_tag = False
        po.cli_args.set_tag = None
        po.cli_args.wait = False
        po.cli_args.action = "test"
        po.cli_args.copr = None
        po.cli_args.brew = None
        po.config = {}
        po.manifest_runs_dir = str(self.tmp / "runs")
        po.manifest_latest = str(self.tmp / "manifest_latest")
        po.pool = None
        po.architectures = []
        po.environment_variables = {}
        po.tmt_context = {}
        po.copr_reference = None
        po.copr_references = []
        po.copr_api = {}
        po.brew_reference = None
        po.brew_references = []
        po.brew_api = {}
        return po

    @staticmethod
    def _spec(set_name):
        return RequestSpec(
            set_name=set_name,
            tier="tier0",
            plan="/plans/p1",
            arch="x86_64",
            source_spec={"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            upgrade_path="8to9",
            effective_values={"event": "nightly", "copr_api": {}, "brew_api": {}},
        )

    @staticmethod
    def _resolver():
        def resolve_builds(compose_name, ctx):
            return [
                {
                    "compose": compose_name,
                    "distro": "rhel",
                    "build_id": "pkg-1.0",
                    "packages": ["pkg-1.0"],
                    "nvr": "pkg-1.0",
                }
            ]

        stub = MagicMock()
        stub.resolve_builds = resolve_builds
        return stub

    def _dispatch(self, set_names, side_effect):
        """Dispatch one spec per name; return (results, writer, records, post)."""
        ctx = self._make_ctx()
        writer = ManifestWriter(
            run_id=generate_ulid(), command="test", argv=["enge", "test"]
        )
        results = []
        with (
            captured_logs("enge") as records,
            patch(PLAN_FILTER, return_value="name: /plans/.*"),
            patch(HTTP_POST, side_effect=side_effect) as post,
        ):
            for idx, name in enumerate(set_names, start=1):
                results.append(
                    process_request_spec(
                        idx=idx,
                        total_expected_requests=len(set_names),
                        spec=self._spec(name),
                        artifact_type="fedora-koji-build",
                        artifact_resolver=self._resolver(),
                        ctx=ctx,
                        manifest_writer=writer,
                    )
                )
        return results, writer, records, post

    def test_rejected_request_is_a_failed_result_logged_once(self):
        results, writer, records, post = self._dispatch(
            ["alpha"], [_Reply(401, {"message": "invalid api key"})]
        )

        post.assert_called_once()
        result = results[0]
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(
            (result["set_name"], result["tier"], result["arch"]),
            ("alpha", "tier0", "x86_64"),
        )
        self.assertIn("HTTP 401", result["error"])
        loud = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
        self.assertEqual(len(loud), 1, loud)
        self.assertEqual(writer.to_dict()["requests"], [])
        self.assertFalse((self.tmp / "runs").exists(), "a manifest was flushed")

    def test_non_2xx_reply_carrying_an_id_is_not_recorded(self):
        results, writer, _, post = self._dispatch(
            ["alpha"], [_Reply(500, {"id": "ghost"})]
        )

        post.assert_called_once()
        self.assertEqual(results[0]["status"], "failed", results[0])
        self.assertEqual(writer.to_dict()["requests"], [])

    def test_transport_error_fails_only_that_request(self):
        results, writer, _, post = self._dispatch(
            ["alpha", "beta"],
            [
                requests.exceptions.ReadTimeout("slow"),
                _Reply(200, {"id": "task-beta"}),
            ],
        )

        self.assertEqual(post.call_count, 2)
        self.assertEqual([r["status"] for r in results], ["failed", "submitted"])
        self.assertIn("may have been created", results[0]["error"])
        recorded = [(r["task_id"], r["set"]) for r in writer.to_dict()["requests"]]
        self.assertEqual(recorded, [("task-beta", "beta")])


class TestDispatchExitCodes(unittest.TestCase):
    """Exit-code semantics are unchanged: all failed 1, some failed 2, none 0."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _ctx(self):
        cli = SimpleNamespace(
            output_format="terminal",
            dryrun=False,
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
            manifest_runs_dir=str(self.tmp / "runs"),
            manifest_latest=str(self.tmp / "latest"),
        )

    def _run(self, statuses):
        def fake(
            idx, total, spec, artifact_type, resolver=None, *, ctx, manifest_writer=None
        ):
            status = statuses[idx - 1]
            if status == "failed":
                return {
                    "status": "failed",
                    "error": "boom",
                    "set_name": "demo",
                    "tier": "tier0",
                    "arch": "x86_64",
                }
            return {"status": "submitted", "summary": "ok"}

        with (
            patch(
                "enge.dispatch.__main__.expand_set_requests",
                return_value=[MagicMock() for _ in statuses],
            ),
            patch(
                "enge.dispatch.__main__.process_request_spec", side_effect=fake
            ) as process,
        ):
            code = main(self._ctx())
        self.assertEqual(process.call_count, len(statuses))
        return code

    def test_all_requests_failed_exits_1(self):
        self.assertEqual(self._run(["failed", "failed"]), 1)

    def test_some_requests_failed_exits_2(self):
        self.assertEqual(self._run(["submitted", "failed"]), 2)

    def test_no_request_failed_exits_0(self):
        self.assertEqual(self._run(["submitted", "submitted"]), 0)


if __name__ == "__main__":
    unittest.main()
