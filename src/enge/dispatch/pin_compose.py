import requests
from enge.utils.http_client import http_get
import logging
import re

LOGGER = logging.getLogger(__name__)


def _extract_version_info(compose_arg):
    """
    Extract version information from a compose argument to help filter relevant options.

    Parameters:
    - compose_arg (str): The compose argument (e.g., "9.7", "RHEL-9.7.0-Nightly")

    Returns:
    - dict: Dictionary with 'major', 'minor', 'distro' keys, or None if parsing fails
    """
    # Try parsing as version number first (e.g., "9.7")
    version_match = re.match(r"^(\d+)\.(\d+)$", compose_arg.strip())
    if version_match:
        return {
            "major": int(version_match.group(1)),
            "minor": int(version_match.group(2)),
            "distro": "rhel",
        }

    # Try parsing as full compose name (e.g., "RHEL-9.7.0-Nightly")
    compose_match = re.match(
        r"^([A-Z]+)-(\d+)\.(\d+)\.(\d+)-(.+)$", compose_arg.strip()
    )
    if compose_match:
        return {
            "major": int(compose_match.group(2)),
            "minor": int(compose_match.group(3)),
            "distro": compose_match.group(1).lower(),
        }

    return None


def _filter_relevant_composes(compose_list, version_info):
    """
    Filter compose list to only include options with same major.minor.

    Parameters:
    - compose_list (list): List of compose names
    - version_info (dict): Version info from _extract_version_info

    Returns:
    - list: Filtered list of relevant composes (same major.minor only)
    """
    if not compose_list or not version_info:
        LOGGER.debug(
            f"No compose_list ({len(compose_list) if compose_list else 0} items) or no version_info ({version_info})"
        )
        return (compose_list or [])[:10]  # Return first 10 if we can't parse or no data

    target_major = version_info["major"]
    target_minor = version_info["minor"]
    target_distro = version_info["distro"]

    LOGGER.debug(
        f"Filtering composes for target: {target_distro} {target_major}.{target_minor}"
    )

    exact_matches = []
    other_distro = []

    for compose in compose_list:
        if not compose or not isinstance(compose, str):
            continue

        # Parse each compose to see if it's relevant
        if target_distro == "rhel" and compose.startswith("RHEL-"):
            compose_match = re.match(r"^RHEL-(\d+)\.(\d+)\.(\d+)-(.+)$", compose)
            if compose_match:
                compose_major = int(compose_match.group(1))
                compose_minor = int(compose_match.group(2))

                LOGGER.debug(
                    f"Checking compose {compose}: major={compose_major}, minor={compose_minor}"
                )

                if compose_major == target_major and compose_minor == target_minor:
                    # Exact major.minor match (any micro version)
                    LOGGER.debug(f"  -> MATCH: Adding {compose}")
                    exact_matches.append(compose)
                else:
                    LOGGER.debug(
                        f"  -> NO MATCH: {compose_major}.{compose_minor} != {target_major}.{target_minor}"
                    )

        elif target_distro.lower() in compose.lower():
            # For non-RHEL distros, show if distro name matches
            LOGGER.debug(f"Non-RHEL match: {compose}")
            other_distro.append(compose)
        else:
            LOGGER.debug(f"Skipping compose {compose} (doesn't match {target_distro})")

    # Sort and limit results
    exact_matches.sort()
    other_distro.sort()

    # Prioritize exact matches, then other distro matches
    result = exact_matches[:10] + other_distro[:5]
    LOGGER.debug(
        f"Filter result: {len(exact_matches)} exact matches, {len(other_distro)} other distro matches"
    )
    LOGGER.debug(f"Final filtered list: {result}")
    return result[:15]


