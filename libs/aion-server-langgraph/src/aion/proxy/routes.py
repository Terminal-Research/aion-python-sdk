"""
Route definitions for AION Agent Proxy Server
"""
from aion.shared.types import HealthResponse, AgentsHealthResponse
from fastapi import FastAPI, Request, Response

from .handlers import RequestHandler


def setup_routes(app: FastAPI, request_handler: RequestHandler):
    """
    Configure routes for the proxy server

    Args:
        app: FastAPI application instance
        request_handler: Request handler instance for forwarding requests
    """

    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Health check",
        description="Check if the proxy server is running"
    )
    async def health_check() -> HealthResponse:
        """
        Health check endpoint

        Returns:
            Simple OK response with 200 status code
        """
        return HealthResponse()

    @app.get(
        "/health/agents",
        response_model=AgentsHealthResponse,
        summary="Agents health check",
        description="Check health status of all configured agents"
    )
    async def agents_health_check() -> AgentsHealthResponse:
        """
        Check health status of all configured agents

        Returns:
            Dictionary with status of proxy and all agents
        """
        result = await request_handler.check_agents_health()
        return AgentsHealthResponse(**result)

    @app.api_route(
        "/{agent_id}/{path:path}",
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
        return await request_handler.forward_request(agent_id, path, request)
