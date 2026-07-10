"""Tests for config preset resolution ([tests.preset.<name>]).

Resolution chain: CLI > set > preset > [tests] > bundled defaults.
Sets opt in via ``extends = "<preset_name>"`` in their set section.
"""

import copy
import unittest
from unittest.mock import MagicMock

from enge.utils.errors import ConfigurationError
from enge.utils.source_target_parser import resolve_effective_values


def _cli_args(**overrides):
    """Minimal cli_args mock with all keys resolve_effective_values reads."""
    attrs = dict(
        source=None,
        target=None,
        architectures=None,
        pool=None,
        git_ref=None,
        git_url=None,
        parallel_limit=None,
        tier=None,
        event=None,
        plan=None,
    )
    attrs.update(overrides)
    return MagicMock(**attrs)


BASE_CONFIG = {
    "tests": {
        "git_url": "https://git.example.com/tests",
        "git_ref": "main",
        "source": "RHEL-8.10.0",
        "target": "RHEL-9.4.0",
        "tiers": ["tier0"],
        "tier": {"tier0": "tag:tier[0]"},
    },
}


# ── helpers ──────────────────────────────────────────────────────────


def _resolve_with_preset(cli_args, set_config, config):
    """Call the preset-aware resolution path.

    Imports _resolve_preset from wherever it lands and composes it with
    resolve_effective_values, mirroring what build_test_attributes does.
    """
    from enge.utils.test_attribute_builder import _resolve_preset

    merged_set = _resolve_preset(set_config, config)
    return resolve_effective_values(cli_args, merged_set, config)


# ── (a) set inherits preset key absent from set ─────────────────────


class TestPresetInheritance(unittest.TestCase):
    def test_set_inherits_absent_key_from_preset(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {
                "source": "RHEL-8.8.0",
                "git_ref": "rhsm-branch",
            },
        }
        set_config = {
            "extends": "rhsm_base",
            "source": "RHEL-8.10.0",
            # git_ref absent → should inherit "rhsm-branch" from preset
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["git_ref"], "rhsm-branch")

    def test_set_inherits_tiers_from_preset(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "quick": {"tiers": ["tier0"]},
        }
        set_config = {
            "extends": "quick",
            # tiers absent → inherit from preset
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["tiers"], ["tier0"])


# ── (b) set key replaces preset key (top-level REPLACE, not merge) ──


class TestPresetReplace(unittest.TestCase):
    def test_set_scalar_replaces_preset_scalar(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-8.8.0"},
        }
        set_config = {
            "extends": "rhsm_base",
            "source": "RHEL-8.10.0",
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["source"], "RHEL-8.10.0")

    def test_set_nested_table_replaces_preset_not_merges(self):
        """A set's nested table wholly replaces the preset's — no deep merge."""
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {
                "environment": {
                    "VAR_A": "from_preset",
                    "VAR_B": "from_preset",
                },
            },
        }
        set_config = {
            "extends": "rhsm_base",
            "environment": {"VAR_A": "from_set"},
            # VAR_B must NOT appear — top-level REPLACE, not deep merge
        }
        from enge.utils.test_attribute_builder import _resolve_preset

        merged = _resolve_preset(set_config, config)
        self.assertEqual(merged["environment"], {"VAR_A": "from_set"})
        self.assertNotIn("VAR_B", merged.get("environment", {}))


# ── (c) preset key beats [tests] ────────────────────────────────────


class TestPresetBeatsTests(unittest.TestCase):
    def test_preset_source_beats_tests_section(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["source"] = "RHEL-8.10.0"  # [tests] level
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-8.8.0"},  # preset level
        }
        set_config = {
            "extends": "rhsm_base",
            # source absent → preset "RHEL-8.8.0" should win over [tests] "RHEL-8.10.0"
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["source"], "RHEL-8.8.0")


# ── (d) CLI beats all ───────────────────────────────────────────────


class TestCLIBeatsAll(unittest.TestCase):
    def test_cli_source_overrides_set_preset_and_tests(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["source"] = "RHEL-8.10.0"
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-8.8.0"},
        }
        set_config = {
            "extends": "rhsm_base",
            "source": "RHEL-8.9.0",
        }
        cli = _cli_args(source="RHEL-8.6.0")
        result = _resolve_with_preset(cli, set_config, config)
        self.assertEqual(result["source"], "RHEL-8.6.0")


# ── (e) set "" inherits preset value (inversion guard) ──────────────


