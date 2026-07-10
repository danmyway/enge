#!/usr/bin/env python3
import copy
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from enge.utils.config_parser import (
    load_config,
    load_default_config,
    merge_configs,
)
from enge.utils.errors import ConfigurationError


class TestConfigParser(unittest.TestCase):
    def test_load_default_config_returns_dict(self):
        cfg = load_default_config()
        self.assertIsInstance(cfg, dict)
        self.assertIn("common", cfg)

    def test_load_config_no_paths_raises(self):
        with self.assertRaises(ConfigurationError):
            load_config(paths=[])


class TestMergeConfigs(unittest.TestCase):
    """Unit tests for the recursive merge helper."""

    def test_shallow_override(self):
        default = {"a": 1, "b": 2}
        user = {"b": 99}
        result = merge_configs(default, user)
        self.assertEqual(result, {"a": 1, "b": 99})

    def test_recursive_table_merge(self):
        default = {"t": {"x": 1, "y": 2}}
        user = {"t": {"y": 99}}
        result = merge_configs(default, user)
        self.assertEqual(result, {"t": {"x": 1, "y": 99}})

    def test_empty_string_inherits_default(self):
        default = {"k": "real"}
        user = {"k": ""}
        result = merge_configs(default, user)
        self.assertEqual(result["k"], "real")

    def test_empty_string_stays_when_default_also_empty(self):
        default = {"k": ""}
        user = {"k": ""}
        result = merge_configs(default, user)
        self.assertEqual(result["k"], "")

    def test_user_adds_new_key(self):
        default = {"a": 1}
        user = {"b": 2}
        result = merge_configs(default, user)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_deep_recursive_merge(self):
        default = {"l1": {"l2": {"l3": "base", "keep": "yes"}}}
        user = {"l1": {"l2": {"l3": "override"}}}
        result = merge_configs(default, user)
        self.assertEqual(result["l1"]["l2"]["l3"], "override")
        self.assertEqual(result["l1"]["l2"]["keep"], "yes")

    def test_idempotent_full_copy(self):
        """Merging a full copy over itself produces the original."""
        original = {
            "version": "1.0.0",
            "common": {"logs_directory": "/var/tmp/", "nested": {"a": 1}},
            "tests": {"git_ref": "main", "tiers": ["tier3"]},
        }
        result = merge_configs(original, copy.deepcopy(original))
        self.assertEqual(result, original)

    def test_none_inherits_default(self):
        default = {"k": "real"}
        user = {"k": None}
        result = merge_configs(default, user)
        self.assertEqual(result["k"], "real")


