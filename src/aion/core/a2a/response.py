"""JSON-RPC 2.0 success envelopes for the Context extension's methods.

See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
"""

from typing import Literal

from aion.core.a2a import A2ABaseModel
from .models import ContextSummaryList, ContextView, DeleteContextResult

__all__ = [
    "DeleteContextSuccessResponse",
    "GetContextSuccessResponse",
    "GetContextsSuccessResponse",
]


class _ContextSuccessResponse(A2ABaseModel):
    id: str | int | None = None
    """
    An identifier established by the Client that MUST contain a String, Number.
    Numbers SHOULD NOT contain fractional parts.
    """
    jsonrpc: Literal['2.0'] = '2.0'
    """
    Specifies the version of the JSON-RPC protocol. MUST be exactly "2.0".
    """


class GetContextsSuccessResponse(_ContextSuccessResponse):
    """Success response for ``GetContexts``: a raw array of context summaries."""

    result: ContextSummaryList


class GetContextSuccessResponse(_ContextSuccessResponse):
    """Success response for ``GetContext``."""

    result: ContextView


class DeleteContextSuccessResponse(_ContextSuccessResponse):
    """Success response for ``DeleteContext``."""

    result: DeleteContextResult
