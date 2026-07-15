#!/usr/bin/env python3


class EngeError(Exception):
    """Base class for enge exceptions."""


class ConfigurationError(EngeError):
    """Configuration is missing, malformed, or cannot be loaded."""


class ValidationError(EngeError):
    """Invalid user input or option dependency failure."""


class ConflictError(EngeError):
    """An idempotent gap-fill write encountered pre-existing, incompatible
    state (e.g. results.json upsert_task_result/write_xunit in
    utils/results_parser.py). The idempotency fence: identical content is a
    no-op, but differing content for the same key is a conflict that must
    never be silently overwritten."""


class NetworkError(EngeError):
    """Remote service is unreachable, timed out, or returned a fatal error."""


class UserAbort(EngeError):
    """User opted to abort an operation."""
