"""Tests for structured RP launch attributes.

Red-first: these tests exercise the structured attribute stamping on RP
launch payloads before the implementation lands.
"""

import unittest
from unittest.mock import patch, MagicMock

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


def _attrs_dict(payload):
    """Convert payload attributes list to {key: value} dict."""
    return {a["key"]: a["value"] for a in payload.get("attributes", [])}


# --- contexts matching dispatch and rerun call patterns ---

DISPATCH_CONTEXT = {
    "event": "preliminary",
    "set_name": "smoke-tests",
    "tier": "tier0",
    "architecture": "x86_64",
    "source_release": "9.7",
    "target_release": "10.1",
    "source_compose": "RHEL-9.7.0-Nightly",
}

DISPATCH_TMT_CONTEXT = {
    "distro": "rhel-9.7",
    "target_distro": "rhel-10.1",
    "source_compose": "RHEL-9.7.0-Nightly",
    "upgrade_path": "9to10",
    "arch": "x86_64",
    "event": "preliminary",
    "tier": "tier0",
}

RERUN_CONTEXT = {
    "tier": "tier0",
    "architecture": "x86_64",
}

RERUN_TMT_CONTEXT = {
    "distro": "rhel-9.7",
    "target_distro": "rhel-10.1",
    "source_compose": "RHEL-9.7.0-Nightly",
    "upgrade_path": "9to10",
    "event": "preliminary",
    "tier": "tier0",
}


class TestDispatchLaunchAttributes(unittest.TestCase):
    """Structured attributes on dispatch-created launches."""

    def _payload(self, run_id="01JTEST0000000000000000000", **kwargs):
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        return rp.generate_launch_payload(
            name="TEST~2026-01-01~tier0~x86_64",
            context=DISPATCH_CONTEXT,
            tmt_context=DISPATCH_TMT_CONTEXT,
            run_id=run_id,
            **kwargs,
        )

    def test_schema_attributes_present(self):
        """Payload contains exactly the schema attributes with expected values."""
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["run_id"], "01JTEST0000000000000000000")
        self.assertEqual(attrs["set"], "smoke-tests")
        self.assertEqual(attrs["tool"], "enge")

    def test_source_attribute(self):
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["source"], "RHEL-9.7.0-Nightly")

    def test_target_attribute(self):
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["target"], "rhel-10.1")

    def test_tier_from_tmt_context_not_duplicated(self):
        """tier already in tmt_context → dedup means one entry, not two."""
        p = self._payload()
        tier_entries = [a for a in p["attributes"] if a["key"] == "tier"]
        self.assertEqual(len(tier_entries), 1)
        self.assertEqual(tier_entries[0]["value"], "tier0")

    def test_arch_from_tmt_context_not_duplicated(self):
        """arch already in tmt_context → dedup means one entry, not two."""
        p = self._payload()
        arch_entries = [a for a in p["attributes"] if a["key"] == "arch"]
        self.assertEqual(len(arch_entries), 1)
        self.assertEqual(arch_entries[0]["value"], "x86_64")

    def test_event_from_tmt_context_not_duplicated(self):
        """event already in tmt_context → dedup means one entry, not two."""
        p = self._payload()
        event_entries = [a for a in p["attributes"] if a["key"] == "event"]
        self.assertEqual(len(event_entries), 1)
        self.assertEqual(event_entries[0]["value"], "preliminary")

    def test_parent_run_id_absent_on_dispatch(self):
        """Dispatch launches must NOT carry parent_run_id."""
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertNotIn("parent_run_id", attrs)

    def test_mutation_check_drop_run_id(self):
        """Mutation check: omitting run_id removes the attribute."""
        p = self._payload(run_id=None)
        attrs = _attrs_dict(p)
        self.assertNotIn("run_id", attrs)

    def test_tags_unchanged(self):
        """Flat tags list is unchanged by attribute stamping."""
        p = self._payload()
        self.assertIn("enge", p["tags"])
        self.assertIn("automated", p["tags"])
        self.assertEqual(len(p["tags"]), 2)


