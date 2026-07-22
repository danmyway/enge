"""Tests for rerun tag inheritance and manifest lineage."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._helpers import make_app_context

from enge.rerun.__main__ import (
    _build_parent_request_index,
    _extract_tags_from_filename,
    _get_next_rerun_tag,
    _unique_preserve,
)
from enge.utils.manifest import ManifestWriter
from enge.utils.ulid import generate_ulid


class TestLegacyRerunTagInheritance(unittest.TestCase):
    def test_extract_tags_from_filename(self):
        path = Path("enge_jobs_archive_20260611.rerun.tier0")
        self.assertEqual(_extract_tags_from_filename(path), ["rerun", "tier0"])

    def test_get_next_rerun_tag_progression(self):
        self.assertEqual(_get_next_rerun_tag([]), "rerun")
        self.assertEqual(_get_next_rerun_tag(["rerun"]), "rerun1")
        self.assertEqual(_get_next_rerun_tag(["rerun1"]), "rerun2")

    def test_unique_preserve_base_and_inherited_ordering(self):
        base_tags = ["enge", "automated", "rerun"]
        current_tags = ["rerun", "tier0"]
        result = _unique_preserve([*base_tags, *current_tags])
        self.assertEqual(result, ["enge", "automated", "rerun", "tier0"])


class TestBuildParentRequestIndex(unittest.TestCase):
    """Unit tests for the parent-manifest request index used for rerun
    field inheritance (set/tier/target_compose)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_indexes_parent_requests_by_task_id(self):
        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id, command="test", argv=["enge", "test"], tags=[]
        )
        parent.add_request(
            "task-a",
            set_name="alpha",
            tier="tier0",
            arch="ppc64le",
            target_compose="RHEL-10.0",
        )
        parent.flush(self.runs_dir, self.latest)

        index = _build_parent_request_index(parent_id, str(self.runs_dir))

        self.assertEqual(index["task-a"]["set"], "alpha")
        self.assertEqual(index["task-a"]["tier"], "tier0")
        self.assertEqual(index["task-a"]["target_compose"], "RHEL-10.0")

    @patch("enge.rerun.__main__.ManifestReader.get_run")
    def test_no_parent_run_id_skips_manifest_load(self, mock_get_run):
        index = _build_parent_request_index(None, str(self.runs_dir))

        self.assertEqual(index, {})
        mock_get_run.assert_not_called()

    def test_load_failure_returns_empty_index_and_logs_debug(self):
        missing_parent_id = generate_ulid()  # no manifest file ever written

        with self.assertLogs("enge.rerun.__main__", level="DEBUG") as log_ctx:
            index = _build_parent_request_index(missing_parent_id, str(self.runs_dir))

        self.assertEqual(index, {})
        self.assertTrue(any("parent manifest" in msg.lower() for msg in log_ctx.output))


TASK_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _mock_tf_response():
    resp = MagicMock()
    resp.json.return_value = {
        "id": TASK_UUID,
        "environments_requested": [{"os": {"compose": "RHEL-9.0"}, "arch": "x86_64"}],
        "test": {"fmf": {"name": "/plan/tier0"}},
    }
    return resp


def _parsed_dict():
    return {
        TASK_UUID: {
            "source_compose": "RHEL-9.0",
            "testsuites": [
                {
                    "testsuite_name": "/plan/tier0",
                    "testsuite_result": "FAILED",
                    "testsuite_arch": "x86_64",
                    "testcases": [
                        {
                            "testcase_name": "test::failing",
                            "testcase_result": "FAILED",
                        },
                    ],
                }
            ],
        },
    }


