import requests
from enge.utils.http_client import http_get
from enge.utils.globals import VERBOSE, REQUEST_TIMEOUT_DEFAULT
from enge.utils.errors import NetworkError, ValidationError
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

        if target_distro == "rhel" and compose.startswith("RHEL-"):
            compose_match = re.match(r"^RHEL-(\d+)\.(\d+)\.(\d+)-(.+)$", compose)
            if compose_match:
                compose_major = int(compose_match.group(1))
                compose_minor = int(compose_match.group(2))
                if compose_major == target_major and compose_minor == target_minor:
                    exact_matches.append(compose)
        elif target_distro.lower() in compose.lower():
            other_distro.append(compose)

    exact_matches.sort()
    other_distro.sort()

    result = exact_matches[:10] + other_distro[:5]
    LOGGER.debug(
        f"Compose filter: {len(exact_matches)} exact + {len(other_distro)} other matches for {target_distro} {target_major}.{target_minor}"
    )
    final = result[:15]
    if final:
        LOGGER.debug(f"Matched composes: {final}")
    return final


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
        response = http_get(url, timeout=REQUEST_TIMEOUT_DEFAULT)
        response.raise_for_status()  # Raises HTTPError for bad responses
    except requests.exceptions.RequestException as e:
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


def _pin_compose_with_fallback(major, minor, suffix, composes_prod_url, cli_args=None):
    """
    Attempts to pin a compose with fallback logic for different RHEL formats.

    Tries:
    1. RHEL-major.minor.0-Nightly (RHEL 8/9 format)
    2. RHEL-major.minor-Nightly (RHEL 10 format)

    Parameters:
    - major (int): Major version number
    - minor (int): Minor version number
    - suffix (str): Compose suffix (e.g., "Nightly")
    - composes_prod_url (str): URL to fetch compose data from
    - cli_args (Namespace, optional): CLI arguments for rerun mode detection

    Returns:
    - str: The found compose

    Raises:
    - ValueError: If neither format is found
    """
    if not composes_prod_url:
        raise ValueError("composes_prod_url not configured")

    data = fetch_data_from_url(composes_prod_url)

    # Format with micro version (RHEL 8/9 style)
    compose_with_micro = f"RHEL-{major}.{minor}.0-{suffix}"

    # Format without micro version (RHEL 10 style)
    compose_without_micro = f"RHEL-{major}.{minor}-{suffix}"
    result = find_compose(compose_with_micro, data) or find_compose(
        compose_without_micro, data
    )
    is_rerun = cli_args is not None and getattr(cli_args, "action", None) == "rerun"
    if not result and is_rerun:
        LOGGER.warning(
            f"Rerun mode: Compose '{compose_with_micro}', '{compose_without_micro}' not found, falling back to the latest Nightly."
        )
        result = find_compose(f"RHEL-{major}.{minor}.0-Nightly", data) or find_compose(
            f"RHEL-{major}.{minor}-Nightly", data
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
        all_symbolic_names = "\n".join(
            [
                "            - " + ",".join(str(k) for k in item.keys())
                for item in symbolic_composes
                if item
            ]
        )
        all_compose_names = "\n".join(
            ["            - " + str(c) for c in all_compose_list if c]
        )
        LOGGER.error(
            f"No relevant composes found for RHEL {version_info['major']}.{version_info['minor']}."
        )
        if all_symbolic_names:
            LOGGER.error(f"All available symbolic composes: \n{all_symbolic_names}")
        if all_compose_names:
            LOGGER.error(f"All available composes: \n{all_compose_names}")
    raise ValidationError("Compose not found")


_repin_cache = {}


def repin_compose(compose_name, composes_prod_url, cli_args=None):
    """
    Re-pin a compose name to the latest available nightly version.

    Extracts major.minor from the original compose name and resolves it
    to the current nightly compose via the Testing Farm composes API.

    Results are cached per (compose_name, composes_prod_url) to avoid
    duplicate API requests and warnings when the same compose is validated
    multiple times (e.g., opt_manager + set_flow).

    Non-RHEL composes (e.g., CentOS-Stream-9) are returned as-is since
    they are already symbolic and don't require re-pinning.

    Parameters:
    - compose_name (str): Original compose name (e.g., "RHEL-8.10.0-20241215.1")
    - composes_prod_url (str): URL to fetch compose data from.
    - cli_args (Namespace, optional): CLI arguments for rerun mode detection

    Returns:
    - str: Updated compose name for RHEL composes, or the original name for non-RHEL.

    Raises:
    - ValueError: If composes_prod_url is not configured or compose name cannot be parsed.
    - ValidationError: If the compose cannot be resolved to an available nightly
      (propagated from _pin_compose_with_fallback).
    """
    cache_key = (compose_name, composes_prod_url)
    if cache_key in _repin_cache:
        return _repin_cache[cache_key]

    if not compose_name:
        raise ValueError("Compose name is empty, cannot re-pin")

    # Non-RHEL composes (e.g., CentOS-Stream-9) are already symbolic
    if not compose_name.startswith("RHEL-"):
        LOGGER.debug("Non-RHEL compose does not require re-pinning: %s", compose_name)
        _repin_cache[cache_key] = compose_name
        return compose_name

    # Extract major.minor from compose name
    # Handles both RHEL-8.10.0-suffix and RHEL-10.1-suffix formats
    match = re.match(r"^RHEL-(\d+)\.(\d+)(?:\.\d+)?-(.+)$", compose_name)
    if not match:
        raise ValueError(f"Could not parse compose name for re-pinning: {compose_name}")

    major = int(match.group(1))
    minor = int(match.group(2))
    suffix = match.group(3)

    if not composes_prod_url:
        raise ValueError(
            "composes_prod_url is not configured, cannot validate compose availability"
        )

    result = _pin_compose_with_fallback(
        major, minor, suffix, composes_prod_url, cli_args=cli_args
    )
    if result != compose_name:
        LOGGER.warning("Compose re-pinned: %s -> %s", compose_name, result)
    else:
        LOGGER.log(VERBOSE, "Compose validated: %s", compose_name)
    _repin_cache[cache_key] = result
    return result
