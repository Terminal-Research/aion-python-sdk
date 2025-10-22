from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator, Optional

from aion.api.http import aion_jwt_manager
from aion.shared.opentelemetry import init_tracing
from aion.shared.settings import api_settings
from starlette.applications import Starlette

from aion.server import services as aion_services
from aion.server.core.platform import AionWebSocketManager, WebsocketTransportFactory

if TYPE_CHECKING:
    from aion.server.core.app import AppFactory


class AppLifespan:
    """Manages the lifecycle of the Starlette application."""

    def __init__(self, app_factory: AppFactory):
        """Initialize the lifespan manager with an app factory."""
        self.app_factory: AppFactory = app_factory
        self._websocket_manager: Optional[AionWebSocketManager] = None

    async def get_websocket_manager(self, create: bool = True) -> AionWebSocketManager:
        """Return the AionWebSocketManager instance."""
        if not self._websocket_manager and create:
            self._websocket_manager = AionWebSocketManager(
                ws_transport_factory=WebsocketTransportFactory(
                    ws_url=api_settings.ws_gql_url,
                    auth_manager=aion_services.AionAuthManagerService(jwt_manager=aion_jwt_manager)
                )
            )
        return self._websocket_manager

    @asynccontextmanager
    async def executor(self, app: Starlette) -> AsyncGenerator[None, None]:
        """Async context manager for application lifespan management."""
        try:
            await self.startup()
            yield
        finally:
            await self.shutdown()

    async def startup(self):
        """Handle application startup events."""
        # SETUP OPEN-TELEMETRY
        init_tracing()

        # fetch token before services execution to reduce number of requests to aion api
        auth_token = await aion_services.AionAuthManagerService(jwt_manager=aion_jwt_manager).get_token()

        if auth_token:
            # START WEBSOCKET CONNECTION WITH AION
            ws_manager = await self.get_websocket_manager(create=True)
            asyncio.create_task(
                aion_services.AionWebSocketService(websocket_manager=ws_manager)
                .start_connection())

    async def shutdown(self):
        """Handle application shutdown events."""
        # stop websocket connection with aion api
        ws_manager = await self.get_websocket_manager(create=False)
        if ws_manager:
            await aion_services.AionWebSocketService(websocket_manager=ws_manager).stop_connection()

        # Delegate other shutdown logic to app factory
        await self.app_factory.shutdown()
