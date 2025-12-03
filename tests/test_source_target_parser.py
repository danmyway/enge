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

    def test_environment_variables_derived_target_for_centos_source(self):
        # Source: CentOS-Stream-9
        source_spec = {
            "major": 9,
            "minor": 0,
            "compose_name": "CentOS-Stream-9",
            "is_version_only": False,
            "is_centos_stream": True,
            "is_major_only": False,
        }
        # Target derived from source: RHEL-10.0.0-Nightly
        target_spec = {
            "major": 10,
            "minor": 0,
            "compose_name": "RHEL-10.0.0-Nightly",
            "is_version_only": False,
            "is_centos_stream": False,
            "is_major_only": False,
        }

        # Case 1: No TARGET_OS -> Should keep minor version (10.0)
        env_vars_default = generate_environment_variables(
            source_spec,
            target_spec,
        )
        self.assertEqual(env_vars_default["SOURCE_RELEASE"], "9")
        self.assertEqual(env_vars_default["TARGET_RELEASE"], "10.0")

        # Case 2: TARGET_OS=centos -> Should drop minor version (10)
        env_vars_centos = generate_environment_variables(
            source_spec, target_spec, target_os="centos"
        )
        self.assertEqual(env_vars_centos["SOURCE_RELEASE"], "9")
        self.assertEqual(env_vars_centos["TARGET_RELEASE"], "10")

        # Case 3: TARGET_OS=rhel -> Should keep minor version (10.0)
        env_vars_rhel = generate_environment_variables(
            source_spec, target_spec, target_os="rhel"
        )
        self.assertEqual(env_vars_rhel["SOURCE_RELEASE"], "9")
        self.assertEqual(env_vars_rhel["TARGET_RELEASE"], "10.0")

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


if __name__ == "__main__":
    unittest.main()
