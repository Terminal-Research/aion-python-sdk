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

    The message names what the API said, not only the status: the start of the
    response body (``detail``) and the identifier the server logged the request
    under (``request_id``), when it sent one. Both are what the operator of the
    API needs to find the refusal on their side.
    """

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        detail: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message, request=request, response=response)
        self.detail = detail
        self.request_id = request_id

    @property
    def summary(self) -> str:
        """The refusal in a few words: status, detail and request id, no URL."""
        text = f"HTTP {self.response.status_code}"
        if self.detail:
            text += f": {self.detail}"
        if self.request_id:
            text += f" (request id {self.request_id})"
        return text

    @classmethod
    def from_response(cls, response: httpx.Response) -> "AionFileStorageError":
        """Build the error for a response that failed ``raise_for_status``.

        Args:
            response: The rejected, fully read response, with its request
                attached.

        Returns:
            An error carrying the status line, the response's detail and
            request id, and httpx's request and response.
        """
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as original:
            detail = _response_detail(response)
            request_id = _request_id(response)
            # httpx's first line names the status and URL; the rest is a link
            # to a generic description of the status.
            message = str(original).splitlines()[0]
            if detail:
                message += f": {detail}"
            if request_id:
                message += f" (request id {request_id})"
            return cls(
                message,
                request=original.request,
                response=original.response,
                detail=detail,
                request_id=request_id,
            )
        raise ValueError("from_response called for a successful response")


# Longest response detail an error carries; an error page is not a diagnosis.
_DETAIL_LIMIT = 300
# Response headers that commonly carry the server's identifier for a request,
# in the order they are looked up.
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "x-correlation-id",
    "request-id",
    "x-amzn-requestid",
    "x-amzn-trace-id",
    "traceparent",
)


def _response_detail(response: httpx.Response) -> str | None:
    """The response body as one line, cut to ``_DETAIL_LIMIT`` characters."""
    try:
        text = response.text
    except httpx.ResponseNotRead:
        return None
    detail = " ".join(text.split())
    if len(detail) > _DETAIL_LIMIT:
        detail = detail[: _DETAIL_LIMIT - 1] + "\u2026"
    return detail or None


def _request_id(response: httpx.Response) -> str | None:
    """The identifier the server gave the request, when it sent one."""
    for name in _REQUEST_ID_HEADERS:
        value = response.headers.get(name)
        if value:
            return value
    return None
