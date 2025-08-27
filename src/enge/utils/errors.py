#!/usr/bin/env python3


class EngeError(Exception):
    """Base class for enge exceptions."""


class ConfigurationError(EngeError):
    """Configuration is missing, malformed, or cannot be loaded."""


class ValidationError(EngeError):
    """Invalid user input or option dependency failure."""


class NetworkError(EngeError):
    """Remote service is unreachable, timed out, or returned a fatal error."""


class UserAbort(EngeError):
    """User opted to abort an operation."""