def _filter_relevant_symbolic_composes(symbolic_composes, version_info):
    """
    Filter symbolic composes to only include options with same major.minor.

    Parameters:
    - symbolic_composes (list): List of symbolic compose dictionaries
    - version_info (dict): Version info from _extract_version_info

    Returns:
    - list: Filtered list of relevant symbolic compose dictionaries (same major.minor only)
    """
    if not symbolic_composes or not version_info:
        return (symbolic_composes or [])[
            :5
        ]  # Return first 5 if we can't parse or no data

    compose_major = version_info["major"]
    compose_minor = version_info["minor"]
    compose_distro = version_info["distro"]

    exact_matches = []
    other_distro = []

    for item in symbolic_composes:
        if not item or not isinstance(item, dict):
            continue

        for key, value in item.items():
            if not value or not isinstance(value, str):
                continue

            # Check if the symbolic compose value is relevant
            if compose_distro == "rhel" and "RHEL-" in value:
                value_match = re.match(r"^RHEL-(\d+)\.(\d+)\.(\d+)-(.+)$", value)
                if value_match:
                    value_major = int(value_match.group(1))
                    value_minor = int(value_match.group(2))

                    if value_major == compose_major and value_minor == compose_minor:
                        exact_matches.append(item)
                        break

            elif compose_distro.lower() in value.lower():
                # For non-RHEL distros, show if distro name matches
                other_distro.append(item)
                break

    # Prioritize exact matches, then other distro matches
    result = exact_matches[:5] + other_distro[:2]
    return result[:7]


def fetch_data_from_url(url):
    """
    Fetches data from the given URL and returns it as a dictionary.

    Parameters:
    - url (str): URL to fetch data from.

    Returns:
    - dict: Data fetched from the URL.

    Raises:
    - SystemExit: If the request fails.
    """
    try:
        from enge.utils.globals import REQUEST_TIMEOUT_DEFAULT

        response = http_get(url, timeout=REQUEST_TIMEOUT_DEFAULT)
        response.raise_for_status()  # Raises HTTPError for bad responses
    except requests.exceptions.RequestException as e:
        from enge.utils.errors import NetworkError

        raise NetworkError(f"Error accessing {url}: {e}") from e

    return response.json()


def find_compose(compose_arg, data):
    """
    Searches for a compose in the provided data.

    Parameters:
    - compose_arg (str): The compose to search for.
    - data (dict): The data to search within.

    Returns:
    - str or None: The found compose, or None if not found.
    """
    symbolic_composes = data.get("SYMBOLIC_COMPOSES", [])
    other_composes = [list(item.keys())[0] for item in data.get("OTHER_COMPOSES", [])]
    compose_list = data.get("COMPOSES", []) + other_composes

    # First, check if compose_arg is a key in any of the symbolic composes
    for item in symbolic_composes:
        if compose_arg in item:
            return item[compose_arg]

    # If not found, check if compose_arg is directly in the compose_list
    if compose_arg in compose_list:
        return compose_arg

    return None


def _pin_compose_with_fallback(major, minor, composes_prod_url):
    """
    Attempts to pin a compose with fallback logic for different RHEL formats.

    Tries:
    1. RHEL-major.minor.0-Nightly (RHEL 8/9 format)
    2. RHEL-major.minor-Nightly (RHEL 10 format)

    Parameters:
    - major (int): Major version number
    - minor (int): Minor version number
    - composes_prod_url (str): URL to fetch compose data from

    Returns:
    - str: The found compose

    Raises:
    - ValueError: If neither format is found
    """
    if not composes_prod_url:
        raise ValueError("composes_prod_url not configured")

    data = fetch_data_from_url(composes_prod_url)

    # Format with micro version (RHEL 8/9 style)
    compose_with_micro = f"RHEL-{major}.{minor}.0-Nightly"

    # Format without micro version (RHEL 10 style)
    compose_without_micro = f"RHEL-{major}.{minor}-Nightly"
    result = find_compose(compose_with_micro, data) or find_compose(
        compose_without_micro, data
    )
    if result:
        LOGGER.debug(f"Found compose with version: {result}")
        return result

    # If neither format is found, show error with both attempted formats
    _show_compose_not_found_error(
        [compose_with_micro, compose_without_micro], data, major, minor
    )


