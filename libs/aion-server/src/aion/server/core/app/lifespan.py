"""FastAPI application lifespan: startup (tracing) and shutdown orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context import RequestScopeRuntimeContextProvider
from aion.server.opentelemetry import init_tracing
from fastapi import FastAPI

if TYPE_CHECKING:
    from aion.server.core.app import AppFactory

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
        # Register runtime context provider so aion-api-client can resolve
        # the active principal selector without depending on aion-server.
        AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())

        # SETUP OPEN-TELEMETRY
        init_tracing()

        # Startup settlement is intentionally disabled. The durable task store
        # is shared by multiple agent processes, so this process cannot safely
        # distinguish its predecessor's orphaned work from work another agent
        # is still executing.
        await self._settle_orphaned_tasks()

    async def _settle_orphaned_tasks(self):
        """Close out tasks a killed predecessor left running in the store.

        Runs here, in startup, because it must happen before the process serves
        anything: while such a task is still active, a client polling it cannot
        tell a dead run from a live one, and this process may hand it back as
        the resumable task of its context.

        A store that cannot answer only costs the reap. Failing the startup
        instead would take the agent down over tasks that are already stale,
        and the same store is about to report its condition on the first
        request anyway.
        """
        # try:
        #     await settle_orphaned_tasks(self.app_factory.store_manager.get_store())
        # except Exception as exc:
        #     logger.error(
        #         "Failed to settle tasks left by a previous process", exc_info=exc
        #     )
        pass

    async def shutdown(self):
        """Handle application shutdown events."""
        await self.app_factory.shutdown()