class TestRerunLaunchAttributes(unittest.TestCase):
    """Structured attributes on rerun-created launches."""

    def _payload(self, run_id="01JRERUN000000000000000000", parent_run_id=None, **kw):
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        return rp.generate_launch_payload(
            name="RERUN~PRELIMINARY~2026-01-01~tier0~x86_64",
            context=RERUN_CONTEXT,
            tmt_context=RERUN_TMT_CONTEXT,
            extra_tags=["rerun"],
            run_id=run_id,
            parent_run_id=parent_run_id,
            **kw,
        )

    def test_parent_run_id_present_on_rerun(self):
        """Rerun launches carry parent_run_id when provided."""
        p = self._payload(parent_run_id="01JPARENT00000000000000000")
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["parent_run_id"], "01JPARENT00000000000000000")

    def test_parent_run_id_absent_when_not_provided(self):
        """Mutation check: no parent_run_id → no attribute."""
        p = self._payload(parent_run_id=None)
        attrs = _attrs_dict(p)
        self.assertNotIn("parent_run_id", attrs)

    def test_rerun_tag_in_tags(self):
        """extra_tags 'rerun' still appears in the flat tags list."""
        p = self._payload()
        self.assertIn("rerun", p["tags"])
        self.assertIn("enge", p["tags"])
        self.assertIn("automated", p["tags"])

    def test_run_id_is_child_not_parent(self):
        """run_id is the rerun's own ID, not the parent."""
        p = self._payload(
            run_id="01JCHILD0000000000000000000",
            parent_run_id="01JPARENT00000000000000000",
        )
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["run_id"], "01JCHILD0000000000000000000")
        self.assertEqual(attrs["parent_run_id"], "01JPARENT00000000000000000")

    def test_source_from_tmt_context_fallback(self):
        """When context lacks source_compose, falls back to tmt_context."""
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["source"], "RHEL-9.7.0-Nightly")

    def test_target_from_tmt_context_fallback(self):
        """When context lacks target_release, falls back to tmt_context target_distro."""
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["target"], "rhel-10.1")

    def test_tool_attribute(self):
        p = self._payload()
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["tool"], "enge")


class TestAttributeOmission(unittest.TestCase):
    """Attributes with unavailable values are omitted, never empty."""

    def _payload(self, context=None, tmt_context=None, **kw):
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        return rp.generate_launch_payload(
            name="TEST~2026-01-01~unknown~x86_64",
            context=context,
            tmt_context=tmt_context,
            run_id="01JTEST0000000000000000000",
            **kw,
        )

    def test_missing_event_omitted(self):
        """No event in context or tmt_context → no event attribute."""
        context = {"tier": "tier0", "architecture": "x86_64"}
        tmt = {"tier": "tier0", "arch": "x86_64"}
        p = self._payload(context=context, tmt_context=tmt)
        attrs = _attrs_dict(p)
        self.assertNotIn("event", attrs)

    def test_no_empty_string_values(self):
        """No attribute ever has an empty-string value."""
        context = {"set_name": "", "event": None, "architecture": "x86_64"}
        tmt = {"arch": "x86_64"}
        p = self._payload(context=context, tmt_context=tmt)
        for attr in p.get("attributes", []):
            self.assertTrue(
                attr["value"],
                f"Attribute {attr['key']!r} has empty value {attr['value']!r}",
            )

    def test_tool_always_present(self):
        """tool:enge is present even with minimal context."""
        p = self._payload(context=None, tmt_context=None)
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["tool"], "enge")


class TestAttributeDedup(unittest.TestCase):
    """Pre-existing attributes from tmt_context are not overwritten."""

    def test_preexisting_arch_survives(self):
        """A pre-existing arch in tmt_context must not be duplicated or overwritten."""
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        tmt = {"arch": "s390x"}
        context = {"architecture": "x86_64"}  # different value
        p = rp.generate_launch_payload(
            name="TEST",
            context=context,
            tmt_context=tmt,
            run_id="01JTEST0000000000000000000",
        )
        arch_entries = [a for a in p["attributes"] if a["key"] == "arch"]
        self.assertEqual(len(arch_entries), 1)
        self.assertEqual(arch_entries[0]["value"], "s390x")

    def test_preexisting_event_survives(self):
        """A pre-existing event in tmt_context wins over context."""
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        tmt = {"event": "original-event"}
        context = {"event": "context-event"}
        p = rp.generate_launch_payload(
            name="TEST",
            context=context,
            tmt_context=tmt,
            run_id="01JTEST0000000000000000000",
        )
        event_entries = [a for a in p["attributes"] if a["key"] == "event"]
        self.assertEqual(len(event_entries), 1)
        self.assertEqual(event_entries[0]["value"], "original-event")


class TestDryRunRunIdAbsent(unittest.TestCase):
    """Dry-run payloads must not carry run_id (no manifest exists)."""

    def test_dryrun_dispatch_no_run_id(self):
        """When run_id=None (dry-run), the run_id attribute is absent."""
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx(dryrun=True)
        rp = ReportPortalLaunch(ctx)
        p = rp.generate_launch_payload(
            name="DRYRUN~TEST",
            context=DISPATCH_CONTEXT,
            tmt_context=DISPATCH_TMT_CONTEXT,
            run_id=None,
        )
        attrs = _attrs_dict(p)
        self.assertNotIn("run_id", attrs)
        self.assertEqual(attrs["tool"], "enge")


