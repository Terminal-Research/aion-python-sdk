"""JSON-RPC 2.0 request envelopes for the Context extension's methods.

See: https://docs.aion.to/a2a/extensions/aion/context/1.0.0
"""

from __future__ import annotations

from typing import Literal

from aion.core.a2a import A2ABaseModel
from .request_params import DeleteContextParams, GetContextParams, GetContextsParams

__all__ = [
    "DeleteContextRequest",
    "GetContextRequest",
    "GetContextsRequest",
]


class _ContextRequest(A2ABaseModel):
    id: str | int
    """
    An identifier established by the Client that MUST contain a String, Number.
    Numbers SHOULD NOT contain fractional parts.
    """
    jsonrpc: Literal['2.0'] = '2.0'
    """
    Specifies the version of the JSON-RPC protocol. MUST be exactly "2.0".
    """


class GetContextsRequest(_ContextRequest):
    """JSON-RPC 2.0 request envelope for ``GetContexts``."""

    method: Literal['GetContexts'] = 'GetContexts'
    params: GetContextsParams = GetContextsParams()


class GetContextRequest(_ContextRequest):
    """JSON-RPC 2.0 request envelope for ``GetContext``."""

    method: Literal['GetContext'] = 'GetContext'
    params: GetContextParams


class DeleteContextRequest(_ContextRequest):
    """JSON-RPC 2.0 request envelope for ``DeleteContext``."""

    method: Literal['DeleteContext'] = 'DeleteContext'
    params: DeleteContextParams
