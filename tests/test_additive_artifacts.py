"""Additive COPR + Brew artifact resolution.

A single dispatch request must be able to carry build artifacts from both
families at once, each entry labelled with its own Testing Farm artifact
type, with every configured reference required to resolve and the same
package refused from both families.

The doubles and the ``_make_ctx`` helper come from the characterization
module written before this behavior existed; the richer doubles below
keep its shape and only make the returned build follow the reference so a
mixed list is distinguishable.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from enge.dispatch.__main__ import get_artifact_info
from enge.dispatch.artifacts import ArtifactResolver
from enge.dispatch.set_flow import RequestSpec, process_request_spec
from enge.dispatch.tf_send_request import SubmitTest
from enge.utils.errors import ArtifactResolutionError, ValidationError
from enge.utils.globals import ARTIFACT_MAPPING

from tests._helpers import captured_logs
from tests.test_get_artifact_info import FakeBrewRef, FakeCoprRef, _make_ctx

COMPOSE = "RHEL-9.6.0-updates-20261105.0"


class RefAwareCoprRef(FakeCoprRef):
    """COPR double whose build id follows the reference it was given."""

    def get_info(self, packages, repo, reference, composes, options):
        ref = str(reference[0])
        return [
            {
                "compose": composes[0],
                "distro": "rhel-9.6",
                "build_id": f"copr-{ref}",
                # The real COPR resolver fills this with binary NVRAs.
                "packages": [f"{packages}-1.0-1.el9.noarch"],
            }
        ]


class RefAwareBrewRef(FakeBrewRef):
    """Brew double that reports the NVR it was asked for, like the real one."""

    def get_info(self, packages, reference, composes, options):
        nvr = str(reference[0])
        return [
            {
                "compose": composes[0],
                "distro": "rhel-9.6",
                "build_id": nvr,
                # The real Brew resolver fills this with the source name.
                "packages": [packages],
                "nvr": nvr,
            }
        ]


class StaleCoprRef(RefAwareCoprRef):
    """COPR double where one reference resolves to nothing, as a stale one does."""

    UNRESOLVABLE = "copr-stale-ref"

    def get_info(self, packages, repo, reference, composes, options):
        if str(reference[0]) == self.UNRESOLVABLE:
            return []
        return super().get_info(packages, repo, reference, composes, options)


class StaleBrewRef(RefAwareBrewRef):
    """Brew double where one reference resolves to nothing, as a stale one does.

    The Brew family is the one the live config sets exercise -- they pair two
    Brew references each -- so its half of the guard gets its own double.
    """

    UNRESOLVABLE = "stale-pkg-9.9.9-1.el9"

    def get_info(self, packages, reference, composes, options):
        if str(reference[0]) == self.UNRESOLVABLE:
            return []
        return super().get_info(packages, reference, composes, options)


class TestBothFamiliesResolve(unittest.TestCase):
    """Both configured families reach the build list, COPR first."""

    @patch("enge.dispatch.__main__.CoprRef", RefAwareCoprRef)
    @patch("enge.dispatch.__main__.BrewRef", RefAwareBrewRef)
    def test_both_families_resolve_into_one_list(self):
        ctx = _make_ctx(
            copr_references=["copr-ref-1"],
            brew_references=["leapp-repository-0.25.0-2.el8_10"],
        )
        result = get_artifact_info(COMPOSE, ctx)

        self.assertEqual(
            [b["build_id"] for b in result],
            ["copr-copr-ref-1", "leapp-repository-0.25.0-2.el8_10"],
            "both families must resolve, COPR entries first",
        )

    @patch("enge.dispatch.__main__.CoprRef", RefAwareCoprRef)
    @patch("enge.dispatch.__main__.BrewRef", RefAwareBrewRef)
    def test_each_build_carries_its_own_type(self):
        ctx = _make_ctx(
            copr_references=["copr-ref-1"],
            brew_references=["leapp-repository-0.25.0-2.el8_10"],
        )
        result = get_artifact_info(COMPOSE, ctx)

        self.assertEqual(
            [b["type"] for b in result],
            [ARTIFACT_MAPPING["copr"], ARTIFACT_MAPPING["brew"]],
        )

    @patch("enge.dispatch.__main__.CoprRef", RefAwareCoprRef)
    def test_cli_brew_does_not_suppress_set_level_copr(self):
        """--brew wins its own family only; configured COPR still resolves."""
        cli_brew = RefAwareBrewRef(["leapp-repository-0.25.0-2.el8_10"])
        ctx = _make_ctx(cli_brew=[cli_brew], copr_references=["copr-ref-1"])
        result = get_artifact_info(COMPOSE, ctx)

        self.assertEqual(
            [b["build_id"] for b in result],
            ["copr-copr-ref-1", "leapp-repository-0.25.0-2.el8_10"],
        )


class TestPerReferenceFailFast(unittest.TestCase):
    """A reference that resolves to nothing fails the request."""

    @patch("enge.dispatch.__main__.CoprRef", StaleCoprRef)
    def test_unresolvable_reference_fails_the_request(self):
        """One stale reference out of two in ONE family is not dropped."""
        ctx = _make_ctx(copr_references=["copr-ref-1", StaleCoprRef.UNRESOLVABLE])

        with self.assertRaises(ArtifactResolutionError):
            get_artifact_info(COMPOSE, ctx)

    @patch("enge.dispatch.__main__.CoprRef", StaleCoprRef)
    def test_error_names_the_unresolved_reference(self):
        ctx = _make_ctx(copr_references=["copr-ref-1", StaleCoprRef.UNRESOLVABLE])

        with self.assertRaises(ArtifactResolutionError) as cm:
            get_artifact_info(COMPOSE, ctx)
        self.assertIn(StaleCoprRef.UNRESOLVABLE, str(cm.exception))

    @patch("enge.dispatch.__main__.BrewRef", StaleBrewRef)
    def test_unresolvable_brew_reference_fails_the_request(self):
        """The Brew half of the guard: two references, one of them stale."""
        ctx = _make_ctx(
            brew_references=[
                "leapp-repository-0.25.0-2.el8_10",
                StaleBrewRef.UNRESOLVABLE,
            ]
        )

        with self.assertRaises(ArtifactResolutionError) as cm:
            get_artifact_info(COMPOSE, ctx)
        self.assertIn(StaleBrewRef.UNRESOLVABLE, str(cm.exception))


class TestPackageCollision(unittest.TestCase):
    """The same package may not be requested from both families."""

    COPR_REF = "lp:main"  # alias table -> leapp
    BREW_SAME = "leapp-0.21.0-3.el9"  # NVR -> leapp
    BREW_OTHER = "leapp-repository-0.25.0-2.el8_10"  # NVR -> leapp-repository

    @patch("enge.dispatch.__main__.CoprRef", RefAwareCoprRef)
    @patch("enge.dispatch.__main__.BrewRef", RefAwareBrewRef)
    def test_same_package_from_both_families_is_rejected(self):
        ctx = _make_ctx(
            copr_references=[self.COPR_REF], brew_references=[self.BREW_SAME]
        )

        with self.assertRaises(ArtifactResolutionError):
            get_artifact_info(COMPOSE, ctx)

    @patch("enge.dispatch.__main__.CoprRef", RefAwareCoprRef)
    @patch("enge.dispatch.__main__.BrewRef", RefAwareBrewRef)
    def test_collision_error_names_package_and_both_sources(self):
        ctx = _make_ctx(
            copr_references=[self.COPR_REF], brew_references=[self.BREW_SAME]
        )

        with self.assertRaises(ArtifactResolutionError) as cm:
            get_artifact_info(COMPOSE, ctx)
        message = str(cm.exception)
        self.assertIn("leapp", message)
        self.assertIn(self.COPR_REF, message)
        self.assertIn(self.BREW_SAME, message)

    @patch("enge.dispatch.__main__.CoprRef", RefAwareCoprRef)
    @patch("enge.dispatch.__main__.BrewRef", RefAwareBrewRef)
    def test_different_packages_from_both_families_dispatch(self):
        """leapp from COPR next to leapp-repository from Brew is the point."""
        ctx = _make_ctx(
            copr_references=[self.COPR_REF], brew_references=[self.BREW_OTHER]
        )
        result = get_artifact_info(COMPOSE, ctx)

        self.assertEqual(len(result), 2)
        self.assertEqual(
            [b["requested_package"] for b in result], ["leapp", "leapp-repository"]
        )


class TestResolverLogSeverity(unittest.TestCase):
    """A recoverable per-request failure must not be logged CRITICAL.

    ``ArtifactResolver.resolve_builds`` logs CRITICAL before re-raising. For
    an ``ArtifactResolutionError`` that contradicts the contract the same
    code establishes -- the request fails, the run continues, exit 2 -- and
    reports one event twice, since the dispatch summary already prints
    ``FAILED set/tier/arch: ...``.
    """

    @patch("enge.dispatch.__main__.BrewRef", StaleBrewRef)
    def test_artifact_resolution_error_is_not_logged_critical(self):
        ctx = _make_ctx(brew_references=[StaleBrewRef.UNRESOLVABLE])

        with captured_logs("enge") as records:
            with self.assertRaises(ArtifactResolutionError):
                ArtifactResolver().resolve_builds(COMPOSE, ctx)

        self.assertEqual(
            [r.getMessage() for r in records if r.levelno >= logging.CRITICAL],
            [],
            "a recoverable per-request failure must not be CRITICAL",
        )

    def test_other_resolution_failures_stay_critical(self):
        """Only the recoverable type is demoted; everything else is unchanged."""
        ctx = _make_ctx(cli_copr=[None])  # bare --copr: still a ValidationError

        with captured_logs("enge") as records:
            with self.assertRaises(ValidationError):
                ArtifactResolver().resolve_builds(COMPOSE, ctx)

        critical = [r.getMessage() for r in records if r.levelno >= logging.CRITICAL]
        self.assertTrue(
            any("Failed to resolve artifacts" in m for m in critical),
            f"expected a CRITICAL from the resolver, got {critical}",
        )


def _dryrun_ctx():
    """Minimal ctx for a dry-run pass through process_request_spec."""
    ctx = MagicMock()
    ctx.testing_farm = {"api_key": "token"}
    ctx.testing_farm_endpoint = MagicMock(
        log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
    )
    ctx.tests = {
        "git_url": "https://git.example/repo",
        "git_ref": "main",
        "parallel_limit": 5,
        "tier": {},
    }
    ctx.project = {"repo_url": "https://git.example/repo", "name": "leapp"}
    ctx.cli_args = MagicMock()
    ctx.cli_args.copr = None
    ctx.cli_args.brew = None
    ctx.cli_args.testfilter = None
    ctx.cli_args.test = None
    ctx.cli_args.planfilter = None
    ctx.cli_args.environment = None
    ctx.cli_args.rp = False
    ctx.cli_args.dryrun = True
    ctx.cli_args.output_format = "json"
    ctx.config = {}
    ctx.pool = None
    ctx.parallel_limit = 5
    ctx.architectures = ["x86_64"]
    ctx.environment_variables = {}
    ctx.tmt_context = {}
    ctx.copr_reference = None
    ctx.copr_references = []
    ctx.copr_api = {}
    ctx.brew_reference = None
    ctx.brew_references = []
    ctx.brew_api = {}
    return ctx


MIXED_BUILDS = [
    {
        "compose": "RHEL-9.2.0",
        "distro": "rhel-9.2",
        "build_id": "9100001",
        "packages": ["leapp-0.21.0-1.el9.noarch"],
        "type": ARTIFACT_MAPPING["copr"],
        "requested_package": "leapp",
    },
    {
        "compose": "RHEL-9.2.0",
        "distro": "rhel-9.2",
        "build_id": "leapp-repository-0.25.0-2.el9",
        "packages": ["leapp-repository"],
        "nvr": "leapp-repository-0.25.0-2.el9",
        "type": ARTIFACT_MAPPING["brew"],
        "requested_package": "leapp-repository",
    },
]


class TestMixedRequestPayload(unittest.TestCase):
    """A mixed build list reaches the Testing Farm payload intact."""

    def test_dry_run_payload_contains_both_artifact_types(self):
        ctx = _dryrun_ctx()
        spec = RequestSpec(
            set_name="mixed",
            tier="tier0",
            plan="/plans/p1",
            arch="x86_64",
            source_spec={"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            upgrade_path="rhel-9.2-to-9.4",
            effective_values={"copr_api": {}, "brew_api": {}},
        )
        resolver = MagicMock()
        resolver.resolve_builds = lambda compose_name, ctx: [
            dict(b) for b in MIXED_BUILDS
        ]

        with (
            patch(
                "enge.dispatch.set_flow.generate_tier_plan_filter",
                return_value="name: /plans/.*",
            ),
            patch(
                "enge.dispatch.set_flow.generate_environment_variables", return_value={}
            ),
            patch(
                "enge.dispatch.set_flow.parse_environment_variables", return_value={}
            ),
            patch(
                "enge.dispatch.set_flow.merge_set_environment_variables",
                return_value={},
            ),
        ):
            result = process_request_spec(
                idx=1,
                total_expected_requests=1,
                spec=spec,
                artifact_resolver=resolver,
                ctx=ctx,
            )

        artifacts = result["payload"]["environments"][0]["artifacts"]
        self.assertEqual(
            [(a["type"], a["id"]) for a in artifacts],
            [
                (ARTIFACT_MAPPING["copr"], "9100001"),
                (ARTIFACT_MAPPING["brew"], "leapp-repository-0.25.0-2.el9"),
            ],
        )

    def test_mixed_request_enriches_tmt_context_with_brew_nvrs_only(self):
        """Deliberate gap: COPR builds never reach the TMT context.

        ``_enrich_tmt_context_with_brew_nvrs`` filters on the Brew type, so a
        mixed request records the Brew RC and not the COPR build that makes
        it installable. Pinned so a later change to that filter is a
        conscious one.
        """
        submit = SubmitTest.__new__(SubmitTest)
        submit.artifacts = [
            {
                "id": "leapp-0.21.0-1.el9",
                "type": ARTIFACT_MAPPING["copr"],
                "package": "leapp",
                "packages": ["leapp-0.21.0-1.el9.noarch"],
            },
            {
                "id": "leapp-repository-0.25.0-2.el9",
                "type": ARTIFACT_MAPPING["brew"],
                "package": "leapp-repository",
                "packages": ["leapp-repository"],
            },
        ]

        enriched = submit._enrich_tmt_context_with_brew_nvrs({"distro": "rhel-9.2"})

        self.assertEqual(enriched.get("leapp-repository"), "0.25.0-2.el9")
        self.assertNotIn("leapp", enriched)


class TestMixedRequestManifest(unittest.TestCase):
    """Manifest records build_artifacts with both id and type for mixed requests."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from pathlib import Path

        tmp = Path(self._tmp.name)
        self.runs_dir = tmp / "runs"
        self.latest = tmp / "latest"

    def test_dispatch_manifest_records_build_artifacts_with_type(self):
        """The dispatch writer records build_artifacts alongside build_ids."""
        import json
        from types import SimpleNamespace

        from enge.utils.manifest import ManifestWriter
        from enge.utils.ulid import generate_ulid

        ctx = _dryrun_ctx()
        ctx.cli_args.dryrun = False
        # Override the MagicMock with real values for fields written to manifest
        ctx.cli_args = SimpleNamespace(
            dryrun=False,
            copr=None,
            brew=None,
            testfilter=None,
            test=None,
            planfilter=None,
            environment=None,
            rp=False,
            output_format="json",
        )
        ctx.manifest_runs_dir = str(self.runs_dir)
        ctx.manifest_latest = str(self.latest)

        spec = RequestSpec(
            set_name="mixed",
            tier="tier0",
            plan="/plans/p1",
            arch="x86_64",
            source_spec={"major": 9, "minor": 2, "compose_name": "RHEL-9.2.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0"},
            upgrade_path="rhel-9.2-to-9.4",
            effective_values={"copr_api": {}, "brew_api": {}},
        )
        resolver = MagicMock()
        resolver.resolve_builds = lambda compose_name, ctx: [
            dict(b) for b in MIXED_BUILDS
        ]

        manifest_writer = ManifestWriter(
            run_id=generate_ulid(),
            command="test",
            argv=["enge", "test"],
        )

        # Mock TF HTTP POST to return success
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "aaaaaaaa-0000-0000-0000-000000000001"}

        with (
            patch(
                "enge.dispatch.set_flow.generate_tier_plan_filter",
                return_value="name: /plans/.*",
            ),
            patch(
                "enge.dispatch.set_flow.generate_environment_variables", return_value={}
            ),
            patch(
                "enge.dispatch.set_flow.parse_environment_variables", return_value={}
            ),
            patch(
                "enge.dispatch.set_flow.merge_set_environment_variables",
                return_value={},
            ),
            patch("enge.dispatch.tf_send_request.requests.Session") as mock_session,
        ):
            mock_session.return_value.post.return_value = mock_response
            process_request_spec(
                idx=1,
                total_expected_requests=1,
                spec=spec,
                artifact_resolver=resolver,
                ctx=ctx,
                manifest_writer=manifest_writer,
            )

        # Read the flushed manifest
        paths = list(self.runs_dir.glob("*.json"))
        self.assertEqual(len(paths), 1, "exactly one manifest should be written")
        manifest = json.loads(paths[0].read_text())

        # Verify the request entry has build_artifacts
        self.assertEqual(len(manifest["requests"]), 1)
        entry = manifest["requests"][0]

        # build_artifacts should mirror build_ids in order
        self.assertIn("build_artifacts", entry)
        self.assertEqual(
            entry["build_artifacts"],
            [
                {"id": "9100001", "type": ARTIFACT_MAPPING["copr"]},
                {
                    "id": "leapp-repository-0.25.0-2.el9",
                    "type": ARTIFACT_MAPPING["brew"],
                },
            ],
        )

        # build_ids should have the same IDs in the same order
        self.assertEqual(
            entry["build_ids"],
            ["9100001", "leapp-repository-0.25.0-2.el9"],
        )


if __name__ == "__main__":
    unittest.main()