def _show_compose_not_found_error(attempted_composes, data, major=None, minor=None):
    """
    Show detailed error message when compose is not found.

    Parameters:
    - attempted_composes (list): List of compose names that were attempted
    - data (dict): The compose data from API
    - major (int, optional): Major version for filtering
    - minor (int, optional): Minor version for filtering
    """
    # Extract version info from the first attempted compose for filtering
    version_info = None

    # If major/minor provided directly, use them
    if major is not None and minor is not None:
        version_info = {"major": major, "minor": minor, "distro": "rhel"}
        LOGGER.debug(f"Using provided version info: major={major}, minor={minor}")
    else:
        # Try to extract from attempted compose
        first_compose = attempted_composes[0] if attempted_composes else ""
        version_info = _extract_version_info(first_compose)
        LOGGER.debug(f"Extracted version info from {first_compose}: {version_info}")

    # If we still don't have version info, log and exit without filtering
    if not version_info:
        LOGGER.debug(
            "Could not extract version info for filtering, showing error without filtering"
        )
        attempted_list = ", ".join(attempted_composes)
        LOGGER.error(f"Compose(s) {attempted_list} not found.")
        LOGGER.debug("Unable to show relevant alternatives - version parsing failed.")
        from enge.utils.errors import ValidationError

        raise ValidationError("Compose not found and version parsing failed")

    # Safely get data with proper None handling
    symbolic_composes = data.get("SYMBOLIC_COMPOSES") or []
    composes = data.get("COMPOSES") or []
    other_composes_data = data.get("OTHER_COMPOSES") or []

    # Safely extract other composes
    other_composes = []
    for item in other_composes_data:
        if item and isinstance(item, dict) and item.keys():
            other_composes.append(list(item.keys())[0])

    all_compose_list = composes + other_composes

    # Filter to show only relevant options
    relevant_symbolic = _filter_relevant_symbolic_composes(
        symbolic_composes, version_info
    )
    relevant_composes = _filter_relevant_composes(all_compose_list, version_info)

    # Prepare filtered lists for the error message
    available_images = "\n".join(
        [
            "            - " + ",".join(str(k) for k in item.keys())
            for item in relevant_symbolic
            if item
        ]
    )
    compose_list = "\n".join(
        ["            - " + str(c) for c in relevant_composes if c]
    )

    attempted_list = ", ".join(attempted_composes)
    LOGGER.error(f"Compose(s) {attempted_list} not found.")
    if available_images:
        LOGGER.error(f"Available relevant symbolic composes: \n{available_images}")
    if compose_list:
        LOGGER.error(f"Available relevant composes: \n{compose_list}")
    if not available_images and not compose_list:
        LOGGER.debug(
            f"No relevant composes found for RHEL {version_info['major']}.{version_info['minor']}."
        )
    from enge.utils.errors import ValidationError

    raise ValidationError("Compose not found")


def _pin_compose(compose_arg, composes_prod_url):
    """
    Attempts to pin a compose based on the given argument.

    Parameters:
    - compose_arg (str): The argument to use for finding a compose.
    - composes_prod_url (str): URL to fetch compose data from.

    Raises:
    - ValueError: If the compose cannot be found.

    Returns:
    - str or None: The found compose, or None if not found.

    """
    if not composes_prod_url:
        raise ValueError("composes_prod_url not configured")

    data = fetch_data_from_url(composes_prod_url)

    compose = find_compose(compose_arg, data)
    if compose is None:
        attempted_composes = [compose_arg]
        major, minor = None, None

        # For full compose names, try fallback logic if it looks like a RHEL compose
        rhel_match = re.match(r"^RHEL-(\d+)\.(\d+)\.(\d+)-(.+)$", compose_arg)
        if rhel_match:
            major = int(rhel_match.group(1))
            minor = int(rhel_match.group(2))
            suffix = rhel_match.group(4)

            # Try without micro version as fallback
            fallback_compose = f"RHEL-{major}.{minor}-{suffix}"
            compose = find_compose(fallback_compose, data)
            if compose:
                LOGGER.debug(f"Found fallback compose: {compose}")
                return compose

            # Add fallback to attempted list
            attempted_composes.append(fallback_compose)

        # Show error with attempted compose(s) and version info if available
        _show_compose_not_found_error(attempted_composes, data, major, minor)

    return compose_arg if compose_arg in (data.get("COMPOSES") or []) else compose
