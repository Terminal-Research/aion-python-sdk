from typing import Optional

from a2a.server.apps.jsonrpc import DefaultCallContextBuilder
from a2a.server.context import ServerCallContext
from pydantic import Field


class AionServerCallContext(ServerCallContext):
    """Extended server call context with agent-specific information.

    Adds agent_id field to the base ServerCallContext for tracking
    which agent is associated with the current request.
    """
    agent_id: Optional[str] = Field(default=None)


class AionCallContextBuilder(DefaultCallContextBuilder):
    """Context builder for Aion server calls.

    Extends the default call context builder to include agent ID
    extracted from request path parameters.
    """

    def build(self, request) -> AionServerCallContext:
        """Build AionServerCallContext from the incoming request.

        Args:
            request: The incoming request object containing path parameters.

        Returns:
            AionServerCallContext with agent_id extracted from path params.
        """
        base_ctx = super().build(request)
        return AionServerCallContext(
            **base_ctx.model_dump(),
            agent_id=request.path_params.get("graph_id"),
        )
