from aion.shared.agent import AionAgent
from aion.shared.config import AgentConfigurationCollector
from aion.shared.types import HealthResponse
from aion.shared.utils.deployment import get_protocol_version
from fastapi import FastAPI
from starlette.responses import JSONResponse

from aion.server.types import ConfigurationFileResponse
from aion.server.constants import CONFIGURATION_FILE_URL, HEALTH_CHECK_URL


class AionExtraHTTPRoutes:
    def __init__(self, agent: AionAgent):
        self.agent = agent

    def register(self, app: FastAPI):
        app.add_api_route(
            HEALTH_CHECK_URL,
            self._handle_health_check,
            methods=["GET"],
            response_class=JSONResponse,
        )

        app.add_api_route(
            CONFIGURATION_FILE_URL,
            self._handle_health_check,
            methods=["GET"],
            response_class=JSONResponse,
        )

    @staticmethod
    async def _handle_health_check() -> JSONResponse:
        return JSONResponse(HealthResponse().model_dump())

    async def _handle_get_configuration_info(self) -> JSONResponse:
        response = ConfigurationFileResponse(
            protocolVersion=get_protocol_version(),
            configuration=AgentConfigurationCollector(
                self.agent.config.configuration
            ).collect(),
        )
        return JSONResponse(response.model_dump())
