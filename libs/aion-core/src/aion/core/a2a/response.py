from typing import Literal

from aion.core.a2a import A2ABaseModel
from .models import Conversation, ContextsList

__all__ = [
    "GetContextSuccessResponse",
    "GetContextsListSuccessResponse",
]


class GetContextSuccessResponse(A2ABaseModel):
    """JSON-RPC 2.0 success response for the GetContext method."""

    id: str | int | None = None
    """
    An identifier established by the Client that MUST contain a String, Number.
    Numbers SHOULD NOT contain fractional parts.
    """
    jsonrpc: Literal['2.0'] = '2.0'
    """
    Specifies the version of the JSON-RPC protocol. MUST be exactly "2.0".
    """
    result: Conversation
    """
    The retrieved conversation payload including history, artifacts, and task status.
    """


class GetContextsListSuccessResponse(A2ABaseModel):
    """JSON-RPC 2.0 success response for the GetContexts (list) method."""

    id: str | int | None = None
    """
    An identifier established by the Client that MUST contain a String, Number.
    Numbers SHOULD NOT contain fractional parts.
    """
    jsonrpc: Literal['2.0'] = '2.0'
    """
    Specifies the version of the JSON-RPC protocol. MUST be exactly "2.0".
    """
    result: ContextsList
    """
    List of context ids
    """
