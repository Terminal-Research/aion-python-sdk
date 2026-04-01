from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "HealthResponse",
]


class HealthResponse(BaseModel):
    """Simple health check response"""
    status: Literal["healthy"] = Field(
        default="healthy",
        description="Health status of the proxy server"
    )