class TestRerunManifestLineageWiring(unittest.TestCase):
    """Wiring-level tests: exercise rerun main() and verify child manifest lineage."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _find_child_manifest(self, exclude_id=None):
        manifests = list(self.runs_dir.glob("*.json"))
        if exclude_id:
            manifests = [m for m in manifests if m.stem != exclude_id]
        self.assertEqual(
            len(manifests), 1, f"Expected 1 child manifest, got {len(manifests)}"
        )
        return json.loads(manifests[0].read_text())

    @patch("enge.rerun.__main__._create_rerun_launch_for_payload", return_value=None)
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_manifest_source_wires_parent_run_id_and_inherited_tags(
        self, mock_parse, mock_xunit, mock_submit_cls, mock_http, _mock_repin, _mock_rp
    ):
        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id,
            command="test",
            argv=["enge", "test"],
            tags=["nightly", "milestone-x"],
        )
        parent.add_request(TASK_UUID, tier="tier0", arch="x86_64")
        parent.flush(self.runs_dir, self.latest)

        api_url = f"https://tf.example.com/api/{TASK_UUID}"
        mock_parse.return_value = (
            [api_url],
            f"manifest:{parent_id}",
            {TASK_UUID: None, api_url: None},
        )
        mock_xunit.return_value = _parsed_dict()
        mock_http.return_value = _mock_tf_response()

        mock_submit = MagicMock()
        mock_submit.set_tag = ["cli-tag"]
        mock_submit.log_artifact_url = f"https://artifacts.example.com/{TASK_UUID}"
        mock_submit_cls.return_value = mock_submit

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

        from enge.rerun.__main__ import main

        main(ctx)

        child = self._find_child_manifest(exclude_id=parent_id)

        self.assertEqual(child["parent_run_id"], parent_id)
        self.assertEqual(child["tags"], ["nightly", "milestone-x", "cli-tag", "rerun"])

    @patch("enge.rerun.__main__._create_rerun_launch_for_payload", return_value=None)
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_raw_input_has_no_parent_and_cli_tags_only(
        self, mock_parse, mock_xunit, mock_submit_cls, mock_http, _mock_repin, _mock_rp
    ):
        api_url = f"https://tf.example.com/api/{TASK_UUID}"
        mock_parse.return_value = (
            [api_url],
            None,
            {TASK_UUID: None, api_url: None},
        )
        mock_xunit.return_value = _parsed_dict()
        mock_http.return_value = _mock_tf_response()

        mock_submit = MagicMock()
        mock_submit.set_tag = ["cli-tag"]
        mock_submit.log_artifact_url = f"https://artifacts.example.com/{TASK_UUID}"
        mock_submit_cls.return_value = mock_submit

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

        from enge.rerun.__main__ import main

        main(ctx)

        child = self._find_child_manifest()

        self.assertIsNone(child["parent_run_id"])
        self.assertEqual(child["tags"], ["cli-tag", "rerun"])

    @patch("enge.rerun.__main__._create_rerun_launch_for_payload", return_value=None)
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    def test_real_resolver_default_path_wires_lineage(
        self, mock_xunit, mock_submit_cls, mock_http, _mock_repin, _mock_rp
    ):
        """Integration: real parse_tasks_with_map against temp manifest store."""
        import uuid as uuid_mod

        task_uuid = str(uuid_mod.uuid4())

        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id,
            command="test",
            argv=["enge", "test"],
            tags=["nightly", "milestone-x"],
        )
        parent.add_request(task_uuid, tier="tier0", arch="x86_64")
        parent.flush(self.runs_dir, self.latest)

        mock_xunit.return_value = {
            task_uuid: {
                "source_compose": "RHEL-9.0",
                "testsuites": [
                    {
                        "testsuite_name": "/plan/tier0",
                        "testsuite_result": "FAILED",
                        "testsuite_arch": "x86_64",
                        "testcases": [
                            {
                                "testcase_name": "test::failing",
                                "testcase_result": "FAILED",
                            },
                        ],
                    }
                ],
            },
        }

        tf_resp = MagicMock()
        tf_resp.json.return_value = {
            "id": task_uuid,
            "environments_requested": [
                {"os": {"compose": "RHEL-9.0"}, "arch": "x86_64"}
            ],
            "test": {"fmf": {"name": "/plan/tier0"}},
        }
        mock_http.return_value = tf_resp

        mock_submit = MagicMock()
        mock_submit.set_tag = ["cli-tag"]
        mock_submit.log_artifact_url = f"https://artifacts.example.com/{task_uuid}"
        mock_submit_cls.return_value = mock_submit

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

        from enge.rerun.__main__ import main

        main(ctx)

        child = self._find_child_manifest(exclude_id=parent_id)

        self.assertEqual(child["parent_run_id"], parent_id)
        self.assertEqual(child["tags"], ["nightly", "milestone-x", "cli-tag", "rerun"])


class TestRerunManifestFieldInheritance(unittest.TestCase):
    """Wiring-level tests: rerun request entries inherit set/tier/target_compose
    from the parent manifest's matching request entry, and always record
    rerun_of (the original task uuid)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _find_child_manifest(self, exclude_id):
        manifests = [m for m in self.runs_dir.glob("*.json") if m.stem != exclude_id]
        self.assertEqual(
            len(manifests), 1, f"Expected 1 child manifest, got {len(manifests)}"
        )
        return json.loads(manifests[0].read_text())

    @patch("enge.rerun.__main__._create_rerun_launch_for_payload", return_value=None)
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_inherits_set_tier_target_compose_and_rerun_of(
        self, mock_parse, mock_xunit, mock_submit_cls, mock_http, _mock_repin, _mock_rp
    ):
        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id, command="test", argv=["enge", "test"], tags=[]
        )
        # Parent's arch ("ppc64le") deliberately differs from the TF payload's
        # arch ("x86_64", set in _mock_tf_response) to pin that arch is always
        # payload-derived, never inherited from the parent.
        parent.add_request(
            TASK_UUID,
            set_name="alpha",
            tier="tier0",
            arch="ppc64le",
            target_compose="RHEL-10.0",
        )
        parent.flush(self.runs_dir, self.latest)

        api_url = f"https://tf.example.com/api/{TASK_UUID}"
        mock_parse.return_value = (
            [api_url],
            f"manifest:{parent_id}",
            {TASK_UUID: None, api_url: None},
        )
        mock_xunit.return_value = _parsed_dict()
        mock_http.return_value = _mock_tf_response()

        mock_submit = MagicMock()
        mock_submit.set_tag = []
        mock_submit.log_artifact_url = f"https://artifacts.example.com/{TASK_UUID}"
        mock_submit_cls.return_value = mock_submit

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

        from enge.rerun.__main__ import main

        main(ctx)

        child = self._find_child_manifest(exclude_id=parent_id)
        entry = child["requests"][0]

        self.assertEqual(entry["set"], "alpha")
        self.assertEqual(entry["tier"], "tier0")
        self.assertEqual(entry["target_compose"], "RHEL-10.0")
        self.assertEqual(entry["arch"], "x86_64")
        self.assertEqual(entry["rerun_of"], TASK_UUID)

    @patch("enge.rerun.__main__._create_rerun_launch_for_payload", return_value=None)
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_original_uuid_absent_from_parent_yields_none_fields(
        self, mock_parse, mock_xunit, mock_submit_cls, mock_http, _mock_repin, _mock_rp
    ):
        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id, command="test", argv=["enge", "test"], tags=[]
        )
        parent.add_request(
            "some-other-task-uuid",
            set_name="alpha",
            tier="tier0",
            target_compose="RHEL-10.0",
        )
        parent.flush(self.runs_dir, self.latest)

        api_url = f"https://tf.example.com/api/{TASK_UUID}"
        mock_parse.return_value = (
            [api_url],
            f"manifest:{parent_id}",
            {TASK_UUID: None, api_url: None},
        )
        mock_xunit.return_value = _parsed_dict()
        mock_http.return_value = _mock_tf_response()

        mock_submit = MagicMock()
        mock_submit.set_tag = []
        mock_submit.log_artifact_url = f"https://artifacts.example.com/{TASK_UUID}"
        mock_submit_cls.return_value = mock_submit

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

        from enge.rerun.__main__ import main

        main(ctx)

        child = self._find_child_manifest(exclude_id=parent_id)
        entry = child["requests"][0]

        self.assertIsNone(entry["set"])
        self.assertIsNone(entry["tier"])
        self.assertIsNone(entry["target_compose"])
        self.assertEqual(entry["rerun_of"], TASK_UUID)

    @patch("enge.rerun.__main__.ManifestReader.get_run")
    @patch("enge.rerun.__main__._create_rerun_launch_for_payload", return_value=None)
    @patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c)
    @patch("enge.rerun.__main__.http_get")
    @patch("enge.rerun.__main__.SubmitTest")
    @patch("enge.rerun.__main__.parse_request_xunit")
    @patch("enge.rerun.__main__.parse_tasks_with_map")
    def test_raw_input_source_skips_parent_load_and_fields_are_none(
        self,
        mock_parse,
        mock_xunit,
        mock_submit_cls,
        mock_http,
        _mock_repin,
        _mock_rp,
        mock_get_run,
    ):
        api_url = f"https://tf.example.com/api/{TASK_UUID}"
        mock_parse.return_value = (
            [api_url],
            None,
            {TASK_UUID: None, api_url: None},
        )
        mock_xunit.return_value = _parsed_dict()
        mock_http.return_value = _mock_tf_response()

        mock_submit = MagicMock()
        mock_submit.set_tag = []
        mock_submit.log_artifact_url = f"https://artifacts.example.com/{TASK_UUID}"
        mock_submit_cls.return_value = mock_submit

        ctx = make_app_context(
            action="rerun",
            extra_cli={"error": False, "fail": False, "dryrun": False},
            manifest_runs_dir=str(self.runs_dir),
            manifest_latest=str(self.latest),
        )

        from enge.rerun.__main__ import main

        main(ctx)

        mock_get_run.assert_not_called()

        child = self._find_child_manifest(exclude_id=None)
        entry = child["requests"][0]

        self.assertIsNone(entry["set"])
        self.assertIsNone(entry["tier"])
        self.assertIsNone(entry["target_compose"])
        self.assertEqual(entry["rerun_of"], TASK_UUID)


