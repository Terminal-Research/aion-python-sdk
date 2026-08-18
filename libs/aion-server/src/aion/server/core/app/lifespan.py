"""FastAPI application lifespan: startup (tracing) and shutdown orchestration."""

from __future__ import annotations

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

        # Safe to run now that a process can tell its predecessor's abandoned
        # work from work another instance is still executing: an unexpired
        # lease says the task has a live owner. It is a no-op until the reaper
        # switch is on.
        await self._settle_orphaned_tasks()

    async def _settle_orphaned_tasks(self):
        """Run the ownership reconciler once before the server accepts work.

        The reconciler distinguishes an expired claim from a live one and also
        covers the older active-task-without-claim anomaly. It does nothing
        unless the reaper is enabled, which is a separate deployment from the
        heartbeat it depends on. A cleanup failure must not make a healthy
        process fail startup; the periodic pass and the next process start
        remain available as fallbacks.
        """
        provider = self.app_factory.store_manager.get_ownership_provider()
        try:
            settled = await provider.reconcile()
        except Exception:
            logger.warning("Failed to reconcile task ownership during startup", exc_info=True)
            return
        if settled:
            logger.info("Settled %d orphaned task(s) during startup", settled)

    async def shutdown(self):
        """Handle application shutdown events."""
        await self.app_factory.shutdown()
