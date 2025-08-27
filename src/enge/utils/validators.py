import logging
from typing import List, Optional

from enge.utils.http_client import http_get
from enge.utils.errors import ValidationError


LOGGER = logging.getLogger(__name__)


def validate_git_repository(url: str, timeout: int = 10) -> None:
    LOGGER.debug(f"Validating git repository URL: {url}")
    response = http_get(url, timeout=timeout)
    if response.status_code == 404:
        LOGGER.critical(f"Git repository not found: {url}")
        LOGGER.critical(f"Response status: {response.status_code}")
        raise ValidationError("Git repository not found")
    elif response.status_code >= 400:
        LOGGER.warning(
            f"Git repository returned status {response.status_code}, but continuing..."
        )


def validate_plan_filters(
    plans_list: List[str],
    cli_planfilter: Optional[str],
    generated_planfilter: Optional[str],
    cli_testfilter: Optional[str],
    cli_test_name: Optional[str],
) -> None:
    if not plans_list:
        LOGGER.critical("No plans provided for testing!")
        raise ValidationError("No plans provided")

    if len(plans_list) > 1 and (
        cli_planfilter or generated_planfilter or cli_testfilter or cli_test_name
    ):
        LOGGER.critical(
            "It is not advised to use testfilter, planfilter, or test name with multiple requested plans."
            " Please specify one plan with additional filters per request."
        )
        raise ValidationError("Incompatible filters with multiple plans")
