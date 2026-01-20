from pathlib import Path
from typing import Optional

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from aion.shared.agent import AionAgent
from aion.shared.logging import get_logger
from aion.shared.settings import db_settings
from fastapi import FastAPI

from aion.server.core.app.a2a_fastapi import AionA2AFastAPIApplication
from aion.server.core.middlewares import TracingMiddleware, AionContextMiddleware
from aion.server.core.request_handlers import AionRequestHandler
from aion.server.agent import AionAgentRequestExecutor
from aion.server.db import verify_connection
from aion.server.db.manager import DbManager
from aion.server.db.migrations import upgrade_to_head
from aion.server.tasks import StoreManager
from aion.server.plugins.manager import PluginManager
from .lifespan import AppLifespan

logger = get_logger()


class AppContext:
    """Application context with settings and managers.

    Attributes:
        db_manager: Database connection manager
        store_manager: Task store manager for handling data storage
        plugin_manager: Plugin manager for handling plugin lifecycle
    """

    def __init__(
            self,
            db_manager: DbManager,
            store_manager: StoreManager,
            plugin_manager: PluginManager
    ):
        self.db_manager = db_manager
        self.store_manager = store_manager
        self.plugin_manager = plugin_manager


class AppFactory:
    """Factory for creating and initializing agent applications.

    Attributes:
        base_path: Base path for resolving module imports
        context: Application context with shared resources
        startup_callback: Optional callback to call after initialization
        a2a_app: Agent-to-agent communication application
        fastapi_app: Main FastAPI web application
    """

    def __init__(
            self,
            aion_agent: AionAgent,
            context: AppContext,
            startup_callback=None
    ):
        """Initialize factory with agent configuration and context.

        Args:
            context: Application context with shared resources
            startup_callback: Optional callback to call after initialization
        """
        self.aion_agent = aion_agent
        self.context = context

        # Application components
        self.a2a_app: Optional[AionA2AFastAPIApplication] = None
        self.fastapi_app: Optional[FastAPI] = None
        self.startup_callback = startup_callback

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
        """Initialize all application components in sequence:
        database, plugins, agent building (framework discovery), A2A app, and FastAPI app.

        Order is critical:
        1. Init DB - creates connection pool
        2. Init plugins - registers adapters, setup with db_manager
        3. Build agent - discovers framework from registered adapters
        4. Create A2A app
        5. Build FastAPI app
        """
        logger.debug("Initializing application for agent '%s'", self.aion_agent.id)

        # Initialize database
        await self._init_db()

        # Initialize plugins (registers framework adapters)
        await self._init_plugins()

        # Build agent (discovers framework and creates executor)
        await self._build_agent()

        # Create A2A application
        await self._create_a2a_app()

        # Build FastAPI application
        await self._build_fastapi_app()

        logger.info("Agent '%s' initialized at http://%s:%s",
                    self.aion_agent.id, self.aion_agent.host, self.aion_agent.port)

    async def _create_a2a_app(self) -> None:
        """Create and configure the A2A application with agent card and request handler."""
        if self.a2a_app:
            raise RuntimeError("A2A application is already created")

        # Create request handler
        request_handler = await self._create_request_handler()

        self.a2a_app = AionA2AFastAPIApplication(
            aion_agent=self.aion_agent,
            http_handler=request_handler
        )

    async def _create_request_handler(self) -> AionRequestHandler:
        """Create and configure the request handler with task store and agent executor."""
        # Initialize task store
        self.context.store_manager.initialize()
        task_store = self.context.store_manager.get_store()

        return AionRequestHandler(
            agent_executor=AionAgentRequestExecutor(self.aion_agent),
            task_store=task_store,
            push_config_store=InMemoryPushNotificationConfigStore()
        )

    async def _build_fastapi_app(self) -> None:
        """Build the FastAPI application from A2A app and add middlewares."""
        if not self.a2a_app:
            raise RuntimeError("A2A application must be created before building FastAPI app")

        lifespan = AppLifespan(app_factory=self)
        self.fastapi_app = self.a2a_app.build(lifespan=lifespan.executor)
        self.fastapi_app.add_middleware(TracingMiddleware)
        self.fastapi_app.add_middleware(AionContextMiddleware)

    async def _init_db(self) -> None:
        """Initialize database connection, run migrations, or skip if POSTGRES_URL not set."""
        pg_url = db_settings.pg_url
        if not pg_url:
            logger.debug("POSTGRES_URL environment variable not set, using in-memory data store")
            return

        # Verify connection
        is_connection_verified = await verify_connection(pg_url)
        if not is_connection_verified:
            logger.warning("Cannot verify postgres connection")
            return

        # Initialize database manager
        try:
            await self.context.db_manager.initialize(pg_url)
        except Exception as exc:
            logger.error("Failed to initialize database", exc_info=exc)
            await self._cleanup_db()
            return

        # Run migrations
        try:
            await upgrade_to_head()
            logger.info("Database migrations completed successfully")
        except Exception as exc:
            logger.error("Migration failed: %s", exc, exc_info=True)
            await self._cleanup_db()
            return

    async def _init_plugins(self) -> None:
        """Discover, register and setup all plugins.

        This step registers framework adapters before agent building.
        """
        try:
            # Discover and register plugins
            await self.context.plugin_manager.discover_and_register()

            # Setup plugins with dependencies
            await self.context.plugin_manager.setup_all(
                db_manager=self.context.db_manager
            )
        except Exception as exc:
            logger.error("Failed to initialize plugins", exc_info=exc)
            await self._cleanup_plugins()

    async def _build_agent(self) -> None:
        """Build the agent by discovering framework and creating executor.

        This must be called after plugins are initialized so that framework
        adapters are registered.
        """
        if self.aion_agent.is_built:
            logger.debug(f"Agent '{self.aion_agent.id}' is already built, skipping")
            return

        try:
            await self.aion_agent.build()
        except Exception as exc:
            raise

    async def shutdown(self) -> None:
        """Shutdown the application and cleanup resources."""
        logger.info("Shutting down application factory")
        await self._cleanup_plugins()
        await self._cleanup_db()

    async def _cleanup_db(self) -> None:
        """Close database connections if initialized."""
        if not self.context.db_manager.is_initialized:
            return

        try:
            await self.context.db_manager.close()
            logger.info("Database connections closed")
        except Exception as exc:
            logger.error("Error closing database", exc_info=exc)

    async def _cleanup_plugins(self) -> None:
        """Teardown all plugins if initialized."""
        if not self.context.plugin_manager.is_initialized():
            return

        try:
            await self.context.plugin_manager.teardown_all()
            logger.info("Plugins cleaned up")
        except Exception as exc:
            logger.error("Error cleaning up plugins", exc_info=exc)

    # Properties for accessing application components

    @property
    def is_initialized(self) -> bool:
        """Check if the factory is fully initialized (agent, A2A app, and FastAPI app exist)."""
        return (
                self.a2a_app is not None and
                self.fastapi_app is not None
        )

    def get_fastapi_app(self) -> Optional[FastAPI]:
        """Get the FastAPI application instance."""
        return self.fastapi_app
