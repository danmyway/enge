import unittest

from enge.utils.errors import ValidationError
from enge.utils.source_target_parser import (
    apply_centos_context_overrides,
    format_ami_compose_name,
    generate_environment_variables,
    generate_tmt_context,
    parse_compose_spec,
    parse_source_target_config,
    validate_ami_architectures,
)


class TestSourceTargetParser(unittest.TestCase):
    def setUp(self):
        self.minimal_config = {"testing_farm": {"composes_prod_url": ""}}

    def test_major_only_target_allowed_for_centos_stream_source(self):
        source_spec, target_spec = parse_source_target_config(
            "CentOS-Stream-9", "10", self.minimal_config
        )

        self.assertTrue(source_spec["is_centos_stream"])
        self.assertEqual(target_spec["major"], 10)
        self.assertEqual(target_spec["minor"], 0)
        self.assertEqual(target_spec["compose_name"], "RHEL-10.0.0-Nightly")

    def test_major_only_target_rejected_for_non_centos_source(self):
        with self.assertRaises(ValueError):
            parse_source_target_config("9.2", "10", self.minimal_config)

    def test_environment_variables_major_only_for_centos_source(self):
        source_spec = {
            "major": 9,
            "minor": 0,
            "compose_name": "CentOS-Stream-9",
            "is_version_only": False,
            "is_centos_stream": True,
            "is_major_only": False,
        }
        target_spec = {
            "major": 10,
            "minor": 0,
            "compose_name": "RHEL-10.0.0-Nightly",
            "is_version_only": True,
            "is_centos_stream": False,
            "is_major_only": True,
        }

        env_vars = generate_environment_variables(
            source_spec,
            target_spec,
        )

        self.assertEqual(env_vars["SOURCE_RELEASE"], "9")
        self.assertEqual(env_vars["TARGET_RELEASE"], "10")

    def test_centos_context_respects_target_os_override(self):
        source_spec, target_spec = parse_source_target_config(
            "CentOS-Stream-9", "10", self.minimal_config
        )
        base_context = generate_tmt_context(source_spec, target_spec)

        default_context = apply_centos_context_overrides(
            base_context, source_spec, target_spec, {}
        )
        self.assertEqual(default_context["distro"], "centos-9")
        self.assertEqual(default_context["target_distro"], "rhel-10")

        centos_context = apply_centos_context_overrides(
            base_context, source_spec, target_spec, {"TARGET_OS": "centos"}
        )
        self.assertEqual(centos_context["target_distro"], "centos-10")

    def test_explicit_minor_target_preserved_with_centos_source(self):
        """When target has explicit minor version (e.g., 10.1), it should be preserved."""
        source_spec, target_spec = parse_source_target_config(
            "cs9", "10.1", self.minimal_config
        )

        # Source should be CentOS Stream (major only)
        self.assertTrue(source_spec["is_centos_stream"])
        self.assertFalse(target_spec["is_major_only"])

        # Environment variables should preserve minor version for target
        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertEqual(env_vars["SOURCE_RELEASE"], "9")
        self.assertEqual(env_vars["TARGET_RELEASE"], "10.1")

        # TMT context should preserve minor version for target
        base_context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(base_context["distro"], "centos-9")
        self.assertEqual(base_context["target_distro"], "rhel-10.1")

        # apply_centos_context_overrides should also preserve minor version
        updated_context = apply_centos_context_overrides(
            base_context, source_spec, target_spec, {}
        )
        self.assertEqual(updated_context["distro"], "centos-9")
        self.assertEqual(updated_context["target_distro"], "rhel-10.1")

    def test_centos_stream_target_uses_centos_prefix(self):
        """When target is CentOS Stream (e.g., cs10), it should use centos prefix and auto-set TARGET_OS."""
        source_spec, target_spec = parse_source_target_config(
            "cs9", "cs10", self.minimal_config
        )

        # Both should be CentOS Stream
        self.assertTrue(source_spec["is_centos_stream"])
        self.assertTrue(target_spec["is_centos_stream"])
        self.assertEqual(target_spec["os_type"], "centos")

        # Environment variables should be major-only for both and include TARGET_OS
        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertEqual(env_vars["SOURCE_RELEASE"], "9")
        self.assertEqual(env_vars["TARGET_RELEASE"], "10")
        self.assertEqual(env_vars["TARGET_OS"], "centos")

        # TMT context should use centos prefix for both
        base_context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(base_context["distro"], "centos-9")
        self.assertEqual(base_context["target_distro"], "centos-10")

        # apply_centos_context_overrides should preserve centos prefix for target
        updated_context = apply_centos_context_overrides(
            base_context, source_spec, target_spec, env_vars
        )
        self.assertEqual(updated_context["distro"], "centos-9")
        self.assertEqual(updated_context["target_distro"], "centos-10")

    def test_rhel_target_does_not_set_target_os(self):
        """When target is RHEL, TARGET_OS should not be auto-set."""
        source_spec, target_spec = parse_source_target_config(
            "cs9", "10.1", self.minimal_config
        )

        self.assertEqual(target_spec["os_type"], "rhel")

        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertNotIn("TARGET_OS", env_vars)