def _mock_tf_response_with_context(
    *,
    git_ref="main",
    event="preliminary",
    source_release="9.9",
    target_release="10.3",
    artifact_ids=None,
):
    """Refetched TF request body carrying full dispatch context, mirroring
    what a real dispatch-created request actually looks like on the wire:
    test.fmf.ref (git_ref), tmt.context.event (event),
    variables.SOURCE_RELEASE/TARGET_RELEASE (source/target, already in the
    manifest's bare {major}.{minor} format -- see
    generate_environment_variables._format_release), and artifacts[].id
    (build_ids)."""
    variables = {}
    if source_release is not None:
        variables["SOURCE_RELEASE"] = source_release
    if target_release is not None:
        variables["TARGET_RELEASE"] = target_release

    environment = {"os": {"compose": "RHEL-9.9.0"}, "arch": "x86_64"}
    if variables:
        environment["variables"] = variables
    if artifact_ids is not None:
        environment["artifacts"] = [
            {"id": aid, "type": "fedora-koji-build", "packages": ["leapp"]}
            for aid in artifact_ids
        ]
    tmt_context = {}
    if event is not None:
        tmt_context["event"] = event
    if tmt_context:
        environment["tmt"] = {"context": tmt_context}

    resp = MagicMock()
    resp.json.return_value = {
        "id": TASK_UUID,
        "environments_requested": [environment],
        "test": (
            {"fmf": {"name": "/plan/tier0", "ref": git_ref}}
            if git_ref is not None
            else {"fmf": {"name": "/plan/tier0"}}
        ),
    }
    return resp


