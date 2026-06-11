"""Aion server exception hierarchy for API and authentication errors."""

__all__ = [
    "AionAuthenticationError",
]


class AionException(Exception):
    """Base AION Exception"""
    pass


class AionAuthenticationError(AionException):
    """Authentication related errors"""
    pass
