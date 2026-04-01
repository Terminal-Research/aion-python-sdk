from typing import Literal

from aion.shared.a2a import A2ABaseModel
from .models import Conversation, ContextsList

__all__ = [
    "GetContextSuccessResponse",
    "GetContextsListSuccessResponse",
]


class GetContextSuccessResponse(A2ABaseModel):
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


class GetContextsListSuccessResponse(A2ABaseModel):
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
