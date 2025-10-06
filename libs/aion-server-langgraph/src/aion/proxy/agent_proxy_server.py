from contextlib import asynccontextmanager
from typing import Dict, Optional
from urllib.parse import urljoin
import logging

import httpx
import uvicorn
from aion.shared.aion_config import AionConfig
from aion.shared.logging import get_logger
from aion.shared.logging.base import AionLogger
from fastapi import FastAPI, Request, HTTPException, Response
from aion.shared.settings import app_settings

# Set custom logger class globally for all loggers including uvicorn/fastapi
logging.setLoggerClass(AionLogger)

logger = get_logger()


class AionAgentProxyServer:
    """
    Simple proxy server that routes requests to different AION agents
    based on agent_id in the URL path
    """

    def __init__(self, config: AionConfig):
        """
        Initialize proxy server with AION configuration

        Args:
            config: AionConfig instance containing agent configurations
        """
        self.config = config
        self.agent_urls: Dict[str, str] = {}
        self.http_client: Optional[httpx.AsyncClient] = None
        self._build_agent_urls()
        self.app = FastAPI(
            title="AION Agent Proxy Server",
            version="1.0.0",
            lifespan=self._lifespan
        )
        self._setup_routes()

    def _build_agent_urls(self):
        """Build agent URL mappings from configuration"""
        for agent_id, agent_config in self.config.agents.items():
            # Build agent URL
            host = "0.0.0.0"  # Default host
            port = agent_config.port
            scheme = "http"  # Default scheme

            agent_url = f"{scheme}://{host}:{port}"
            self.agent_urls[agent_id] = agent_url
            logger.info(f"Mapped agent '{agent_id}' to {agent_url}")

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        """
        Lifespan event handler for startup and shutdown
        """
        # Startup
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True
        )
        logger.info("HTTP client initialized")

        yield

        # Shutdown
        if self.http_client:
            await self.http_client.aclose()
            logger.info("HTTP client closed")

    def _setup_routes(self):
        @self.app.api_route(
            "/{agent_id}/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
        async def proxy_request(agent_id: str, path: str, request: Request):
            """
            Proxy requests to the appropriate agent server

            Args:
                agent_id: The target agent identifier
                path: The remaining path to forward to the agent
                request: The incoming FastAPI request

            Returns:
                Response from the target agent server
            """
            return await self._forward_request(agent_id, path, request)

    async def _forward_request(self, agent_id: str, path: str, request: Request) -> Response:
        """
        Forward the incoming request to the target agent

        Args:
            agent_id: Target agent identifier
            path: Path to forward to the agent
            request: Incoming FastAPI request

        Returns:
            Response from the target agent
        """
        # Check if agent exists
        if agent_id not in self.agent_urls:
            available_agents = list(self.agent_urls.keys())
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"Agent '{agent_id}' not found",
                    "available_agents": available_agents
                }
            )

        # Build target URL
        agent_base_url = self.agent_urls[agent_id]
        target_url = urljoin(f"{agent_base_url}/", path)

        # Add query parameters if present
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        try:
            # Prepare headers (exclude host header to avoid conflicts)
            headers = dict(request.headers)
            headers.pop('host', None)

            # Read request body
            body = await request.body()

            # Forward the request
            response = await self.http_client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body
            )

            # Prepare response headers
            response_headers = dict(response.headers)
            # Remove headers that might cause issues
            response_headers.pop('content-encoding', None)
            response_headers.pop('transfer-encoding', None)

            # Return response
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response_headers.get('content-type')
            )

        except httpx.ConnectError:
            logger.error(f"Failed to connect to agent '{agent_id}' at {agent_base_url}")
            raise HTTPException(
                status_code=503,
                detail=f"Agent '{agent_id}' is not available"
            )
        except httpx.TimeoutException:
            logger.error(f"Timeout when connecting to agent '{agent_id}'")
            raise HTTPException(
                status_code=504,
                detail=f"Timeout when connecting to agent '{agent_id}'"
            )
        except Exception as e:
            logger.error(f"Error forwarding request to agent '{agent_id}': {str(e)}")
            raise HTTPException(
                status_code=502,
                detail=f"Error forwarding request to agent '{agent_id}'"
            )

    async def start(self, host: str = "0.0.0.0", port: Optional[int] = None):
        """
        Start the proxy server

        Args:
            host: Host to bind the server to
            port: Port to bind the server to (defaults to config.proxy.port)
        """
        if port is None:
            port = self.config.proxy.port

        config = uvicorn.Config(
            app=self.app,
            host=host,
            port=port,
            log_level=app_settings.log_level.lower(),
            log_config=None
        )

        server = uvicorn.Server(config)
        logger.info(f"Starting AION Proxy Server on http://{host}:{port}")
        await server.serve()
