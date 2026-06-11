"""Additional HTTP routes (health check and configuration) registered on the FastAPI app."""

from aion.server.agent.aion_agent import AionAgent
from aion.core.config import AgentConfigurationCollector
from aion.core.http import HealthResponse
from aion.server.utils.deployment import get_protocol_version
from fastapi import FastAPI
from starlette.responses import JSONResponse

from aion.server.constants import CONFIGURATION_FILE_URL, HEALTH_CHECK_URL
from aion.server.types import ConfigurationFileResponse


class AionExtraHTTPRoutes:
    """Registers Aion-specific HTTP endpoints (health and configuration) on a FastAPI app."""

    def __init__(self, agent: AionAgent):
        self.agent = agent

    def register(self, app: FastAPI):
        """Attach health-check and configuration routes to the given FastAPI application."""
        app.add_api_route(
            HEALTH_CHECK_URL,
            self._handle_health_check,
            methods=["GET"],
            response_class=JSONResponse,
        )

        app.add_api_route(
            CONFIGURATION_FILE_URL,
            self._handle_get_configuration_info,
            methods=["GET"],
            response_class=JSONResponse,
        )

    @staticmethod
    async def _handle_health_check() -> JSONResponse:
        """Return a 200 OK health response."""
        return JSONResponse(HealthResponse().model_dump())

    async def _handle_get_configuration_info(self) -> JSONResponse:
        """Return agent protocol version and collected configuration as JSON."""
        response = ConfigurationFileResponse(
            protocolVersion=get_protocol_version(),
            configuration=AgentConfigurationCollector(
                self.agent.config.configuration
            ).collect(),
        )
        return JSONResponse(response.model_dump())
