"""Error codes the server itself gives, shared by every toolkit.

A toolkit's own codes live in the toolkit and its documentation; the server
does not know them and only checks that a code is a non-empty string. The
codes here are reserved: a toolkit returns one only in the same meaning (a
toolkit that stops for a server shutdown says ``server_shutdown``).

Whether a failure is worth retrying is decided by whoever creates the error.
For these codes the server decides, and the answer is fixed: ``RETRYABLE``.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = [
    "CALLER_NOT_ALLOWED",
    "CONTEXT_BUSY",
    "EXTENSION_UNAVAILABLE",
    "INTERNAL_ERROR",
    "INVALID_PAYLOAD",
    "RETRYABLE",
    "SERVER_SHUTDOWN",
]

CONTEXT_BUSY = "context_busy"
"""The context already has an unfinished task of a toolkit that runs one at a time."""

EXTENSION_UNAVAILABLE = "extension_unavailable"
"""The toolkit that serves the request is not available on this server."""

CALLER_NOT_ALLOWED = "caller_not_allowed"
"""The caller may not start this toolkit."""

INVALID_PAYLOAD = "invalid_payload"
"""The request carries a payload of the toolkit that its payload models refuse.

The default of ``ToolkitDescriptor.invalid_payload_code``; a toolkit may name
a code of its own there instead.
"""

SERVER_SHUTDOWN = "server_shutdown"
"""The server stopped while the turn was running."""

INTERNAL_ERROR = "internal_error"
"""The toolkit or the server failed unexpectedly; the details are in the log."""

RETRYABLE = MappingProxyType(
    {
        CONTEXT_BUSY: True,
        EXTENSION_UNAVAILABLE: True,
        CALLER_NOT_ALLOWED: False,
        INVALID_PAYLOAD: False,
        SERVER_SHUTDOWN: True,
        INTERNAL_ERROR: False,
    }
)
"""Whether repeating the request can succeed, for each code above."""