class TestConfigLayering(unittest.TestCase):
    """Tests for three-layer config merging: bundled < system/external < user.

    Tests (a)-(d) are RED on devel (first-found-wins, no layering) and GREEN
    after the layering rework.  Test (e) and (f) are GREEN on both.

    The patches use create=True so the new symbols (_load_bundled_config,
    SYSTEM_CONFIG_PATHS, USER_CONFIG_PATHS) are harmlessly created on devel
    where they don't yet exist.  The old code ignores them and runs its own
    first-found logic, producing wrong results → assertion failure → RED.
    """

    def _write_toml(self, directory, filename, content):
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return path

    def _layered_patches(self, bundled_path, system_paths, user_paths):
        """Return a stack of patches controlling the three-layer sources.

        Works on both old code (symbols created but unused → old behavior) and
        new code (symbols consumed → layered behavior).
        """
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with (
                patch(
                    "enge.utils.config_parser._load_bundled_config",
                    create=True,
                    return_value=_load_toml(bundled_path),
                ),
                patch(
                    "enge.utils.config_parser.SYSTEM_CONFIG_PATHS",
                    create=True,
                    new=system_paths,
                ),
                patch(
                    "enge.utils.config_parser.USER_CONFIG_PATHS",
                    create=True,
                    new=user_paths,
                ),
                patch(
                    "enge.utils.config_parser.DEFAULT_USER_CONFIG_PATHS",
                    create=True,
                    new=(),
                ),
            ):
                yield

        return _ctx()

    # ------------------------------------------------------------------
    # (a) Bundled-only key survives when external lacks it
    # ------------------------------------------------------------------
    def test_a_bundled_key_inherited_through_external(self):
        """Key present ONLY in bundled defaults, external file exists without
        it → merged config must carry the bundled value.

        RED today: external replaces bundled wholesale, so bundled-only keys
        vanish.
        """
        with tempfile.TemporaryDirectory() as td:
            bundled = self._write_toml(
                td,
                "bundled.toml",
                'version = "1.0.0"\n'
                "[common]\n"
                "logs_directory = '/var/tmp/enge/logs/'\n"
                "bundled_only_key = 'from_bundled'\n",
            )
            system = self._write_toml(
                td,
                "system.toml",
                'version = "2.0.0"\n' "[common]\n" "logs_directory = '/opt/logs/'\n",
            )
            user = self._write_toml(
                td,
                "user.toml",
                "[common]\nuser_thing = 'mine'\n",
            )

            with self._layered_patches(bundled, (str(system),), (str(user),)):
                merged = load_config(paths=[str(user)])

            self.assertEqual(merged["common"]["bundled_only_key"], "from_bundled")
            self.assertEqual(merged["common"]["logs_directory"], "/opt/logs/")
            self.assertEqual(merged["common"]["user_thing"], "mine")

    # ------------------------------------------------------------------
    # (b) Per-key table merge between bundled and system
    # ------------------------------------------------------------------
    def test_b_external_overrides_one_key_siblings_from_bundled(self):
        """External overrides one key in a table; sibling keys in that table
        come from bundled defaults.

        RED today: external replaces bundled wholesale (load_default_config
        picks first-found).
        """
        with tempfile.TemporaryDirectory() as td:
            bundled = self._write_toml(
                td,
                "bundled.toml",
                "[tests]\n"
                "git_url = 'https://default.git'\n"
                "git_ref = 'main'\n"
                "tiers = ['tier3']\n"
                "\n[tests.tier]\n"
                "tier0 = 'tag:tier[0]'\n"
                "tier1 = 'tag:tier[01]'\n",
            )
            system = self._write_toml(
                td,
                "system.toml",
                "[tests]\n" "git_url = 'https://team.git'\n",
            )
            nonexistent = str(Path(td) / "nonexistent.toml")

            with self._layered_patches(bundled, (str(system),), ()):
                merged = load_config(paths=[nonexistent])

            self.assertEqual(merged["tests"]["git_url"], "https://team.git")
            self.assertEqual(merged["tests"]["git_ref"], "main")
            self.assertEqual(merged["tests"]["tiers"], ["tier3"])
            self.assertEqual(merged["tests"]["tier"]["tier0"], "tag:tier[0]")

    # ------------------------------------------------------------------
    # (c) User > external per-key; absent keys inherit external
    # ------------------------------------------------------------------
    def test_c_user_overrides_external_per_key_absent_inherits(self):
        """User layer overrides external per-key; keys absent in user
        inherit from external.

        RED today: only one file loaded as user config, no system layer.
        """
        with tempfile.TemporaryDirectory() as td:
            bundled = self._write_toml(
                td,
                "bundled.toml",
                "[project]\nname = 'default_proj'\nowner = 'default_owner'\n",
            )
            system = self._write_toml(
                td,
                "system.toml",
                "[project]\nname = 'team_proj'\nowner = 'team_owner'\n",
            )
            user = self._write_toml(
                td,
                "user.toml",
                "[project]\nname = 'my_proj'\n",
            )

            with self._layered_patches(bundled, (str(system),), (str(user),)):
                merged = load_config(paths=[str(user)])

            self.assertEqual(merged["project"]["name"], "my_proj")
            self.assertEqual(merged["project"]["owner"], "team_owner")

    # ------------------------------------------------------------------
    # (d) "" in user inherits external value with WARNING
    # ------------------------------------------------------------------
    def test_d_empty_string_in_user_inherits_external_with_warning(self):
        """Empty string in user layer for a key set in external inherits
        the external value, WARNING emitted naming file and key.

        RED today: no system layer, real defaults have empty api_endpoint_url.
        """
        with tempfile.TemporaryDirectory() as td:
            bundled = self._write_toml(
                td,
                "bundled.toml",
                "[testing_farm]\napi_endpoint_url = 'https://bundled.api'\n",
            )
            system = self._write_toml(
                td,
                "system.toml",
                "[testing_farm]\napi_endpoint_url = 'https://team.api'\n",
            )
            user = self._write_toml(
                td,
                "user.toml",
                "[testing_farm]\napi_endpoint_url = ''\n",
            )

            with self._layered_patches(bundled, (str(system),), (str(user),)):
                with self.assertLogs("enge.utils.config_parser", level="WARNING") as cm:
                    merged = load_config(paths=[str(user)])

            self.assertEqual(
                merged["testing_farm"]["api_endpoint_url"], "https://team.api"
            )
            warning_found = any(
                "[testing_farm].api_endpoint_url" in msg and str(user) in msg
                for msg in cm.output
            )
            self.assertTrue(
                warning_found,
                f"Expected WARNING naming file and key; got: {cm.output}",
            )

    # ------------------------------------------------------------------
    # (e) Characterization: full-copy external = identical (GREEN on both)
    # ------------------------------------------------------------------
    def test_e_characterization_full_copy_external_identical(self):
        """A full-copy external config (every bundled key present) produces
        the identical merged dict.  GREEN before AND after."""
        with tempfile.TemporaryDirectory() as td:
            content = (
                'version = "1.0.0"\n'
                "[common]\n"
                "logs_directory = '/var/tmp/enge/logs/'\n"
                "archive_tasks_latest = '/tmp/enge_latest_jobs'\n"
                "\n[tests]\n"
                "git_url = 'https://default.git'\n"
                "git_ref = 'main'\n"
                "tiers = ['tier3']\n"
                "\n[tests.tier]\n"
                "tier0 = 'tag:tier[0]'\n"
                "\n[project]\n"
                "name = 'leapp'\n"
                "owner = 'oamg'\n"
            )
            path = self._write_toml(td, "config.toml", content)
            original = _load_toml(path)

            result = merge_configs(original, copy.deepcopy(original))
            self.assertEqual(result, original)

    # ------------------------------------------------------------------
    # (f) --config = user layer, bundled underneath (GREEN on both)
    # ------------------------------------------------------------------
    def test_f_config_flag_becomes_user_layer_bundled_underneath(self):
        """--config path becomes the user layer; bundled defaults still
        contribute keys not present in the user file."""
        with tempfile.TemporaryDirectory() as td:
            bundled = self._write_toml(
                td,
                "bundled.toml",
                "[common]\n"
                "logs_directory = '/var/tmp/enge/logs/'\n"
                "archive_tasks_latest = '/tmp/enge_latest_jobs'\n"
                "\n[tests]\n"
                "git_url = ''\n"
                "git_ref = 'main'\n",
            )
            custom = self._write_toml(
                td,
                "custom.toml",
                "[tests]\ngit_url = 'https://custom.git'\n",
            )

            with self._layered_patches(bundled, (), ()):
                merged = load_config(paths=[str(custom)])

            self.assertEqual(merged["tests"]["git_url"], "https://custom.git")
            self.assertEqual(merged["tests"]["git_ref"], "main")
            self.assertEqual(merged["common"]["logs_directory"], "/var/tmp/enge/logs/")


def _load_toml(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


if __name__ == "__main__":
    unittest.main()
