"""
Utility functions and classes for enge.

This module provides common utilities including date/time helpers.
"""

import calendar
import copy
import re
from datetime import datetime, timedelta
from typing import Any


_RELATIVE_DATE_RE = re.compile(r"^(\d+)([hdwmy])$", re.IGNORECASE)


def _subtract_months(dt: datetime, n: int) -> datetime:
    """Subtract *n* calendar months from *dt*, clamping the day."""
    month = dt.month - n
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def parse_date_arg(value: str) -> datetime:
    """Parse a date CLI argument into a :class:`datetime`.

    Accepts either an absolute date (``YYYY-MM-DD``) or a relative
    alias that means "N units ago from now":

    - ``h`` — hours  (e.g. ``6h``)
    - ``d`` — days   (e.g. ``3d``)
    - ``w`` — weeks  (e.g. ``2w``)
    - ``m`` — months (e.g. ``1m``)
    - ``y`` — years  (e.g. ``1y``)

    Raises:
        ValueError: If *value* matches neither format.
    """
    m = _RELATIVE_DATE_RE.match(value)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        now = datetime.now()
        if unit == "h":
            return now - timedelta(hours=amount)
        if unit == "d":
            return now - timedelta(days=amount)
        if unit == "w":
            return now - timedelta(weeks=amount)
        if unit == "m":
            return _subtract_months(now, amount)
        # unit == "y"
        return _subtract_months(now, amount * 12)

    return datetime.strptime(value, "%Y-%m-%d")


def get_datetime() -> str:
    """
    Get current datetime as a formatted string.

    Returns:
        Current datetime in YYYYMMDDHHMMSS format
    """
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_timestamp() -> str:
    """
    Get current timestamp as a human-readable string.

    Returns:
        Current timestamp in YYYY-MM-DD HH:MM:SS format
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_SENSITIVE_RE = re.compile(
    r"(token|api_key|apikey|secret|password|authorization)", re.IGNORECASE
)
_REDACTED = "***REDACTED***"


def redact_sensitive(obj: Any) -> Any:
    """Return a deep copy of *obj* with sensitive values replaced.

    Never mutates the input — the payloads sent to APIs keep real credentials.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SENSITIVE_RE.search(k):
                out[k] = _REDACTED
            else:
                out[k] = redact_sensitive(v)
        return out
    if isinstance(obj, list):
        return [redact_sensitive(item) for item in obj]
    return copy.deepcopy(obj) if isinstance(obj, (dict, list)) else obj
