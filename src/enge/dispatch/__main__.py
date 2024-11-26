#!/usr/bin/env python3
import logging
import sys

import requests

from enge.utils.globals import ARTIFACT_MAPPING
from enge.utils.opt_manager import parsed_opts
from .tf_send_request import SubmitTest

LOGGER = logging.getLogger(__name__)

compose_mapping = parsed_opts.tests_compose_mapping
tests_repo_base_url = parsed_opts.tests.get("git_url") or parsed_opts.project.get(
    "repo_url"
)
git_response = requests.get(tests_repo_base_url)

plans = parsed_opts.plans

if parsed_opts.cli_args.copr:
    artifact_type_alias = "copr"
    artifact_type = ARTIFACT_MAPPING["copr"]
    reference = [parsed_opts.copr_reference]
elif parsed_opts.cli_args.brew:
    artifact_type_alias = "brew"
    artifact_type = ARTIFACT_MAPPING["brew"]
    reference = [parsed_opts.brew_reference]
copr_pkg_name = parsed_opts.copr_api.get("package") or ""
copr_repo = parsed_opts.copr_api.get("repository") or ""

brew_pkg_name = parsed_opts.brew_api.get("package") or ""


def main():
    submit_test = SubmitTest()

    submit_test.api_key = parsed_opts.testing_farm.get("api_key")
    submit_test.tests_git_url = tests_repo_base_url
    submit_test.tests_git_branch = parsed_opts.tests.get("git_branch")
    submit_test.planfilter = parsed_opts.cli_args.planfilter
    submit_test.testfilter = parsed_opts.cli_args.testfilter
    submit_test.architecture = parsed_opts.cli_args.architecture
    submit_test.artifact_type = artifact_type
    submit_test.package = parsed_opts.project.get("name")
    submit_test.business_unit_tag = (
        parsed_opts.testing_farm.get("cloud_resources_tag") or None
    )
    submit_test.boot_method = "bios"
    if parsed_opts.cli_args.uefi:
        submit_test.boot_method = "uefi"
    submit_test.parallel_limit = (
        parsed_opts.cli_args.parallel_limit or parsed_opts.parallel_limit or None
    )
    submit_test.print_header = True

    if git_response.status_code == 404:
        LOGGER.critical(f"There is an issue with reaching the tests repository url.")
        LOGGER.critical(
            f"The response from the {tests_repo_base_url} returned status code of {git_response.status_code}."
        )
        sys.exit(99)

    # Exit if multiple plans requested with additional filter(s)
    if len(plans) > 1 and (
        parsed_opts.cli_args.planfilter or parsed_opts.cli_args.testfilter
    ):
        LOGGER.critical(
            "It is not advised to use testfilter or planfilter with multiple requested plans."
            " Please specify one plan with additional filters per request."
        )
        sys.exit(2)

    summary_count = 0

    for plan in plans:
        item = plan.rstrip("/")
        submit_test.plan = plan

        req_compose = compose_mapping.keys()
        if parsed_opts.cli_args.target:
            req_compose = parsed_opts.cli_args.target

        if parsed_opts.cli_args.copr:
            info = parsed_opts.cli_args.copr.get_info(
                copr_pkg_name, copr_repo, reference, req_compose, parsed_opts
            )
        else:
            info = parsed_opts.cli_args.brew.get_info(
                brew_pkg_name, reference, req_compose, parsed_opts
            )

        for build in info:
            submit_test.compose = build["compose"]
            submit_test.artifact_id = str(build["build_id"])
            submit_test.tmt_distro = build["distro"]
            req_header, req_payload = submit_test.build_payload()
            submit_test.send_request(req_payload, req_header)

            submit_test.print_header = False


if __name__ == "__main__":
    sys.exit(main())
