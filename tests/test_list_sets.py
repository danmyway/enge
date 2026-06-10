"""Smoke test for --list-sets table rendering."""

import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

from enge.__main__ import _handle_list_sets


class TestHandleListSets(unittest.TestCase):
    def _mock_cli(self, *, list_sets=False, list_detail=False):
        cli = MagicMock()
        cli.action = "test"
        cli.list_sets = list_sets
        cli.list_sets_detail = list_detail
        cli.config = None
        return cli

    @patch("enge.__main__.get_arguments")
    def test_no_list_flag_returns_false(self, mock_args):
        mock_args.return_value = self._mock_cli()
        self.assertFalse(_handle_list_sets())

    @patch("enge.__main__.get_arguments")
    def test_list_sets_brief(self, mock_args):
        mock_args.return_value = self._mock_cli(list_sets=True)
        config = {
            "tests": {
                "set": {
                    "smoke": {
                        "source": "9.7",
                        "architectures": ["x86_64"],
                        "tiers": ["tier0"],
                    }
                }
            }
        }
        with patch("enge.utils.config_parser.load_config", return_value=config):
            result = _handle_list_sets()
        self.assertTrue(result)

    @patch("enge.__main__.get_arguments")
    def test_list_sets_empty_config(self, mock_args):
        mock_args.return_value = self._mock_cli(list_sets=True)
        with patch(
            "enge.utils.config_parser.load_config", return_value={"tests": {"set": {}}}
        ):
            result = _handle_list_sets()
        self.assertTrue(result)

    @patch("enge.__main__.get_arguments")
    def test_non_test_action_returns_false(self, mock_args):
        cli = self._mock_cli(list_sets=True)
        cli.action = "report"
        mock_args.return_value = cli
        self.assertFalse(_handle_list_sets())


if __name__ == "__main__":
    unittest.main()
