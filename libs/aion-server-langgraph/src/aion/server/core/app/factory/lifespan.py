import asyncio
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, AsyncGenerator

from starlette.applications import Starlette

from aion.server.core.platform import aion_websocket_manager

if TYPE_CHECKING:
    from .factory import AppFactory


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
        # start websocket connection with aion api
        asyncio.create_task(aion_websocket_manager.start())

    async def shutdown(self):
        """Handle application shutdown events."""
        # stop websocket connection with aion api
        with suppress(Exception):
            await aion_websocket_manager.stop()
        # Delegate other shutdown logic to app factory
        await self.app_factory.shutdown()
