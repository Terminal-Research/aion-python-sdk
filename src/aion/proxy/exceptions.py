"""
Custom exceptions for AION Agent Proxy Server
"""
from typing import List, Optional
from fastapi import HTTPException

from aion.core.exceptions import AionError

# HTTPException stays a base: FastAPI's handler picks these up by
# isinstance, and losing it would turn a mapped status code into a 500.


class AgentNotFoundException(AionError, HTTPException):
    """Raised when the requested agent is not found"""

    def __init__(self, agent_id: str, available_agents: List[str]):
        super().__init__(
            status_code=404,
            detail={
                "error": f"Agent '{agent_id}' not found",
                "available_agents": available_agents
            }
        )


class AgentUnavailableException(AionError, HTTPException):
    """Raised when the agent server is not reachable"""

    def __init__(self, agent_id: str):
        super().__init__(
            status_code=503,
            detail=f"Agent '{agent_id}' is not available"
        )


class AgentTimeoutException(AionError, HTTPException):
    """Raised when the agent server times out"""

    def __init__(self, agent_id: str):
        super().__init__(
            status_code=504,
            detail=f"Timeout when connecting to agent '{agent_id}'"
        )


class AgentProxyException(AionError, HTTPException):
    """Raised when there's an error forwarding request to agent"""

    def __init__(self, agent_id: str, error: Optional[str] = None):
        detail = f"Error forwarding request to agent '{agent_id}'"
        if error:
            detail += f": {error}"
        super().__init__(
            status_code=502,
            detail=detail
        )