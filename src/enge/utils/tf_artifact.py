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
from .errors import ConfigurationError, ValidationError, UserAbort

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

    @staticmethod
    def _parse_copr_reference(ref_string: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse COPR reference format: alias:reference or buildID.

        Args:
            ref_string: Reference string (e.g., "lp:pr123", "lpr:pr456", or "12345")

        Returns:
            Tuple of (package_name, version_reference) or (None, None) if buildID

        Examples:
            "lp:pr123" -> ("leapp", "pr123")
            "lpr:pr456" -> ("leapp-repository", "pr456")
            "12345" -> (None, None)  # buildID format
        """
        if ":" not in ref_string:
            # No colon means it's a build ID
            return None, None

        from .globals import COPR_PACKAGE_ALIASES

        parts = ref_string.split(":", 1)
        if len(parts) != 2:
            LOGGER.warning(f"Invalid COPR reference format: {ref_string}")
            return None, None

        alias, version_ref = parts
        package_name = COPR_PACKAGE_ALIASES.get(alias)

        if not package_name:
            LOGGER.warning(
                f"Unknown package alias '{alias}'. Valid aliases: {list(COPR_PACKAGE_ALIASES.keys())}"
            )
            return None, None

        return package_name, version_ref

    @staticmethod
    def _derive_chroot_from_source(source_spec: Dict[str, Any]) -> str:
        """
        Derive COPR chroot from source specification.

        Args:
            source_spec: Source specification dict with 'major' key

        Returns:
            Chroot string (e.g., "epel-8-x86_64")

        Examples:
            {"major": 8, "minor": 10} -> "epel-8-x86_64"
            {"major": 9, "minor": 4} -> "epel-9-x86_64"
        """
        major_version = source_spec.get("major")
        if not major_version:
            raise ValidationError("Source specification missing 'major' version")

        # COPR packages are built as noarch, so we always use x86_64
        chroot = f"epel-{major_version}-x86_64"
        LOGGER.debug(f"Derived COPR chroot: {chroot}")
        return chroot

    def _fetch_built_packages(self, build_id: int) -> Dict[str, Any]:
        """
        Fetch built packages for a COPR build from the API.

        Args:
            build_id: COPR build ID

        Returns:
            Dict with chroot keys containing package information

        Example response structure:
            {
                "epel-8-x86_64": {
                    "packages": [
                        {"name": "pkg", "version": "1.0", "release": "1.el8", "arch": "noarch", "epoch": null}
                    ]
                }
            }
        """
        from .globals import COPR_BUILT_PACKAGES_API_URL
        from .http_client import http_get

        url = f"{COPR_BUILT_PACKAGES_API_URL}/{build_id}"
        LOGGER.debug(f"Fetching built packages from: {url}")

        try:
            response = http_get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            LOGGER.debug(f"Successfully fetched packages for build {build_id}")
            return data
        except Exception as e:
            LOGGER.error(f"Failed to fetch built packages for build {build_id}: {e}")
            raise ValidationError(f"Failed to fetch COPR built packages: {e}")

    @staticmethod
    def _build_package_list(packages_data: Dict[str, Any], chroot: str) -> List[str]:
        """
        Build list of package names from COPR built-packages API response.

        Args:
            packages_data: Response from built-packages API
            chroot: Target chroot (e.g., "epel-8-x86_64")

        Returns:
            List of package names (excluding 'src' arch packages)

        Example:
            Input: {"epel-8-x86_64": {"packages": [
                {"name": "pkg1", "arch": "noarch"},
                {"name": "pkg2", "arch": "src"}
            ]}}
            Output: ["pkg1"]
        """
        if chroot not in packages_data:
            LOGGER.warning(
                f"Chroot '{chroot}' not found in built packages. Available: {list(packages_data.keys())}"
            )
            return []

        chroot_data = packages_data.get(chroot, {})
        packages = chroot_data.get("packages", [])

        # Filter out 'src' architecture packages
        package_names = [
            f'{pkg["name"]}-{pkg["version"]}-{pkg["release"]}.{pkg["arch"]}'
            for pkg in packages
            if pkg.get("arch") != "src" and pkg.get("name")
        ]

        LOGGER.info(
            f"Found {len(package_names)} packages for chroot {chroot}: {package_names}"
        )
        return package_names

    def get_info(self, packages, repo, reference, composes, options):
        """
        Get COPR build information for testing.

        Supports two reference formats:
        1. Build ID (integer): Direct build ID lookup
        2. Alias:reference (string): e.g., "lp:pr123" for leapp PR 123

        Args:
            packages: Package name(s) - can be overridden by alias parsing
            repo: Repository name
            reference: List of references (build IDs or alias:ref strings)
            composes: List of compose names (source compose)
            options: Parsed configuration options

        Returns:
            List of build info dicts with packages list from built-packages API
        """
        # Parse reference format
        ref_str = str(reference[0]) if reference and reference[0] is not None else None

        if not ref_str:
            LOGGER.critical("No COPR reference provided!")
            raise ValidationError("COPR reference is required")

        # Try to parse as build ID first
        try:
            self.build_id = int(ref_str)
            parsed_package = None  # Will use config package name
            version_ref = None
            LOGGER.debug(f"Parsed reference as build ID: {self.build_id}")
        except (ValueError, TypeError):
            # Try parsing as alias:reference format
            parsed_package, version_ref = self._parse_copr_reference(ref_str)
            if parsed_package and version_ref:
                LOGGER.info(
                    f"Parsed reference '{ref_str}' as package '{parsed_package}', version ref '{version_ref}'"
                )
                self.build_reference = [version_ref]
            else:
                LOGGER.critical(f"Invalid COPR reference format: '{ref_str}'")
                LOGGER.critical(
                    "Expected: build ID (integer) or 'alias:reference' (e.g., 'lp:pr123')"
                )
                raise ValidationError("Invalid COPR reference format")

        # Determine effective package name
        if parsed_package:
            # Use package from alias parsing
            package = parsed_package
        else:
            # Use package from config
            if isinstance(packages, list):
                package = packages[0] if packages else None
            else:
                package = packages
            # Fallback to config package name
            if not package:
                package = options.copr_api.get("package") or options.project.get("name")

        if not package:
            LOGGER.critical("No package name could be determined!")
            raise ValidationError("Package name is required")

        # Setup COPR API connection
        owner = options.copr_api.get("owner") or options.project.get("owner")
        repository = repo
        owner_is_group = options.copr_api.get("owner_is_group") or False
        copr_owner = f"@{owner}" if owner_is_group else owner

        # Build base URL for logging
        group_str = "g" if owner_is_group else ""
        self.copr_build_baseurl = os.path.join(
            "https://copr.fedorainfracloud.org/coprs",
            group_str if group_str else "",
            owner or "",
            repository or package or "",
            "build",
        )

        # Use source compose
        source_compose = options.source_spec["compose_name"]
        info = []

        # Handle reference-based search (alias:ref format)
        if self.build_reference and version_ref:
            LOGGER.info(
                f"Searching for COPR build matching package '{package}', version '{version_ref}'"
            )

            try:
                query = self.session.get_list(copr_owner, repository)
            except CoprNoResultException as no_copr:
                LOGGER.critical(
                    "Failed to query COPR builds - check copr_api configuration"
                )
                LOGGER.critical("Verify: owner, owner_is_group, repository settings")
                LOGGER.debug(f"{type(no_copr).__name__}: {no_copr}")
                raise ValidationError("COPR configuration invalid")

            # Build regex pattern for version matching
            reference_pattern = rf".*{version_ref}(\..*|$)"
            found_build = None

            for build_munch in query:
                if isinstance(build_munch, list):
                    build_munch = build_munch[0] if build_munch else None
                if not build_munch:
                    continue

                # Match: not failed, correct package, version matches pattern
                if (
                    hasattr(build_munch, "state")
                    and build_munch.state != "failed"
                    and hasattr(build_munch, "source_package")
                    and build_munch.source_package.get("name") == package
                    and build_munch.source_package.get("version")
                    and re.match(
                        reference_pattern, build_munch.source_package.get("version", "")
                    )
                ):
                    found_build = build_munch
                    break

            if not found_build:
                LOGGER.critical(
                    f"No COPR build found for package '{package}' matching '{version_ref}'"
                )
                LOGGER.critical(f"Check builds at: {self.copr_build_baseurl}s")
                raise ValidationError(
                    f"No matching COPR build found for {package}:{version_ref}"
                )

            # Extract build ID from found build
            self.build_id = found_build.id
            LOGGER.info(f"Found COPR build ID: {self.build_id}")

        # Handle direct build ID lookup
        if self.build_id:
            LOGGER.info(
                f"Fetching COPR build information for build ID: {self.build_id}"
            )

            try:
                build_munch = self.session.get(self.build_id)
            except coprexcept.CoprNoResultException as no_copr:
                LOGGER.critical(f"COPR build {self.build_id} not found: {no_copr}")
                raise ValidationError(f"COPR build {self.build_id} not found")

            # Unwrap if list
            if isinstance(build_munch, list):
                build_munch = build_munch[0] if build_munch else None

            if not build_munch:
                LOGGER.critical(f"Empty response for build ID {self.build_id}")
                raise ValidationError("Empty build response")

            # Validate build state
            if hasattr(build_munch, "state") and build_munch.state == "failed":
                LOGGER.critical(
                    FormatText.format_text(
                        f"Build {self.build_id} is in failed state!",
                        text_col=FormatText.RED,
                        bold=True,
                    )
                )
                raise ValidationError(f"COPR build {self.build_id} is in failed state")

            # Optionally validate package name match (if we have expected package)
            if package and hasattr(build_munch, "source_package"):
                build_package = build_munch.source_package.get("name")
                if build_package and build_package != package:
                    LOGGER.warning(
                        f"Build {self.build_id} is for package '{build_package}', "
                        f"expected '{package}' - proceeding anyway"
                    )

            # Get build info using new method
            for build_info in self.get_build_dictionary(build_munch, composes, options):
                info.append(build_info)

        if not info:
            LOGGER.critical("No COPR build information could be retrieved!")
            raise ValidationError("Failed to retrieve COPR build information")

        return info

    def get_build_dictionary(self, build, composes, options):
        """
        Get the dictionary containing build information using the new API-based approach.

        Args:
            build: The COPR build object.
            composes (list): A list of compose names (source compose).
            options: Parsed configuration options with source_spec.

        Returns:
            list: A list of dictionaries containing build information with packages list.
        """
        build_info = []

        def get_first_non_list(obj):
            if isinstance(obj, list):
                return obj[0] if obj else None
            return obj

        build_obj = get_first_non_list(build)
        if not build_obj:
            return build_info

        # Check for running build
        if hasattr(build_obj, "state") and build_obj.state == "running":
            LOGGER.warning(
                f"Build {build_obj.id} is currently running. Consider waiting for completion."
            )
            LOGGER.warning(f"See: {self.copr_build_baseurl}s/")

            if not sys.stdin.isatty():
                LOGGER.warning(
                    "Non-interactive environment - cannot wait for running build"
                )
                raise ValidationError("Build is still running")
            else:
                user_input = input("Do you wish to continue anyway? (y/n) ")
                if user_input.lower() != "y":
                    raise UserAbort("User aborted due to running COPR build")

        # Get package info from build object
        if hasattr(build_obj, "source_package"):
            package_name = build_obj.source_package.get("name", "unknown")
            package_version = build_obj.source_package.get("version", "unknown")
        else:
            package_name = "unknown"
            package_version = "unknown"

        build_id = getattr(build_obj, "id", None)
        if not build_id:
            LOGGER.error("Build object has no ID")
            return build_info

        LOGGER.info(f"Processing build {build_id}: {package_name}-{package_version}")

        # Log build URL
        build_url = os.path.join(str(self.copr_build_baseurl), str(build_id))
        LOGGER.debug(f"Build URL: {build_url}")

        # Derive chroot from source specification
        try:
            chroot = self._derive_chroot_from_source(options.source_spec)
        except ValidationError as e:
            LOGGER.error(f"Failed to derive chroot: {e}")
            return build_info

        # Fetch packages from API
        try:
            packages_data = self._fetch_built_packages(build_id)
            package_list = self._build_package_list(packages_data, chroot)
        except ValidationError as e:
            LOGGER.error(f"Failed to fetch packages: {e}")
            return build_info

        if not package_list:
            LOGGER.warning(f"No packages found for chroot {chroot}")
            return build_info

        # Build info dict for this compose
        source_compose = (
            composes[0] if composes else options.source_spec.get("compose_name")
        )

        copr_info_dict = {
            "build_id": f"{build_id}:{chroot}",
            "compose": source_compose,
            "chroot": chroot,
            "distro": source_compose,
            "nvr": f"{package_name}-{package_version}",
            "packages": package_list,
        }

        buildid_fmt = FormatText.format_text(build_id, bold=True)
        compose_fmt = FormatText.format_text(source_compose, bold=True)
        LOGGER.info(
            f"COPR build {buildid_fmt} for {compose_fmt}: {len(package_list)} packages"
        )
        LOGGER.debug(f"Packages: {', '.join(package_list)}")

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
            raise ValidationError("Build volume mismatch")
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
                  The 'build_id' field will always contain the NVR (resolved from task ID if needed).
        """
        # Validate and process references
        if not reference or len(reference) == 0:
            LOGGER.critical("No build artifact reference provided!")
            LOGGER.critical(
                "Please provide either a task ID (integer) or full NVR (name-version-release)."
            )
            raise ValidationError("No build artifact reference provided")

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
                raise ValidationError("Invalid build reference format")
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
                raise ConfigurationError("Package name required when using task IDs")

        elif self.build_reference and len(self.build_reference) > 0:
            # For NVRs, parse the package name from the reference
            first_ref = str(self.build_reference[0])
            effective_package_name = self._parse_package_name_from_nvr(first_ref)
            if not effective_package_name:
                LOGGER.critical(f"Failed to parse package name from NVR '{first_ref}'!")
                LOGGER.critical("NVR format should be: package-name-version-release")
                raise ValidationError("Invalid NVR format")
        else:
            LOGGER.critical("No valid build reference provided!")
            raise ValidationError("No valid build reference provided")

        # Final validation - ensure we have a valid package name
        if not effective_package_name or not effective_package_name.strip():
            LOGGER.critical("No valid package name could be determined!")
            raise ValidationError("No valid package name could be determined")

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
                    f"Including brew build {nvr} ({effective_package_name}) from {volume_name}"
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
                    LOGGER.debug(f"  • Build {nvr} (task {task_id}) from {volume_name}")

        for task_id, (volume_name, nvr) in task_ids_dict.items():
            # Parse package name from each individual NVR to handle multiple different packages
            individual_package_name = self._parse_package_name_from_nvr(nvr)
            if not individual_package_name:
                LOGGER.warning(
                    f"Failed to parse package name from NVR '{nvr}', using fallback '{effective_package_name}'"
                )
                individual_package_name = effective_package_name

            brew_info_dict = {
                "build_id": nvr,  # Use NVR instead of task_id as the artifact identifier
                "packages": [individual_package_name],  # List format for consistency
                "compose": source_compose,
                "distro": options.source_spec.get("compose_name", source_compose),
                "nvr": nvr,
            }
            info.append(brew_info_dict)

        return info

    def get_brew_task_and_compose(self, package, reference, session, options):
        """
        Get the Brew build task IDs and associated composes for a given package and reference.

        Validates both Task IDs and NVRs through the Brew API. Task IDs are resolved to their
        corresponding NVRs, and NVRs are validated for existence.

        Args:
            package (str): The name of the package.
            reference (str, int): List of references for the package (Task IDs or NVRs).

        Returns:
            dict: A dictionary with Brew task IDs as keys and tuples of (volume_name, nvr) as values.
                  The NVRs from these tuples are used as artifact identifiers in the payload.
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
            raise ValidationError("No build artifact reference provided")

        task_ids = list(set(tasks))
        if not task_ids:
            LOGGER.critical(
                f"No suitable tasks found for reference {reference}. Please verify the reference is correct."
            )
            raise ValidationError("No suitable tasks found for reference")

        # Validate volume compatibility with source release
        expected_volume = self._get_expected_volume_name(options.source_spec)
        self._validate_volume_compatibility(volume_names, expected_volume, reference)

        # Log build information concisely
        for i, task_id in enumerate(task_ids):
            LOGGER.debug(
                f"Build task {task_id} available at {brewbuild_baseurl}{task_id}"
            )

        return {task_ids[i]: (volume_names[i], nvrs[i]) for i in range(len(task_ids))}
