"""
AION Agent Proxy Server
Simple proxy server that routes requests to different AION agents based on agent_id
"""
import aion.core.logging.base  # noqa: F401
import logging
import uvicorn
from aion.server.opentelemetry.startup import StartupSpan, startup_phase
from aion.server.startup import StartupServer
from aion.server.settings import app_settings
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI
from typing import Dict, Optional, Callable

from .client import ProxyHttpClient
from .handlers import RequestHandler
from .middlewares import ProxySwaggerUIFixMiddleware, ProxyLoggingMiddleware
from .routes import ProxyRouter

logger = logging.getLogger(__name__)


class AionAgentProxyServer:
    """
    Simple proxy server that routes requests to different AION agents
    based on agent_id in the URL path
    """

    def __init__(self, agents: Dict[str, str], startup_callback: Optional[Callable] = None,
                 startup_span: StartupSpan | None = None):
        """
        Initialize proxy server with agent mappings

        Args:
            agents: Dictionary mapping agent_id to agent_url (e.g., {"my-agent": "http://0.0.0.0:8001"})
            startup_callback: Notify the launcher after sockets are listening.
            startup_span: Startup attempt owned by the launching process.
        """
        self.agent_urls = agents
        self.http_client_manager = ProxyHttpClient()
        self.request_handler: Optional[RequestHandler] = None
        self.startup_callback = startup_callback
        self.startup_span = startup_span or StartupSpan("proxy.startup")

        # Log agent mappings
        for agent_id, agent_url in self.agent_urls.items():
            logger.debug(f"Mapped agent '{agent_id}' to {agent_url}")

        self.app = FastAPI(
            title="AION Agent Proxy Server",
            version="1.0.0",
            lifespan=self._lifespan
        )
        self.add_middlewares()

    def add_middlewares(self):
        """Add middlewares to app"""
        self.app.add_middleware(ProxySwaggerUIFixMiddleware)
        self.app.add_middleware(ProxyLoggingMiddleware)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        """
        Lifespan event handler for startup and shutdown.
        Initializes the HTTP client, request handler, and routes. The server
        acknowledges readiness separately, after binding its sockets.
        """
        # Startup
        async with AsyncExitStack() as resources:
            with startup_phase("proxy.initialize", self.startup_span.context):
                http_client = await resources.enter_async_context(self.http_client_manager.lifespan())
                # Initialize request handler with HTTP client
                self.request_handler = RequestHandler(self.agent_urls, http_client)

                # Setup routes
                ProxyRouter(agent_proxy_server=self, request_handler=self.request_handler).register_routes()

            yield

        # Shutdown handled by context manager

    async def start(self, port: int, host: str = "0.0.0.0", serialized_socket=None):
        """
        Start the proxy server

        Args:
            port: Port to bind the server to
            host: Host to bind the server to (default: "0.0.0.0")
            serialized_socket: Optional serialized socket from parent process
        """
        # Deserialize socket if provided
        sockets = None
        if serialized_socket is not None:
            from aion.server.utils.ports.reservation import deserialize_socket
            sock = deserialize_socket(serialized_socket)
            sockets = [sock]
            logger.debug(f"Using passed socket for proxy on port {port}")

        config = uvicorn.Config(
            app=self.app,
            host=host if sockets is None else None,
            port=port if sockets is None else None,
            log_level=app_settings.log_level.lower(),
            log_config=None,
            access_log=False
        )

        server = StartupServer(
            config, self.startup_span, "Proxy", logger, self.startup_callback
        )
        if sockets is not None:
            server.servers = []  # Clear default servers

        with self.startup_span.activate():
            logger.info(f"Starting AION Proxy Server on http://{host}:{port}")
        await server.serve(sockets=sockets)
