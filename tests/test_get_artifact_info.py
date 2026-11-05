"""Characterization tests for dispatch.__main__.get_artifact_info().

These tests pin the current single-family artifact resolution behavior before
Task 3 rewrites it to be additive. They change no production code.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from enge.dispatch.__main__ import get_artifact_info
from enge.utils.errors import ValidationError


class FakeCoprRef:
    """Test double for CoprRef that returns a fixed build dict."""

    def __init__(self, references):
        self.ref = references

    def get_info(self, packages, repo, reference, composes, options):
        """Return a fake COPR build."""
        return [
            {
                "compose": composes[0],
                "distro": "rhel-9.6",
                "build_id": "copr-123",
                "packages": [packages],
            }
        ]


class FakeBrewRef:
    """Test double for BrewRef that returns a fixed build dict."""

    def __init__(self, references):
        self.ref = references

    def get_info(self, packages, reference, composes, options):
        """Return a fake Brew build."""
        return [
            {
                "compose": composes[0],
                "distro": "rhel-9.6",
                "build_id": "brew-456",
                "packages": [packages],
            }
        ]


def _make_ctx(
    cli_copr=None,
    cli_brew=None,
    copr_references=None,
    brew_references=None,
    copr_reference=None,
    brew_reference=None,
    tmt_context=None,
):
    """Build a minimal context object for get_artifact_info tests."""
    if tmt_context is None:
        tmt_context = {"distro": "rhel-9.6"}

    cli_attrs = {}
    if cli_copr is not None:
        cli_attrs["copr"] = cli_copr
    if cli_brew is not None:
        cli_attrs["brew"] = cli_brew

    return SimpleNamespace(
        cli_args=SimpleNamespace(**cli_attrs),
        copr_references=copr_references or [],
        brew_references=brew_references or [],
        copr_reference=copr_reference,
        brew_reference=brew_reference,
        copr_api={"package": "test-pkg", "repository": "test-repo"},
        brew_api={"package": "test-pkg"},
        project={"name": "test-project"},
        tmt_context=tmt_context,
        source_spec={"major": "9", "minor": "5"},
    )


class TestGetArtifactInfo(unittest.TestCase):
    """Characterization tests for the current single-family artifact resolution."""

    @patch("enge.dispatch.__main__.CoprRef", FakeCoprRef)
    def test_cli_copr_only_returns_copr_builds(self):
        """CLI --copr returns COPR builds only."""
        fake_copr = FakeCoprRef(["copr-ref-1"])
        ctx = _make_ctx(cli_copr=[fake_copr])
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["build_id"], "copr-123")
        self.assertIn("packages", result[0])

    @patch("enge.dispatch.__main__.BrewRef", FakeBrewRef)
    def test_cli_brew_only_returns_brew_builds(self):
        """CLI --brew returns Brew builds only."""
        fake_brew = FakeBrewRef(["brew-ref-1"])
        ctx = _make_ctx(cli_brew=[fake_brew])
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["build_id"], "brew-456")
        self.assertIn("packages", result[0])

    @patch("enge.dispatch.__main__.CoprRef", FakeCoprRef)
    def test_config_copr_references_only_returns_copr_builds(self):
        """Config copr_references returns COPR builds only."""
        ctx = _make_ctx(copr_references=["copr-ref-1"])
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["build_id"], "copr-123")
        self.assertIn("packages", result[0])

    @patch("enge.dispatch.__main__.BrewRef", FakeBrewRef)
    def test_config_brew_references_only_returns_brew_builds(self):
        """Config brew_references returns Brew builds only."""
        ctx = _make_ctx(brew_references=["brew-ref-1"])
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["build_id"], "brew-456")
        self.assertIn("packages", result[0])

    @patch("enge.dispatch.__main__.CoprRef", FakeCoprRef)
    def test_legacy_single_copr_reference_returns_copr_builds(self):
        """Legacy single copr_reference returns COPR builds only."""
        ctx = _make_ctx(copr_reference="copr-ref-1")
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["build_id"], "copr-123")
        self.assertIn("packages", result[0])

    @patch("enge.dispatch.__main__.BrewRef", FakeBrewRef)
    def test_legacy_single_brew_reference_returns_brew_builds(self):
        """Legacy single brew_reference returns Brew builds only."""
        ctx = _make_ctx(brew_reference="brew-ref-1")
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["build_id"], "brew-456")
        self.assertIn("packages", result[0])

    def test_no_references_returns_one_compose_entry(self):
        """No artifact references returns a single compose-only entry."""
        compose_name = "RHEL-9.6.0-updates-20261105.0"
        ctx = _make_ctx()
        result = get_artifact_info(compose_name, ctx)

        self.assertEqual(len(result), 1)
        # Assert the three keys individually per controller ruling
        self.assertEqual(result[0]["compose"], compose_name)
        self.assertIsNone(result[0]["build_id"])
        self.assertEqual(result[0]["distro"], ctx.tmt_context["distro"])

    def test_no_references_entry_has_no_artifact_type(self):
        """The compose-only entry carries no Testing Farm artifact type."""
        ctx = _make_ctx()
        result = get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)

        self.assertIsNone(result[0]["type"])

    def test_bare_copr_flag_raises_validation_error(self):
        """Bare --copr flag (parsed as [None]) raises ValidationError."""
        ctx = _make_ctx(cli_copr=[None])
        with self.assertRaises(ValidationError) as cm:
            get_artifact_info("RHEL-9.6.0-updates-20261105.0", ctx)
        self.assertIn("Failed to get artifact information", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
