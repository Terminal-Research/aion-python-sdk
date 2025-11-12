"""
Route definitions for AION Agent Proxy Server
"""
from aion.shared.types import HealthResponse, A2AManifest
from fastapi import Request, Response

from .constants import (
    HEALTH_CHECK_URL,
    SYSTEM_HEALTH_CHECK_URL,
    MANIFEST_URL,
    AGENT_PROXY_URL,
)
from .handlers import RequestHandler
from .types import SystemHealthResponse
from .utils import generate_a2a_manifest


class ProxyRouter:
    """
    Router class for AION Agent Proxy Server

    Manages all HTTP routes and their handlers for the proxy server.
    """

    def __init__(self, agent_proxy_server, request_handler: RequestHandler):
        """
        Initialize the router and register all routes

        Args:
            agent_proxy_server: AION Agent Proxy Server instance
            request_handler: Request handler instance for forwarding requests
        """
        self.ap_server = agent_proxy_server
        self.app = self.ap_server.app
        self.request_handler = request_handler

    def register_routes(self) -> None:
        """Register all routes with the FastAPI application"""
        self._register_health_check()
        self._register_system_health_check()
        self._register_manifest()
        self._register_proxy()

    def _register_health_check(self) -> None:
        """Register health check endpoint"""

        @self.app.get(
            HEALTH_CHECK_URL,
            response_model=HealthResponse,
            summary="Health check",
            description="Check if the proxy server is running"
        )
        async def health_check() -> HealthResponse:
            """
            Health check endpoint

            Returns:
                Simple status response with 200 status code
            """
            return HealthResponse()

    def _register_system_health_check(self) -> None:
        """Register system health check endpoint"""

        @self.app.get(
            SYSTEM_HEALTH_CHECK_URL,
            response_model=SystemHealthResponse,
            summary="Agents health check",
            description="Check health status of all configured agents"
        )
        async def agents_health_check() -> SystemHealthResponse:
            """
            Check health status of all configured agents

            Returns:
                Dictionary with status of proxy and all agents
            """
            result = await self.request_handler.check_agents_health()
            return SystemHealthResponse(**result)

    def _register_manifest(self) -> None:
        """Register manifest endpoint"""

        @self.app.get(
            MANIFEST_URL,
            response_model=A2AManifest,
            summary="Manifest",
            description="Get a manifest from the deployment"
        )
        async def get_manifest() -> A2AManifest:
            """
            Get deployment manifest with service information and agent endpoints

            Returns:
                RootManifest containing API version, service name, and agent endpoints
            """
            return generate_a2a_manifest(agent_ids=list(self.ap_server.agent_urls.keys()))

    def _register_proxy(self) -> None:
        """Register proxy endpoint for forwarding requests to agents"""

        @self.app.api_route(
            AGENT_PROXY_URL,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
        )
        async def proxy_request(agent_id: str, path: str, request: Request) -> Response:
            """
            Proxy requests to the appropriate agent server

            Args:
                agent_id: The target agent identifier
                path: The remaining path to forward to the agent
                request: The incoming FastAPI request

            Returns:
                Response from the target agent server
            """
            return await self.request_handler.forward_request(agent_id, path, request)
