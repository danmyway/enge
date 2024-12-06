# #!/usr/bin/env python3
import os
import re
import sys
from datetime import datetime
from logging import getLogger

import koji
from copr.v3 import BuildProxy, CoprNoResultException
from copr.v3 import exceptions as coprexcept

from . import FormatText

LOGGER = getLogger(__name__)


class CoprRef:
    def __init__(self, ref_arg):
        self.ref = ref_arg
        self.build_id = None
        self.build_reference = None
        self.session = BuildProxy({"copr_url": "https://copr.fedorainfracloud.org"})
        self.copr_build_baseurl = None
        self.compose_mapping = None
        try:
            self.build_id = int(ref_arg[0])
        except (ValueError, TypeError):
            self.build_reference = ref_arg

    def get_info(self, package, repository, reference, composes, options):
        """ """
        try:
            self.build_id = int(reference[0])
        except (ValueError, TypeError):
            self.build_reference = reference

        if self.build_reference == [None]:
            LOGGER.warning("No specific value was provided for the copr build query!")
            LOGGER.warning(
                "The latest build from the project will be used as a testing artifact!"
            )
        owner = options.copr_api.get("owner") or options.project.get("owner")
        rpm_name = options.copr_api.get("package") or options.project.get("name")
        owner_is_group = options.copr_api.get("owner_is_group") or False
        copr_owner = owner
        if owner_is_group:
            copr_owner = "".join(("@", owner))
        group = "g" if owner_is_group else ""
        info = []
        if not self.ref:
            reference = [options.copr_api.get("build_reference")]
        build_reference = reference[0] if isinstance(reference, list) else reference
        self.copr_build_baseurl = os.path.join(
            "https://copr.fedorainfracloud.org/coprs",
            group,
            owner,
            rpm_name,
            "build",
        )
        self.compose_mapping = options.tests_compose_mapping
        targets = options.cli_args.target or self.compose_mapping.keys()
        for target in targets:
            if target not in self.compose_mapping.keys():
                LOGGER.critical(
                    f"Requested target {target} not found in the configured mapping!"
                )
                LOGGER.critical(
                    f"The configured mapping contains the following targets: {[i for i in self.compose_mapping.keys()]}"
                )
                sys.exit(2)

        if self.build_reference:

            def _get_correct_build_list(build_ref=None):
                """
                Get a clean list of COPR builds that match the specified reference.

                Returns:
                    list: A list of COPR builds that match the specified reference.
                """
                clean_build_list = []
                reference_pattern = fr".*{build_reference}(\..*|$)"
                message = f"Gathering the fedora-copr-build information for the referenced {build_ref}."
                # If no value is provided for the --copr argument nor is set in the config,
                # query for the latest build in the project
                if self.build_reference == [None]:
                    message = f"Gathering the fedora-copr-build information for the project's latest copr build."
                LOGGER.info(message)
                try:
                    query = self.session.get_list(copr_owner, repository)
                except CoprNoResultException as no_copr:
                    LOGGER.critical(
                        "There seems to be an issue with the copr_api configuration."
                    )
                    if not owner_is_group:
                        LOGGER.critical(
                            "Please check, that the owner, owner_is_group and package options are set correctly."
                        )
                    LOGGER.debug(f"{type(no_copr).__name__}: {no_copr}")
                    sys.exit(99)

                for build_munch in query:
                    if (
                        build_munch.state != "failed"
                        and build_munch.source_package["name"] == package
                        and build_munch.source_package["version"] is not None
                        and re.match(
                            reference_pattern, build_munch.source_package["version"]
                        )
                    ):
                        clean_build_list.append(build_munch)

                if not clean_build_list:
                    LOGGER.warning(
                        f"No build for given reference {build_reference} found!"
                    )
                    LOGGER.warning(self.copr_build_baseurl + "s")
                return clean_build_list

            for build_munch in _get_correct_build_list(build_reference):
                build = build_munch
                for build_info in self.get_build_dictionary(build, composes):
                    info.append(build_info)
                # Break so just the latest is selected
                break

        elif self.build_id:
            LOGGER.info(
                f"Gathering the fedora-copr-build information for the referenced buildID {build_reference}."
            )
            try:
                build_munch = self.session.get(build_reference)
            except coprexcept.CoprNoResultException as no_copr:
                LOGGER.critical(f"{type(no_copr).__name__}: {no_copr}")
                LOGGER.critical(f"Cowardly refusing to continue.")
                sys.exit(99)

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
                    FormatText.format_text(
                        "Exiting.", text_col=FormatText.red, bold=True
                    )
                )
                sys.exit(99)

            else:
                build = build_munch

            for build_info in self.get_build_dictionary(build, composes):
                info.append(build_info)
        else:
            LOGGER.critical("No build artifact reference nor ID provided!")
            LOGGER.critical(
                "Please provide a reference for the build installation "
                "either through the command line or the config file."
            )

        return info

    def get_build_dictionary(self, build, composes):
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
                f"There is currently {build.state} build task, please consider waiting for completion."
            )
            LOGGER.warning(
                f"See the project's builds dashboard: {self.copr_build_baseurl}" + "s/"
            )
            while True:
                user_response = input(
                    "Do you wish to continue with an older build? (y/n) "
                )
                if user_response.lower() == "y":
                    LOGGER.info("Moving on with an older build.")
                    break
                elif user_response.lower() == "n":
                    LOGGER.info("Exiting.")
                    sys.exit(0)
                else:
                    LOGGER.warning("Invalid response, please enter 'y' or 'n'. ")

        LOGGER.info(
            "Looking for a buildID of the %s version %s.",
            build.source_package["name"],
            build.source_package["version"],
        )
        timestamp_str = build.source_package["version"].split(".")[3]
        timestamp_format = "%Y%m%d%H%M%S"
        build_time = datetime.strptime(timestamp_str[0:13], timestamp_format)

        LOGGER.debug(f"Last build found was built at {build_time}")
        LOGGER.debug(
            f"Build URL: {os.path.join(self.copr_build_baseurl, str(build.id))}"
        )

        for distro in composes:
            copr_info_dict = {
                "build_id": None,
                "compose": self.compose_mapping.get(distro).get("compose"),
                "chroot": None,
                "distro": self.compose_mapping.get(distro).get("distro"),
            }
            for chroot in build.chroots:
                if self.compose_mapping.get(distro).get("chroot") == chroot:
                    copr_info_dict["chroot"] = self.compose_mapping.get(distro).get(
                        "chroot"
                    )
                    copr_info_dict["build_id"] = f"{build.id}:{chroot}" or None
                    buildid = FormatText.format_text(build.id, bold=True)
                    compose = FormatText.format_text(
                        copr_info_dict["compose"], bold=True
                    )
                    LOGGER.info(
                        f"The copr buildID {buildid} for testing on {compose} was assigned for the test job."
                    )

                    build_info.append(copr_info_dict)

        return build_info


