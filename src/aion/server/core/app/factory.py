"""Application factory for orchestrating initialization of all components.

This module provides AppFactory which coordinates the initialization of
database, plugins, agent, and FastAPI application using dependency injection.
"""
import logging

from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes
from a2a.utils.constants import DEFAULT_RPC_URL
from aion.db.postgres import DbFactory
from aion.server.agent.aion_agent import AionAgent
from aion.server.auth import AuthConfigurationError, TokenVerifier, build_token_verifier
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage.manager import FileUploadManager
from fastapi import FastAPI
from starlette.routing import BaseRoute, Route
from typing import Optional

from aion.server.agent.execution import AionAgentRequestExecutor, AionRequestContextBuilder
from aion.server.agent.factory import AgentFactory
from aion.server.core.app.api import AionExtraHTTPRoutes, ContextHTTPRoutes
from aion.server.core.app.handlers import AionJsonRpcDispatcher, AionRequestHandler
from aion.server.core.app.handlers.request_preprocessors import A2ARequestPreprocessor, FilePartPreprocessor
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware, TracingMiddleware
from aion.server.plugins import PluginFactory
from aion.server.tasks import StoreManager, PushNotificationFactory
from aion.server.tasks.push_notifications import push_url_validator
from .lifespan import AppLifespan
from .registry import app_registry

