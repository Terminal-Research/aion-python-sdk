__all__ = [
    "AionAuthenticationError",
]


class AionException(Exception):
    """Base AION Exception"""
    pass


class AionAuthenticationError(AionException):
    """Authentication related errors"""
    pass
