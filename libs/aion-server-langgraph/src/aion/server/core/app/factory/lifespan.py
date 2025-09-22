import asyncio
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, AsyncGenerator

from aion.api.http import aion_jwt_manager
from starlette.applications import Starlette

from aion.server.core.platform import aion_websocket_manager
from aion.server.services import AionAgentStartupBroadcastService, AionWebSocketService, AionGetAuthTokenService

if TYPE_CHECKING:
    pass


class AppLifespan:
    """Manages the lifecycle of the Starlette application."""

    def __init__(self, app_factory: "AppFactory"):
        """Initialize the lifespan manager with an app factory."""
        self.app_factory: "AppFactory" = app_factory

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
        # fetch token before services execution to reduce number of requests to aion api
        await AionGetAuthTokenService(jwt_manager=aion_jwt_manager).execute()

        asyncio.create_task(
            AionAgentStartupBroadcastService()
            .execute(self.app_factory.agent_config))

        asyncio.create_task(
            AionWebSocketService(websocket_manager=aion_websocket_manager)
            .start_connection())

    async def shutdown(self):
        """Handle application shutdown events."""
        # stop websocket connection with aion api
        await AionWebSocketService(websocket_manager=aion_websocket_manager).stop_connection()
        # Delegate other shutdown logic to app factory
        await self.app_factory.shutdown()