class BrewRef:
    def __init__(self, ref_arg):
        self.ref = ref_arg
        self.task_id = None
        self.build_reference = None
        self.session = None
        self.compose_mapping = None
        self.epel_composes = None
        try:
            self.task_id = int(ref_arg[0])
        except (ValueError, TypeError):
            self.build_reference = ref_arg

    def get_info(self, package, reference, composes, options):
        """
        Get information about the package and its associated composes for testing.

        Args:
            package (str): The name of the package.
            reference (list): List of references for the package.
            composes (list): List of composes to check.

        Returns:
            tuple: A tuple containing a list of dictionaries with build information and the build reference.
        """
        try:
            self.task_id = int(reference[0])
        except (ValueError, TypeError):
            self.build_reference = reference
        brew_dict = {}
        info = []
        compose_selection = []

        self.session = koji.ClientSession(options.brew_api.get("session_url"))
        self.session.gssapi_login()

        self.compose_mapping = options.tests_compose_mapping
        if not self.compose_mapping:
            LOGGER.critical("Compose mapping not found!")
            LOGGER.critical(
                "Please validate, that you have configured the compose mapping in the configuration file."
            )

        self.epel_composes = {
            f"rhel-{version}": [
                entry.get("compose")
                for entry in self.compose_mapping.values()
                if f"epel-{version}" in entry.get("chroot", "")
            ]
            for version in ["9", "10"]
        }

        for compose in composes:
            compose_selection.append(self.compose_mapping.get(compose).get("compose"))

        for build_reference, volume_name in self.get_brew_task_and_compose(
            package, reference, self.session, options
        ).items():
            brew_dict[build_reference] = list(
                set(compose_selection).intersection(self.epel_composes.get(volume_name))
            )

        for build_reference in brew_dict:
            for distro in brew_dict[build_reference]:
                brew_info_dict = {
                    "build_id": build_reference,
                    "compose": compose,
                    "chroot": None,
                    "distro": None,
                }
                LOGGER.info(
                    f"The brew build {build_reference} for testing on {distro} was assigned for the test job."
                )
                # Assign correct SOURCE_RELEASE and TARGET_RELEASE
                brew_info_dict["build_id"] = build_reference
                brew_info_dict["compose"] = distro
                for compose_choice in composes:
                    if (
                        self.compose_mapping.get(compose_choice).get("compose")
                        == distro
                    ):
                        brew_info_dict["chroot"] = self.compose_mapping.get(
                            compose_choice
                        ).get("chroot")
                        brew_info_dict["distro"] = self.compose_mapping.get(
                            compose_choice
                        ).get("distro")
                info.append(brew_info_dict.copy())

        return info

    def get_brew_task_and_compose(self, package, reference, session, options):
        """
        Get the Brew build task IDs and associated composes for a given package and reference.

        Args:
            package (str): The name of the package.
            reference (str, int): List of references for the package.

        Returns:
            dict: A dictionary with Brew task IDs as keys and associated composes as values.
        """
        query = session.listBuilds(prefix=package)
        brewbuild_baseurl = options.brew_api.get("taskid_url")
        tasks = []
        if self.build_reference:
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

        elif self.task_id:
            LOGGER.info(
                f"Gathering the brew build information for the {package} taskID {reference}."
            )
            tasks = reference
            volume_names = [
                build_info.get("volume_name")
                for task in tasks
                for build_info in query
                if int(task) == build_info.get("task_id")
            ]
        else:
            LOGGER.critical("No build artifact reference nor ID provided!")
            LOGGER.critical(
                "Please provide a reference for the build installation "
                "either through the command line or the config file."
            )

        task_ids = list(set(tasks))
        if not task_ids:
            LOGGER.warning(
                f"No suitable tasks found for the provided reference {reference}."
            )
            LOGGER.warning("Please validate that the reference is correct.")
            sys.exit(99)

        for i in range(len(task_ids)):
            LOGGER.info(
                f"Available build task ID {task_ids[i]} for {volume_names[i]} assigned."
            )
            LOGGER.info(f"LINK: {brewbuild_baseurl}{task_ids[i]}")

        return {task_ids[i]: volume_names[i] for i in range(len(task_ids))}
