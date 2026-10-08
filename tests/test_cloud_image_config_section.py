#!/usr/bin/env python3
"""[sources.images] cloud-image aliases and the deprecated [sources.ami] fallback.

Rulings Q-RN-1..5 (2026-10-08): the alias section is [sources.images];
[sources.ami] is still read, merged per alias underneath it ([sources.images]
wins a conflicting alias), and WARNs once per invocation -- at config load --
only when it holds at least one alias.
"""

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._helpers import captured_logs, matching

# Alias shapes copied from tests/test_source_target_parser.py.
ALMA97_BASE = "AlmaLinux OS 9.7.20251118"
ALMA96_BASE = "AlmaLinux OS 9.6.20250313"
ROCKY97_BASE = "Rocky-9-EC2-Base-9.7-20251123.2"

DEPRECATION_NEEDLE = "[sources.ami] is deprecated"
DEPRECATION_TEXT = (
    "Config section [sources.ami] is deprecated; move its aliases to "
    "[sources.images] (aliases in [sources.images] take precedence)."
)


def _parser_config(**sections):
    """A parser config; keyword args become [sources.<name>] tables."""
    config = {"testing_farm": {"composes_prod_url": ""}}
    if sections:
        config["sources"] = dict(sections)
    return config


def _parse(spec, config):
    from enge.utils.source_target_parser import parse_compose_spec

    return parse_compose_spec(spec, config)


class TestCloudImageAliasResolution(unittest.TestCase):
    """The parser resolves aliases from [sources.images] over [sources.ami]."""

    def test_images_section_alone_resolves_aliases(self):
        # Premise: the alias value is itself a valid cloud-image name.
        self.assertEqual(_parse(ALMA97_BASE, _parser_config())["os_type"], "alma")

        spec = _parse("alma97", _parser_config(images={"alma97": ALMA97_BASE}))
        self.assertEqual(spec["compose_name"], ALMA97_BASE)
        self.assertTrue(spec["is_cloud_image_source"])

    def test_ami_section_alone_still_resolves(self):
        spec = _parse("alma97", _parser_config(ami={"alma97": ALMA97_BASE}))
        self.assertEqual(spec["compose_name"], ALMA97_BASE)
        self.assertTrue(spec["is_cloud_image_source"])

    def test_both_sections_merge_per_alias(self):
        config = _parser_config(
            ami={"alma97": ALMA97_BASE}, images={"rocky97": ROCKY97_BASE}
        )
        # Premise: the ami-only alias resolves from this config.
        self.assertEqual(_parse("alma97", config)["compose_name"], ALMA97_BASE)

        self.assertEqual(_parse("rocky97", config)["compose_name"], ROCKY97_BASE)

    def test_conflicting_alias_images_wins(self):
        # Premise: the two candidate values are distinct, valid names.
        self.assertEqual(_parse(ALMA96_BASE, _parser_config())["minor"], 6)
        self.assertEqual(_parse(ALMA97_BASE, _parser_config())["minor"], 7)

        config = _parser_config(
            ami={"alma97": ALMA96_BASE}, images={"alma97": ALMA97_BASE}
        )
        spec = _parse("alma97", config)
        self.assertEqual(spec["compose_name"], ALMA97_BASE)
        self.assertEqual(spec["minor"], 7)

    def test_neither_section_unchanged(self):
        spec = _parse(f"{ALMA97_BASE} x86_64", _parser_config())
        self.assertEqual(spec["compose_name"], ALMA97_BASE)
        self.assertEqual(spec["os_type"], "alma")
        self.assertTrue(spec["is_cloud_image_source"])

    def test_non_table_section_reaches_parser_unchanged(self):
        """No new type validation: a lone non-table section behaves as the
        old [sources.ami] reader did with it -- a direct name still parses."""
        for section in ("ami", "images"):
            with self.subTest(section=section):
                spec = _parse(ALMA97_BASE, _parser_config(**{section: "x"}))
                self.assertEqual(spec["compose_name"], ALMA97_BASE)


class TestDeprecatedAmiWarning(unittest.TestCase):
    """The real config loader WARNs about a populated [sources.ami], once."""

    def _load(self, user_toml):
        """Run load_config on one user file over the real bundled defaults,
        with no system or home-directory layers. Returns (config, records)."""
        from enge.utils.config_parser import load_config

        with tempfile.TemporaryDirectory() as td:
            user = Path(td) / "user.toml"
            if user_toml is not None:
                user.write_text(user_toml, encoding="utf-8")
            with (
                patch("enge.utils.config_parser.SYSTEM_CONFIG_PATHS", new=()),
                patch("enge.utils.config_parser.USER_CONFIG_PATHS", new=()),
                captured_logs("enge") as records,
            ):
                config = load_config(paths=[str(user)])
        return config, records

    def test_populated_ami_warns_once(self):
        config, records = self._load(f"[sources.ami]\nalma97 = '{ALMA97_BASE}'\n")
        # Premise: the user alias reached the merged config.
        self.assertEqual(config["sources"]["ami"], {"alma97": ALMA97_BASE})

        self.assertEqual(
            matching(records, DEPRECATION_NEEDLE, level=logging.WARNING),
            [DEPRECATION_TEXT],
        )

    def test_empty_ami_table_is_silent(self):
        # Premise: a populated table in the same harness does warn.
        _, records = self._load(f"[sources.ami]\nalma97 = '{ALMA97_BASE}'\n")
        self.assertEqual(
            len(matching(records, DEPRECATION_NEEDLE, level=logging.WARNING)), 1
        )

        config, records = self._load("[sources.ami]\n")
        self.assertEqual(config["sources"]["ami"], {})
        self.assertEqual(matching(records, DEPRECATION_NEEDLE, level=None), [])

    def test_images_only_is_silent(self):
        config, records = self._load(f"[sources.images]\nalma97 = '{ALMA97_BASE}'\n")
        self.assertEqual(config["sources"]["images"], {"alma97": ALMA97_BASE})

        self.assertEqual(matching(records, DEPRECATION_NEEDLE, level=None), [])

    def test_warning_once_despite_repeated_resolution(self):
        from enge.utils.config_parser import load_config

        with tempfile.TemporaryDirectory() as td:
            user = Path(td) / "user.toml"
            user.write_text(
                f"[sources.ami]\nalma97 = '{ALMA97_BASE}'\n", encoding="utf-8"
            )
            with (
                patch("enge.utils.config_parser.SYSTEM_CONFIG_PATHS", new=()),
                patch("enge.utils.config_parser.USER_CONFIG_PATHS", new=()),
                captured_logs("enge") as records,
            ):
                config = load_config(paths=[str(user)])
                for _ in range(3):
                    spec = _parse("alma97", config)
                    self.assertEqual(spec["compose_name"], ALMA97_BASE)

        self.assertEqual(
            len(matching(records, DEPRECATION_NEEDLE, level=logging.WARNING)), 1
        )

    def test_default_config_has_no_populated_ami(self):
        # No user file: load_config returns the real bundled defaults alone.
        config, records = self._load(None)
        # Premise: the bundled file was loaded and carries a [sources] table.
        self.assertIsInstance(config["sources"], dict)

        self.assertEqual(matching(records, DEPRECATION_NEEDLE, level=None), [])


if __name__ == "__main__":
    unittest.main()
