#!/usr/bin/env python3
"""
Helper functions for machine reservation in Testing Farm.

This module provides utilities for:
- Reading and encoding SSH public keys
- Generating reservation environment variables
- Building TMT extra_args for the discover step
- Fetching public IP for security group rules
"""

import base64
import glob
import logging
import os
import requests
from typing import Dict, List, Optional

from enge.utils.errors import ValidationError

LOGGER = logging.getLogger(__name__)


def read_glob_paths(glob_paths: List[str]) -> str:
    """
    Read and concatenate contents from files matching glob patterns.

    Args:
        glob_paths: List of glob patterns to match files

    Returns:
        Concatenated contents of all matched files

    Raises:
        ValidationError: If any matched file cannot be read
    """
    paths = [
        path
        for glob_path in glob_paths
        for path in glob.glob(os.path.expanduser(glob_path))
    ]

    contents: List[str] = []

    for path in paths:
        if not os.path.isfile(path) or not os.access(path, os.R_OK):
            raise ValidationError(f"Error reading '{path}'.")
        with open(path, "r") as file:
            contents.append(file.read())

    return "".join(contents)


def get_ssh_authorized_keys_base64() -> Optional[str]:
    """
    Read SSH public keys from ~/.ssh/*.pub and encode them as base64.

    This function searches for all .pub files in the user's ~/.ssh/ directory,
    concatenates them, and returns a base64-encoded string suitable for
    Testing Farm's TF_RESERVATION_AUTHORIZED_KEYS_BASE64 environment variable.

    Returns:
        Base64-encoded string of concatenated SSH public keys, or None if no keys found

    Examples:
        >>> keys = get_ssh_authorized_keys_base64()
        >>> # Returns base64-encoded SSH public keys or None
    """
    ssh_public_keys = ["~/.ssh/*.pub"]

    try:
        authorized_keys = read_glob_paths(ssh_public_keys)
        if not authorized_keys:
            LOGGER.warning("No SSH public keys found in ~/.ssh/*.pub")
            return None

        authorized_keys_bytes = base64.b64encode(authorized_keys.encode("utf-8"))
        return authorized_keys_bytes.decode("utf-8")
    except ValidationError as e:
        LOGGER.warning(f"Failed to read SSH public keys: {e}")
        return None
    except Exception as e:
        LOGGER.warning(f"Unexpected error reading SSH public keys: {e}")
        return None


def get_reservation_environment_variables(duration_minutes: int = 60) -> Dict[str, str]:
    """
    Generate environment variables required for Testing Farm reservation.

    Args:
        duration_minutes: Duration of the reservation in minutes (default: 60)

    Returns:
        Dictionary of environment variables for reservation

    Examples:
        >>> env_vars = get_reservation_environment_variables(120)
        >>> env_vars["TF_RESERVATION_DURATION"]
        '120'
    """
    return {"TF_RESERVATION_DURATION": str(duration_minutes)}


def get_reservation_tmt_extra_args() -> Dict[str, List[str]]:
    """
    Generate TMT extra_args for the discover step to enable system reservation.

    This configures TMT to insert the Testing Farm reserve-system test,
    which handles the reservation logic after test completion.

    Returns:
        Dictionary with discover step extra arguments

    Examples:
        >>> extra_args = get_reservation_tmt_extra_args()
        >>> extra_args["discover"]
        ['--insert --how fmf --url https://gitlab.com/testing-farm/tests --ref main --test reserve-system']
    """
    return {
        "discover": [
            "--insert --how fmf --url https://gitlab.com/testing-farm/tests --ref main --test reserve-system"
        ]
    }


def get_reservation_secrets() -> Optional[Dict[str, str]]:
    """
    Generate secrets dictionary with SSH authorized keys for reservation.

    This function reads SSH public keys and returns them in the format
    expected by Testing Farm's secrets configuration.

    Returns:
        Dictionary with TF_RESERVATION_AUTHORIZED_KEYS_BASE64, or None if no keys found

    Examples:
        >>> secrets = get_reservation_secrets()
        >>> # Returns {'TF_RESERVATION_AUTHORIZED_KEYS_BASE64': '<base64-string>'} or None
    """
    ssh_keys_base64 = get_ssh_authorized_keys_base64()
    if ssh_keys_base64:
        return {"TF_RESERVATION_AUTHORIZED_KEYS_BASE64": ssh_keys_base64}
    return None


def adjust_test_selection_for_reservation(
    test_name: Optional[str], test_filter: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """
    Adjust test_name or test_filter to include the reserve system test.

    When using --reserve with --test or --testfilter, the reserve system test
    needs to be appended to ensure it runs after the specified tests.

    Args:
        test_name: Original test name from --test argument (may be None)
        test_filter: Original test filter from --testfilter argument (may be None)

    Returns:
        Tuple of (modified_test_name, modified_test_filter)

    Examples:
        >>> adjust_test_selection_for_reservation("/some/test", None)
        ('/some/test | reserve-system', None)

        >>> adjust_test_selection_for_reservation(None, "tier:0")
        (None, 'tier:0 | name:/testing-farm/reserve-system')

        >>> adjust_test_selection_for_reservation(None, None)
        (None, None)
    """
    modified_test_name = test_name
    modified_test_filter = test_filter

    if test_name:
        # Append reserve system test to test_name
        modified_test_name = f"{test_name} | reserve-system"
        LOGGER.debug(f"Modified test_name for reservation: {modified_test_name}")
    elif test_filter:
        # Append reserve system test to test_filter
        modified_test_filter = f"{test_filter} | name:/testing-farm/reserve-system"
        LOGGER.debug(f"Modified test_filter for reservation: {modified_test_filter}")

    return modified_test_name, modified_test_filter


def get_public_ip() -> Optional[str]:
    """
    Fetch the public IP address from https://ipv4.icanhazip.com.

    This is used to create security group rules that allow SSH access
    to the reserved machine from the user's current IP address.

    Returns:
        Public IP address as a string, or None if fetching fails

    Examples:
        >>> ip = get_public_ip()
        >>> # Returns something like "203.0.113.42" or None
    """
    try:
        response = requests.get("https://ipv4.icanhazip.com", timeout=5)
        response.raise_for_status()
        public_ip = response.text.strip()
        LOGGER.debug(f"Fetched public IP: {public_ip}")
        return public_ip
    except requests.RequestException as e:
        LOGGER.warning(f"Failed to fetch public IP address: {e}")
        return None
    except Exception as e:
        LOGGER.warning(f"Unexpected error fetching public IP: {e}")
        return None


def get_security_group_rules() -> Optional[List[Dict[str, any]]]:
    """
    Generate security group rules for allowing ingress traffic from the user's public IP.

    This creates a security group rule that allows all traffic from the user's current
    public IP address to the reserved machine, enabling SSH and other access.

    Returns:
        List containing a single ingress rule dict, or None if public IP cannot be determined

    Examples:
        >>> rules = get_security_group_rules()
        >>> # Returns [{"type": "ingress", "protocol": "-1", "cidr": "203.0.113.42/32", ...}] or None
    """
    public_ip = get_public_ip()
    if not public_ip:
        LOGGER.warning("Could not determine public IP for security group rules")
        return None

    rule = {
        "type": "ingress",
        "protocol": "-1",
        "cidr": f"{public_ip}/32",
        "port_min": 0,
        "port_max": 65535,
    }

    LOGGER.debug(f"Generated security group rule: {rule}")
    return [rule]
