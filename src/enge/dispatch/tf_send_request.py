#!/usr/bin/env python3
import json
import logging
import os
import time

import requests

from enge.utils import FormatText, get_datetime
from enge.utils.globals import TESTING_FARM_ENDPOINT, LOG_ARTIFACT_BASE_URL
from enge.utils.opt_manager import parsed_opts

LOGGER = logging.getLogger(__name__)


class SubmitTest:
    def __init__(self):
        self.api_key = None
        self.tests_git_url = None
        self.tests_git_branch = None
        self.plan = None
        self.planfilter = None
        self.testfilter = None
        self.architecture = None
        self.compose = None
        self.artifact_id = None
        self.artifact_type = None
        self.package = None
        self.business_unit_tag = None
        self.tmt_distro = None
        self.boot_method = None
        self.parallel_limit = None
        self.authorization_header = {}
        self.payload_raw = {}
        self.latest_tasks_file = None
        self.archive_tasks_default_path = None
        self.archive_tasks_file = None
        self.datetime_stamp = get_datetime()
        self.archive_tasks_filename = f"enge_jobs_archive_{self.datetime_stamp}"
        self.task_id = None
        self.log_artifact_base_url = LOG_ARTIFACT_BASE_URL
        self.testing_farm_endpoint = TESTING_FARM_ENDPOINT
        self.request_status = None
        self.log_artifact_url = None
        self.dispatch_summary = None
        self.print_header = None
        self.tag = parsed_opts.cli_args.tag

    def record_task_ids(self, task_id):
        self.latest_tasks_file = parsed_opts.archive_tasks_latest
        self.archive_tasks_default_path = parsed_opts.archive_tasks_default
        self.archive_tasks_file = os.path.join(
            self.archive_tasks_default_path, self.archive_tasks_filename
        )
        self.archive_tasks_file = (
            ".".join([self.archive_tasks_file] + parsed_opts.cli_args.tag)
            if self.tag
            else self.archive_tasks_file
        )

        def _handle_archive_files():
            if os.path.exists(self.latest_tasks_file):
                os.unlink(self.latest_tasks_file)
            if not os.path.exists(self.archive_tasks_default_path):
                os.makedirs(self.archive_tasks_default_path)

        _handle_archive_files()

        latest_jobs_file = open(self.latest_tasks_file, "a")
        latest_jobs_archive = open(self.archive_tasks_file, "a")
        latest_jobs_file.write(f"{task_id}\n")
        latest_jobs_archive.write(f"{task_id}\n")

    def build_payload(self):
        # Payload documentation > https://testing-farm.gitlab.io/api/#operation/requestsPost
        self.authorization_header = {"Authorization": f"Bearer {self.api_key}"}
        self.payload_raw = {
            "test": {
                "fmf": {
                    "url": self.tests_git_url,
                    "ref": self.tests_git_branch,
                    "name": self.plan,
                    "plan_filter": self.planfilter,
                    "test_filter": self.testfilter,
                }
            },
            "environments": [
                {
                    "arch": self.architecture,
                    "os": {"compose": self.compose},
                    "artifacts": [
                        {
                            "id": self.artifact_id,
                            "type": self.artifact_type,
                            "packages": [self.package],
                        }
                    ],
                    "settings": {
                        "provisioning": {
                            "tags": {"BusinessUnit": self.business_unit_tag},
                        }
                    },
                    "tmt": {
                        "context": {
                            "distro": self.tmt_distro,
                            "arch": self.architecture,
                            "boot_method": self.boot_method,
                        }
                    },
                    "hardware": {
                        "boot": {
                            "method": self.boot_method,
                        }
                    },
                }
            ],
            "settings": {"pipeline": {"parallel-limit": self.parallel_limit}},
        }

        return self.authorization_header, self.payload_raw

    def _response_watcher(self, log_artifact_url):
        response_timeout = parsed_opts.cli_args.wait
        clear_line = "\x1b[2K"
        while True:
            response = requests.get(log_artifact_url)
            response_status = response.status_code
            response_message = response.reason
            print(end=clear_line)
            print(
                FormatText.format_text(
                    f"Waiting for a successful response for {response_timeout} seconds. ",
                    bold=True,
                ),
                f"Current response is: {response_status} {response_message}",
                end="\r",
                flush=True,
            )
            time.sleep(1)
            response_timeout -= 1
            if response_status > 200 and response_timeout == 0:
                print(end=clear_line)
                print(
                    f"{FormatText.bold}Processing the request takes longer this time.\n"
                    f"The request response is still {response_status} {response_message}\n"
                    f"Here is the link for the requested job, try refreshing the website after a couple of minutes.\n",
                    flush=True,
                )
                print(self.dispatch_summary)
                break
            elif response_status == 200:
                print(f"\nResponse successful!\n")
                print(self.dispatch_summary)
                break

    def assess_summary_message(self):
        print_header = self.print_header
        summary_header = "\n~ SUMMARY ~~~~~~~~\n" if print_header else ""
        self.dispatch_summary = (
            ""
            + FormatText.format_text(f"{summary_header}", bold=True)
            + f"   Targeted system:  {self.compose}\n"
            f"   Plan:             {self.plan}\n"
            f"   Test results:     {self.log_artifact_url}\n"
            "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
        )

        def _handle_dry_run(payload_raw=self.build_payload()):
            from pygments import highlight, lexers, formatters

            print_payload_dryrun_msg = f"\nDRY RUN  | Printing out requested payload:"
            payload_formatted = json.dumps(payload_raw, indent=4)
            colorful_json = highlight(
                payload_formatted, lexers.JsonLexer(), formatters.TerminalFormatter()
            )
            self.dispatch_summary = f"{print_payload_dryrun_msg}\n{colorful_json}"
            return self.dispatch_summary

        if parsed_opts.cli_args.dryrun:
            self.dispatch_summary = _handle_dry_run()

        return self.dispatch_summary

    def send_request(self, payload_raw, header):
        try:
            response = requests.post(
                self.testing_farm_endpoint, json=payload_raw, headers=header
            )
            task_id = response.json()["id"]
            self.log_artifact_url = f"{self.log_artifact_base_url}/{task_id}"
            self.dispatch_summary = self.assess_summary_message()
            if parsed_opts.cli_args.action != "rerun" and parsed_opts.cli_args.wait:
                self._response_watcher(self.log_artifact_url)
            else:
                print(self.dispatch_summary)

            self.record_task_ids(task_id)
        except KeyError as ke:
            LOGGER.error(json.dumps(response.json(), indent=2, sort_keys=True))
