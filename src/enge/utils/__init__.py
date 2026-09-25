"""
Utility functions and classes for enge.

This module provides common utilities including date/time helpers.
"""

import calendar
import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple


_RELATIVE_DATE_RE = re.compile(r"^(\d+)([hdwmy])$", re.IGNORECASE)


def _subtract_months(dt: datetime, n: int) -> datetime:
    """Subtract *n* calendar months from *dt*, clamping the day."""
    month = dt.month - n
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _relative_ago(now: datetime, value: str) -> Optional[datetime]:
    """Resolve a relative alias to *now* minus that span.

    Returns ``None`` when *value* is not a relative alias, leaving the
    caller to parse it as an absolute date. The tzinfo of *now* carries
    through, so an aware *now* yields an aware result.
    """
    m = _RELATIVE_DATE_RE.match(value)
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2).lower()
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


def parse_date_arg(value: str) -> datetime:
    """Parse a date CLI argument into a naive local :class:`datetime`.

    Accepts either an absolute date (``YYYY-MM-DD``) or a relative
    alias that means "N units ago from now":

    - ``h`` — hours  (e.g. ``6h``)
    - ``d`` — days   (e.g. ``3d``)
    - ``w`` — weeks  (e.g. ``2w``)
    - ``m`` — months (e.g. ``1m``)
    - ``y`` — years  (e.g. ``1y``)

    The result is naive and, for a relative alias, anchored to local
    time. That is what the ``reportportal`` subcommands and the legacy
    archive lookup in ``utils/task_resolver.py`` need, because both
    compare it against local-time values. Manifest selection compares
    against UTC ``created_at`` instead and uses `resolve_utc_window`.

    Raises:
        ValueError: If *value* matches neither format.
    """
    relative = _relative_ago(datetime.now(), value)
    if relative is not None:
        return relative

    return datetime.strptime(value, "%Y-%m-%d")


def resolve_utc_window(
    since: Optional[str],
    until: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Resolve ``--since``/``--until`` into tz-aware UTC bounds.

    This is the date parser for the manifest selection paths, whose
    bounds are compared against a manifest's UTC ``created_at``:

    - a relative alias (same units as `parse_date_arg`) is the exact
      instant that long before *now*, for ``until`` as much as for
      ``since`` — there is no end-of-day rounding;
    - an absolute ``YYYY-MM-DD`` is the UTC calendar day: ``since``
      from ``00:00:00Z``, ``until`` through ``23:59:59Z``.

    Each bound resolves independently; ``None`` in gives ``None`` out.
    *now* is a test-injection seam and must be tz-aware.

    Raises:
        ValueError: If a bound matches neither format, or *now* is naive.
    """
    resolved_now = datetime.now(timezone.utc) if now is None else now
    if resolved_now.tzinfo is None:
        raise ValueError("resolve_utc_window() requires a tz-aware 'now'")

    def _bound(value: Optional[str], end_of_day: bool) -> Optional[datetime]:
        if value is None:
            return None
        relative = _relative_ago(resolved_now, value)
        if relative is not None:
            return relative
        absolute = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            absolute = absolute.replace(hour=23, minute=59, second=59)
        return absolute

    return _bound(since, False), _bound(until, True)


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
