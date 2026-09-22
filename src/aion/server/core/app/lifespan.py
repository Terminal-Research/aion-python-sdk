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
        # Register runtime context provider so aion.api can resolve
        # the active principal selector without importing aion.server.
        AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())

        # SETUP OPEN-TELEMETRY
        init_tracing()

        # One reconciliation pass before this process serves anything, so
        # ownership that was already due for reclaiming is not left waiting
        # for the first periodic pass. It settles nothing on the strength of
        # this process having started: what it acts on is an expired claim, an
        # overdue cancellation, or an active task with no claim at all.
        await self._reconcile_task_ownership()
        self._start_event_listener()

    def _start_event_listener(self):
        """Start the cross-pod task-event listener, if this store has one.

        ``None`` for the in-memory backend - see ``StoreManager.initialize``.
        Started here rather than by the store manager itself because it needs
        a running event loop, which does not exist yet when stores are built.
        Any provider subscription made before this point (see
        ``PostgresOwnershipProvider``'s ``event_listener`` argument) is
        unaffected: subscribing only touches the listener's local maps, not
        its connection.
        """
        listener = self.app_factory.store_manager.get_event_listener()
        if listener is not None:
            listener.start()

    async def _reconcile_task_ownership(self):
        """Run the ownership reconciler once before the server accepts work.

        One pass of the same mechanism the periodic reaper runs, not a second
        recovery path, and it settles the same three things that pass does:
        a task whose claim has expired, so its owner stopped renewing and is
        presumed gone; a cancellation the owner has not honored within its
        grace period, whose lease is still being renewed normally; and a task
        presented as active with no claim behind it at all, which no lease
        expiry can ever surface. A task whose claim is live and current is
        left to the process that holds it - a new process starting is not
        evidence that the previous owner died.

        It does nothing unless the reaper is enabled, which is a separate
        deployment from the heartbeat it depends on. A reconciliation failure
        must not make a healthy process fail startup; the periodic pass
        remains the primary recovery mechanism either way.
        """
        provider = self.app_factory.store_manager.get_ownership_provider()
        try:
            settled = await provider.reconcile()
        except Exception:
            logger.warning("Failed to reconcile task ownership during startup", exc_info=True)
            return
        if settled:
            logger.info("Reconciled task ownership at startup: settled %d task(s)", settled)

    async def shutdown(self):
        """Handle application shutdown events."""
        listener = self.app_factory.store_manager.get_event_listener()
        if listener is not None:
            await listener.stop()
        await self.app_factory.shutdown()