class TestCallChainRunIdThreading(unittest.TestCase):
    """run_id reaches generate_launch_payload from the dispatch and rerun call sites."""

    @patch("enge.reportportal.__main__.ReportPortalLaunch")
    def test_dispatch_helper_threads_run_id(self, mock_rp_cls):
        """The reportportal_helper.create_launch facade forwards run_id."""
        from enge.utils.reportportal_helper import create_launch

        mock_instance = MagicMock()
        mock_instance.create_launch.return_value = "fake-uuid"
        mock_rp_cls.return_value = mock_instance

        ctx = _make_rp_ctx()
        create_launch(
            ctx=ctx,
            context=DISPATCH_CONTEXT,
            tmt_context=DISPATCH_TMT_CONTEXT,
            run_id="01JTHREAD000000000000000000",
        )

        mock_instance.create_launch.assert_called_once()
        call_kwargs = mock_instance.create_launch.call_args
        self.assertEqual(
            call_kwargs.kwargs.get("run_id"), "01JTHREAD000000000000000000"
        )

    @patch("enge.reportportal.__main__.ReportPortalLaunch")
    def test_dispatch_helper_dryrun_no_run_id(self, mock_rp_cls):
        """Dry-run path in helper: run_id not forwarded (None)."""
        from enge.utils.reportportal_helper import create_launch

        mock_instance = MagicMock()
        mock_instance.generate_launch_payload.return_value = {"name": "test"}
        mock_rp_cls.return_value = mock_instance

        ctx = _make_rp_ctx(dryrun=True)
        create_launch(
            ctx=ctx,
            context=DISPATCH_CONTEXT,
            tmt_context=DISPATCH_TMT_CONTEXT,
            run_id="01JTHREAD000000000000000000",
            dryrun=True,
        )

        mock_instance.generate_launch_payload.assert_called_once()
        call_kwargs = mock_instance.generate_launch_payload.call_args
        self.assertIsNone(call_kwargs.kwargs.get("run_id"))

    @patch("enge.reportportal.__main__.ReportPortalLaunch")
    def test_rerun_threads_run_id_and_parent(self, mock_rp_cls):
        """Rerun path forwards both run_id and parent_run_id."""
        from enge.rerun.__main__ import _create_rerun_launch_for_payload

        mock_instance = MagicMock()
        mock_instance.create_launch.return_value = "fake-rerun-uuid"
        mock_rp_cls.return_value = mock_instance

        ctx = _make_rp_ctx()
        payload = {
            "environments": [
                {
                    "arch": "x86_64",
                    "tmt": {"context": {"event": "preliminary", "tier": "tier0"}},
                }
            ],
        }
        _create_rerun_launch_for_payload(
            payload,
            False,
            ctx,
            run_id="01JCHILD0000000000000000000",
            parent_run_id="01JPARENT00000000000000000",
        )

        call_kwargs = mock_instance.create_launch.call_args
        self.assertEqual(
            call_kwargs.kwargs.get("run_id"), "01JCHILD0000000000000000000"
        )
        self.assertEqual(
            call_kwargs.kwargs.get("parent_run_id"), "01JPARENT00000000000000000"
        )


class TestTargetAttributeCompose(unittest.TestCase):
    """target attribute must carry the target compose name, not distro."""

    def test_dispatch_target_is_compose_name(self):
        """Dispatch with target_compose in context → target = compose name."""
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        context = {
            **DISPATCH_CONTEXT,
            "target_compose": "RHEL-10.1.0-Nightly",
        }
        p = rp.generate_launch_payload(
            name="TEST~2026-01-01~tier0~x86_64",
            context=context,
            tmt_context=DISPATCH_TMT_CONTEXT,
            run_id="01JTEST0000000000000000000",
        )
        attrs = _attrs_dict(p)
        self.assertEqual(attrs["target"], "RHEL-10.1.0-Nightly")

    def test_rerun_target_omitted(self):
        """Rerun path has no target compose → no target attribute at all."""
        from enge.reportportal.__main__ import ReportPortalLaunch

        ctx = _make_rp_ctx()
        rp = ReportPortalLaunch(ctx)
        p = rp.generate_launch_payload(
            name="RERUN~PRELIMINARY~2026-01-01~tier0~x86_64",
            context=RERUN_CONTEXT,
            tmt_context=RERUN_TMT_CONTEXT,
            extra_tags=["rerun"],
            run_id="01JRERUN000000000000000000",
            parent_run_id="01JPARENT00000000000000000",
        )
        attrs = _attrs_dict(p)
        self.assertNotIn("target", attrs)
