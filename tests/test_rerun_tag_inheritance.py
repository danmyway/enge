"""Tests for rerun tag inheritance and manifest lineage."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._helpers import make_app_context

from enge.rerun.__main__ import (
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


if __name__ == "__main__":
    unittest.main()
