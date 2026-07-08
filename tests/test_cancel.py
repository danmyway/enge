import unittest
from unittest.mock import patch, MagicMock

from tests._helpers import make_app_context


class TestCancelCharacterization(unittest.TestCase):
    """Lock down cancel's observable behavior at the module boundary."""

    def _setup_cancel_mocks(
        self, mock_delete, mock_parse, mock_submit_cls, task_ids=None, status_code=200
    ):
        if task_ids is None:
            task_ids = ["aaaa-bbbb-cccc"]
        urls = [f"https://tf.example.com/api/{tid}" for tid in task_ids]
        mock_parse.return_value = (urls, "latest")
        mock_submit = MagicMock()
        mock_submit.build_payload.return_value = ({"Authorization": "Bearer x"}, {})
        mock_submit_cls.return_value = mock_submit
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.text = ""
        mock_delete.return_value = mock_response

    @patch("enge.cancel.__main__.SubmitTest")
    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_full_success_returns_zero(self, mock_delete, mock_parse, mock_submit_cls):
        self._setup_cancel_mocks(
            mock_delete, mock_parse, mock_submit_cls, status_code=200
        )
        ctx = make_app_context(api_key="secret-key")

        from enge.cancel.__main__ import main
        from enge.utils.globals import ExitCode

        result = main(ctx)
        self.assertEqual(result, ExitCode.SUCCESS)

    @patch("enge.cancel.__main__.SubmitTest")
    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_partial_failure_returns_exit_2(
        self, mock_delete, mock_parse, mock_submit_cls
    ):
        self._setup_cancel_mocks(
            mock_delete, mock_parse, mock_submit_cls, status_code=500
        )
        ctx = make_app_context()

        from enge.cancel.__main__ import main
        from enge.utils.globals import ExitCode

        result = main(ctx)
        self.assertEqual(result, ExitCode.TEST_FAILURE)

    @patch("enge.cancel.__main__.parse_tasks")
    def test_dryrun_output_content(self, mock_parse):
        mock_parse.return_value = (
            [
                "https://tf.example.com/api/aaaa-bbbb-cccc",
                "https://tf.example.com/api/dddd-eeee-ffff",
            ],
            "latest",
        )
        ctx = make_app_context(extra_cli={"dryrun": True})

        from enge.cancel.__main__ import main

        with patch("enge.cancel.__main__.console") as mock_console:
            main(ctx)
            printed = " ".join(str(c) for c in mock_console.print.call_args_list)
            self.assertIn("Would cancel 2 task(s):", printed)
            self.assertIn("https://tf.example.com/artifacts/aaaa-bbbb-cccc", printed)
            self.assertIn("https://tf.example.com/artifacts/dddd-eeee-ffff", printed)
            calls = mock_console.print.call_args_list
            self.assertTrue(calls[0].args[0].startswith("Would cancel"))
            self.assertTrue(calls[1].args[0].startswith("  - "))
            self.assertTrue(calls[2].args[0].startswith("  - "))


class TestCancelMain(unittest.TestCase):
    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_dryrun_does_not_send_requests(self, mock_delete, mock_parse):
        mock_parse.return_value = (
            ["https://tf.example.com/api/aaaa-bbbb-cccc"],
            "latest",
        )
        ctx = make_app_context(extra_cli={"dryrun": True})

        from enge.cancel.__main__ import main

        main(ctx)

        mock_delete.assert_not_called()

    @patch("enge.cancel.__main__.SubmitTest")
    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_cancel_sends_delete_request(
        self, mock_delete, mock_parse, mock_submit_cls
    ):
        mock_parse.return_value = (
            ["https://tf.example.com/api/aaaa-bbbb-cccc"],
            "latest",
        )
        mock_submit = MagicMock()
        mock_submit.build_payload.return_value = ({"Authorization": "Bearer x"}, {})
        mock_submit_cls.return_value = mock_submit
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""
        mock_delete.return_value = mock_response
        ctx = make_app_context(api_key="secret-key")

        from enge.cancel.__main__ import main

        main(ctx)

        mock_delete.assert_called_once()

    @patch("enge.cancel.__main__.SubmitTest")
    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_failed_cancellation_returns_partial_failure(
        self, mock_delete, mock_parse, mock_submit_cls
    ):
        mock_parse.return_value = (
            ["https://tf.example.com/api/aaaa-bbbb-cccc"],
            "latest",
        )
        mock_submit = MagicMock()
        mock_submit.build_payload.return_value = ({"Authorization": "Bearer x"}, {})
        mock_submit_cls.return_value = mock_submit
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "server error"
        mock_delete.return_value = mock_response
        ctx = make_app_context()

        from enge.cancel.__main__ import main
        from enge.utils.globals import ExitCode

        result = main(ctx)
        self.assertEqual(result, ExitCode.TEST_FAILURE)

    def test_no_parsed_opts_reference_in_module(self):
        import enge.cancel.__main__ as cancel_mod

        with open(cancel_mod.__file__) as f:
            source = f.read()
        self.assertNotIn("parsed_opts", source)


class TestCancelJobs(unittest.TestCase):
    @patch("enge.cancel.__main__.parse_tasks")
    def test_init_stores_ctx(self, mock_parse):
        mock_parse.return_value = ([], None)
        ctx = make_app_context()

        from enge.cancel.__main__ import CancelJobs

        cj = CancelJobs(ctx)

        self.assertIs(cj.ctx, ctx)

    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_cancel_tasks_uses_ctx_api_key(self, mock_delete, mock_parse):
        mock_parse.return_value = (
            ["https://tf.example.com/api/aaaa-bbbb-cccc"],
            "latest",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""
        mock_delete.return_value = mock_response
        ctx = make_app_context(api_key="my-secret-key")

        from enge.cancel.__main__ import CancelJobs

        with patch("enge.cancel.__main__.SubmitTest") as mock_submit_cls:
            mock_submit = MagicMock()
            mock_submit.build_payload.return_value = ({"Authorization": "Bearer x"}, {})
            mock_submit_cls.return_value = mock_submit

            cj = CancelJobs(ctx)
            cj.cancel_tasks()

            self.assertEqual(mock_submit.api_key, "my-secret-key")

    @patch("enge.cancel.__main__.SubmitTest")
    @patch("enge.cancel.__main__.parse_tasks")
    @patch("enge.cancel.__main__.http_delete")
    def test_display_results_uses_ctx_baseurl(
        self, mock_delete, mock_parse, mock_submit_cls
    ):
        mock_parse.return_value = (
            ["https://tf.example.com/api/dead-beef"],
            "latest",
        )
        mock_submit = MagicMock()
        mock_submit.build_payload.return_value = ({"Authorization": "Bearer x"}, {})
        mock_submit_cls.return_value = mock_submit
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "err"
        mock_delete.return_value = mock_response
        ctx = make_app_context(log_artifact_baseurl="https://artifacts.test")

        from enge.cancel.__main__ import CancelJobs

        cj = CancelJobs(ctx)
        cj.cancel_tasks()

        with patch("enge.cancel.__main__.console") as mock_console:
            cj.display_results()
            printed = " ".join(str(c) for c in mock_console.print.call_args_list)
            self.assertIn("https://artifacts.test", printed)

    @patch("enge.cancel.__main__.parse_tasks")
    def test_empty_task_list_returns_early(self, mock_parse):
        mock_parse.return_value = ([], None)
        ctx = make_app_context()

        from enge.cancel.__main__ import CancelJobs

        cj = CancelJobs(ctx)
        results = cj.cancel_tasks()

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
