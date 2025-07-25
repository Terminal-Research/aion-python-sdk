__all__ = [
    "AionException",
    "AionAuthenticationError",
]


class AionException(Exception):
    """Base AION Exception"""
    pass


class AionAuthenticationError(AionException):
    """Authentication related errors"""
    pass
