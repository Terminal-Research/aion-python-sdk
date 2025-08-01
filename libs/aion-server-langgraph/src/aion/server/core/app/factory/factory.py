import logging
from typing import Optional

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import AgentCard
from starlette.applications import Starlette

from aion.server.db import db_manager, verify_connection
from aion.server.db.migrations import upgrade_to_head
from aion.server.langgraph.a2a import LanggraphAgentExecutor
from aion.server.langgraph.agent import BaseAgent, agent_manager
from aion.server.tasks import store_manager
from aion.server.configs import db_settings
from .configs import AppConfig
from .lifespan import AppLifespan
from .a2a_starlette_application import AionA2AStarletteApplication
from aion.server.core.request_handlers import AionRequestHandler

logger = logging.getLogger(__name__)


class AppFactory:
    """Factory for creating and configuring the application."""

    config: AppConfig
    a2a_app: Optional[AionA2AStarletteApplication]
    starlette_app: Optional[Starlette]
    agent: Optional[BaseAgent]

    def __init__(self, config: AppConfig):
        if config and not isinstance(config, AppConfig):
            raise TypeError("config must be an instance of AppConfig")

        self.config = config if config else AppConfig()
        self.a2a_app = None
        self.starlette_app = None
        self.agent = None

    @classmethod
    async def initialize(cls, config: AppConfig) -> Optional["AppFactory"]:
        """Initialize and create an AppFactory instance.

        Args:
            config: Application configuration.

        Returns:
            Initialized AppFactory instance or None if initialization failed.
        """
        instance = cls(config)
        try:
            await instance.create_a2a_app()
            await instance.build_starlette_app()
        except Exception as exc:
            logger.error("Failed to build server", exc_info=exc)
            await instance.shutdown()
            return

        return instance

    async def create_a2a_app(self) -> "AionA2AStarletteApplication":
        """Create and configure the A2A application.

        Returns:
            Configured A2AStarletteApplication instance.

        Raises:
            RuntimeError: If app is already created.
        """
        if self.a2a_app:
            raise RuntimeError("This app is already created")

        await self._init_db()
        await self._init_agents()

        agent_card = self._create_agent_card()
        request_handler = await self._create_request_handler()

        self.a2a_app = AionA2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler)
        return self.a2a_app

    def _create_agent_card(self) -> AgentCard:
        """Create agent card from the loaded agent.

        Returns:
            Agent card with capabilities description.

        Raises:
            RuntimeError: If no agent is available.
        """
        base_url = f'http://{self.config.host}:{self.config.port}/'

        if self.agent:
            logger.info("Getting agent card from agent instance")
            return self.agent.get_agent_card(base_url)

        raise RuntimeError("No agent available to create agent card")

    async def _create_request_handler(self) -> "AionRequestHandler":
        """Create and configure the request handler.

        Returns:
            Configured DefaultRequestHandler instance.
        """
        store_manager.initialize()

        return AionRequestHandler(
            agent_executor=LanggraphAgentExecutor(self.agent.get_compiled_graph()),
            task_store=store_manager.get_store(),
            push_config_store=InMemoryPushNotificationConfigStore())

    async def _init_agents(self):
        """Initialize agents from configuration."""
        agent_manager.initialize_agents()

        if not agent_manager.has_active_agents():
            raise RuntimeError("Agent manager has no agents after initialization")

        # Get the first available agent
        self.agent = agent_manager.get_first_agent()
        if not self.agent:
            raise RuntimeError("No agent available after initialization")

        agent_id = next(iter(agent_manager.agents.keys()))
        logger.info("Using agent '%s' (%s) with compiled graph",
                    agent_id, self.agent.__class__.__name__)

    async def _init_db(self):
        """Initialize database connection and run migrations."""
        pg_url = db_settings.pg_url
        if not pg_url:
            logger.info("POSTGRES_URL environment variable not set, using in-memory data store")
            return

        is_connection_verified = await verify_connection(pg_url)
        if not is_connection_verified:
            logger.warning("Can't verify postgres connection")
            return

        try:
            await db_manager.initialize(pg_url)
        except Exception as exc:
            logger.error("Failed to initialize database", exc_info=exc)
            await self._cleanup_db()
            return

        try:
            await upgrade_to_head()
        except Exception as exc:
            logger.error(f"Migration failed: {exc}", exc_info=True)
            await self._cleanup_db()
            return

    async def build_starlette_app(self) -> Starlette:
        """Build the Starlette application.

        Returns:
            Configured Starlette application instance.
        """
        lifespan = AppLifespan(app_factory=self)
        self.starlette_app = self.a2a_app.build(lifespan=lifespan.executor)
        return self.starlette_app

    async def shutdown(self):
        """Shutdown the application and cleanup resources."""
        await self._cleanup_db()

    async def _cleanup_db(self):
        """Cleanup database connections."""
        if not db_manager.is_initialized:
            return

        try:
            await db_manager.close()
        except Exception as exc:
            logger.error("Error closing database", exc_info=exc)
