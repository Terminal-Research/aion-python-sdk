from typing import Optional

from starlette.endpoints import HTTPEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse

from aion.server.configs import app_settings
from aion.server.langgraph.agent import agent_manager, BaseAgent


class WellKnownSpecificAgentCardEndpoint(HTTPEndpoint):
    """HTTP endpoint for serving agent card information via well-known URI."""

    _agent: Optional[BaseAgent]

    @property
    def graph_id(self) -> str:
        """Extract agent ID from URL path parameters."""
        return self.scope["path_params"].get("graph_id")

    @property
    def agent(self):
        """Get agent instance by ID with lazy loading."""
        if hasattr(self, "_agent"):
            return self._agent

        self._agent = agent_manager.get_agent(agent_id=self.graph_id)
        return self._agent

    async def get(self, request: Request) -> JSONResponse:
        """
        Retrieve agent card for specific agent.

        Returns agent card JSON or 404 if agent not found.
        """
        if not self.agent:
            return JSONResponse(
                {"error": f"No agent found by passed id \"{self.graph_id}\""},
                status_code=404
            )

        card_to_serve = self.agent.generate_agent_card(app_settings.url)
        return JSONResponse(
            card_to_serve.model_dump(
                exclude_none=True,
                by_alias=True,
            )
        )


class WellKnownAgentsListEndpoint(HTTPEndpoint):
    """HTTP endpoint for serving list of available agents via well-known URI."""

    async def get(self, request: Request) -> JSONResponse:
        """
        Retrieve list of all registered agents.

        Returns array of agent IDs.
        """
        agents = agent_manager.agents.keys()
        return JSONResponse({"graphs_ids": list(agents)})
