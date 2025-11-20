"""
AION Agent Proxy Server
Simple proxy server that routes requests to different AION agents based on agent_id
"""
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional, Callable

import uvicorn
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from aion.shared.settings import app_settings
from fastapi import FastAPI

from .client import ProxyHttpClient
from .handlers import RequestHandler
from .middleware import ProxyLoggingMiddleware
from .routes import ProxyRouter

# Set custom logger class globally for all loggers including uvicorn/fastapi
logging.setLoggerClass(AionLogger)

logger = get_logger()


class AionAgentProxyServer:
    """
    Simple proxy server that routes requests to different AION agents
    based on agent_id in the URL path
    """

    def __init__(self, agents: Dict[str, str], startup_callback: Optional[Callable] = None):
        """
        Initialize proxy server with agent mappings

        Args:
            agents: Dictionary mapping agent_id to agent_url (e.g., {"my-agent": "http://0.0.0.0:8001"})
            startup_callback: Optional callback to call after server lifespan startup completes
        """
        self.agent_urls = agents
        self.http_client_manager = ProxyHttpClient()
        self.request_handler: Optional[RequestHandler] = None
        self.startup_callback = startup_callback

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
        self.app.add_middleware(ProxyLoggingMiddleware)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        """
        Lifespan event handler for startup and shutdown.
        Initializes HTTP client, request handler, routes, and calls startup callback.
        """
        # Startup
        async with self.http_client_manager.lifespan() as http_client:
            # Initialize request handler with HTTP client
            self.request_handler = RequestHandler(self.agent_urls, http_client)

            # Setup routes
            ProxyRouter(agent_proxy_server=self, request_handler=self.request_handler).register_routes()

            # Call startup callback if provided - server is now ready
            if self.startup_callback is not None:
                self.startup_callback()

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
            from aion.shared.utils.ports.reservation import deserialize_socket
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

        server = uvicorn.Server(config)
        if sockets is not None:
            server.servers = []  # Clear default servers

        logger.info(f"Starting AION Proxy Server on http://{host}:{port}")
        await server.serve(sockets=sockets)
