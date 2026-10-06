"""FastAPI application lifespan: startup (tracing) and shutdown orchestration."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import TYPE_CHECKING, AsyncGenerator

from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context import RequestScopeRuntimeContextProvider
from aion.server.opentelemetry import init_tracing
from fastapi import FastAPI

if TYPE_CHECKING:
    from aion.server.core.app import AppFactory

logger = logging.getLogger(__name__)

class AppLifespan:
    """Manages the lifecycle of the FastAPI application.

    The connection to the Aion platform is deliberately not started here. It
    announces the presence of a *deployment version*, which is registered once by
    the ``aion serve`` process that owns these agents - so the socket lives there
    too, opened only if that registration succeeded. Held per agent process it was
    both duplicated across agents and connected regardless of whether the platform
    had ever heard of the version, which made an unreachable agent look healthy.
    """

    def __init__(self, app_factory: AppFactory):
        """Initialize the lifespan manager with an app factory."""
        self.app_factory: AppFactory = app_factory
        self._context_deletions: asyncio.Task | None = None

    @asynccontextmanager
    async def executor(self, app: FastAPI) -> AsyncGenerator[None, None]:
        """Async context manager for application lifespan management."""
        try:
            await self.startup()

            # call startup callback if presented
            if self.app_factory.startup_callback is not None:
                self.app_factory.startup_callback()

            yield
        finally:
            await self.shutdown()

    async def startup(self):
        """Handle application startup events."""
        # Register runtime context provider so aion.api can resolve
        # the active principal selector without importing aion.server.
        AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())

        # SETUP OPEN-TELEMETRY
        init_tracing()

        self._resume_context_deletions()

    def _resume_context_deletions(self):
        """Finish, in the background, the context deletions a previous process left ``deleting``.

        In the background because a deletion waits for its tasks'
        cancellations, which can take as long as the agent needs to stop;
        serving must not wait for that.
        """
        handler = self.app_factory._request_handler
        if handler is None:
            return
        self._context_deletions = asyncio.create_task(
            handler.resume_pending_context_deletions(), name="resume-context-deletions"
        )

    async def shutdown(self):
        """Handle application shutdown events."""
        if self._context_deletions is not None and not self._context_deletions.done():
            # Durable: whatever it did not finish, the next start resumes.
            self._context_deletions.cancel()
        await self.app_factory.shutdown()
