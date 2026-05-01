"""Application factory for orchestrating initialization of all components.

This module provides AppFactory which coordinates the initialization of
database, plugins, agent, and FastAPI application using dependency injection.
"""

from a2a.server.routes import create_agent_card_routes
from a2a.utils.constants import DEFAULT_RPC_URL
from aion.db.postgres import DbFactory
from aion.shared.agent import AionAgent
from aion.shared.files.a2a import A2AFileTransformer
from aion.shared.files.storage.manager import FileUploadManager
from aion.shared.logging import get_logger
from fastapi import FastAPI
from starlette.routing import Route
from typing import Optional

from aion.server.agent import AionAgentRequestExecutor, AgentFactory
from aion.server.core.app.api import AionExtraHTTPRoutes
from aion.server.core.app.handlers import AionJsonRpcDispatcher, AionRequestHandler
from aion.server.core.app.handlers.request_preprocessors import A2ARequestPreprocessor, FilePartPreprocessor
from aion.server.core.middlewares import TracingMiddleware, AionContextMiddleware
from aion.server.plugins import PluginFactory
from aion.server.tasks import StoreManager, PushNotificationFactory
from .lifespan import AppLifespan
from .registry import app_registry
from ...agent.agent_execution.request_context_builder import AionRequestContextBuilder

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
            upload_manager: Optional[FileUploadManager] = None,
            startup_callback=None,
    ):
        """Initialize factory with all dependencies via dependency injection.

        Args:
            aion_agent: Agent instance to build and run
            db_factory: Factory for database initialization
            agent_factory: Factory for agent building
            plugin_factory: Factory for plugin lifecycle management (already has db_manager)
            store_manager: Task store manager
            upload_manager: Optional pre-built FileUploadManager. If None, one is
                created automatically from FILE_STORAGE_BACKEND env var. If the
                env var is not set, file uploading is disabled and inline parts pass
                through unchanged.
            startup_callback: Optional callback to call after initialization
        """
        self.aion_agent = aion_agent
        self.db_factory = db_factory
        self.agent_factory = agent_factory
        self.plugin_factory = plugin_factory
        self.store_manager = store_manager
        self.startup_callback = startup_callback
        self.upload_manager = upload_manager or FileUploadManager.from_settings()
        self.file_transformer = A2AFileTransformer(self.upload_manager)

        self.fastapi_app: Optional[FastAPI] = None
        self._executor: Optional[AionAgentRequestExecutor] = None

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

        # 2. Build FastAPI application
        await self._build_app()

        # 3. Initialize plugins - Phase 1: infrastructure setup
        await self.plugin_factory.initialize(file_upload_manager=self.upload_manager)

        # 4. Build agent
        await self.agent_factory.build()

        # 5. Configure app - Phase 2: integrate plugins with built app and agent
        await self.plugin_factory.configure_app(self.fastapi_app, self.aion_agent)

        # 6. Apply custom app extensions from AppRegistry
        app_registry.apply_to_app(self.fastapi_app)

        logger.info("Agent '%s' initialized at http://%s:%s",
                    self.aion_agent.id, self.aion_agent.host, self.aion_agent.port)

    async def _build_app(self) -> None:
        """Build FastAPI application with all necessary configuration."""
        if self.fastapi_app:
            raise RuntimeError("Application is already created")

        request_handler = await self._create_request_handler()

        jsonrpc_dispatcher = AionJsonRpcDispatcher(
            request_handler=request_handler,
            enable_v0_3_compat=True,
        )
        routes = [
            *create_agent_card_routes(self.aion_agent.card),
            Route(DEFAULT_RPC_URL, endpoint=jsonrpc_dispatcher.handle_requests, methods=['POST']),
        ]

        lifespan = AppLifespan(app_factory=self)
        self.fastapi_app = FastAPI(routes=routes, lifespan=lifespan.executor)
        AionExtraHTTPRoutes(self.aion_agent).register(self.fastapi_app)
        self._add_extra_middlewares()

    async def _create_request_handler(self) -> AionRequestHandler:
        """Create and configure the request handler with task store and agent executor."""
        self.store_manager.initialize()
        task_store = self.store_manager.get_store()

        self._executor = AionAgentRequestExecutor(
            self.aion_agent,
            file_transformer=self.file_transformer,
        )

        push_config_store, push_sender = PushNotificationFactory.create(self.db_factory.db_manager)

        return AionRequestHandler(
            agent_executor=self._executor,
            task_store=task_store,
            push_config_store=push_config_store,
            push_sender=push_sender,
            agent_card=self.aion_agent.card,
            request_context_builder=AionRequestContextBuilder(
                task_store=task_store,
                auto_discover_interrupted_task=True,
            ),
            preprocessors=[
                FilePartPreprocessor(self.file_transformer, wait_upload=True),
            ],
        )

    def _add_extra_middlewares(self):
        self.fastapi_app.add_middleware(TracingMiddleware)
        self.fastapi_app.add_middleware(AionContextMiddleware)

    async def shutdown(self) -> None:
        """Shutdown the application and cleanup resources."""
        logger.info("Shutting down application factory")

        if self._executor is not None:
            try:
                await self._executor.drain()
            except Exception as exc:
                logger.error("Error draining uploads", exc_info=exc)

        if self.plugin_factory.is_initialized():
            try:
                await self.plugin_factory.teardown_all()
                logger.info("Plugins cleaned up")
            except Exception as exc:
                logger.error("Error cleaning up plugins", exc_info=exc)

        if self.db_factory.is_initialized:
            try:
                await self.db_factory.cleanup()
                logger.info("Database cleaned up")
            except Exception as exc:
                logger.error("Error cleaning up database", exc_info=exc)

    @property
    def is_initialized(self) -> bool:
        """Check if the factory is fully initialized."""
        return self.fastapi_app is not None

    def get_fastapi_app(self) -> Optional[FastAPI]:
        """Get the FastAPI application instance."""
        return self.fastapi_app
