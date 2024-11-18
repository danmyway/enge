#!/usr/bin/env python3
import ast
import importlib
import logging
import sys

import requests

from src.enge.utils.opt_manager import parsed_opts
from src.enge.utils.globals import ARTIFACT_MAPPING
from src.enge.utils import FormatText
from .tf_send_request import SubmitTest

LOGGER = logging.getLogger(__name__)


def main():
    compose_mapping = ast.literal_eval(parsed_opts.tests.get("composes"))
    copr_repo = copr_package = parsed_opts.project.get("name")
    tests_repo_base_url = parsed_opts.tests.get("git_url") or parsed_opts.project.get(
        "repo_url"
    )
    git_response = requests.get(tests_repo_base_url)
    reference = None
    if parsed_opts.reference:
        reference = parsed_opts.reference
    elif parsed_opts.task_id:
        reference = parsed_opts.task_id
    plans = parsed_opts.plans

    submit_test = SubmitTest()

    submit_test.api_key = parsed_opts.testing_farm.get("api_key")
    submit_test.tests_git_url = tests_repo_base_url
    submit_test.tests_git_branch = parsed_opts.tests.get("git_branch")
    submit_test.planfilter = parsed_opts.planfilter
    submit_test.testfilter = parsed_opts.testfilter
    submit_test.architecture = parsed_opts.architecture
    submit_test.artifact_type = ARTIFACT_MAPPING[parsed_opts.artifact_type]
    submit_test.package = parsed_opts.project.get("name")
    submit_test.business_unit_tag = parsed_opts.testing_farm.get("cloud_resources_tag")
    submit_test.boot_method = "bios"
    if parsed_opts.uefi:
        submit_test.boot_method = "uefi"
    submit_test.parallel_limit = parsed_opts.parallel_limit

    if git_response.status_code == 404:
        LOGGER.critical(f"There is an issue with reaching the tests repository url.")
        LOGGER.critical(
            f"The response from the {tests_repo_base_url} returned status code of {git_response.status_code}."
        )
        sys.exit(99)

    try:
        artifact_module_name = parsed_opts.artifact_type + "_api"
        artifact_module = importlib.import_module(
            "src.enge.dispatch." + artifact_module_name
        )
    except ImportError as ie:
        LOGGER.debug(ie)
        LOGGER.error(f"Artifact_module could not be loaded!")
        return 99

    # Exit if multiple plans requested with additional filter(s)
    if len(plans) > 1 and (parsed_opts.planfilter or parsed_opts.testfilter):
        LOGGER.critical(
            "It is not advised to use testfilter or planfilter with multiple requested plans."
            " Please specify one plan with additional filters per request."
        )
        sys.exit(2)

    for plan in plans:
        item = plan.rstrip("/")
        submit_test.plan = plan

        req_compose = compose_mapping.keys()
        if parsed_opts.target:
            req_compose = parsed_opts.target

        info, build_reference = artifact_module.get_info(
            copr_package, copr_repo, reference, req_compose
        )

        for build in info:
            submit_test.compose = build["compose"]
            submit_test.artifact_id = str(build["build_id"])
            submit_test.tmt_distro = build["distro"]

            if not parsed_opts.dryrun:
                LOGGER.info(
                    f"Sending a test plan "
                    + FormatText.format_text(
                        item.split("/")[-1], text_col=FormatText.blue, bold=True
                    )
                    + f" for {parsed_opts.artifact_type} build {build_reference} for {build['compose']} to the Testing Farm.\n"
                )

                req_header, req_payload = submit_test.build_payload()
                submit_test.assess_summary_message()
                submit_test.send_request(req_payload, req_header)
            else:
                print(submit_test.assess_summary_message())


if __name__ == "__main__":
    sys.exit(main())
