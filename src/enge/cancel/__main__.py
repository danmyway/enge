#!/usr/bin/env python3
import logging
import sys
from typing import List

import requests
from rich import box
from rich.table import Table

from enge.utils.http_client import http_delete

from enge.dispatch.tf_send_request import SubmitTest
from enge.report.__main__ import parse_tasks
from enge.utils.console import console
from enge.utils.errors import ValidationError, UserAbort, EngeError
from enge.utils.opt_manager import parsed_opts
from enge.utils.globals import REQUEST_TIMEOUT_DEFAULT

LOGGER = logging.getLogger(__name__)


class CancelJobs:
    """
    A class to handle the cancellation of Testing Farm tasks.

    Reuses the same input handling and UUID validation logic as report and rerun modules.
    """

    def __init__(self):
        self.cancel_results = []
        self.req_url_list = []
        self.task_source = None

        # Retrieve task URLs and their source using the same logic as report/rerun
        self.req_url_list, self.task_source = parse_tasks()

    def cancel_tasks(self) -> List[dict]:
        """
        Cancel the specified tasks by sending DELETE requests to Testing Farm API.

        Returns:
            List of dictionaries containing cancellation results
        """
        if not self.req_url_list:
            LOGGER.warning("No valid task IDs found to cancel")
            return []

        LOGGER.info(f"Found {len(self.req_url_list)} task(s) to cancel")

        # Get authorization header using the same method as rerun
        submit = SubmitTest()
        submit.api_key = parsed_opts.testing_farm.get("api_key")
        req_header, _ = submit.build_payload()

        for task_url in self.req_url_list:
            task_id = task_url.split("/")[-1]

            try:
                LOGGER.debug(f"Cancelling task: {task_id}")

                # Send DELETE request to cancel the task
                response = http_delete(
                    task_url, headers=req_header, timeout=REQUEST_TIMEOUT_DEFAULT
                )

                result = {
                    "task_id": task_id,
                    "status_code": response.status_code,
                    "success": response.status_code
                    in [200, 204, 404],  # 404 = already completed/not found
                    "message": self._get_status_message(response.status_code),
                    "response_text": (
                        response.text[:200] if response.text else ""
                    ),  # Truncate long responses
                }

                self.cancel_results.append(result)

                # Log the result
                if result["success"]:
                    if response.status_code == 404:
                        LOGGER.info(f"Task {task_id}: Already completed or not found")
                    else:
                        LOGGER.info(f"Task {task_id}: Successfully cancelled")
                else:
                    LOGGER.error(
                        f"Task {task_id}: Failed to cancel (HTTP {response.status_code})"
                    )

            except requests.exceptions.RequestException as e:
                LOGGER.error(f"Network error cancelling task {task_id}: {e}")
                result = {
                    "task_id": task_id,
                    "status_code": None,
                    "success": False,
                    "message": f"Network error: {str(e)}",
                    "response_text": "",
                }
                self.cancel_results.append(result)
            except Exception as e:
                LOGGER.error(f"Unexpected error cancelling task {task_id}: {e}")
                result = {
                    "task_id": task_id,
                    "status_code": None,
                    "success": False,
                    "message": f"Unexpected error: {str(e)}",
                    "response_text": "",
                }
                self.cancel_results.append(result)

        return self.cancel_results

    def _get_status_message(self, status_code: int) -> str:
        """Get human-readable message for HTTP status codes."""
        status_messages = {
            200: "Successfully cancelled",
            204: "Successfully cancelled (no content)",
            401: "Unauthorized - check API key",
            403: "Forbidden - insufficient permissions",
            404: "Task not found or already completed",
            409: "Conflict - task may already be completed",
            500: "Server error",
        }
        return status_messages.get(status_code, f"HTTP {status_code}")

    def display_results(self):
        """Display cancellation results in a formatted table."""
        if not self.cancel_results:
            LOGGER.info("No tasks were processed")
            return

        # Create results table
        table = Table(box=box.ROUNDED)
        table.add_column("Task ID")
        table.add_column("Status")
        table.add_column("Message")

        successful_count = 0
        failed_count = 0

        for result in self.cancel_results:
            status_text = "[bold green]✓[/]" if result["success"] else "[bold red]✗[/]"

            # Keep full task ID for display
            table.add_row(result["task_id"], status_text, result["message"])

            if result["success"]:
                successful_count += 1
            else:
                failed_count += 1

        console.print()
        console.print("[bold]CANCELLATION RESULTS[/]")
        console.print(table)

        # Summary
        total_count = len(self.cancel_results)
        console.print(
            f"\nSummary: {successful_count}/{total_count} tasks processed successfully"
        )

        if failed_count > 0:
            console.print(
                f"[bold yellow]Warning: {failed_count} task(s) failed to cancel[/]"
            )
            console.print("\nFailed to cancel task URLs:")
            for result in self.cancel_results:
                if not result["success"]:
                    task_url = f"{parsed_opts.testing_farm.get('log_artifact_baseurl')}/{result['task_id']}"
                    console.print(f"  - {task_url}")


def main():
    """
    Main function to cancel Testing Farm tasks.

    Reuses the same argument parsing and UUID validation as report and rerun modules.
    """
    try:
        # Initialize the cancel handler
        cancel_handler = CancelJobs()

        # Check if running in dry-run mode
        if getattr(parsed_opts.cli_args, "dryrun", False):
            LOGGER.info("DRY RUN MODE - No tasks will actually be cancelled")
            print(f"Would cancel {len(cancel_handler.req_url_list)} task(s):")
            for task_url in cancel_handler.req_url_list:
                task_id = task_url.split("/")[-1]
                view_url = (
                    f"{parsed_opts.testing_farm.get('log_artifact_baseurl')}/{task_id}"
                )
                print(f"  - {view_url}")
            return

        # Cancel the tasks
        results = cancel_handler.cancel_tasks()

        # Display results
        cancel_handler.display_results()

        # Set exit code based on results
        failed_count = sum(1 for r in results if not r["success"])
        if failed_count > 0:

            raise ValidationError("Some cancellations failed")
        else:
            return

    except KeyboardInterrupt:
        LOGGER.info("Cancellation interrupted by user")

        raise UserAbort("Cancellation interrupted by user")
    except Exception as e:
        LOGGER.error(f"Unexpected error in cancel operation: {e}")

        raise EngeError("Unexpected error in cancel operation") from e


if __name__ == "__main__":
    main()
