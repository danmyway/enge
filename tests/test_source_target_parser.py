import unittest

from enge.utils.source_target_parser import (
    apply_centos_context_overrides,
    generate_environment_variables,
    generate_tmt_context,
    parse_source_target_config,
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


if __name__ == "__main__":
    unittest.main()
