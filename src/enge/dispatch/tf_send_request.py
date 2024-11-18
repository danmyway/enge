#!/usr/bin/env python3
import json
import os
import time

import requests

from src.enge.utils.opt_manager import parsed_opts
from src.enge.utils import FormatText, get_datetime
import logging

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
        self.log_artifact_base_url = parsed_opts.testing_farm.get("log_artifacts_url")
        self.testing_farm_endpoint = parsed_opts.testing_farm.get("endpoint_url")
        self.request_status = None
        self.log_artifact_url = None
        self.dispatch_summary = None

    def record_task_ids(self, task_id):
        self.latest_tasks_file = parsed_opts.common.get("archive_tasks_latest")
        self.archive_tasks_default_path = os.path.expanduser(
            parsed_opts.common.get("archive_tasks_default")
        )
        self.archive_tasks_file = os.path.join(
            self.archive_tasks_default_path, self.archive_tasks_filename
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
        response_timeout = parsed_opts.wait or 20
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
        self.dispatch_summary = (
            f""
            f"~ SUMMARY ~~~~~~~~\n"
            f"   Targeted system:  {self.compose}\n"
            f"   Plan:             {self.plan}\n"
            f"   Test results:     {self.log_artifact_url}\n"
            f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
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

        if parsed_opts.dryrun:
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
            if parsed_opts.wait:
                self._response_watcher(self.log_artifact_url)
            else:
                print(self.dispatch_summary)
        except KeyError as ke:
            LOGGER.error(json.dumps(response.json(), indent=2, sort_keys=True))