class TestEmptyStringInversion(unittest.TestCase):
    def test_set_empty_string_inherits_preset_value(self):
        """A set's explicit "" must resolve to the preset's value, not fall
        through the or-chain to [tests]."""
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["source"] = "RHEL-from-tests"
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-from-preset"},
        }
        set_config = {
            "extends": "rhsm_base",
            "source": "",  # explicit unset → should get preset value
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["source"], "RHEL-from-preset")

    def test_set_empty_string_warning_fires(self):
        """WARNING must fire naming set key and preset."""
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-from-preset"},
        }
        set_config = {
            "extends": "rhsm_base",
            "source": "",
        }
        from enge.utils.test_attribute_builder import _resolve_preset

        with self.assertLogs(level="WARNING") as cm:
            _resolve_preset(set_config, config)

        warning_text = "\n".join(cm.output)
        self.assertIn("source", warning_text)
        self.assertIn("rhsm_base", warning_text)

    def test_set_none_inherits_preset_value(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-from-preset"},
        }
        set_config = {
            "extends": "rhsm_base",
            "source": None,
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["source"], "RHEL-from-preset")


# ── (f) preset "" yields nothing, resolution falls to [tests] ───────


class TestPresetEmptyString(unittest.TestCase):
    def test_preset_empty_string_falls_to_tests(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["source"] = "RHEL-from-tests"
        config["tests"]["preset"] = {
            "rhsm_base": {"source": ""},
        }
        set_config = {
            "extends": "rhsm_base",
            # source absent in set, preset has "" → should fall through to [tests]
        }
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["source"], "RHEL-from-tests")

    def test_preset_empty_string_warning_fires(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["source"] = "RHEL-from-tests"
        config["tests"]["preset"] = {
            "rhsm_base": {"source": ""},
        }
        set_config = {"extends": "rhsm_base"}

        from enge.utils.test_attribute_builder import _resolve_preset

        with self.assertLogs(level="WARNING") as cm:
            _resolve_preset(set_config, config)

        warning_text = "\n".join(cm.output)
        self.assertIn("source", warning_text)
        self.assertIn("rhsm_base", warning_text)


# ── (g) preset-extends-preset → exit 99 ─────────────────────────────


class TestPresetChainError(unittest.TestCase):
    def test_preset_with_extends_raises_configuration_error(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "base": {"source": "RHEL-8.8.0"},
            "chained": {
                "extends": "base",
                "target": "RHEL-9.4.0",
            },
        }
        set_config = {"extends": "chained"}

        from enge.utils.test_attribute_builder import _resolve_preset

        with self.assertRaises(ConfigurationError) as ctx:
            _resolve_preset(set_config, config)

        msg = str(ctx.exception)
        self.assertIn("chained", msg)
        self.assertIn("base", msg)


# ── (h) unknown preset → exit 99 ────────────────────────────────────


class TestUnknownPresetError(unittest.TestCase):
    def test_unknown_preset_raises_configuration_error(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {"source": "RHEL-8.8.0"},
        }
        set_config = {"extends": "nonexistent"}

        from enge.utils.test_attribute_builder import _resolve_preset

        with self.assertRaises(ConfigurationError) as ctx:
            _resolve_preset(set_config, config)

        msg = str(ctx.exception)
        self.assertIn("nonexistent", msg)
        self.assertIn("rhsm_base", msg)

    def test_unknown_preset_no_presets_defined(self):
        config = copy.deepcopy(BASE_CONFIG)
        # no [tests.preset] at all
        set_config = {"extends": "nonexistent"}

        from enge.utils.test_attribute_builder import _resolve_preset

        with self.assertRaises(ConfigurationError) as ctx:
            _resolve_preset(set_config, config)

        msg = str(ctx.exception)
        self.assertIn("nonexistent", msg)


# ── (i) set-only key via preset flows into effective values ──────────


class TestPresetKeyFlowsToEffective(unittest.TestCase):
    def test_environment_from_preset_flows_through(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {
                "environment": {"RHSM_SETUP": "1"},
            },
        }
        set_config = {"extends": "rhsm_base"}
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["environment"], {"RHSM_SETUP": "1"})

    def test_copr_api_from_preset(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "rhsm_base": {
                "copr_api": {"owner": "rhsm-team", "build_references": ["pkg-1"]},
            },
        }
        set_config = {"extends": "rhsm_base"}
        result = _resolve_with_preset(_cli_args(), set_config, config)
        self.assertEqual(result["copr_api"]["owner"], "rhsm-team")


# ── (j) characterization: preset-free config resolves identically ───


