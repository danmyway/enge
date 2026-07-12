import unittest

from enge.utils.errors import ConfigurationError, ValidationError
from enge.utils.source_target_parser import (
    apply_centos_context_overrides,
    format_ami_compose_name,
    generate_environment_variables,
    generate_tmt_context,
    generate_upgrade_path_alias,
    is_rhui_compose_name,
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

    def test_symbolic_rhui_compose_specs(self):
        cases = [
            ("RHEL-8-rhui", "RHEL-8-rhui"),
            ("RHEL-8-sap-hana-rhui", "RHEL-8-sap-hana-rhui"),
            ("RHEL-8-sap-netweaver-rhui", "RHEL-8-sap-netweaver-rhui"),
            ("rhel-8-sap-hana-rhui", "RHEL-8-sap-hana-rhui"),
        ]
        for spec, expected_compose in cases:
            with self.subTest(spec=spec):
                parsed = parse_compose_spec(spec, self.minimal_config)
                self.assertEqual(parsed["compose_name"], expected_compose)
                self.assertEqual(parsed["major"], int(expected_compose.split("-")[1]))
                self.assertEqual(parsed["minor"], 0)
                self.assertTrue(parsed["is_major_only"])
                self.assertFalse(parsed["is_centos_stream"])
                self.assertFalse(parsed["is_ami_source"])

    def test_non_rhui_compose_specs_rejected(self):
        invalid_specs = [
            "RHEL-8",
            "RHEL-8-sap-hana",
            "RHEL-8-sap-netweaver",
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    parse_compose_spec(spec, self.minimal_config)

    def test_symbolic_rhui_source_target_config(self):
        source_spec, target_spec = parse_source_target_config(
            "RHEL-8-rhui", "9.4", self.minimal_config
        )
        self.assertEqual(source_spec["compose_name"], "RHEL-8-rhui")
        self.assertEqual(source_spec["major"], 8)
        self.assertTrue(source_spec["is_major_only"])

        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertEqual(env_vars["SOURCE_RELEASE"], "8")
        self.assertEqual(env_vars["TARGET_RELEASE"], "9.4")

        context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(context["distro"], "rhel-8")
        self.assertEqual(context["source_compose"], "RHEL-8-rhui")
        self.assertEqual(context["upgrade_path"], "8to9")


class TestIsRhuiComposeName(unittest.TestCase):
    def test_canonical_rhui_compose_name(self):
        self.assertTrue(is_rhui_compose_name("RHEL-8-rhui"))

    def test_rhui_compose_name_with_middle_segment(self):
        self.assertTrue(is_rhui_compose_name("RHEL-8-sap-hana-rhui"))

    def test_non_rhui_compose_name(self):
        self.assertFalse(is_rhui_compose_name("RHEL-8.10"))

    def test_uppercase_suffix_is_not_matched(self):
        self.assertFalse(is_rhui_compose_name("RHEL-8-RHUI"))

    def test_empty_string(self):
        self.assertFalse(is_rhui_compose_name(""))


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


class TestComposeTargetMap(unittest.TestCase):
    """[composes.target_map] resolves a source's default target as config
    data, used only when the CLI/set/preset/[tests] chain produced no
    explicit target — falling back to the minor-6 formula on a miss."""

    def setUp(self):
        self.base_config = {"testing_farm": {"composes_prod_url": ""}}

    def _config_with_map(self, target_map):
        config = dict(self.base_config)
        config["composes"] = {"target_map": target_map}
        return config

    def test_map_hit_resolves_mapped_target_end_to_end(self):
        config = self._config_with_map({"8.10": "9.9"})

        source_spec, target_spec = parse_source_target_config("8.10", None, config)

        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 9)

        env_vars = generate_environment_variables(source_spec, target_spec)
        self.assertEqual(env_vars["TARGET_RELEASE"], "9.9")

        context = generate_tmt_context(source_spec, target_spec)
        self.assertEqual(context["target_distro"], "rhel-9.9")

        self.assertEqual(generate_upgrade_path_alias(source_spec, target_spec), "8to9")

    def test_map_miss_falls_to_formula_with_warning(self):
        config = self._config_with_map({})

        with self.assertLogs("enge.utils.source_target_parser", level="WARNING") as log:
            source_spec, target_spec = parse_source_target_config("8.10", None, config)

        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 4)

        warning_text = "\n".join(log.output)
        self.assertIn("derived", warning_text.lower())
        self.assertIn("8.10", warning_text)
        self.assertIn("target_map", warning_text)

    def test_explicit_target_bypasses_map_entirely(self):
        config = self._config_with_map({"8.10": "9.9"})

        source_spec, target_spec = parse_source_target_config("8.10", "9.5", config)

        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 5)

    def test_invalid_map_value_raises_configuration_error(self):
        config = self._config_with_map({"8.10": "not-a-valid-spec!!"})

        with self.assertRaises(ConfigurationError) as ctx:
            parse_source_target_config("8.10", None, config)

        message = str(ctx.exception)
        self.assertIn("8.10", message)
        self.assertIn("not-a-valid-spec!!", message)

    def test_empty_string_map_value_treated_as_miss(self):
        config = self._config_with_map({"8.10": ""})

        with self.assertLogs("enge.utils.source_target_parser", level="WARNING"):
            source_spec, target_spec = parse_source_target_config("8.10", None, config)

        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 4)

    def test_target_map_skips_centos_stream_source_despite_decoy_key(self):
        # CentOS Stream sources carry minor=0 internally; a "9.0" map entry
        # must not be mistaken for a genuine major.minor match.
        config = self._config_with_map({"9.0": "99.99"})

        source_spec, target_spec = parse_source_target_config(
            "CentOS-Stream-9", None, config
        )

        self.assertTrue(source_spec["is_centos_stream"])
        self.assertEqual(target_spec["major"], 10)
        self.assertEqual(target_spec["minor"], 0)

    def test_target_map_skips_major_only_source_despite_decoy_key(self):
        # Major-only (e.g. symbolic RHUI) sources carry minor=0 internally;
        # an "8.0" map entry must not be mistaken for a genuine match.
        config = self._config_with_map({"8.0": "99.99"})

        source_spec, target_spec = parse_source_target_config(
            "RHEL-8-rhui", None, config
        )

        self.assertTrue(source_spec["is_major_only"])
        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 0)

    def test_no_warning_for_centos_stream_source_ineligible_for_map(self):
        # Stream sources are fenced out of the map entirely; falling through
        # to the formula must not warn about a key the guard will never
        # consult (the map-miss WARNING is only meaningful for eligible
        # sources).
        config = self._config_with_map({"9.0": "99.99"})

        with self.assertNoLogs("enge.utils.source_target_parser", level="WARNING"):
            source_spec, target_spec = parse_source_target_config(
                "CentOS-Stream-9", None, config
            )

        self.assertTrue(source_spec["is_centos_stream"])
        self.assertEqual(target_spec["major"], 10)
        self.assertEqual(target_spec["minor"], 0)

    def test_no_warning_for_major_only_source_ineligible_for_map(self):
        # Same as above for major-only (symbolic RHUI) sources.
        config = self._config_with_map({"8.0": "99.99"})

        with self.assertNoLogs("enge.utils.source_target_parser", level="WARNING"):
            source_spec, target_spec = parse_source_target_config(
                "RHEL-8-rhui", None, config
            )

        self.assertTrue(source_spec["is_major_only"])
        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 0)

    def test_formula_boundary_gap_minor_below_six(self):
        """Source minor < 6 clamps target minor to 0 (max(0, minor - 6))."""
        config = self._config_with_map({})

        source_spec, target_spec = parse_source_target_config("8.5", None, config)

        self.assertEqual(target_spec["major"], 9)
        self.assertEqual(target_spec["minor"], 0)


if __name__ == "__main__":
    unittest.main()
