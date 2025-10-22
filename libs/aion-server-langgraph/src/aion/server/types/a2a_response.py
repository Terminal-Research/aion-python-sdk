from typing import Literal

from aion.shared.base import A2ABaseModel
from a2a.types import JSONRPCErrorResponse
from pydantic import RootModel

from .a2a_models import Conversation, ContextsList

__all__ = [
    "GetContextSuccessResponse",
    "GetContextResponse",
    "GetContextsListSuccessResponse",
    "GetContextsListResponse",
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

class GetContextResponse(
    RootModel[JSONRPCErrorResponse | GetContextSuccessResponse]
):
    root: JSONRPCErrorResponse | GetContextSuccessResponse


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

class GetContextsListResponse(
    RootModel[JSONRPCErrorResponse | GetContextsListSuccessResponse]
):
    root: JSONRPCErrorResponse | GetContextsListSuccessResponse
