#!/usr/bin/env python3
"""Tests for Oracle Linux Azure image sources and the provisioning-pool requirement."""

import copy
import unittest

from enge.utils.arg_parser import get_arguments
from enge.utils.errors import ValidationError
from enge.utils.source_target_parser import (
    format_cloud_image_compose_name,
    generate_environment_variables,
    generate_tmt_context,
    normalize_tmt_compose_context,
    parse_compose_spec,
    validate_cloud_image_architectures,
)

ORACLE_URN = "Oracle:Oracle-Linux:ol98-lvm-gen2:9.8.2"
ORACLE_ARM_URN = "Oracle:Oracle-Linux:ol98-arm64-lvm-gen2:9.8.2"
ORACLE_URN_NO_GEN2 = "Oracle:Oracle-Linux:ol810-lvm:8.10.1"
ALMA_BASE = "AlmaLinux OS 9.7.20251118"

PARSER_CONFIG = {
    "testing_farm": {"composes_prod_url": ""},
    "sources": {"images": {"oracle9": ORACLE_URN, "alma97": ALMA_BASE}},
}

# Modelled on tests/test_opt_manager.py's MINIMAL_CONFIG, with an empty
# composes_prod_url so target derivation stays offline (RHEL-<x.y>.0-Nightly).
BUILDER_CONFIG = {
    "testing_farm": {
        "api_key": "test-tf-key",
        "cloud_resources_tag": "test-biz-tag",
        "api_endpoint_url": "https://api.example.tf",
        "log_artifact_baseurl": "https://logs.example.tf",
        "composes_prod_url": "",
    },
    "project": {
        "name": "test-project",
        "repo_url": "https://github.com/oamg/test",
    },
    "tests": {
        "git_url": "https://github.com/oamg/tests",
        "git_ref": "main",
        "tier": {
            "tier0": "tag:tier[0]",
            "tier1": "tag:tier[01]",
        },
    },
    "copr_api": {
        "owner": "",
        "owner_is_group": True,
        "repository": "",
        "package": "",
        "build_references": [],
    },
    "brew_api": {"session_url": "", "taskid_url": ""},
    "reportportal": {"url": "", "project": ""},
    "sources": {"images": {"oracle9": ORACLE_URN}},
}


def _builder_config():
    """Return a fresh builder config so a test may mutate it freely."""
    return copy.deepcopy(BUILDER_CONFIG)


def _two_set_config():
    """Builder config with an Alma set 's1' and an Oracle set 's2', targets explicit."""
    cfg = _builder_config()
    cfg["tests"]["set"] = {
        "s1": {
            "source": ALMA_BASE,
            "target": "10.2",
            "architectures": ["x86_64"],
            "tiers": ["tier0"],
        },
        "s2": {
            "source": "oracle9",
            "target": "10.2",
            "architectures": ["x86_64"],
            "tiers": ["tier0"],
        },
    }
    return cfg


def _oracle_spec():
    """Parse the direct Oracle URN into a source spec."""
    return parse_compose_spec(ORACLE_URN, PARSER_CONFIG)


class TestOracleSourceParsing(unittest.TestCase):
    """Oracle Linux Azure image URNs parse as an AMI-family source."""

    def test_o1_oracle_alias_parsed_correctly(self):
        spec = parse_compose_spec("oracle9", PARSER_CONFIG)
        self.assertEqual(spec["os_type"], "oracle")
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 8)
        self.assertTrue(spec["is_cloud_image_source"])
        self.assertEqual(spec["compose_name"], ORACLE_URN)

    def test_o2_oracle_direct_urn_parsed_correctly(self):
        spec = parse_compose_spec(ORACLE_URN, PARSER_CONFIG)
        self.assertEqual(spec["os_type"], "oracle")
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 8)
        self.assertTrue(spec["is_cloud_image_source"])
        self.assertEqual(spec["compose_name"], ORACLE_URN)

    def test_o3_oracle_without_gen2_and_two_digit_minor(self):
        # Version comes from the trailing image version (8.10.1), not the SKU
        # digits (ol810), which are ambiguous for two-digit minors.
        spec = parse_compose_spec(ORACLE_URN_NO_GEN2, PARSER_CONFIG)
        self.assertEqual(spec["os_type"], "oracle")
        self.assertEqual(spec["major"], 8)
        self.assertEqual(spec["minor"], 10)

    def test_o4_oracle_arm64_urn_rejected(self):
        cfg = {
            "testing_farm": {"composes_prod_url": ""},
            "sources": {"images": {"oracle9": ORACLE_URN, "oraclearm": ORACLE_ARM_URN}},
        }
        # Premise: the x86_64 URN in this very config does parse as oracle.
        premise = parse_compose_spec("oracle9", cfg)
        self.assertEqual(premise["os_type"], "oracle")

        # Testing Farm accepts the arm64- variant; enge deliberately does not.
        with self.assertRaises(ValueError) as cm:
            parse_compose_spec("oraclearm", cfg)
        self.assertIn("Oracle Linux", str(cm.exception))


