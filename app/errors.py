"""Typed errors raised by the service."""


class ServiceError(Exception):
    """Base class for expected, client-visible errors."""
    code = "internal-error"


class LanguageNotSupportedError(ServiceError):
    code = "language-not-supported"
