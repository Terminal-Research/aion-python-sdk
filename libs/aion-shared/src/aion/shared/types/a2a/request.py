from __future__ import annotations

from typing import Literal

from aion.shared.a2a import A2ABaseModel
from .request_params import GetContextParams, GetContextsListParams

__all__ = [
    "GetContextRequest",
    "GetContextsListRequest",
]


class GetContextRequest(A2ABaseModel):
    id: str | int
    """
    An identifier established by the Client that MUST contain a String, Number.
    Numbers SHOULD NOT contain fractional parts.
    """
    jsonrpc: Literal['2.0'] = '2.0'
    """
    Specifies the version of the JSON-RPC protocol. MUST be exactly "2.0".
    """
    method: Literal['context/get'] = 'context/get'
    """
    A String containing the name of the method to be invoked.
    """
    params: GetContextParams
    """
    A Structured value that holds the parameter values to be used during the invocation of the method.
    """


class GetContextsListRequest(A2ABaseModel):
    id: str | int
    """
    An identifier established by the Client that MUST contain a String, Number.
    Numbers SHOULD NOT contain fractional parts.
    """
    jsonrpc: Literal['2.0'] = '2.0'
    """
    Specifies the version of the JSON-RPC protocol. MUST be exactly "2.0".
    """
    method: Literal['contexts/get'] = 'contexts/get'
    """
    A String containing the name of the method to be invoked.
    """
    params: GetContextsListParams
    """
    A Structured value that holds the parameter values to be used during the invocation of the method.
    """
