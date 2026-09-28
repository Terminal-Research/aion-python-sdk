"""Public API errors raised while talking to the Aion control plane.

Most of the classes live with the rest of the SDK's public hierarchy in
:mod:`aion.core.exceptions`; this module re-exports them, so the import path
callers already use keeps working. ``AionFileStorageError`` is defined here
instead: it is an ``httpx`` error as well as an SDK one, and ``aion.core`` does
not depend on ``httpx``.
"""

import httpx

from aion.core.exceptions import (
    AionAuthenticationError,
    AionError,
    AionFileValidationError,
    AionModelPrincipalError,
)

__all__ = [
    "AionError",
    "AionFileStorageError",
    "AionFileValidationError",
    "AionAuthenticationError",
    "AionModelPrincipalError",
]


class AionFileStorageError(AionError, httpx.HTTPStatusError):
    """The Files API rejected a mutation.

    Also an ``httpx.HTTPStatusError``, and constructed from the same request
    and response, so code that already catches the transport error - including
    anything reading ``.response.status_code`` to tell a retryable failure from
    a permanent one - keeps working, while SDK-wide handlers can catch
    ``AionError``.
    """

    @classmethod
    def from_response(cls, response: httpx.Response) -> "AionFileStorageError":
        """Build the error for a response that failed ``raise_for_status``.

        Args:
            response: The rejected response, with its request attached.

        Returns:
            An error carrying httpx's own message, request and response.
        """
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as original:
            return cls(
                str(original),
                request=original.request,
                response=original.response,
            )
        raise ValueError("from_response called for a successful response")
