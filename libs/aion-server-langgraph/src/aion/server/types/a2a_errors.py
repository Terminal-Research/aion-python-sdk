from typing import Any, Literal

from a2a.types import JSONRPCError


class AgentNotFoundError(JSONRPCError):
    """
    Error indicating that the requested agent was not found.
    """
    code: Literal[-32001] = -32001
    message: str = "Agent not found"
    data: Any | None = None

    @classmethod
    def with_id(cls, agent_id: str) -> "AgentNotFoundError":
        return cls(data={"graphId": agent_id})
