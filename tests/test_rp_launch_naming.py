"""Tests for RP launch naming: {upgrade_path}~{tier}~{arch} grammar.

Red-first: these tests define the D1v2/D2v2 naming contract before the
implementation changes land.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests._helpers import make_app_context


def _make_rp_ctx(**extra_cli):
    return make_app_context(
        action="test",
        extra_config={
            "reportportal": {
                "url": "https://rp.example.com",
                "token": "fake-token",
                "project": "test-project",
            },
        },
        extra_cli=extra_cli,
    )


class TestLaunchNameGrammar(unittest.TestCase):
    """D1v2: auto-generated name must be {upgrade_path}~{tier}~{arch}."""

    def test_dispatch_name_format(self):
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name(
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertEqual(name, "9to10~tier0~x86_64")

    def test_set_name_excluded_from_name(self):
        """set_name is debugging context (carried by attribute), not part of the name."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name(
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertNotIn("smoke", name.lower())

    def test_event_excluded_from_name(self):
        """Event is run context (carried by attribute), not part of the name."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name_with_event = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
            source_release="9.7",
            target_release="10.1",
        )
        name_without_event = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertEqual(name_with_event, name_without_event)
        self.assertNotIn("preliminary", name_with_event.lower())

    def test_no_date_in_name(self):
        """Generated names must contain no date substring."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertIsNotNone(name)
        self.assertNotRegex(name, r"\d{4}-\d{2}-\d{2}")

    def test_centos_stream_upgrade_path(self):
        """CentOS Stream 9 -> RHEL 10 produces 9to10."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.0",
            target_release="10.1",
        )
        self.assertEqual(name, "9to10~tier0~x86_64")

    def test_omit_empty_segments(self):
        """Missing segments omitted, never blank; all-empty returns None."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name(
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertEqual(name, "9to10~tier0")
        self.assertNotIn("~~", name)

    def test_all_empty_returns_none(self):
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name()
        self.assertIsNone(name)

    def test_no_set_flow_equals_set_flow(self):
        """No-set flow name == set flow name for identical coordinates."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        set_flow_name = _generate_auto_launch_name(
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
            source_release="9.7",
            target_release="10.1",
        )
        no_set_flow_name = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertEqual(set_flow_name, no_set_flow_name)


class TestRerunNameEqualsDispatch(unittest.TestCase):
    """D2v2: rerun launch name == original dispatch name for same coordinates."""

    def test_rerun_name_matches_dispatch(self):
        """Rerun and dispatch for the same upgrade_path/tier/arch produce identical names."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        dispatch_name = _generate_auto_launch_name(
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
            source_release="9.7",
            target_release="10.1",
        )
        rerun_name = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertEqual(dispatch_name, rerun_name)

    def test_raw_input_rerun_name_matches(self):
        """Raw-input reruns (-i/-f, no parent manifest) produce the same name."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        dispatch_name = _generate_auto_launch_name(
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
            source_release="9.7",
            target_release="10.1",
        )
        raw_rerun_name = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertEqual(dispatch_name, raw_rerun_name)

    def test_no_rerun_prefix(self):
        """RERUN~ prefix must not appear in any generated name."""
        from enge.utils.source_target_parser import _generate_auto_launch_name

        name = _generate_auto_launch_name(
            architecture="x86_64",
            tier="tier0",
            source_release="9.7",
            target_release="10.1",
        )
        self.assertNotIn("RERUN", name)


class TestRerunSetAttribute(unittest.TestCase):
    """D3v2: rerun set attribute stamped from parent manifest, omitted when absent."""

    def _make_parent_manifest(self, tmpdir, run_id, requests):
        runs_dir = Path(tmpdir) / "runs"
        runs_dir.mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "requests": requests,
        }
        (runs_dir / f"{run_id}.json").write_text(json.dumps(manifest))
        return str(runs_dir)

    def test_set_attribute_stamped_from_parent(self):
        from enge.rerun.__main__ import _resolve_set_name_from_parent

        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = self._make_parent_manifest(
                tmpdir,
                "01PARENT000000000000000000",
                [{"task_id": "abc-123", "set": "smoke-tests", "tier": "tier0"}],
            )
            result = _resolve_set_name_from_parent(
                "01PARENT000000000000000000", "abc-123", runs_dir
            )
            self.assertEqual(result, "smoke-tests")

    def test_set_attribute_omitted_when_no_parent(self):
        from enge.rerun.__main__ import _resolve_set_name_from_parent

        result = _resolve_set_name_from_parent(None, "abc-123", "/nonexistent")
        self.assertIsNone(result)


class TestNamePrecedence(unittest.TestCase):
    """CLI --rp-launch and config [reportportal].launch override auto-gen."""

    def test_cli_rp_launch_overrides(self):
        from enge.utils.source_target_parser import (
            generate_reportportal_environment_variables,
        )
        from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX

        ctx = _make_rp_ctx(rp_launch="CUSTOM-NAME")
        env_vars = generate_reportportal_environment_variables(
            ctx.config,
            cli_args=ctx.cli_args,
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
        )
        launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
        self.assertEqual(env_vars[launch_key], "CUSTOM-NAME")

    def test_config_launch_overrides(self):
        from enge.utils.source_target_parser import (
            generate_reportportal_environment_variables,
        )
        from enge.utils.globals import TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX

        ctx = make_app_context(
            action="test",
            extra_config={
                "reportportal": {
                    "url": "https://rp.example.com",
                    "token": "fake-token",
                    "project": "test-project",
                    "launch": "CONFIG-NAME",
                },
            },
        )
        env_vars = generate_reportportal_environment_variables(
            ctx.config,
            cli_args=ctx.cli_args,
            set_name="smoke-tests",
            architecture="x86_64",
            tier="tier0",
            event="preliminary",
        )
        launch_key = f"{TMT_PLUGIN_REPORT_REPORTPORTAL_PREFIX}LAUNCH"
        self.assertEqual(env_vars[launch_key], "CONFIG-NAME")


class TestReportPortalLaunchFallbackName(unittest.TestCase):
    """ReportPortalLaunch.generate_launch_name must use the same grammar."""

    def test_generate_launch_name_grammar(self):
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)

        name = rp.generate_launch_name(
            context={
                "event": "preliminary",
                "set_name": "smoke-tests",
                "tier": "tier0",
                "architecture": "x86_64",
                "upgrade_path": "9to10",
            }
        )
        self.assertEqual(name, "9to10~tier0~x86_64")

    def test_generate_launch_name_no_date(self):
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)

        name = rp.generate_launch_name(
            context={
                "tier": "tier0",
                "architecture": "x86_64",
                "upgrade_path": "8to9",
            }
        )
        self.assertNotRegex(name, r"\d{4}-\d{2}-\d{2}")

    def test_no_context_fallback(self):
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        name = rp.generate_launch_name()
        self.assertEqual(name, "ENGE_Launch")
