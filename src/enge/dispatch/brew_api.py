#!/usr/bin/env python3
import logging

import koji

from src.enge.utils.opt_manager import parsed_opts

LOGGER = logging.getLogger(__name__)

# Initialize the Koji client session
SESSION = koji.ClientSession(parsed_opts.brew.get("session_url"))
SESSION.gssapi_login()

COMPOSE_MAPPING = parsed_opts.tests_compose_mapping

EPEL_COMPOSES = {
    "rhel-9": [
        COMPOSE_MAPPING.get(i).get("compose")
        for i in COMPOSE_MAPPING
        if "epel-9" in COMPOSE_MAPPING.get(i).get("chroot")
    ],
    "rhel-10": [
        COMPOSE_MAPPING.get(i).get("compose")
        for i in COMPOSE_MAPPING
        if "epel-10" in COMPOSE_MAPPING.get(i).get("chroot")
    ],
}

BREWBUILD_BASEURL = parsed_opts.brew.get("build_baseurl")


def get_brew_task_and_compose(package, reference):
    """
    Get the Brew build task IDs and associated composes for a given package and reference.

    Args:
        package (str): The name of the package.
        reference (str, int): List of references for the package.

    Returns:
        dict: A dictionary with Brew task IDs as keys and associated composes as values.
    """
    query = SESSION.listBuilds(prefix=package)
    tasks = None
    if parsed_opts.reference:
        LOGGER.info(
            f"Gathering the brew build information for the {package} version {reference}."
        )
        # Append the list of TaskID's collected from the listBuilds query
        tasks = [
            build_info.get("task_id")
            for build_info in query
            for ref in reference
            if ref in build_info.get("nvr")
        ]
        volume_names = [
            build_info.get("volume_name")
            for build_info in query
            for ref in reference
            if ref in build_info.get("nvr")
        ]
    elif parsed_opts.task_id:
        LOGGER.info(
            f"Gathering the brew build information for the {package} task ID {reference}."
        )
        tasks = reference
        volume_names = [
            build_info.get("volume_name")
            for task in tasks
            for build_info in query
            if int(task) == build_info.get("task_id")
        ]

    LOGGER.info("Checking for available builds.")
    if not tasks:
        LOGGER.warning(
            f"No suitable tasks found for the provided reference {reference}."
        )
        LOGGER.warning("Please validate that the reference is valid.")
    for i in range(len(tasks)):
        LOGGER.info(
            f"Available build task ID {tasks[i]} for {volume_names[i]} assigned."
        )
        LOGGER.info(f"LINK: {BREWBUILD_BASEURL}{tasks[i]}")

    return {tasks[i]: volume_names[i] for i in range(len(tasks))}


def get_info(
    package, repository, reference, composes, source_release=None, target_release=None
):
    """
    Get information about the package and its associated composes for testing.

    Args:
        package (str): The name of the package.
        repository (str): The repository name.
        reference (list): List of references for the package.
        composes (list): List of composes to check.
        source_release (str, optional): The source release version. Defaults to None.
        target_release (str, optional): The target release version. Defaults to None.

    Returns:
        tuple: A tuple containing a list of dictionaries with build information and the build reference.
    """
    brew_dict = {}
    info = []
    compose_selection = []
    build_reference = None

    for compose in composes:
        compose_selection.append(COMPOSE_MAPPING.get(compose).get("compose"))

    for build_reference, volume_name in get_brew_task_and_compose(
        package, reference
    ).items():
        brew_dict[build_reference] = list(
            set(compose_selection).intersection(EPEL_COMPOSES.get(volume_name))
        )

    for build_reference in brew_dict:
        for distro in brew_dict[build_reference]:
            brew_info_dict = {
                "build_id": build_reference,
                "compose": compose,
                "chroot": None,
                "distro": None,
                "source_release": None,
                "target_release": None,
            }
            LOGGER.info(
                f"Assigning build id {build_reference} for testing on {distro} to test batch."
            )
            # Assign correct SOURCE_RELEASE and TARGET_RELEASE
            brew_info_dict["build_id"] = build_reference
            brew_info_dict["compose"] = distro
            brew_info_dict["source_release"] = source_release
            brew_info_dict["target_release"] = target_release
            for compose_choice in composes:
                if COMPOSE_MAPPING.get(compose_choice).get("compose") == distro:
                    brew_info_dict["chroot"] = COMPOSE_MAPPING.get(compose_choice).get(
                        "chroot"
                    )
                    brew_info_dict["distro"] = COMPOSE_MAPPING.get(compose_choice).get(
                        "distro"
                    )
            info.append(brew_info_dict.copy())

    return info, build_reference
