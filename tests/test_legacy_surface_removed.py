"""The legacy-archive surface is gone from the CLI, config and context.

Q-L30'a (2026-09-29) retires the whole pre-manifest archive: the
`--get-tag` lookup on every task-consuming subcommand, the dead
`report --path`, the `enge migrate-archive` subcommand with its
`enge.migrate` package and `enge.utils.legacy_archive` bridge, and the
`[common]` keys `archive_tasks_latest`/`archive_tasks_default`.

Q-L30'b: leftover copies of those two keys in a user or
`/etc/enge/enge_default_config.toml` file are SILENTLY ignored -- no
error and no warning -- because `enge.spec` ships the system file as
`%config(noreplace)`, so edited copies keep the old keys forever.  S6
and S7 pin that silence from both directions: present-but-garbage and
absent.
"""

import contextlib
import copy
import dataclasses
import importlib
import io
import unittest

from tests.test_opt_manager import MINIMAL_CONFIG, _make_partial_opts
from enge.utils.app_context import AppContext
from enge.utils.arg_parser import get_arguments

ARCHIVE_KEYS = ("archive_tasks_latest", "archive_tasks_default")


def _parse(argv):
    """Parse argv, returning (namespace|None, exit_code|None, stderr)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            return get_arguments(args=argv), None, err.getvalue()
        except SystemExit as exc:
            return None, exc.code, err.getvalue()


class TestRetiredFlagsRejected(unittest.TestCase):

    GET_TAG_INVOCATIONS = (
        ["report"],
        ["rerun"],
        ["cancel"],
        ["reportportal"],
        ["reportportal", "finish"],
        ["reportportal", "enrich"],
        ["reportportal", "delete-logs"],
    )

    def test_s1_get_tag_is_rejected_on_every_subcommand(self):
        # The `--get-tag=x` form, not `--get-tag x`: on the bare
        # `reportportal` parser a separate `x` is swallowed as the
        # rp_subcommand positional, and argparse reports that invalid
        # choice instead of the unknown option.  Both forms were accepted
        # before the retirement, so this still fails on the old parser.
        for base in self.GET_TAG_INVOCATIONS:
            label = " ".join(base)
            with self.subTest(subcommand=label):
                # Premise guard: the invocation parses without the flag.
                args, code, _ = _parse(list(base))
                self.assertIsNotNone(
                    args, f"premise failed: 'enge {label}' does not parse (exit {code})"
                )

                _, code, stderr = _parse([*base, "--get-tag=x"])

                self.assertEqual(
                    code, 2, f"'enge {label} --get-tag=x' was accepted (exit {code})"
                )
                self.assertIn("--get-tag", stderr, f"stderr for 'enge {label}'")

    def test_s2_report_path_is_rejected(self):
        args, code, _ = _parse(["report"])
        self.assertIsNotNone(args, f"premise failed: 'enge report' (exit {code})")

        _, code, stderr = _parse(["report", "--path", "x"])

        self.assertEqual(code, 2)
        self.assertIn("--path", stderr)

    def test_s3_migrate_archive_is_rejected_as_a_subcommand(self):
        args, code, _ = _parse(["report"])
        self.assertIsNotNone(args, f"premise failed: 'enge report' (exit {code})")

        _, code, stderr = _parse(["migrate-archive"])

        self.assertEqual(code, 2)
        self.assertIn("migrate-archive", stderr)


class TestRetiredModulesGone(unittest.TestCase):

    def test_s4_legacy_modules_are_not_importable(self):
        for name in ("enge.migrate", "enge.utils.legacy_archive"):
            with self.subTest(module=name):
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(name)


class TestArchiveConfigKeysRetired(unittest.TestCase):

    def test_s5_bundled_defaults_no_longer_ship_the_archive_keys(self):
        from enge.utils.config_parser import _load_bundled_config

        common = _load_bundled_config().get("common", {})

        for key in ARCHIVE_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, common)

    def test_s6_leftover_archive_keys_are_ignored_whatever_their_type(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        for key in ARCHIVE_KEYS:
            cfg["common"][key] = 1
        # Premise guard: the config really does carry both leftovers.
        for key in ARCHIVE_KEYS:
            self.assertIn(key, cfg["common"])

        po = _make_partial_opts(config=cfg)

        po._validate_static_configuration()  # must not raise
        po._validate_operational_defaults()  # must not raise

    def test_s7_absent_archive_keys_are_not_required(self):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        for key in ARCHIVE_KEYS:
            cfg["common"].pop(key, None)

        po = _make_partial_opts(config=cfg)

        po._validate_operational_defaults()  # must not raise


class TestAppContextFieldsRetired(unittest.TestCase):

    def test_s8_app_context_has_no_archive_fields(self):
        names = {f.name for f in dataclasses.fields(AppContext)}

        for key in ARCHIVE_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, names)


if __name__ == "__main__":
    unittest.main()
