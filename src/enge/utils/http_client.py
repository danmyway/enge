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

_BACKOFF_FACTOR = 0.5

_session: Optional[Session] = None
_create_session: Optional[Session] = None


def get_session() -> Session:
    global _session
    if _session is not None:
        return _session

    session = requests.Session()

    # Configure reasonable connection pooling and retries
    adapter_kwargs: dict[str, Any] = {"pool_connections": 10, "pool_maxsize": 20}
    adapter = HTTPAdapter(**adapter_kwargs)

    if Retry is not None:
        # POST and PATCH are excluded: they are not idempotent, so a retry
        # after the request may have reached the server can duplicate the
        # resource it creates. POSTs go through _get_create_session()
        # instead, which retries only what provably never was processed.
        retry = Retry(
            total=3,
            read=3,
            connect=3,
            backoff_factor=_BACKOFF_FACTOR,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "PUT", "DELETE"),
        )
        adapter = HTTPAdapter(max_retries=retry, **adapter_kwargs)

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    _session = session
    return session


def _get_create_session() -> Session:
    """Session for non-idempotent POSTs, retrying only provably safe failures."""
    global _create_session
    if _create_session is not None:
        return _create_session

    session = requests.Session()

    adapter_kwargs: dict[str, Any] = {"pool_connections": 10, "pool_maxsize": 20}
    adapter = HTTPAdapter(**adapter_kwargs)

    if Retry is not None:
        retry = Retry(
            total=3,
            # A connect error means the request never left the client, so
            # nothing can have been created; retrying is free.
            connect=3,
            # read=False re-raises the original timeout instead of retrying:
            # a read error can follow a create the server completed, and the
            # response we never saw is the only record of its id.
            read=False,
            status=3,
            backoff_factor=_BACKOFF_FACTOR,
            # 429 is a rejection issued before the request is processed, so
            # nothing was created. Every 5xx is excluded, because it may
            # equally well follow a successful create.
            status_forcelist=(429,),
            allowed_methods=frozenset({"POST"}),
            # Honouring Retry-After would make urllib3 retry 413/429/503
            # whenever the header is present, re-opening the 503 hole that
            # keeping 503 out of the forcelist above is meant to close.
            respect_retry_after_header=False,
        )
        adapter = HTTPAdapter(max_retries=retry, **adapter_kwargs)

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    _create_session = session
    return session


def http_get(url: str, timeout: Optional[float] = None, **kwargs):
    session = get_session()
    effective_timeout = timeout if timeout is not None else REQUEST_TIMEOUT_DEFAULT
    return session.get(url, timeout=effective_timeout, **kwargs)


def http_post(url: str, timeout: Optional[float] = None, **kwargs):
    session = _get_create_session()
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
