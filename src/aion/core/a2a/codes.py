"""Error codes the server itself gives, shared by every executable extension.

An executable extension's own codes live with the extension and its
documentation; the server does not know them and only checks that a code is a
non-empty string. The codes here are reserved: an extension returns one only
in the same meaning (an executor that stops for a server shutdown says
``server_shutdown``).

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
    "TASK_ABANDONED",
    "TASK_BUSY",
]

CONTEXT_BUSY = "context_busy"
"""The context already has an unfinished task of an extension that runs one at a time."""

TASK_BUSY = "task_busy"
"""The task is running, and its executor takes no message until the turn ends."""

EXTENSION_UNAVAILABLE = "extension_unavailable"
"""The extension that serves the request is not available on this server."""

CALLER_NOT_ALLOWED = "caller_not_allowed"
"""This kind of caller may not start the extension's task on this agent."""

INVALID_PAYLOAD = "invalid_payload"
"""The request carries input the extension cannot take; the client fixes it and does not retry.

It is the code for any invalid extension input, whoever finds it: an
executor refuses with it a payload its models reject and the input that
fails its own validation (an empty instruction, a value the backend cannot
take).
Extension-specific codes are for refusals a client can answer differently.
"""

SERVER_SHUTDOWN = "server_shutdown"
"""The server stopped while the turn was running."""

TASK_ABANDONED = "task_abandoned"
"""The turn wrote nothing for longer than ``AION_STALE_TASK_TIMEOUT``: its server died or hung.

The server settles such a task ``FAILED`` with this code; no executor returns it.
"""

INTERNAL_ERROR = "internal_error"
"""The executor or the server failed unexpectedly; the details are in the log."""

RETRYABLE = MappingProxyType(
    {
        CONTEXT_BUSY: True,
        TASK_BUSY: True,
        EXTENSION_UNAVAILABLE: True,
        CALLER_NOT_ALLOWED: False,
        INVALID_PAYLOAD: False,
        SERVER_SHUTDOWN: True,
        TASK_ABANDONED: True,
        INTERNAL_ERROR: False,
    }
)
"""Whether repeating the request can succeed, for each code above."""
