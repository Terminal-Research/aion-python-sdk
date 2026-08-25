"""Aion's own slice of the JSON-RPC server-error code space.

JSON-RPC 2.0 reserves the whole -32768..-32000 band for the spec's own future
use, and carves out -32000..-32099 within it as "Server error - reserved for
implementation-defined server-errors" - the only part of that band anything
outside the spec itself may legally claim. a2a-sdk already lives there
(TaskNotFoundError: -32001 ... VersionNotSupportedError: -32009 as of
a2a-sdk 1.1.2) and grows upward from -32001, so Aion's own errors take the
bottom of that same block: -32050..-32099. Not a hard guarantee - the range is
shared, not owned - but it keeps Aion and a2a-sdk growing from opposite ends
instead of the same one, so an ordinary a2a-sdk upgrade would have to add
dozens of codes before it reaches into this block.

``register_aion_error`` is the single choke point through which any Aion
error claims a code in that block. It exists because there is no aion-owned
layer between an ``A2AError`` subclass and a2a-sdk's own module-level maps
(``JSON_RPC_ERROR_CODE_MAP``, ``EXCEPTION_MAP``, ``A2A_ERROR_MAPPING``) - see
``aion.server.tasks.ownership.types.TaskOwnershipBusy`` for the first,
and so far only, caller. Failing loudly here, at import time, is what turns a
future collision - Aion claiming a code twice, or landing outside its own
block - into an immediate startup failure instead of two error types silently
sharing one wire code.
"""

from __future__ import annotations

from a2a.server.jsonrpc_models import JSONRPCError
from a2a.server.request_handlers.response_helpers import EXCEPTION_MAP
from a2a.utils.errors import (
    A2A_ERROR_MAPPING,
    A2A_ERROR_REASONS,
    A2A_REASON_TO_ERROR,
    JSON_RPC_ERROR_CODE_MAP,
    A2AError,
    ErrorMapping,
)

__all__ = ["AION_ERROR_CODE_RANGE", "register_aion_error"]

# Inclusive on both ends. Assigned bottom-up from -32050; leaves -32000..-32049
# as headroom for a2a-sdk's own upward growth before either side can collide.
AION_ERROR_CODE_RANGE = range(-32099, -32049)


def register_aion_error(
    error_cls: type[A2AError],
    code: int,
    *,
    http_status: int,
    grpc_status: str,
    reason: str,
) -> None:
    """Give an Aion ``A2AError`` subclass its own JSON-RPC/REST/gRPC identity.

    Must run once, at import time, for every Aion error that should not fall
    back to the generic -32603 an unmapped ``A2AError`` gets.

    Raises:
        ValueError: If ``code`` falls outside :data:`AION_ERROR_CODE_RANGE`,
            or is already claimed - by a2a-sdk, by this function's own prior
            call for a different error, or by ``reason`` colliding with an
            existing one. Any of these is a bug to fix before shipping, not a
            condition to handle at runtime, so this raises rather than logs.
    """
    if code not in AION_ERROR_CODE_RANGE:
        raise ValueError(
            f"{error_cls.__name__}'s code {code} is outside Aion's reserved "
            f"range {AION_ERROR_CODE_RANGE.start}..{AION_ERROR_CODE_RANGE.stop - 1}"
        )
    claimed_by = JSON_RPC_ERROR_CODE_MAP.get(error_cls)
    if claimed_by is not None and claimed_by != code:
        raise ValueError(f"{error_cls.__name__} already registered as {claimed_by}")
    if code in JSON_RPC_ERROR_CODE_MAP.values() and error_cls not in JSON_RPC_ERROR_CODE_MAP:
        raise ValueError(f"JSON-RPC code {code} is already claimed by another error type")
    if reason in A2A_REASON_TO_ERROR and A2A_REASON_TO_ERROR[reason] is not error_cls:
        raise ValueError(f"Reason {reason!r} is already claimed by another error type")

    JSON_RPC_ERROR_CODE_MAP[error_cls] = code
    EXCEPTION_MAP[error_cls] = JSONRPCError
    mapping = ErrorMapping(http_status, grpc_status, reason)
    A2A_ERROR_MAPPING[error_cls] = mapping
    A2A_ERROR_REASONS[error_cls] = reason
    A2A_REASON_TO_ERROR[reason] = error_cls