class TestOracleComposeNameAndArchitectures(unittest.TestCase):
    """The Oracle URN is sent unchanged and is x86_64 only."""

    def test_o5_oracle_compose_name_has_no_arch_suffix(self):
        spec = _oracle_spec()
        self.assertEqual(format_cloud_image_compose_name(spec, "x86_64"), ORACLE_URN)

    def test_o6_oracle_rejects_aarch64(self):
        spec = _oracle_spec()
        # Premise: x86_64 is accepted.
        validate_cloud_image_architectures(spec, ["x86_64"])

        with self.assertRaises(ValidationError) as cm:
            validate_cloud_image_architectures(spec, ["aarch64"])
        message = str(cm.exception)
        self.assertIn("aarch64", message)
        self.assertIn("Oracle Linux", message)

    def test_o7_alma_still_supports_aarch64_and_suffix(self):
        spec = parse_compose_spec("alma97", PARSER_CONFIG)
        validate_cloud_image_architectures(spec, ["aarch64"])
        self.assertEqual(
            format_cloud_image_compose_name(spec, "aarch64"),
            "AlmaLinux OS 9.7.20251118 aarch64",
        )


class TestOracleContextAndEnvironment(unittest.TestCase):
    """Oracle sources feed tmt context and environment variables."""

    def test_o8_tmt_context_distro_and_source_compose(self):
        source_spec = _oracle_spec()
        target_spec = parse_compose_spec("10.2", PARSER_CONFIG)
        context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(context["distro"], "oracle-9.8")
        self.assertEqual(
            normalize_tmt_compose_context(context)["source_compose"], ORACLE_URN
        )

    def test_o9_environment_variables_source_release(self):
        source_spec = _oracle_spec()
        target_spec = parse_compose_spec("10.2", PARSER_CONFIG)
        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertEqual(env_vars["SOURCE_RELEASE"], "9.8")
        self.assertNotIn("TARGET_OS", env_vars)


class TestValidateSourcePool(unittest.TestCase):
    """Oracle sources require an explicitly configured provisioning pool."""

    def test_p1_missing_pool_raises_and_names_every_knob(self):
        from enge.utils.source_target_parser import validate_source_pool

        with self.assertRaises(ValidationError) as cm:
            validate_source_pool(_oracle_spec(), None)
        message = str(cm.exception)
        self.assertIn("--pool", message)
        self.assertIn("[tests].pool", message)
        # enge supplies no default pool and must never name one.
        self.assertNotIn("azure", message.lower())

    def test_p2_empty_pool_raises(self):
        from enge.utils.source_target_parser import validate_source_pool

        with self.assertRaises(ValidationError):
            validate_source_pool(_oracle_spec(), "")

    def test_p3_configured_pool_accepted(self):
        from enge.utils.source_target_parser import validate_source_pool

        spec = _oracle_spec()
        # Premise: the same spec without a pool is rejected.
        with self.assertRaises(ValidationError):
            validate_source_pool(spec, None)

        validate_source_pool(spec, "some-pool")

    def test_p4_non_oracle_sources_need_no_pool(self):
        from enge.utils.source_target_parser import validate_source_pool

        validate_source_pool(parse_compose_spec("alma97", PARSER_CONFIG), None)
        validate_source_pool(parse_compose_spec("CentOS-Stream-9", PARSER_CONFIG), None)

    def test_p5_message_names_the_test_set(self):
        from enge.utils.source_target_parser import validate_source_pool

        with self.assertRaises(ValidationError) as cm:
            validate_source_pool(_oracle_spec(), None, set_name="ol")
        self.assertIn("test set 'ol'", str(cm.exception))


class TestOraclePoolRequirementInBuilder(unittest.TestCase):
    """build_test_attributes enforces the pool requirement before dispatch."""

    def _build(self, args, cfg):
        from enge.utils.test_attribute_builder import build_test_attributes

        return build_test_attributes(get_arguments(args=args), cfg)

    def test_b1_no_set_without_pool_raises(self):
        with self.assertRaises(ValidationError) as cm:
            self._build(
                [
                    "test",
                    "-s",
                    ORACLE_URN,
                    "-t",
                    "10.2",
                    "-T",
                    "tier0",
                    "--arch",
                    "x86_64",
                ],
                _builder_config(),
            )
        self.assertIn("--pool", str(cm.exception))

    def test_b2_no_set_with_pool_succeeds(self):
        result = self._build(
            [
                "test",
                "-s",
                ORACLE_URN,
                "-t",
                "10.2",
                "-T",
                "tier0",
                "--arch",
                "x86_64",
                "--pool",
                "p",
            ],
            _builder_config(),
        )
        self.assertEqual(result["pool"], "p")
        self.assertEqual(result["source_spec"]["os_type"], "oracle")

    def test_b3_oracle_set_without_pool_names_the_set(self):
        with self.assertRaises(ValidationError) as cm:
            self._build(["test", "-S", "s1", "-S", "s2"], _two_set_config())
        self.assertIn("test set 's2'", str(cm.exception))

    def test_b4_set_level_pool_satisfies_the_requirement(self):
        cfg = _two_set_config()
        cfg["tests"]["set"]["s2"]["pool"] = "p"
        result = self._build(["test", "-S", "s1", "-S", "s2"], cfg)
        self.assertEqual(
            result["individual_test_sets"][1]["effective_values"]["pool"], "p"
        )

    def test_b5_config_level_pool_satisfies_the_requirement(self):
        cfg = _two_set_config()
        cfg["tests"]["pool"] = "p"
        self._build(["test", "-S", "s1", "-S", "s2"], cfg)

    def test_b6_no_set_aarch64_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            self._build(
                [
                    "test",
                    "-s",
                    ORACLE_URN,
                    "-t",
                    "10.2",
                    "-T",
                    "tier0",
                    "--arch",
                    "aarch64",
                    "--pool",
                    "p",
                ],
                _builder_config(),
            )
        self.assertIn("aarch64", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
