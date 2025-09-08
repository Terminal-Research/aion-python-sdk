from typing import Optional

from starlette.endpoints import HTTPEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse

from aion.server.langgraph.agent import agent_manager, BaseAgent


class WellKnownSpecificAgentCardEndpoint(HTTPEndpoint):
    """HTTP endpoint for serving agent card information via well-known URI."""

    _agent: Optional[BaseAgent]

    @property
    def agent_id(self) -> str:
        """Extract agent ID from URL path parameters."""
        return self.scope["path_params"].get("agent_id")

    @property
    def agent(self):
        """Get agent instance by ID with lazy loading."""
        if hasattr(self, "_agent"):
            return self._agent

        self._agent = agent_manager.get_agent(agent_id=self.agent_id)
        return self._agent

    async def get(self, request: Request) -> JSONResponse:
        """
        Retrieve agent card for specific agent.

        Returns agent card JSON or 404 if agent not found.
        """
        if not self.agent:
            return JSONResponse(
                {"error": f"No agent found by passed id \"{self.agent_id}\""},
                status_code=404
            )

        card_to_serve = self.agent.get_agent_card("http://localhost:10000")
        return JSONResponse(
            card_to_serve.model_dump(
                exclude_none=True,
                by_alias=True,
            )
        )
