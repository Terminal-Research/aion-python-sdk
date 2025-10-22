from pathlib import Path
from pathlib import Path
from typing import Optional

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import AgentCard
from aion.shared.aion_config import AgentConfig
from aion.shared.logging import get_logger
from aion.shared.settings import db_settings
from starlette.applications import Starlette

from aion.server.core.app.a2a_starlette import AionA2AStarletteApplication
from aion.server.core.middlewares import TracingMiddleware, AionContextMiddleware
from aion.server.core.request_handlers import AionRequestHandler
from aion.server.db import verify_connection
from aion.server.db.manager import DbManager
from aion.server.db.migrations import upgrade_to_head
from aion.server.langgraph.a2a import LanggraphAgentExecutor
from aion.server.langgraph.agent import BaseAgent, AgentManager
from aion.server.tasks import StoreManager
from .lifespan import AppLifespan

logger = get_logger()


class AppContext:
    """Application context with settings and managers.

    Attributes:
        db_manager: Database connection manager
        store_manager: Task store manager for handling data storage
    """

    def __init__(
            self,
            db_manager: DbManager,
            store_manager: StoreManager
    ):
        self.db_manager = db_manager
        self.store_manager = store_manager


class AppFactory:
    """Factory for creating and initializing agent applications.

    Attributes:
        agent_id: Unique identifier for the agent
        agent_config: Agent configuration with runtime settings
        base_path: Base path for resolving module imports
        context: Application context with shared resources
        a2a_app: Agent-to-agent communication application
        starlette_app: Main Starlette web application
        agent: Initialized agent instance
        agent_manager: Manager for agent lifecycle operations
    """

    def __init__(
            self,
            agent_id: str,
            agent_config: AgentConfig,
            context: AppContext,
            base_path: Optional[Path] = None
    ):
        """Initialize factory with agent configuration."""
        self.agent_id = agent_id
        self.agent_config = agent_config
        self.base_path = base_path
        self.context = context

        # Application components
        self.a2a_app: Optional[AionA2AStarletteApplication] = None
        self.starlette_app: Optional[Starlette] = None
        self.agent: Optional[BaseAgent] = None
        self.agent_manager: Optional[DirectAgentManager] = None

    async def initialize(self):
        """Initialize the application factory."""
        try:
            await self._initialize()
            return self
        except Exception as exc:
            logger.error("Failed to initialize application factory", exc_info=exc)
            await self.shutdown()
            return None

    async def _initialize(self) -> None:
        """Initialize all application components."""
        logger.debug("Initializing application for agent '%s'", self.agent_id)

        # Initialize database
        await self._init_db()

        # Create agent
        await self._init_agent()

        # Create A2A application
        await self._create_a2a_app()

        # Build Starlette application
        await self._build_starlette_app()

        logger.info("Agent '%s' initialized successfully at http://%s:%s",
                    self.agent_id, self.get_agent_host(), self.agent_config.port)

    async def _init_agent(self) -> None:
        """Initialize agent from configuration."""
        self.agent_manager = AgentManager(base_path=self.base_path)

        # Create and register agent
        self.agent = self.agent_manager.create_agent(self.agent_id, self.agent_config)

        # Pre-compile the graph
        if not self.agent_manager.precompile_agent():
            raise RuntimeError(f"Failed to pre-compile agent '{self.agent_id}'")

        logger.debug("Agent '%s' (%s) initialized with compiled graph",
                     self.agent_id, self.agent.__class__.__name__)

    async def _create_a2a_app(self) -> None:
        """Create and configure the A2A application."""
        if self.a2a_app:
            raise RuntimeError("A2A application is already created")

        if not self.agent:
            raise RuntimeError("Agent must be initialized before creating A2A app")

        # Create agent card
        agent_card = self._create_agent_card()

        # Create request handler
        request_handler = await self._create_request_handler()

        self.a2a_app = AionA2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler
        )

    def _create_agent_card(self) -> AgentCard:
        """Create agent card from configuration."""
        if not self.agent:
            raise RuntimeError("No agent available to create agent card")

        # Create base URL from config
        base_url = f'http://0.0.0.0:{self.agent_config.port}'

        return self.agent.card

    async def _create_request_handler(self) -> AionRequestHandler:
        """Create and configure the request handler."""
        # Initialize task store
        self.context.store_manager.initialize()
        task_store = self.context.store_manager.get_store()

        return AionRequestHandler(
            agent_executor=LanggraphAgentExecutor(self.agent.get_compiled_graph()),
            task_store=task_store,
            push_config_store=InMemoryPushNotificationConfigStore()
        )

    async def _build_starlette_app(self) -> None:
        """Build the Starlette application."""
        if not self.a2a_app:
            raise RuntimeError("A2A application must be created before building Starlette app")

        lifespan = AppLifespan(app_factory=self)
        self.starlette_app = self.a2a_app.build(lifespan=lifespan.executor)
        self.starlette_app.add_middleware(TracingMiddleware)
        self.starlette_app.add_middleware(AionContextMiddleware)

    async def _init_db(self) -> None:
        """Initialize database connection and run migrations."""
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

    async def shutdown(self) -> None:
        """Shutdown the application and cleanup resources."""
        logger.info("Shutting down application factory")
        await self._cleanup_db()

    async def _cleanup_db(self) -> None:
        """Cleanup database connections."""
        if not self.context.db_manager.is_initialized:
            return

        try:
            await self.context.db_manager.close()
            logger.info("Database connections closed")
        except Exception as exc:
            logger.error("Error closing database", exc_info=exc)

    # Properties for accessing application components

    @property
    def is_initialized(self) -> bool:
        """Check if the factory is fully initialized."""
        return (
                self.agent is not None and
                self.a2a_app is not None and
                self.starlette_app is not None
        )

    def get_agent(self) -> Optional[BaseAgent]:
        """Get the initialized agent instance."""
        return self.agent

    def get_starlette_app(self) -> Optional[Starlette]:
        """Get the Starlette application instance."""
        return self.starlette_app

    def get_agent_config(self) -> AgentConfig:
        """Get the agent configuration."""
        return self.agent_config

    def get_agent_host(self) -> str:
        """Get the agent host address."""
        return "0.0.0.0"
