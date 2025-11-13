from typing import Literal, Optional, Dict

from pydantic import BaseModel, Field


class AgentHealthInfo(BaseModel):
    """Health status of a single agent"""
    status: Literal["healthy", "unhealthy", "unavailable", "timeout", "error"] = Field(
        description="Current status of the agent"
    )
    url: str = Field(
        description="Base URL of the agent"
    )
    status_code: Optional[int] = Field(
        default=None,
        description="HTTP status code from the agent (if available)"
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message (if any)"
    )


class SystemHealthResponse(BaseModel):
    """Health check response for proxy and attached agents"""
    proxy_status: Literal["healthy"] = Field(
        default="healthy",
        description="Status of the proxy server itself"
    )
    overall_agents_status: Literal["healthy", "degraded"] = Field(
        description="Overall status of all agents"
    )
    agents: Dict[str, AgentHealthInfo] = Field(
        description="Health status of each configured agent"
    )
