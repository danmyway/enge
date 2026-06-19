#!/usr/bin/env python3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import enge.__main__ as enge_main
from enge.utils.errors import (
    ConfigurationError,
    ValidationError,
    NetworkError,
    UserAbort,
)
from enge.utils.globals import (
    EXIT_CONFIG_ERROR,
    EXIT_PARTIAL_FAILURE,
    EXIT_INTERRUPT,
    EXIT_GENERAL_ERROR,
)

_STUB_CONFIG = {
    "testing_farm": {"api_endpoint_url": "u", "log_artifact_baseurl": "u"},
    "common": {
        "archive_tasks_latest": "/tmp/l",
        "archive_tasks_default": "/tmp/d",
    },
}


def _stub_parsed(action="cancel"):
    return SimpleNamespace(
        cli_args=SimpleNamespace(debug=False, action=action),
        config=_STUB_CONFIG,
        testing_farm_endpoint=SimpleNamespace(
            api_endpoint_url="u", log_artifact_baseurl="u"
        ),
        archive_tasks_latest="/tmp/l",
        archive_tasks_default="/tmp/d",
    )


def _apply_stub(mock_parsed, stub):
    mock_parsed.cli_args = stub.cli_args
    mock_parsed.config = stub.config
    mock_parsed.testing_farm_endpoint = stub.testing_farm_endpoint
    mock_parsed.archive_tasks_latest = stub.archive_tasks_latest
    mock_parsed.archive_tasks_default = stub.archive_tasks_default


class TestCliErrorMapping(unittest.TestCase):
    @patch.object(enge_main, "get_arguments", return_value=SimpleNamespace(debug=False))
    @patch.object(enge_main, "parsed_opts")
    @patch("enge.cancel.__main__.main")
    def test_cancel_receives_app_context(self, mock_cancel_main, mock_parsed, _):
        _apply_stub(mock_parsed, _stub_parsed(action="cancel"))
        mock_cancel_main.return_value = None
        enge_main.main()
        mock_cancel_main.assert_called_once()
        ctx_arg = mock_cancel_main.call_args[0][0]
        from enge.utils.app_context import AppContext

        self.assertIsInstance(ctx_arg, AppContext)

    @patch.object(enge_main, "get_arguments", return_value=SimpleNamespace(debug=False))
    @patch.object(enge_main, "parsed_opts")
    @patch("enge.cancel.__main__.main")
    def test_configuration_error_maps_to_exit_99(self, mock_subcommand, mock_parsed, _):
        _apply_stub(mock_parsed, _stub_parsed())
        mock_subcommand.side_effect = ConfigurationError("bad config")
        code = enge_main.main()
        self.assertEqual(code, EXIT_CONFIG_ERROR)

    @patch.object(enge_main, "get_arguments", return_value=SimpleNamespace(debug=False))
    @patch.object(enge_main, "parsed_opts")
    @patch("enge.cancel.__main__.main")
    def test_validation_error_maps_to_exit_2(self, mock_subcommand, mock_parsed, _):
        _apply_stub(mock_parsed, _stub_parsed())
        mock_subcommand.side_effect = ValidationError("bad args")
        code = enge_main.main()
        self.assertEqual(code, EXIT_PARTIAL_FAILURE)

    @patch.object(enge_main, "get_arguments", return_value=SimpleNamespace(debug=False))
    @patch.object(enge_main, "parsed_opts")
    @patch("enge.cancel.__main__.main")
    def test_network_error_maps_to_exit_1(self, mock_subcommand, mock_parsed, _):
        _apply_stub(mock_parsed, _stub_parsed())
        mock_subcommand.side_effect = NetworkError("net down")
        code = enge_main.main()
        self.assertEqual(code, EXIT_GENERAL_ERROR)

    @patch.object(enge_main, "get_arguments", return_value=SimpleNamespace(debug=False))
    @patch.object(enge_main, "parsed_opts")
    @patch("enge.cancel.__main__.main")
    def test_user_abort_maps_to_130(self, mock_subcommand, mock_parsed, _):
        _apply_stub(mock_parsed, _stub_parsed())
        mock_subcommand.side_effect = UserAbort("abort")
        code = enge_main.main()
        self.assertEqual(code, EXIT_INTERRUPT)


if __name__ == "__main__":
    unittest.main()
