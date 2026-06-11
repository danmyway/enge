import unittest
from types import SimpleNamespace
from unittest.mock import patch

from enge.utils.source_target_parser import generate_detailed_upgrade_path_alias


class TestDetailedUpgradePathAlias(unittest.TestCase):
    def test_generate_detailed_upgrade_path_alias(self):
        source = {"major": 9, "minor": 8}
        target = {"major": 10, "minor": 2}
        self.assertEqual(
            generate_detailed_upgrade_path_alias(source, target), "98to102"
        )


class TestSetAutoTags(unittest.TestCase):
    def test_set_auto_tags_includes_upgrade_path(self):
        from enge.dispatch import tf_send_request

        parsed_stub = SimpleNamespace(
            cli_args=SimpleNamespace(set_tag=None, auto_tag=True),
            testing_farm_endpoint=SimpleNamespace(
                log_artifact_baseurl="http://logs", api_endpoint_url="http://api"
            ),
        )
        with patch.object(tf_send_request, "parsed_opts", parsed_stub):
            submit = tf_send_request.SubmitTest()
            submit.set_auto_tags(
                set_name="pre-release",
                architecture="x86_64",
                tier="tier0",
                upgrade_path_tag="98to102",
            )
            self.assertEqual(submit.auto_generated_tags[-1], "98to102")
            self.assertIn("pre-release", ".".join(submit.auto_generated_tags))


if __name__ == "__main__":
    unittest.main()