class TestPresetFreeCharacterization(unittest.TestCase):
    def test_no_preset_no_extends_resolves_identically(self):
        """A config with no presets and no extends must produce byte-identical
        resolved dicts to the pre-preset code path."""
        config = copy.deepcopy(BASE_CONFIG)
        set_config = {"source": "RHEL-8.10.0", "git_ref": "main"}
        cli = _cli_args()

        baseline = resolve_effective_values(cli, set_config, config)
        via_preset = _resolve_with_preset(cli, set_config, config)

        self.assertEqual(baseline, via_preset)

    def test_empty_preset_section_resolves_identically(self):
        """A config with an empty [tests.preset] section behaves identically."""
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {}
        set_config = {"source": "RHEL-8.10.0", "git_ref": "main"}
        cli = _cli_args()

        baseline = resolve_effective_values(cli, set_config, config)
        via_preset = _resolve_with_preset(cli, set_config, config)

        self.assertEqual(baseline, via_preset)


# ── validation: _validate_test_set_structure accepts extends ─────────


class TestExtendsInValidKeys(unittest.TestCase):
    def test_extends_is_not_flagged_as_unknown(self):
        """After implementation, extends must be in valid_keys so it does
        not trigger the unknown-key warning."""
        from enge.utils.opt_manager import ParsedOpts
        from enge.utils.arg_parser import get_arguments

        po = object.__new__(ParsedOpts)
        po._validation_hooks = {}
        po.config = copy.deepcopy(BASE_CONFIG)
        po.config["tests"]["set"] = {
            "myset": {
                "extends": "rhsm_base",
                "source": "RHEL-8.10.0",
                "git_ref": "main",
                "tiers": ["tier0"],
            },
        }
        po.cli_args = get_arguments(args=["test"])

        import logging

        with self.assertLogs(level="WARNING") as cm:
            logging.getLogger().warning("sentinel")
            po._validate_test_set_structure("myset", po.config["tests"]["set"]["myset"])

        warnings = [m for m in cm.output if "unknown keys" in m.lower()]
        self.assertEqual(warnings, [], f"extends flagged as unknown: {warnings}")


# ── validation: preset chain/unknown wired into opt_manager ──────────


class TestPresetValidationInOptManager(unittest.TestCase):
    def _make_po(self, config):
        from enge.utils.opt_manager import ParsedOpts
        from enge.utils.arg_parser import get_arguments

        po = object.__new__(ParsedOpts)
        po._validation_hooks = {}
        po.config = config
        po.cli_args = get_arguments(args=["test", "-S", "myset"])
        po.options = po._get_config_options()
        return po

    def test_preset_chain_detected_in_validation(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["set"] = {
            "myset": {"extends": "chained", "git_ref": "main", "tiers": ["tier0"]},
        }
        config["tests"]["preset"] = {
            "chained": {"extends": "base", "source": "x"},
            "base": {"source": "y"},
        }
        po = self._make_po(config)
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_option_dependencies()
        msg = str(ctx.exception)
        self.assertIn("chained", msg)

    def test_unknown_preset_detected_in_validation(self):
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["set"] = {
            "myset": {"extends": "nope", "git_ref": "main", "tiers": ["tier0"]},
        }
        config["tests"]["preset"] = {"real": {"source": "x"}}
        po = self._make_po(config)
        with self.assertRaises(ConfigurationError) as ctx:
            po._validate_option_dependencies()
        msg = str(ctx.exception)
        self.assertIn("nope", msg)
        self.assertIn("real", msg)


# ── validation: preset structure validated with same policy as sets ──


class TestPresetStructureValidation(unittest.TestCase):
    def test_unknown_preset_key_warns(self):
        """Unknown keys in a preset produce a warning, same as sets."""
        config = copy.deepcopy(BASE_CONFIG)
        config["tests"]["preset"] = {
            "bad": {"source": "x", "bogus_key": "y"},
        }
        config["tests"]["set"] = {
            "myset": {"extends": "bad", "git_ref": "main", "tiers": ["tier0"]},
        }

        from enge.utils.opt_manager import ParsedOpts
        from enge.utils.arg_parser import get_arguments

        po = object.__new__(ParsedOpts)
        po._validation_hooks = {}
        po.config = config
        po.cli_args = get_arguments(args=["test", "-S", "myset"])
        po.options = po._get_config_options()

        with self.assertLogs(level="WARNING") as cm:
            po._validate_option_dependencies()

        warnings = [m for m in cm.output if "bogus_key" in m]
        self.assertTrue(
            warnings, "Expected warning about unknown preset key 'bogus_key'"
        )


if __name__ == "__main__":
    unittest.main()
