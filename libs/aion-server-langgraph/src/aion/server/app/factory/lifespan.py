from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

from starlette.applications import Starlette

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
        pass

    async def shutdown(self):
        """Handle application shutdown events."""
        # Delegate shutdown logic to app factory
        await self.app_factory.shutdown()
