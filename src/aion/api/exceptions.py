from typing import Optional

__all__ = [
    "AionException",
    "AionAuthenticationError",
]


class AionException(Exception):
    """Base AION Exception"""
    pass


class AionAuthenticationError(AionException):
    """Authentication related errors.

    Args:
        message (str): Human readable description of the failure.
        status_code (Optional[int]): HTTP status returned by the auth endpoint
            when the error originated from a response, ``None`` otherwise.
            Callers use this to tell rejected credentials (401) apart from
            failures that are worth retrying.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
