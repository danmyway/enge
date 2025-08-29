import logging
from typing import Optional, Any

import requests
from requests import Session
from requests.adapters import HTTPAdapter

try:
    # Retry is optional but available via requests' urllib3 dependency
    from urllib3.util.retry import Retry  # type: ignore
except Exception:  # pragma: no cover - Retry not strictly required
    Retry = None  # type: ignore

from .globals import REQUEST_TIMEOUT_DEFAULT


LOGGER = logging.getLogger(__name__)

_session: Optional[Session] = None


def get_session() -> Session:
    global _session
    if _session is not None:
        return _session

    session = requests.Session()

    # Configure reasonable connection pooling and retries
    adapter_kwargs: dict[str, Any] = {"pool_connections": 10, "pool_maxsize": 20}
    adapter = HTTPAdapter(**adapter_kwargs)

    if Retry is not None:
        retry = Retry(
            total=3,
            read=3,
            connect=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST", "PUT", "DELETE", "PATCH"),
        )
        adapter = HTTPAdapter(max_retries=retry, **adapter_kwargs)

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    _session = session
    return session


def http_get(url: str, timeout: Optional[float] = None, **kwargs):
    session = get_session()
    effective_timeout = timeout if timeout is not None else REQUEST_TIMEOUT_DEFAULT
    return session.get(url, timeout=effective_timeout, **kwargs)


def http_post(url: str, timeout: Optional[float] = None, **kwargs):
    session = get_session()
    effective_timeout = timeout if timeout is not None else REQUEST_TIMEOUT_DEFAULT
    return session.post(url, timeout=effective_timeout, **kwargs)


def http_put(url: str, timeout: Optional[float] = None, **kwargs):
    session = get_session()
    effective_timeout = timeout if timeout is not None else REQUEST_TIMEOUT_DEFAULT
    return session.put(url, timeout=effective_timeout, **kwargs)


def http_delete(url: str, timeout: Optional[float] = None, **kwargs):
    session = get_session()
    effective_timeout = timeout if timeout is not None else REQUEST_TIMEOUT_DEFAULT
    return session.delete(url, timeout=effective_timeout, **kwargs)