logger = logging.getLogger(__name__)


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
            token_verifier: Optional[TokenVerifier] = None,
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
            token_verifier: Optional pre-built verifier for request tokens. If
                None, ``aion.server.auth.build_token_verifier`` builds the one
                for the mode the server runs in at initialization.
        """
        self.aion_agent = aion_agent
        self.db_factory = db_factory
        self.agent_factory = agent_factory
        self.plugin_factory = plugin_factory
        self.store_manager = store_manager
        self.startup_callback = startup_callback
        self.token_verifier = token_verifier
        self.upload_manager = upload_manager or FileUploadManager.from_settings()
        self.file_transformer = A2AFileTransformer(self.upload_manager)

        self.fastapi_app: Optional[FastAPI] = None
        self._executor: Optional[AionAgentRequestExecutor] = None
        self._request_handler: Optional[AionRequestHandler] = None
        self._push_sender = None
        # The AppRegistry routes, filled once they are mounted; the
        # authentication middleware leaves them to the application.
        self._application_routes: list[BaseRoute] = []

    async def initialize(self):
        """Initialize the application factory.

        Returns:
            Self if initialization successful, None if initialization failed

        Raises:
            AuthConfigurationError: The authentication settings are wrong; the
                server must not start, rather than start unprotected.
        """
        try:
            await self._initialize()
            return self
        except AuthConfigurationError:
            await self.shutdown()
            raise
        except Exception as exc:
            logger.error("Failed to initialize application factory", exc_info=exc)
            await self.shutdown()
            return None

    async def _initialize(self) -> None:
        """Initialize all application components in sequence."""
        logger.debug("Initializing application for agent '%s'", self.aion_agent.id)

        # 0. Get the keys request tokens are verified with ready
        if self.token_verifier is None:
            self.token_verifier = build_token_verifier()
        await self.token_verifier.load()

        # 1. Initialize database
        await self.db_factory.initialize()

        # 2. Build FastAPI application
        await self._build_app()

        # 3. Initialize plugins - Phase 1: infrastructure setup
        await self.plugin_factory.initialize()

        # 4. Build agent
        await self.agent_factory.build()

        # 5. Configure app - Phase 2: integrate plugins with built app and agent
        await self.plugin_factory.configure_app(self.fastapi_app, self.aion_agent)

        # 6. Apply custom app extensions from AppRegistry
        self._application_routes.extend(app_registry.apply_to_app(self.fastapi_app))

        logger.info("Agent '%s' initialized at http://%s:%s",
                    self.aion_agent.id, self.aion_agent.host, self.aion_agent.port)

    async def _build_app(self) -> None:
        """Build FastAPI application with all necessary configuration."""
        if self.fastapi_app:
            raise RuntimeError("Application is already created")

        request_handler = await self._create_request_handler()
        self._request_handler = request_handler

        jsonrpc_dispatcher = AionJsonRpcDispatcher(
            request_handler=request_handler,
            enable_v0_3_compat=True,
        )
        lifespan = AppLifespan(app_factory=self)
        # No /docs or /redoc: an agent's server is not browsed. The OpenAPI
        # schema stays, behind the same bearer token as everything but the
        # public paths.
        self.fastapi_app = FastAPI(lifespan=lifespan.executor, docs_url=None, redoc_url=None)

        # Registered through the SDK helper rather than FastAPI(routes=...) so
        # the A2A endpoints are re-wrapped as APIRoute and appear in the OpenAPI
        # schema with proto-derived request schemas. The routes themselves are
        # unchanged — JSON-RPC still dispatches through AionJsonRpcDispatcher.
        add_a2a_routes_to_fastapi(
            self.fastapi_app,
            agent_card_routes=create_agent_card_routes(self.aion_agent.card),
            jsonrpc_routes=[
                Route(DEFAULT_RPC_URL, endpoint=jsonrpc_dispatcher.handle_requests, methods=['POST']),
            ],
        )
        AionExtraHTTPRoutes(self.aion_agent).register(self.fastapi_app)
        # The Context extension's HTTP+JSON binding, beside the JSON-RPC
        # endpoint its paths are relative to.
        ContextHTTPRoutes(request_handler, base_url=DEFAULT_RPC_URL).register(self.fastapi_app)
        self._add_extra_middlewares()

    async def _create_request_handler(self) -> AionRequestHandler:
        """Create and configure the request handler with task store and agent executor."""
        # The guard follows the manager that is actually installed - which may
        # have been injected without FILE_STORAGE_BACKEND set - rather than
        # the setting, so the two cannot disagree.
        self.store_manager.initialize(
            agent_id=self.aion_agent.id,
            guard_inline_files=self.upload_manager is not None,
            owner_resolver=self.aion_agent.owner_resolver,
        )
        task_store = self.store_manager.get_store()
        # The tasks and the agent's framework state have to name the same
        # owner for every request. A store initialized earlier, with another
        # resolver, would split them silently; stopping here is the only
        # safe answer.
        if task_store.owner_resolver is not self.aion_agent.owner_resolver:
            raise RuntimeError(
                "the task store and the agent resolve owners differently; build the "
                "store with the agent's owner_resolver"
            )

        self._executor = await AionAgentRequestExecutor.create(
            self.aion_agent,
            file_transformer=self.file_transformer,
        )

        # One push URL policy for the configs the handler accepts and the
        # deliveries the sender makes.
        url_validator = push_url_validator()
        push_config_store, push_sender = PushNotificationFactory.create(
            self.db_factory.db_manager,
            owner_resolver=self.aion_agent.owner_resolver,
            push_url_validator=url_validator,
        )
        self._push_sender = push_sender

        return AionRequestHandler(
            agent_executor=self._executor,
            task_store=self.store_manager.get_handler_store(),
            event_stream=self.store_manager.get_event_stream(),
            admission=self.store_manager.get_admission(),
            context_catalog=self.store_manager.get_context_catalog(),
            push_config_store=push_config_store,
            push_sender=push_sender,
            push_url_validator=url_validator,
            agent_card=self.aion_agent.card,
            request_context_builder=AionRequestContextBuilder(
                task_store=task_store,
                auto_discover_interrupted_task=True,
            ),
            preprocessors=[
                FilePartPreprocessor(self.file_transformer),
            ],
        )

    def _add_extra_middlewares(self):
        # The middleware added last runs first: the caller is named - and a
        # request without a valid token refused - before anything reads it, and
        # the execution scope is populated before the span reads it.
        self.fastapi_app.add_middleware(TracingMiddleware)
        self.fastapi_app.add_middleware(AionContextMiddleware)
        self.fastapi_app.add_middleware(
            AionAuthMiddleware, verifier=self.token_verifier, application_routes=self._application_routes
        )

    async def shutdown(self) -> None:
        """Shutdown the application and cleanup resources."""
        logger.info("Shutting down application factory")

        # Drain in-flight tasks before the resources they hold are torn down.
        # The SDK spawns a producer and a consumer asyncio.Task per active task;
        # without this they survive until event-loop shutdown and surface as
        # "Task was destroyed but it is pending!", still writing to a task store
        # and plugins that the steps below are about to close.
        if self._request_handler is not None:
            try:
                await self._request_handler.aclose()
                logger.info("Active tasks drained")
            except Exception as exc:
                logger.error("Error draining active tasks", exc_info=exc)

        if self._push_sender is not None:
            try:
                await self._push_sender.aclose()
                logger.info("Push notification client closed")
            except Exception as exc:
                logger.error("Error closing push notification client", exc_info=exc)

        if self.token_verifier is not None:
            try:
                await self.token_verifier.aclose()
            except Exception as exc:
                logger.error("Error closing the token verifier", exc_info=exc)

        if self.plugin_factory.is_initialized():
            try:
                await self.plugin_factory.teardown_all()
                logger.info("Plugins cleaned up")
            except Exception as exc:
                logger.error("Error cleaning up plugins", exc_info=exc)

        # One owner for the storage client, closed once and last among its
        # users: the manager is handed to the transformer and to plugins
        # alike, and a plugin is free to store something while tearing down.
        # Closing from any of them would close it out from under the others.
        if self.upload_manager is not None:
            try:
                await self.upload_manager.aclose()
                logger.info("File upload manager closed")
            except Exception as exc:
                logger.error("Error closing the file upload manager", exc_info=exc)

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
