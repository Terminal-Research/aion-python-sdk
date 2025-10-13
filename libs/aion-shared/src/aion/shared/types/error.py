from typing import Optional, List, Union

from pydantic import BaseModel, Field

__all__ = [
    "ErrorDetail",
    "ErrorResponse",
]


class ErrorDetail(BaseModel):
    """Error detail information"""
    error: str = Field(
        description="Error message"
    )
    available_agents: Optional[List[str]] = Field(
        default=None,
        description="List of available agent IDs (for 404 errors)"
    )


class ErrorResponse(BaseModel):
    """Standard error response"""
    detail: Union[str, ErrorDetail] = Field(
        description="Error details"
    )
