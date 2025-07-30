from a2a._base import A2ABaseModel


from pydantic import Field
from typing import Optional, Dict, Any


__all__ = [
    "GetContextParams",
    "GetContextsListParams",
]


class GetContextParams(A2ABaseModel):
    context_id: str = Field(..., description="Context identifier")
    history_length: Optional[int] = Field(None, description="Number of recent tasks to be retrieved")
    history_offset: Optional[int] = Field(None, description="The offset starting with the most recent message")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class GetContextsListParams(A2ABaseModel):
   history_length: Optional[int] = Field(None, description="Number of recent contexts to be retrieved")
   history_offset: Optional[int] = Field(None, description="The offset starting with the most recent context from which the server should start returning history")
   metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
