import unittest
from types import SimpleNamespace

from enge.utils.app_context import AppContext
from enge.utils.opt_manager import TestingFarmEndpoint


class TestAppContext(unittest.TestCase):
    def _make_po(self, **overrides):
        defaults = {
            "cli_args": SimpleNamespace(action="cancel", debug=False),
            "config": {
                "testing_farm": {
                    "api_key": "tok",
                    "api_endpoint_url": "https://tf.example.com/api",
                    "log_artifact_baseurl": "https://tf.example.com/artifacts",
                },
                "common": {
                    "archive_tasks_latest": "/tmp/latest",
                    "archive_tasks_default": "/tmp/default",
                },
                "project": {"name": "test"},
                "tests": {},
                "reportportal": {},
            },
            "testing_farm_endpoint": TestingFarmEndpoint(
                "https://tf.example.com/api",
                "https://tf.example.com/artifacts",
            ),
            "archive_tasks_latest": "/tmp/latest",
            "archive_tasks_default": "/tmp/default",
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_from_parsed_opts_copies_core_fields(self):
        po = self._make_po()
        ctx = AppContext.from_parsed_opts(po)
        self.assertIs(ctx.cli_args, po.cli_args)
        self.assertIs(ctx.config, po.config)
        self.assertIs(ctx.testing_farm_endpoint, po.testing_farm_endpoint)
        self.assertEqual(ctx.archive_tasks_latest, "/tmp/latest")
        self.assertEqual(ctx.archive_tasks_default, "/tmp/default")

    def test_testing_farm_property(self):
        po = self._make_po()
        ctx = AppContext.from_parsed_opts(po)
        self.assertIs(ctx.testing_farm, po.config["testing_farm"])

    def test_project_property(self):
        po = self._make_po()
        ctx = AppContext.from_parsed_opts(po)
        self.assertIs(ctx.project, po.config["project"])

    def test_common_property(self):
        po = self._make_po()
        ctx = AppContext.from_parsed_opts(po)
        self.assertIs(ctx.common, po.config["common"])

    def test_tests_property_defaults_to_empty_dict(self):
        po = self._make_po()
        po.config.pop("tests")
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(ctx.tests, {})

    def test_reportportal_property_defaults_to_empty_dict(self):
        po = self._make_po()
        po.config.pop("reportportal")
        ctx = AppContext.from_parsed_opts(po)
        self.assertEqual(ctx.reportportal, {})

    def test_frozen_prevents_mutation(self):
        po = self._make_po()
        ctx = AppContext.from_parsed_opts(po)
        with self.assertRaises(AttributeError):
            ctx.config = {}


if __name__ == "__main__":
    unittest.main()
