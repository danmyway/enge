# #!/usr/bin/env python3
import os
import re
import sys
from datetime import datetime
from logging import getLogger
from typing import List, Optional, Dict, Tuple, Any

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
        self.artifact_ids: Optional[List[str]] = None  # For multiple artifacts
        self.packages: Optional[List[str]] = None  # For multiple packages
        try:
            self.build_id = int(ref_arg[0])
        except (ValueError, TypeError):
            self.build_reference = ref_arg

    def get_info(self, packages, repo, reference, composes, options):
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
        # Get the effective package name (handle both string and list)
        if isinstance(packages, list):
            package = packages[0] if packages else None
        else:
            package = packages
        repository = repo  # Keep the original name for compatibility

        owner = options.copr_api.get("owner") or options.project.get("owner")
        rpm_name = options.copr_api.get("package") or options.project.get("name")
        owner_is_group = options.copr_api.get("owner_is_group") or False
        copr_owner = owner
        if owner_is_group:
            copr_owner = "".join(("@", owner))
        group = "g" if owner_is_group else ""
        info = []
        if not self.ref:
            reference = [options.copr_api.get("build_references")]
        build_reference = reference[0] if isinstance(reference, list) else reference
        group_str = group if group is not None else ""
        owner_str = owner if owner is not None else ""
        rpm_name_str = rpm_name if rpm_name is not None else ""
        self.copr_build_baseurl = os.path.join(
            "https://copr.fedorainfracloud.org/coprs",
            group_str,
            owner_str,
            rpm_name_str,
            "build",
        )
        # Use source compose directly from options
        source_compose = options.source_spec["compose_name"]

        # For the new system, we use the source compose directly

        if self.build_reference:

            def _get_correct_build_list(build_ref=None):
                """
                Get a clean list of COPR builds that match the specified reference.

                Returns:
                    list: A list of COPR builds that match the specified reference.
                """
                clean_build_list = []
                reference_pattern = rf".*{build_reference}(\..*|$)"
                message = f"Gathering the fedora-copr-build information for the referenced {build_ref}."
                # If no value is provided for the --copr argument nor is set in the config,
                # query for the latest build in the project
                if self.build_reference == [None]:
                    message = "Gathering the fedora-copr-build information for the project's latest copr build."
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
                    if isinstance(build_munch, list):
                        if build_munch:
                            build_munch = build_munch[0]
                        else:
                            continue
                    # Only add builds that match the reference, are not failed, and have the correct package/version
                    if (
                        hasattr(build_munch, "state")
                        and build_munch.state != "failed"
                        and hasattr(build_munch, "source_package")
                        and build_munch.source_package.get("name") == package
                        and build_munch.source_package.get("version") is not None
                        and re.match(
                            reference_pattern,
                            build_munch.source_package.get("version", ""),
                        )
                    ):
                        clean_build_list.append(build_munch)

                if not clean_build_list:
                    LOGGER.warning(
                        f"No build for given reference {build_reference} found!"
                    )
                    baseurl_str = (
                        str(self.copr_build_baseurl)
                        if self.copr_build_baseurl is not None
                        else ""
                    )
                    LOGGER.warning(baseurl_str + "s")
                return clean_build_list

            for build_munch in _get_correct_build_list(build_reference):
                if isinstance(build_munch, list):
                    if build_munch:
                        build_munch = build_munch[0]
                    else:
                        continue
                build = build_munch
                for build_info in self.get_build_dictionary(build, composes):
                    info.append(build_info)
                break

        elif self.build_id:
            LOGGER.info(
                f"Gathering the fedora-copr-build information for the referenced buildID {build_reference}."
            )
            try:
                build_munch = self.session.get(build_reference)
            except coprexcept.CoprNoResultException as no_copr:
                LOGGER.critical(f"{type(no_copr).__name__}: {no_copr}")
                LOGGER.critical("Cowardly refusing to continue.")
                sys.exit(99)

            if isinstance(build_munch, list):
                if build_munch:
                    build_munch = build_munch[0]
                else:
                    return
            if (
                hasattr(build_munch, "source_package")
                and build_munch.source_package.get("name") != package
            ):
                LOGGER.critical(
                    f"There seems to be some mismatch with the given buildID {build_reference}!"
                )
                # Fix: Check for ownername and projectname attributes
                ownername = getattr(build_munch, "ownername", "unknown")
                projectname = getattr(build_munch, "projectname", "unknown")
                LOGGER.critical(
                    f"The ID points to owner: {ownername}, project: {projectname}"
                )
                LOGGER.critical("Cowardly refusing to continue.")
                sys.exit(99)

            elif hasattr(build_munch, "state") and build_munch.state == "failed":
                LOGGER.critical(
                    FormatText.format_text(
                        f"The build with the given ID {build_reference} reports as failed!",
                        text_col=FormatText.RED,
                        bold=True,
                    )
                )
                LOGGER.critical(
                    FormatText.format_text(
                        "Please provide a valid build ID.",
                        text_col=FormatText.RED,
                        bold=True,
                    )
                )
                LOGGER.critical(
                    FormatText.format_text(
                        "Exiting.", text_col=FormatText.RED, bold=True
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

        def get_first_non_list(obj):
            if isinstance(obj, list):
                if obj:
                    return obj[0]
                else:
                    return None
            return obj

        build_obj = get_first_non_list(build)
        if build_obj is None:
            return build_info

        build_obj = get_first_non_list(build_obj)
        if build_obj is None:
            return build_info
        if hasattr(build_obj, "state") and build_obj.state == "running":
            LOGGER.warning(
                f"There is currently {build_obj.state} build task, please consider waiting for completion."
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

        build_obj = get_first_non_list(build_obj)
        if build_obj is None:
            return build_info
        if hasattr(build_obj, "source_package"):
            package_name = build_obj.source_package.get("name", "unknown")
            package_version = build_obj.source_package.get("version", "unknown")
        else:
            package_name = "unknown"
            package_version = "unknown"
        LOGGER.info(
            "Looking for a buildID of the %s version %s.",
            package_name,
            package_version,
        )
        if isinstance(package_version, str) and len(package_version.split(".")) > 3:
            timestamp_str = package_version.split(".")[3]
            timestamp_format = "%Y%m%d%H%M%S"
            build_time = datetime.strptime(timestamp_str[0:13], timestamp_format)
            LOGGER.debug(f"Last build found was built at {build_time}")
        else:
            build_time = None
            LOGGER.debug("Could not parse build time from version string.")
        LOGGER.debug(
            f"Build URL: {os.path.join(str(self.copr_build_baseurl), str(getattr(build_obj, 'id', ''))) }"
        )

        for distro in composes:
            copr_info_dict = {
                "build_id": None,
                "compose": distro,
                "chroot": None,
                "distro": distro,
            }
            build_obj = get_first_non_list(build_obj)
            if build_obj is None:
                continue
            if hasattr(build_obj, "chroots"):
                for chroot in build_obj.chroots:
                    copr_info_dict["chroot"] = chroot
                    copr_info_dict["build_id"] = f"{build_obj.id}:{chroot}" or None
                    package_name = build_obj.source_package.get("name", "unknown")
                    package_version = build_obj.source_package.get("version", "unknown")
                    copr_info_dict["nvr"] = f"{package_name}-{package_version}"
                    copr_info_dict["package"] = package_name
                    buildid = FormatText.format_text(build_obj.id, bold=True)
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
        self.artifact_ids: Optional[List[str]] = None  # For multiple artifacts
        self.packages: Optional[List[str]] = None  # For multiple packages
        try:
            self.task_id = int(ref_arg[0])
        except (ValueError, TypeError):
            self.build_reference = ref_arg

    @staticmethod
    def _parse_package_name_from_nvr(nvr: str) -> Optional[str]:
        """
        Parse package name from NVR (Name-Version-Release) format.

        Args:
            nvr: The NVR string (e.g., 'leapp-0.16.0-1.el8')

        Returns:
            Package name or None if parsing fails

        Examples:
            'leapp-0.16.0-1.el8' -> 'leapp'
            'python3-leapp-0.16.0-1.el8' -> 'python3-leapp'
            'invalid' -> None
        """
        if not nvr or not isinstance(nvr, str):
            return None

        # Split by hyphens
        parts = nvr.split("-")

        # NVR must have at least 3 parts (name, version, release)
        if len(parts) < 3:
            return None

        # The last part is release, second-to-last is version
        # Everything before that is the package name
        try:
            # Validate that the last two parts look like version-release
            version = parts[-2]

            # Basic validation: version should contain digits or dots
            if not re.search(r"[\d.]", version):
                return None

            # Package name is everything except the last two parts
            package_name = "-".join(parts[:-2])

            if not package_name:
                return None

            return package_name

        except (IndexError, AttributeError):
            return None

    @staticmethod
    def _validate_reference_format(ref: str) -> Tuple[bool, str]:
        """
        Validate that the reference is either a task ID (integer) or a valid NVR.

        Args:
            ref: The reference string to validate

        Returns:
            Tuple of (is_valid, reference_type) where reference_type is 'task_id' or 'nvr'
        """
        if not ref or not isinstance(ref, str):
            return False, "invalid"

        # Check if it's a task ID (integer)
        try:
            int(ref)
            return True, "task_id"
        except ValueError:
            pass

        # Check if it's a valid NVR format
        package_name = BrewRef._parse_package_name_from_nvr(ref)
        if package_name:
            return True, "nvr"

        return False, "invalid"

    @staticmethod
    def _get_expected_volume_name(source_spec: Dict[str, Any]) -> str:
        """
        Get the expected volume name for the source release.

        Args:
            source_spec: Source specification containing major version info

        Returns:
            Expected volume name (e.g., 'rhel-8')
        """
        major_version = source_spec.get("major")
        if major_version:
            return f"rhel-{major_version}"
        return "unknown"

    def _validate_volume_compatibility(
        self, volume_names: List[str], expected_volume: str, reference: List[str]
    ) -> None:
        """
        Validate that build volume names are compatible with the source release.

        Args:
            volume_names: List of volume names from the build
            expected_volume: Expected volume name for the source release
            reference: The reference used to find the build
        """
        if not volume_names:
            LOGGER.warning("No volume names found for build validation")
            return

        # Check if any volume name matches the expected volume
        compatible_volumes = [
            vol for vol in volume_names if vol and expected_volume in vol
        ]

        if not compatible_volumes:
            # No compatible volumes found - issue single warning
            LOGGER.critical(
                f"Build volume mismatch: requested {reference} matches {volume_names[0]}, expected '{expected_volume}'."
            )
            LOGGER.critical("Build may not be compatible with target release. Exiting.")
            sys.exit(99)
        else:
            # Compatible volumes found - log for debugging
            LOGGER.debug(f"Volume compatibility check passed: {compatible_volumes}")

    def get_info(self, packages, reference, composes, options):
        """
        Get information about packages and their associated composes for testing.

        Args:
            packages (str or list): Fallback package name(s) - will be overridden by NVR parsing.
            reference (list): List of references (task IDs or NVRs).
            composes (list): List of composes to check (should contain source compose name).

        Returns:
            list: A list of dictionaries with build information for each reference.
        """
        # Validate and process references
        if not reference or len(reference) == 0:
            LOGGER.critical("No build artifact reference provided!")
            LOGGER.critical(
                "Please provide either a task ID (integer) or full NVR (name-version-release)."
            )
            sys.exit(99)

        # Validate each reference format
        for ref in reference:
            if ref is None:
                continue
            is_valid, ref_type = self._validate_reference_format(str(ref))
            if not is_valid:
                LOGGER.critical(f"Invalid reference format: '{ref}'")
                LOGGER.critical(
                    "Reference must be either a task ID (integer) or valid NVR (package-version-release)"
                )
                sys.exit(99)
            LOGGER.debug(f"Validated reference '{ref}' as {ref_type}")

        try:
            self.task_id = int(reference[0])
        except (ValueError, TypeError):
            self.build_reference = reference

        # Determine package name based on reference type
        effective_package_name = None

        if self.task_id:
            # For task IDs, use the provided package name
            if isinstance(packages, str) and packages.strip():
                effective_package_name = packages
            elif (
                isinstance(packages, list) and len(packages) > 0 and packages[0].strip()
            ):
                effective_package_name = packages[0]
            else:
                LOGGER.critical(
                    f"Task ID {self.task_id} provided but no valid package name specified!"
                )
                LOGGER.critical(
                    "When using task IDs, you must provide the package name via configuration."
                )
                sys.exit(99)

        elif self.build_reference and len(self.build_reference) > 0:
            # For NVRs, parse the package name from the reference
            first_ref = str(self.build_reference[0])
            effective_package_name = self._parse_package_name_from_nvr(first_ref)
            if not effective_package_name:
                LOGGER.critical(f"Failed to parse package name from NVR '{first_ref}'!")
                LOGGER.critical("NVR format should be: package-name-version-release")
                sys.exit(99)
        else:
            LOGGER.critical("No valid build reference provided!")
            sys.exit(99)

        # Final validation - ensure we have a valid package name
        if not effective_package_name or not effective_package_name.strip():
            LOGGER.critical("No valid package name could be determined!")
            sys.exit(99)

        LOGGER.debug(f"Using package name: {effective_package_name}")

        self.session = koji.ClientSession(options.brew_api.get("session_url"))
        self.session.gssapi_login()

        # Get task IDs for the effective package
        task_ids_dict = self.get_brew_task_and_compose(
            effective_package_name, reference, self.session, options
        )

        # Convert to the expected format - one entry per build ID
        info = []
        source_compose = options.source_spec["compose_name"]

        # Log summary of builds being included
        if task_ids_dict:
            build_count = len(task_ids_dict)
            if build_count == 1:
                task_id, (volume_name, nvr) = next(iter(task_ids_dict.items()))
                LOGGER.info(
                    f"Including brew build {task_id} ({effective_package_name}) from {volume_name}"
                )
            else:
                volume_names = list(set(item[0] for item in task_ids_dict.values()))
                volume_str = (
                    ", ".join(volume_names)
                    if len(volume_names) > 1
                    else volume_names[0] if volume_names else "unknown"
                )
                LOGGER.info(
                    f"Including {build_count} brew builds for {effective_package_name} from {volume_str}"
                )
                for task_id, (volume_name, nvr) in task_ids_dict.items():
                    LOGGER.debug(f"  • Build {task_id} from {volume_name}")

        for task_id, (volume_name, nvr) in task_ids_dict.items():
            brew_info_dict = {
                "build_id": task_id,
                "package": effective_package_name,
                "compose": source_compose,
                "distro": options.source_spec.get("compose_name", source_compose),
                "nvr": nvr,
            }
            info.append(brew_info_dict)

        return info

    def get_brew_task_and_compose(self, package, reference, session, options):
        """
        Get the Brew build task IDs and associated composes for a given package and reference.

        Args:
            package (str): The name of the package.
            reference (str, int): List of references for the package.

        Returns:
            dict: A dictionary with Brew task IDs as keys and tuples of (volume_name, nvr) as values.
        """
        query = session.listBuilds(prefix=package)
        brewbuild_baseurl = options.brew_api.get("taskid_url")
        tasks = []

        if self.build_reference:
            LOGGER.debug(
                f"Gathering brew build information for {package} version {reference}"
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
            nvrs = [
                build_info.get("nvr")
                for build_info in query
                for ref in reference
                if ref in build_info.get("nvr")
            ]

        elif self.task_id:
            LOGGER.debug(
                f"Gathering brew build information for {package} task ID {reference}"
            )
            tasks = reference
            volume_names = [
                build_info.get("volume_name")
                for task in tasks
                for build_info in query
                if int(task) == build_info.get("task_id")
            ]
            nvrs = [
                build_info.get("nvr")
                for task in tasks
                for build_info in query
                if int(task) == build_info.get("task_id")
            ]
        else:
            LOGGER.critical("No build artifact reference provided!")
            sys.exit(99)

        task_ids = list(set(tasks))
        if not task_ids:
            LOGGER.critical(
                f"No suitable tasks found for reference {reference}. Please verify the reference is correct."
            )
            sys.exit(99)

        # Validate volume compatibility with source release
        expected_volume = self._get_expected_volume_name(options.source_spec)
        self._validate_volume_compatibility(volume_names, expected_volume, reference)

        # Log build information concisely
        for i, task_id in enumerate(task_ids):
            LOGGER.debug(
                f"Build task {task_id} available at {brewbuild_baseurl}{task_id}"
            )

        return {task_ids[i]: (volume_names[i], nvrs[i]) for i in range(len(task_ids))}
