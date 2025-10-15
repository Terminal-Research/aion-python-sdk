"""
AION Agent Proxy Server
Simple proxy server that routes requests to different AION agents based on agent_id
"""
from contextlib import asynccontextmanager
from typing import Dict, Optional
import logging

import uvicorn
from aion.shared.aion_config import AionConfig
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from fastapi import FastAPI
from aion.shared.settings import app_settings

from .client import ProxyHttpClient
from .handlers import RequestHandler
from .routes import setup_routes

# Set custom logger class globally for all loggers including uvicorn/fastapi
logging.setLoggerClass(AionLogger)

logger = get_logger()


class AionAgentProxyServer:
    """
    Simple proxy server that routes requests to different AION agents
    based on agent_id in the URL path
    """

    def __init__(self, config: AionConfig, startup_callback=None):
        """
        Initialize proxy server with AION configuration

        Args:
            config: AionConfig instance containing agent configurations
            startup_callback: Optional callback to call after server lifespan startup completes
        """
        self.config = config
        self.agent_urls: Dict[str, str] = {}
        self.http_client_manager = ProxyHttpClient()
        self.request_handler: Optional[RequestHandler] = None
        self.startup_callback = startup_callback

        self._build_agent_urls()

        self.app = FastAPI(
            title="AION Agent Proxy Server",
            version="1.0.0",
            lifespan=self._lifespan
        )

    def _build_agent_urls(self):
        """Build agent URL mappings from configuration using hardcoded host 0.0.0.0 and http scheme"""
        for agent_id, agent_config in self.config.agents.items():
            # Build agent URL
            host = "0.0.0.0"
            port = agent_config.port
            scheme = "http"

            agent_url = f"{scheme}://{host}:{port}"
            self.agent_urls[agent_id] = agent_url
            logger.debug(f"Mapped agent '{agent_id}' to {agent_url}")

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
            setup_routes(self.app, self.request_handler)

            # Call startup callback if provided - server is now ready
            if self.startup_callback is not None:
                self.startup_callback()

            yield

        # Shutdown handled by context manager

    async def start(self, host: str = "0.0.0.0", port: Optional[int] = None):
        """
        Start the proxy server

        Args:
            host: Host to bind the server to (default: "0.0.0.0")
            port: Port to bind the server to (default: config.proxy.port)
        """
        if port is None:
            port = self.config.proxy.port

        config = uvicorn.Config(
            app=self.app,
            host=host,
            port=port,
            log_level=app_settings.log_level.lower(),
            log_config=None,
            access_log=False
        )

        server = uvicorn.Server(config)
        logger.info(f"Starting AION Proxy Server on http://{host}:{port}")
        await server.serve()
