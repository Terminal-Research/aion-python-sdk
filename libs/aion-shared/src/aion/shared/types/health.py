"""
Pydantic models for AION Agent Proxy Server
"""
from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Simple health check response"""
    status: Literal["ok"] = Field(
        default="ok",
        description="Health status of the proxy server"
    )


class AgentHealthStatus(BaseModel):
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


class AgentsHealthResponse(BaseModel):
    """Health check response for all agents"""
    proxy_status: Literal["ok"] = Field(
        default="ok",
        description="Status of the proxy server itself"
    )
    overall_agents_status: Literal["healthy", "degraded"] = Field(
        description="Overall status of all agents"
    )
    agents: Dict[str, AgentHealthStatus] = Field(
        description="Health status of each configured agent"
    )
