import unittest
from unittest.mock import MagicMock

from enge.dispatch.context import RequestContext
from enge.dispatch.set_flow import RequestSpec


class TestRequestContext(unittest.TestCase):
    def _make_spec(self, **overrides):
        defaults = dict(
            set_name="test-set",
            tier="tier0",
            plan=None,
            arch="x86_64",
            source_spec={"major": 8, "minor": 10, "compose_name": "RHEL-8.10.0"},
            target_spec={"major": 9, "minor": 4, "compose_name": "RHEL-9.4.0-Nightly"},
            upgrade_path="8to9",
            effective_values={"tiers": ["tier0"], "architectures": ["x86_64"]},
        )
        defaults.update(overrides)
        return RequestSpec(**defaults)

    def _make_ctx(self, spec=None, **overrides):
        defaults = dict(
            spec=spec or self._make_spec(),
            config={"testing_farm": {"api_key": "k"}},
            cli_args=MagicMock(),
            api_key="k",
            event=None,
            auto_env_vars={"SOURCE_RELEASE": "8.10"},
            set_env_vars={},
            cli_env_vars={},
            set_reportportal_config={},
            shared_archive_filename="archive",
            artifact_type="compose",
        )
        defaults.update(overrides)
        return RequestContext(**defaults)

    def test_construction(self):
        ctx = self._make_ctx()
        self.assertEqual(ctx.spec.set_name, "test-set")
        self.assertEqual(ctx.api_key, "k")

    def test_derived_properties(self):
        ctx = self._make_ctx()
        self.assertEqual(ctx.set_name, "test-set")
        self.assertEqual(ctx.tier, "tier0")
        self.assertEqual(ctx.arch, "x86_64")
        self.assertEqual(ctx.upgrade_path, "8to9")
        self.assertEqual(ctx.source_release, "8.10")
        self.assertEqual(ctx.target_release, "9.4")
        self.assertEqual(ctx.source_compose, "RHEL-8.10.0")
        self.assertEqual(ctx.target_compose, "RHEL-9.4.0-Nightly")

    def test_effective_values_delegate(self):
        ctx = self._make_ctx()
        self.assertEqual(
            ctx.effective_values, {"tiers": ["tier0"], "architectures": ["x86_64"]}
        )

    def test_source_target_spec_delegate(self):
        ctx = self._make_ctx()
        self.assertEqual(ctx.source_spec["major"], 8)
        self.assertEqual(ctx.target_spec["major"], 9)
