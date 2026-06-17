import unittest

from tests._helpers import make_app_context
from enge.report.concurrent_parser import ConcurrentRequestParser, TaskResult


def _task_result(state: str) -> TaskResult:
    return TaskResult(
        request_uuid="00000000-0000-0000-0000-000000000001",
        request_source_compose="AlmaLinux-9",
        request_target_release="10",
        request_upgrade_path="9 to 10",
        request_arch="x86_64",
        request_state=state,
        request_datetime_created="2026-01-01T00:00:00",
        request_plan="/plans/smoke",
        request_plan_filter="",
        request_summary="Undefined",
        request_result_overall="Undefined",
        results_xml_url="http://example.com/results.xml",
        url="http://example.com/api/tasks/00000000-0000-0000-0000-000000000001",
    )


class TestConcurrentParserTaskState(unittest.TestCase):
    def test_new_state_waits_when_wait_flag_set(self):
        ctx = make_app_context(action="report", extra_cli={"wait": True})
        parser = ConcurrentRequestParser(ctx)
        task_result = _task_result("NEW")
        wait_called = []

        def mock_wait(task):
            wait_called.append(task)
            task.request_state = "COMPLETE"

        parser._wait_for_completion = mock_wait
        parser._process_task_state(task_result)

        self.assertEqual(wait_called, [task_result])
        self.assertEqual(task_result.request_state, "COMPLETE")
        self.assertFalse(task_result.should_skip)

    def test_new_state_skipped_without_wait(self):
        ctx = make_app_context(action="report", extra_cli={"wait": False})
        parser = ConcurrentRequestParser(ctx)
        task_result = _task_result("NEW")
        parser._process_task_state(task_result)

        self.assertTrue(task_result.should_skip)
        self.assertEqual(task_result.skip_reason, "queued")


if __name__ == "__main__":
    unittest.main()