class TestAMISourceParser(unittest.TestCase):
    """Tests for Alma Linux and Rocky Linux AMI source parsing."""

    def setUp(self):
        self.config_with_aliases = {
            "testing_farm": {"composes_prod_url": ""},
            "sources": {
                "ami": {
                    "alma97": "AlmaLinux OS 9.7.20251118",
                    "alma96": "AlmaLinux OS 9.6.20250313",
                    "rocky97": "Rocky-9-EC2-Base-9.7-20251123.2",
                    "rocky96": "Rocky-9-EC2-Base-9.6-20250310.0",
                }
            },
        }
        self.config_no_aliases = {"testing_farm": {"composes_prod_url": ""}}

    # -- Alias parsing --

    def test_alma_alias_parsed_correctly(self):
        spec = parse_compose_spec("alma97", self.config_with_aliases)
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 7)
        self.assertEqual(spec["compose_name"], "AlmaLinux OS 9.7.20251118")
        self.assertTrue(spec["is_ami_source"])
        self.assertFalse(spec["is_centos_stream"])
        self.assertEqual(spec["os_type"], "alma")

    def test_rocky_alias_parsed_correctly(self):
        spec = parse_compose_spec("rocky97", self.config_with_aliases)
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 7)
        self.assertEqual(spec["compose_name"], "Rocky-9-EC2-Base-9.7-20251123.2")
        self.assertTrue(spec["is_ami_source"])
        self.assertFalse(spec["is_centos_stream"])
        self.assertEqual(spec["os_type"], "rocky")

    def test_alma96_alias(self):
        spec = parse_compose_spec("alma96", self.config_with_aliases)
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 6)
        self.assertEqual(spec["os_type"], "alma")

    def test_rocky96_alias(self):
        spec = parse_compose_spec("rocky96", self.config_with_aliases)
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 6)
        self.assertEqual(spec["os_type"], "rocky")

    # -- Direct AMI name parsing --

    def test_alma_direct_name_without_arch(self):
        spec = parse_compose_spec("AlmaLinux OS 9.7.20251118", self.config_no_aliases)
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 7)
        self.assertEqual(spec["compose_name"], "AlmaLinux OS 9.7.20251118")
        self.assertTrue(spec["is_ami_source"])
        self.assertEqual(spec["os_type"], "alma")

    def test_alma_direct_name_with_x86_64(self):
        spec = parse_compose_spec(
            "AlmaLinux OS 9.7.20251118 x86_64", self.config_no_aliases
        )
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 7)
        self.assertEqual(spec["compose_name"], "AlmaLinux OS 9.7.20251118")
        self.assertEqual(spec["os_type"], "alma")

    def test_alma_direct_name_with_aarch64(self):
        spec = parse_compose_spec(
            "AlmaLinux OS 9.7.20251118 aarch64", self.config_no_aliases
        )
        self.assertEqual(spec["compose_name"], "AlmaLinux OS 9.7.20251118")
        self.assertEqual(spec["os_type"], "alma")

    def test_rocky_direct_name_without_arch(self):
        spec = parse_compose_spec(
            "Rocky-9-EC2-Base-9.7-20251123.2", self.config_no_aliases
        )
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 7)
        self.assertEqual(spec["compose_name"], "Rocky-9-EC2-Base-9.7-20251123.2")
        self.assertTrue(spec["is_ami_source"])
        self.assertEqual(spec["os_type"], "rocky")

    def test_rocky_direct_name_with_x86_64(self):
        spec = parse_compose_spec(
            "Rocky-9-EC2-Base-9.7-20251123.2.x86_64", self.config_no_aliases
        )
        self.assertEqual(spec["compose_name"], "Rocky-9-EC2-Base-9.7-20251123.2")
        self.assertEqual(spec["os_type"], "rocky")

    def test_rocky_ec2_lvm_variant(self):
        spec = parse_compose_spec(
            "Rocky-9-EC2-LVM-9.5-20241118.0", self.config_no_aliases
        )
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 5)
        self.assertEqual(spec["os_type"], "rocky")

    def test_rocky_ec2_no_variant(self):
        spec = parse_compose_spec("Rocky-9-Ec2-9.5-20241118.0", self.config_no_aliases)
        self.assertEqual(spec["major"], 9)
        self.assertEqual(spec["minor"], 5)
        self.assertEqual(spec["os_type"], "rocky")

    # -- Target derivation --

    def test_alma_target_derivation(self):
        """alma97 (9.7) should derive target as 10.1 (major+1, minor-6)."""
        source_spec, target_spec = parse_source_target_config(
            "alma97", None, self.config_with_aliases
        )
        self.assertEqual(source_spec["major"], 9)
        self.assertEqual(source_spec["minor"], 7)
        self.assertEqual(target_spec["major"], 10)
        self.assertEqual(target_spec["minor"], 1)
        self.assertEqual(target_spec["compose_name"], "RHEL-10.1.0-Nightly")

    def test_rocky_target_derivation(self):
        """rocky96 (9.6) should derive target as 10.0."""
        source_spec, target_spec = parse_source_target_config(
            "rocky96", None, self.config_with_aliases
        )
        self.assertEqual(source_spec["major"], 9)
        self.assertEqual(source_spec["minor"], 6)
        self.assertEqual(target_spec["major"], 10)
        self.assertEqual(target_spec["minor"], 0)

    # -- format_ami_compose_name --

    def test_format_alma_compose_with_x86_64(self):
        spec = {"compose_name": "AlmaLinux OS 9.7.20251118", "os_type": "alma"}
        self.assertEqual(
            format_ami_compose_name(spec, "x86_64"),
            "AlmaLinux OS 9.7.20251118 x86_64",
        )

    def test_format_alma_compose_with_aarch64(self):
        spec = {"compose_name": "AlmaLinux OS 9.7.20251118", "os_type": "alma"}
        self.assertEqual(
            format_ami_compose_name(spec, "aarch64"),
            "AlmaLinux OS 9.7.20251118 aarch64",
        )

    def test_format_rocky_compose_with_x86_64(self):
        spec = {
            "compose_name": "Rocky-9-EC2-Base-9.7-20251123.2",
            "os_type": "rocky",
        }
        self.assertEqual(
            format_ami_compose_name(spec, "x86_64"),
            "Rocky-9-EC2-Base-9.7-20251123.2.x86_64",
        )

    def test_format_rocky_compose_with_aarch64(self):
        spec = {
            "compose_name": "Rocky-9-EC2-Base-9.7-20251123.2",
            "os_type": "rocky",
        }
        self.assertEqual(
            format_ami_compose_name(spec, "aarch64"),
            "Rocky-9-EC2-Base-9.7-20251123.2.aarch64",
        )

    # -- Architecture validation --

    def test_valid_architectures_pass(self):
        spec = {"is_ami_source": True, "os_type": "alma"}
        validate_ami_architectures(spec, ["x86_64"])
        validate_ami_architectures(spec, ["aarch64"])
        validate_ami_architectures(spec, ["x86_64", "aarch64"])

    def test_invalid_architecture_raises(self):
        spec = {"is_ami_source": True, "os_type": "alma"}
        with self.assertRaises(ValidationError):
            validate_ami_architectures(spec, ["s390x"])

    def test_mixed_valid_invalid_raises(self):
        spec = {"is_ami_source": True, "os_type": "rocky"}
        with self.assertRaises(ValidationError):
            validate_ami_architectures(spec, ["x86_64", "ppc64le"])

    def test_non_ami_source_skips_validation(self):
        spec = {"is_ami_source": False, "os_type": "rhel"}
        validate_ami_architectures(spec, ["s390x", "ppc64le"])

    # -- TMT context --

    def test_alma_tmt_context(self):
        source_spec, target_spec = parse_source_target_config(
            "alma97", None, self.config_with_aliases
        )
        context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(context["distro"], "alma-9.7")
        self.assertEqual(context["target_distro"], "rhel-10.1")
        self.assertEqual(context["upgrade_path"], "9to10")
        self.assertEqual(context["source_compose"], "AlmaLinux OS 9.7.20251118")

    def test_normalize_tmt_compose_context_replaces_spaces(self):
        from enge.utils.source_target_parser import normalize_tmt_compose_context

        context = {
            "distro": "alma-9.7",
            "source_compose": "AlmaLinux OS 9.7.20251118",
            "target_compose": "RHEL-10.0-19700101.0",
            "event": "my event",
        }
        normalized = normalize_tmt_compose_context(context)
        self.assertEqual(normalized["source_compose"], "AlmaLinux-OS-9.7.20251118")
        self.assertEqual(normalized["target_compose"], "RHEL-10.0-19700101.0")
        self.assertEqual(normalized["event"], "my event")
        self.assertEqual(context["source_compose"], "AlmaLinux OS 9.7.20251118")

    def test_normalize_tmt_compose_context_empty(self):
        from enge.utils.source_target_parser import normalize_tmt_compose_context

        self.assertEqual(normalize_tmt_compose_context({}), {})
        self.assertEqual(normalize_tmt_compose_context(None), {})

    def test_rocky_tmt_context(self):
        source_spec, target_spec = parse_source_target_config(
            "rocky97", None, self.config_with_aliases
        )
        context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(context["distro"], "rocky-9.7")
        self.assertEqual(context["target_distro"], "rhel-10.1")

    # -- Environment variables --

    def test_alma_environment_variables(self):
        source_spec, target_spec = parse_source_target_config(
            "alma97", None, self.config_with_aliases
        )
        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertEqual(env_vars["SOURCE_RELEASE"], "9.7")
        self.assertEqual(env_vars["TARGET_RELEASE"], "10.1")
        self.assertNotIn("TARGET_OS", env_vars)

    # -- Error cases --

    def test_unknown_alias_without_ami_pattern_raises(self):
        with self.assertRaises(ValueError):
            parse_compose_spec("nonexistent99", self.config_no_aliases)

    def test_bad_ami_alias_value_raises(self):
        """Alias that resolves to a non-matching AMI name should raise."""
        bad_config = {
            "testing_farm": {"composes_prod_url": ""},
            "sources": {"ami": {"bad_alias": "NotAnAMIName"}},
        }
        with self.assertRaises(ValueError):
            parse_compose_spec("bad_alias", bad_config)


if __name__ == "__main__":
    unittest.main()
