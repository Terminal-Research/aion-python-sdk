from typing import Optional, Dict, Any

from pydantic import Field

from aion.core.a2a import A2ABaseModel

__all__ = [
    "GetContextParams",
    "GetContextsListParams",
]


class GetContextParams(A2ABaseModel):
    """Parameters for the GetContext JSON-RPC method."""

    context_id: str = Field(..., description="Unique identifier of the conversation context to retrieve.")
    history_length: Optional[int] = Field(default=None, description="Maximum number of recent tasks to return. Defaults to server-defined limit.")
    history_offset: Optional[int] = Field(default=None, description="Number of most-recent messages to skip before returning results. Defaults to 0.")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class GetContextsListParams(A2ABaseModel):
    """Parameters for the GetContexts (list) JSON-RPC method."""

    history_length: Optional[int] = Field(default=None, description="Maximum number of contexts to return. Defaults to server-defined limit.")
    history_offset: Optional[int] = Field(
        default=None,
        description="Number of most-recent contexts to skip before returning results. Defaults to 0.",
    )
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
