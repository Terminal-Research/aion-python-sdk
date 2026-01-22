"""Application factory for orchestrating initialization of all components.

This module provides AppFactory which coordinates the initialization of
database, plugins, agent, and FastAPI application using dependency injection.
"""

from typing import Optional

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from aion.shared.agent import AionAgent
from aion.shared.logging import get_logger
from fastapi import FastAPI

from aion.server.agent import AionAgentRequestExecutor, AgentFactory
from aion.server.core.app.a2a_fastapi import AionA2AFastAPIApplication
from aion.server.core.middlewares import TracingMiddleware, AionContextMiddleware
from aion.server.core.request_handlers import AionRequestHandler
from aion.server.db import DbFactory
from aion.server.plugins import PluginFactory
from aion.server.tasks import StoreManager
from .lifespan import AppLifespan

logger = get_logger()


class AppFactory:
    """Factory for creating and initializing agent applications.

    This factory orchestrates the initialization of all application components
    in the correct order using dependency injection. It coordinates:
    - Database initialization
    - Plugin discovery and setup
    - Agent building
    - FastAPI application creation
    """

    def __init__(
            self,
            aion_agent: AionAgent,
            db_factory: DbFactory,
            agent_factory: AgentFactory,
            plugin_factory: PluginFactory,
            store_manager: StoreManager,
            startup_callback=None
    ):
        """Initialize factory with all dependencies via dependency injection.

        Args:
            aion_agent: Agent instance to build and run
            db_factory: Factory for database initialization
            agent_factory: Factory for agent building
            plugin_factory: Factory for plugin lifecycle management (already has db_manager)
            store_manager: Task store manager
            startup_callback: Optional callback to call after initialization
        """
        self.aion_agent = aion_agent
        self.db_factory = db_factory
        self.agent_factory = agent_factory
        self.plugin_factory = plugin_factory
        self.store_manager = store_manager
        self.startup_callback = startup_callback

        # Application components (created during initialization)
        self.a2a_app: Optional[AionA2AFastAPIApplication] = None
        self.fastapi_app: Optional[FastAPI] = None

    async def initialize(self):
        """Initialize the application factory.

        Returns:
            Self if initialization successful, None if initialization failed
        """
        try:
            await self._initialize()
            return self
        except Exception as exc:
            logger.error("Failed to initialize application factory", exc_info=exc)
            await self.shutdown()
            return None

    async def _initialize(self) -> None:
        """Initialize all application components in sequence."""
        logger.debug("Initializing application for agent '%s'", self.aion_agent.id)

        # 1. Initialize database
        await self.db_factory.initialize()

        # 2. Build application (A2A + FastAPI)
        await self._build_app()

        # 3. Initialize plugins - Phase 1: infrastructure setup
        await self.plugin_factory.initialize()

        # 4. Build agent
        await self.agent_factory.build()

        # 5. Configure app - Phase 2: integrate plugins with built app and agent
        await self.plugin_factory.configure_app(self.fastapi_app, self.aion_agent)

        logger.info("Agent '%s' initialized at http://%s:%s",
                    self.aion_agent.id, self.aion_agent.host, self.aion_agent.port)

    async def _build_app(self) -> None:
        """Build A2A and FastAPI applications with all necessary configuration."""
        if self.a2a_app or self.fastapi_app:
            raise RuntimeError("Applications are already created")

        # Create request handler
        request_handler = await self._create_request_handler()

        # Create A2A application
        self.a2a_app = AionA2AFastAPIApplication(
            aion_agent=self.aion_agent,
            http_handler=request_handler
        )

        # Build FastAPI application from A2A app
        lifespan = AppLifespan(app_factory=self)
        self.fastapi_app = self.a2a_app.build(lifespan=lifespan.executor)
        self._add_extra_middlewares()

    async def _create_request_handler(self) -> AionRequestHandler:
        """Create and configure the request handler with task store and agent executor."""

        # Initialize task store
        self.store_manager.initialize()
        task_store = self.store_manager.get_store()

        return AionRequestHandler(
            agent_executor=AionAgentRequestExecutor(self.aion_agent),
            task_store=task_store,
            push_config_store=InMemoryPushNotificationConfigStore()
        )

    def _add_extra_middlewares(self):
        """Adds extra middleware to the FastAPI application.

        This method is used to extend the FastAPI application's functionality
        with additional middleware that are not included by default.
        """
        self.fastapi_app.add_middleware(TracingMiddleware)
        self.fastapi_app.add_middleware(AionContextMiddleware)

    async def shutdown(self) -> None:
        """Shutdown the application and cleanup resources."""
        logger.info("Shutting down application factory")

        # Cleanup plugins (via PluginFactory)
        if self.plugin_factory.is_initialized():
            try:
                await self.plugin_factory.teardown_all()
                logger.info("Plugins cleaned up")
            except Exception as exc:
                logger.error("Error cleaning up plugins", exc_info=exc)

        # Cleanup database (via DbFactory)
        if self.db_factory.is_initialized:
            try:
                await self.db_factory.cleanup()
                logger.info("Database cleaned up")
            except Exception as exc:
                logger.error("Error cleaning up database", exc_info=exc)

    @property
    def is_initialized(self) -> bool:
        """Check if the factory is fully initialized.

        Returns:
            bool: True if both A2A app and FastAPI app exist
        """
        return (
                self.a2a_app is not None and
                self.fastapi_app is not None
        )

    def get_fastapi_app(self) -> Optional[FastAPI]:
        """Get the FastAPI application instance.

        Returns:
            Optional[FastAPI]: The FastAPI app if initialized, None otherwise
        """
        return self.fastapi_app
