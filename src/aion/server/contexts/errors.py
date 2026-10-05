"""Context lifecycle errors of the Context extension.

These are the extension's own error codes (``1000``-``1002``), outside the
JSON-RPC server-error band, so they are not ``A2AError`` subclasses and are
not registered with a2a-sdk's error maps. The two transports that serve the
extension render them directly: the JSON-RPC dispatcher as an ``error``
object with ``data``, the HTTP+JSON routes as problem details.

See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
"""

from __future__ import annotations

from typing import Any, Optional

from aion.core.constants.a2a import CONTEXT_EXTENSION_URI_V1

__all__ = [
    "ContextDeletionInProgress",
    "ContextLifecycleError",
    "ContextNotDeletable",
    "ContextNotFound",
]


class ContextLifecycleError(Exception):
    """A context method failed for a reason the contract names.

    Attributes:
        code: JSON-RPC error code.
        http_status: HTTP+JSON status code.
        problem: Fragment appended to the extension URI to form the problem
            ``type``.
        retryable: Whether repeating the same request can succeed.
        context_id: The context the request named.
        deletion_operation_id: Diagnostic identifier of a durable deletion,
            when one exists.
    """

    code: int
    http_status: int
    problem: str
    message: str
    retryable: bool

    def __init__(self, context_id: str, *, deletion_operation_id: Optional[str] = None) -> None:
        super().__init__(self.message)
        self.context_id = context_id
        self.deletion_operation_id = deletion_operation_id

    def data(self) -> dict[str, Any]:
        """The sanitized fields both transports carry."""
        data: dict[str, Any] = {"contextId": self.context_id, "retryable": self.retryable}
        if self.deletion_operation_id is not None:
            data["deletionOperationId"] = self.deletion_operation_id
        return data

    def jsonrpc_error(self) -> dict[str, Any]:
        """The JSON-RPC ``error`` object."""
        return {"code": self.code, "message": self.message, "data": self.data()}

    def problem_details(self) -> dict[str, Any]:
        """The HTTP+JSON problem-details body."""
        return {
            "type": f"{CONTEXT_EXTENSION_URI_V1}#{self.problem}",
            "title": self.message,
            "status": self.http_status,
            **self.data(),
        }


class ContextNotFound(ContextLifecycleError):
    """``GetContext`` named a context the caller cannot see.

    Raised the same way whether the context does not exist or belongs to
    another caller, so the answer reveals nothing about other callers.
    """

    code = 1000
    http_status = 404
    problem = "context-not-found"
    message = "Context not found"
    retryable = False


class ContextDeletionInProgress(ContextLifecycleError):
    """Deletion is durable but not finished; retry the same ``DeleteContext``."""

    code = 1001
    http_status = 409
    problem = "context-deletion-in-progress"
    message = "Context deletion is in progress"
    retryable = True


class ContextNotDeletable(ContextLifecycleError):
    """Deletion was refused definitively and the context is active again."""

    code = 1002
    http_status = 409
    problem = "context-not-deletable"
    message = "Context cannot be deleted"
    retryable = False
