#!/usr/bin/env python3
import logging
import os
import sys
from datetime import datetime

from copr.v3 import BuildProxy

from src.enge.utils.opt_manager import parsed_opts
from src.enge.utils import FormatText

SESSION = BuildProxy({"copr_url": "https://copr.fedorainfracloud.org"})

LOGGER = logging.getLogger(__name__)

proj_name = parsed_opts.project.get("name") or ""
copr_name = parsed_opts.copr.get("package") or ""
proj_owner = parsed_opts.project.get("owner") or ""
copr_owner = parsed_opts.copr.get("owner") or ""
rpm_name = copr_name if copr_name else proj_name
rpm_owner = copr_owner if copr_name else proj_owner
owner_is_group = parsed_opts.copr.get("owner_is_group")
group = "g" if owner_is_group else ""
build_baseurl = os.path.join(
    "https://copr.fedorainfracloud.org/coprs",
    group,
    rpm_owner,
    rpm_name,
    "build",
)


def get_info(package, repository, reference, composes):
    """
    Get information about a COPR build for a specific package and reference.

    Args:
        package (str): The name of the package for which to get information.
        repository (str): The name of the Copr repository containing the package build.
        reference (str, int): The reference for the package build. This can be either a commit reference
                         (e.g., "master", "main") or a pull request ID (e.g., "pull/123").
        composes (list): A list of strings representing the target distributions for the COPR build.

    Returns:
        tuple: A tuple containing two elements:
            - A list of dictionaries containing build information for each target distribution.
            - The build reference used for selecting the COPR build.
    """
    owner = rpm_owner
    if owner_is_group:
        owner = "".join(("@", owner))
    info = []
    build_reference = reference[0]
    project_repo_baseurl = os.path.join(parsed_opts.project.get("repo_url"), "pull")
    pullrequest_url = os.path.join(project_repo_baseurl, build_reference[2:])
    if parsed_opts.reference:

        def _get_correct_build_list(build_ref=None):
            """
            Get a clean list of COPR builds that match the specified reference.

            Returns:
                list: A list of COPR builds that match the specified reference.
            """
            clean_build_list = []
            if build_ref not in ["master", "main"]:
                build_ref = str.upper(build_ref)
                LOGGER.info(
                    f"Requested build for testing triggered from: {pullrequest_url}"
                )
            LOGGER.info(
                f"Gathering the fedora-copr-build information for the referenced {build_ref}."
            )

            query = SESSION.get_list(owner, repository)

            for build_munch in query:
                if (
                    build_munch.state != "failed"
                    and build_munch.source_package["name"] == package
                    and build_munch.source_package["version"] is not None
                    and build_reference in build_munch.source_package["version"]
                ):
                    clean_build_list.append(build_munch)

            if not clean_build_list:
                LOGGER.warning(
                    FormatText.format_text(
                        f"No build for given reference {build_reference} found!",
                        text_col=FormatText.yellow,
                        bold=True,
                    )
                )
                LOGGER.warning(build_baseurl + "s")
            return clean_build_list

        for build_munch in _get_correct_build_list(build_reference):
            build = build_munch
            for build_info in get_build_dictionary(build, composes):
                info.append(build_info)
            # Break so just the latest is selected
            break

    elif parsed_opts.task_id:
        build = None
        LOGGER.info(
            f"Gathering the fedora-copr-build information for the referenced buildID {build_reference}."
        )
        build_munch = SESSION.get(build_reference)
        if build_munch.source_package["name"] != package:
            LOGGER.critical(
                f"There seems to be some mismatch with the given buildID {build_reference}!"
            )
            LOGGER.critical(
                f"The ID points to owner: {build_munch.ownername}, project: {build_munch.projectname}"
            )
            LOGGER.critical(f"Cowardly refusing to continue.")
            sys.exit(99)

        elif build_munch.state == "failed":
            LOGGER.critical(
                FormatText.format_text(
                    f"The build with the given ID {build_reference} reports as failed!",
                    text_col=FormatText.red,
                    bold=True,
                )
            )
            LOGGER.critical(
                FormatText.format_text(
                    "Please provide a valid build ID.",
                    text_col=FormatText.red,
                    bold=True,
                )
            )
            LOGGER.critical(
                FormatText.format_text("Exiting.", text_col=FormatText.red, bold=True)
            )
            sys.exit(99)

        else:
            build = build_munch

        for build_info in get_build_dictionary(build, composes):
            info.append(build_info)

    return info, build_reference


def get_build_dictionary(build, composes):
    """
    Get the dictionary containing build information for each target distribution.

    Args:
        build: The COPR build object.
        composes (list): A list of strings representing the target distributions for the COPR build.

    Returns:
        list: A list of dictionaries containing build information for each target distribution.
    """
    build_info = []

    # In case of a race condition occurs and referenced build is in a running state,
    # thus uninstallable, raise a warning
    if build.state == "running":
        LOGGER.warning(
            f"There is currently {build.state} build task, consider waiting for completion."
        )
        LOGGER.warning(
            f"See the project's builds dashboard: {build_baseurl[:-1]}" + "s/"
        )
    LOGGER.info(
        "Looking for a buildID for the %s version %s.",
        build.source_package["name"],
        build.source_package["version"],
    )
    timestamp_str = build.source_package["version"].split(".")[3]
    timestamp_format = "%Y%m%d%H%M%S"
    build_time = datetime.strptime(timestamp_str[0:13], timestamp_format)

    LOGGER.info(f"Last build found built at {build_time}")
    LOGGER.info(f"Build URL: {os.path.join(build_baseurl, str(build.id))}")
    for distro in composes:
        copr_info_dict = {
            "build_id": None,
            "compose": parsed_opts.tests_compose_mapping.get(distro).get("compose"),
            "chroot": None,
            "distro": parsed_opts.tests_compose_mapping.get(distro).get("distro"),
        }
        for chroot in build.chroots:
            if parsed_opts.tests_compose_mapping.get(distro).get("chroot") == chroot:
                copr_info_dict["chroot"] = parsed_opts.tests_compose_mapping.get(
                    distro
                ).get("chroot")
                copr_info_dict["build_id"] = f"{build.id}:{chroot}"
                buildid = FormatText.format_text(build.id, bold=True)
                compose = FormatText.format_text(copr_info_dict["compose"], bold=True)
                LOGGER.info(
                    f"Assigned the copr buildID {buildid} for testing on {compose} to the test batch."
                )

                build_info.append(copr_info_dict)

    return build_info
