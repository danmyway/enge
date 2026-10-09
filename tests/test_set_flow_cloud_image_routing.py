#!/usr/bin/env python3
"""Characterization tests for cloud image compose routing in set_flow.

``_resolve_artifacts`` picks ``submit_test.compose`` from one of three
sources, keyed on flags the source parser sets on the source spec.  The
parser tests pin the flag and the format tests pin the formatter, but
nothing pinned the router that joins them: renaming the key it reads left
the whole suite green.  These tests drive the real parser into the real
router, with a resolver stub whose build compose is a sentinel so a wrong
branch is visible in ``submit_test.compose``.
"""

import unittest
from unittest.mock import MagicMock

from enge.dispatch.set_flow import RequestSpec, _resolve_artifacts
from enge.utils.source_target_parser import parse_source_target_config

SENTINEL = "SENTINEL-BUILD-COMPOSE"

CONFIG = {
    "testing_farm": {"composes_prod_url": ""},
    "sources": {
        "images": {
            "alma97": "AlmaLinux OS 9.7.20251118",
            "rocky97": "Rocky-9-EC2-Base-9.7-20251123.2",
            "oracle9": "Oracle:Oracle-Linux:ol98-lvm-gen2:9.8.2",
        }
    },
}


class TestCloudImageComposeRouting(unittest.TestCase):
    def _route(self, source, arch):
        """Parse *source*, run ``_resolve_artifacts``, return the pieces."""
        source_spec, target_spec = parse_source_target_config(source, None, CONFIG)

        ctx = MagicMock()
        ctx.cli_args.copr = None
        ctx.cli_args.brew = None
        ctx.copr_reference = None
        ctx.copr_references = []
        ctx.copr_api = {}
        ctx.brew_reference = None
        ctx.brew_references = []
        ctx.brew_api = {}
        ctx.project = {"name": "leapp"}

        spec = RequestSpec(
            set_name=None,
            tier="tier0",
            plan=None,
            arch=arch,
            source_spec=source_spec,
            target_spec=target_spec,
            upgrade_path="routing-test",
            effective_values={"brew_api": {}, "copr_api": {}},
        )

        class StubResolver:
            def resolve_builds(self, compose_name, ctx):
                return [
                    {
                        "compose": SENTINEL,
                        "distro": "stub-distro",
                        "build_id": None,
                        "packages": [],
                    }
                ]

        submit = MagicMock()
        submit.artifacts = []
        result = _resolve_artifacts(spec, submit, {}, ctx, "compose", StubResolver())
        return source_spec, submit, result

    def test_alma_alias_routes_to_cloud_image_compose_name(self):
        source_spec, submit, result = self._route("alma97", "x86_64")

        self.assertTrue(source_spec["is_cloud_image_source"])
        self.assertIsNone(result)
        self.assertEqual(submit.compose, "AlmaLinux OS 9.7.20251118 x86_64")

    def test_rocky_alias_routes_to_cloud_image_compose_name(self):
        source_spec, submit, result = self._route("rocky97", "aarch64")

        self.assertTrue(source_spec["is_cloud_image_source"])
        self.assertIsNone(result)
        self.assertEqual(submit.compose, "Rocky-9-EC2-Base-9.7-20251123.2.aarch64")

    def test_oracle_alias_routes_to_urn_unchanged(self):
        source_spec, submit, result = self._route("oracle9", "x86_64")

        self.assertTrue(source_spec["is_cloud_image_source"])
        self.assertIsNone(result)
        self.assertEqual(submit.compose, "Oracle:Oracle-Linux:ol98-lvm-gen2:9.8.2")

    def test_rhel_source_routes_to_build_compose(self):
        source_spec, submit, result = self._route("9.7", "x86_64")

        self.assertFalse(source_spec["is_cloud_image_source"])
        self.assertNotEqual(source_spec["compose_name"], SENTINEL)
        self.assertIsNone(result)
        self.assertEqual(submit.compose, SENTINEL)


if __name__ == "__main__":
    unittest.main()