class TestRerunDispatchContextFromPayload(unittest.TestCase):
    """Rerun-written manifest entries must carry source/target/git_ref/
    event/build_ids derived directly from the refetched TF payload
    -- never from _build_parent_request_index, which has no data for these
    fields on any manifest predating this schema (i.e. every real parent
    manifest today). See Step-0 item 5 in the dispatch-context-schema
    session: the payload's variables.SOURCE_RELEASE/TARGET_RELEASE are
    already in the exact bare format the schema needs; parent-index
    lookup is a dead end for this specific data."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.runs_dir = self.tmp / "runs"
        self.latest = self.tmp / "latest"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _find_child_manifest(self, exclude_id):
        manifests = [m for m in self.runs_dir.glob("*.json") if m.stem != exclude_id]
        self.assertEqual(
            len(manifests), 1, f"Expected 1 child manifest, got {len(manifests)}"
        )
        return json.loads(manifests[0].read_text())

    def _run_rerun(self, tf_response, parent_request_kwargs=None):
        """Build a parent manifest with NO dispatch-context fields on its
        request (matching every real manifest today, since this branch is
        what introduces them), run rerun main(), return the child's single
        request entry."""
        parent_id = generate_ulid()
        parent = ManifestWriter(
            run_id=parent_id, command="test", argv=["enge", "test"], tags=[]
        )
        parent.add_request(TASK_UUID, **(parent_request_kwargs or {"tier": "tier0"}))
        parent.flush(self.runs_dir, self.latest)

        api_url = f"https://tf.example.com/api/{TASK_UUID}"

        with (
            patch(
                "enge.rerun.__main__._create_rerun_launch_for_payload",
                return_value=None,
            ),
            patch("enge.rerun.__main__.repin_compose", side_effect=lambda c, u: c),
            patch("enge.rerun.__main__.http_get", return_value=tf_response),
            patch("enge.rerun.__main__.SubmitTest") as mock_submit_cls,
            patch(
                "enge.rerun.__main__.parse_request_xunit",
                return_value=_parsed_dict(),
            ),
            patch(
                "enge.rerun.__main__.parse_tasks_with_map",
                return_value=(
                    [api_url],
                    f"manifest:{parent_id}",
                    {TASK_UUID: None, api_url: None},
                ),
            ),
        ):
            mock_submit = MagicMock()
            mock_submit.set_tag = []
            mock_submit.log_artifact_url = f"https://artifacts.example.com/{TASK_UUID}"
            mock_submit_cls.return_value = mock_submit

            ctx = make_app_context(
                action="rerun",
                extra_cli={"error": False, "fail": False, "dryrun": False},
                manifest_runs_dir=str(self.runs_dir),
                manifest_latest=str(self.latest),
            )

            from enge.rerun.__main__ import main

            main(ctx)

        child = self._find_child_manifest(exclude_id=parent_id)
        return child["requests"][0], parent_id

    def test_entry_carries_full_context_from_payload(self):
        tf_response = _mock_tf_response_with_context(
            git_ref="rhsm-branch",
            event="preliminary",
            source_release="9.9",
            target_release="10.3",
            artifact_ids=["12345:centos-stream9-x86_64"],
        )
        entry, _parent_id = self._run_rerun(tf_response)

        self.assertEqual(entry["git_ref"], "rhsm-branch")
        self.assertEqual(entry["event"], "preliminary")
        self.assertEqual(entry["source"], "9.9")
        self.assertEqual(entry["target"], "10.3")
        self.assertEqual(entry["build_ids"], ["12345:centos-stream9-x86_64"])

    def test_entry_emit_always_defaults_when_payload_lacks_context(self):
        tf_response = _mock_tf_response_with_context(
            git_ref=None,
            event=None,
            source_release=None,
            target_release=None,
            artifact_ids=None,
        )
        entry, _parent_id = self._run_rerun(tf_response)

        self.assertIn("git_ref", entry)
        self.assertIsNone(entry["git_ref"])
        self.assertIn("event", entry)
        self.assertIsNone(entry["event"])
        self.assertIn("source", entry)
        self.assertIsNone(entry["source"])
        self.assertIn("target", entry)
        self.assertIsNone(entry["target"])
        self.assertIn("build_ids", entry)
        self.assertEqual(entry["build_ids"], [])

    def test_entry_ignores_parent_index_which_lacks_these_fields(self):
        """The parent manifest's request entry (built by the CURRENT
        add_request, pre-dating this branch's fields) has no source/
        target/git_ref/event/build_ids at all. The rerun-written
        child entry must still get correct values -- from the payload,
        never from the (data-less) parent index."""
        tf_response = _mock_tf_response_with_context(
            git_ref="main",
            event="ctc1",
            source_release="8.10",
            target_release="9.4",
            artifact_ids=["pkg-x"],
        )
        entry, parent_id = self._run_rerun(
            tf_response,
            parent_request_kwargs={
                "set_name": "alpha",
                "tier": "tier0",
                "target_compose": "RHEL-9.4.0",
            },
        )

        # Sanity: the parent entry carries no real value for these fields
        # (None/[] -- add_request's own emit-always defaults, not a value
        # this test set up), proving the child's values below cannot have
        # been inherited from the parent index.
        parent_manifest = json.loads((self.runs_dir / f"{parent_id}.json").read_text())
        found_parent = parent_manifest["requests"][0]
        self.assertEqual(found_parent["task_id"], TASK_UUID)
        self.assertIsNone(found_parent.get("source"))
        self.assertEqual(found_parent.get("build_ids"), [])

        self.assertEqual(entry["source"], "8.10")
        self.assertEqual(entry["target"], "9.4")
        self.assertEqual(entry["git_ref"], "main")
        self.assertEqual(entry["event"], "ctc1")
        self.assertEqual(entry["build_ids"], ["pkg-x"])


if __name__ == "__main__":
    unittest.main()
