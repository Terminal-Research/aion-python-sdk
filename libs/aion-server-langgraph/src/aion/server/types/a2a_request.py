from __future__ import annotations

from typing import Literal, Union

from aion.shared.base import A2ABaseModel
from a2a.types import A2ARequest
from pydantic import RootModel

from .a2a_request_params import GetContextParams, GetContextsListParams

__all__ = [
    "GetContextRequest",
    "GetContextsListRequest",
    "CustomA2ARequest",
    "ExtendedA2ARequest",
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


CustomA2ARequestTypes = Union[
    GetContextRequest,
    GetContextsListRequest,
]
class CustomA2ARequest(RootModel[CustomA2ARequestTypes]):
    root: CustomA2ARequestTypes


class ExtendedA2ARequest(CustomA2ARequest, A2ARequest):
    root: Union[
        A2ARequest.model_fields['root'].annotation,
        CustomA2ARequest.model_fields['root'].annotation
    ]
