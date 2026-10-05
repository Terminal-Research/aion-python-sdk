"""Parameter models for the Context extension's RPC methods.

``GetContexts``, ``GetContext`` and ``DeleteContext`` take these objects as
JSON-RPC ``params`` and as HTTP+JSON request bodies. Validation failures are
answered as invalid params (JSON-RPC ``-32602``, HTTP ``400``).

See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
"""

from typing import Any, Dict, Optional

from pydantic import Field, field_validator

from aion.core.a2a import A2ABaseModel

__all__ = [
    "CONTEXT_OFFSET_MAX",
    "CONTEXT_PAGE_DEFAULT",
    "CONTEXT_PAGE_MAX",
    "DeleteContextParams",
    "GetContextParams",
    "GetContextsParams",
]

CONTEXT_PAGE_DEFAULT = 50
"""Page size used when a request omits ``historyLength``."""

CONTEXT_PAGE_MAX = 100
"""Largest ``historyLength`` a request may ask for."""

CONTEXT_OFFSET_MAX = 2**31 - 1
"""Largest ``historyOffset``: anything beyond is no position a store can reach."""


def _require_context_id(value: str) -> str:
    if not value.strip():
        raise ValueError("contextId must not be blank")
    return value


class GetContextsParams(A2ABaseModel):
    """Parameters for ``GetContexts``: one newest-first page of the caller's contexts."""

    history_length: int = Field(
        default=CONTEXT_PAGE_DEFAULT,
        ge=0,
        le=CONTEXT_PAGE_MAX,
        description="Contexts to return.",
    )
    history_offset: int = Field(
        default=0,
        ge=0,
        le=CONTEXT_OFFSET_MAX,
        description="Newest contexts to skip.",
    )
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Request metadata.")


class GetContextParams(A2ABaseModel):
    """Parameters for ``GetContext``: one context with a newest-relative page of its history."""

    context_id: str = Field(..., description="Caller-visible context identifier.")
    history_length: int = Field(
        default=CONTEXT_PAGE_DEFAULT,
        ge=0,
        le=CONTEXT_PAGE_MAX,
        description="History messages to return.",
    )
    history_offset: int = Field(
        default=0,
        ge=0,
        le=CONTEXT_OFFSET_MAX,
        description="Newest history messages to skip.",
    )
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Request metadata.")

    @field_validator("context_id")
    @classmethod
    def _context_id_not_blank(cls, value: str) -> str:
        return _require_context_id(value)


class DeleteContextParams(A2ABaseModel):
    """Parameters for ``DeleteContext``."""

    context_id: str = Field(..., description="Caller-visible context identifier.")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Request metadata.")

    @field_validator("context_id")
    @classmethod
    def _context_id_not_blank(cls, value: str) -> str:
        return _require_context_id(value)
