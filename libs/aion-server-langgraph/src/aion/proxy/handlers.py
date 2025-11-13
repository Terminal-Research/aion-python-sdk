"""
Request handlers for AION Agent Proxy Server
"""
from typing import Dict, Any
from urllib.parse import urljoin

import httpx
from aion.shared.logging import get_logger
from fastapi import Request, Response

from .exceptions import (
    AgentNotFoundException,
    AgentUnavailableException,
    AgentTimeoutException,
    AgentProxyException
)
from .types import AgentHealthInfo

logger = get_logger()


class RequestHandler:
    """Handles request forwarding to agent servers"""

    def __init__(self, agent_urls: Dict[str, str], http_client: httpx.AsyncClient):
        """
        Initialize request handler

        Args:
            agent_urls: Mapping of agent_id to agent base URLs
            http_client: HTTP client for making requests
        """
        self.agent_urls = agent_urls
        self.http_client = http_client

    async def check_agents_health(self) -> Dict[str, Any]:
        """
        Check health status of all configured agents

        Returns:
            Dictionary with status of each agent compatible with SystemHealthResponse
        """
        results = {}

        for agent_id, agent_url in self.agent_urls.items():
            try:
                # Try to connect to agent's health endpoint or root
                response = await self.http_client.get(
                    f"{agent_url}/health/",
                    timeout=5.0
                )
                results[agent_id] = AgentHealthInfo(
                    status="healthy" if response.status_code == 200 else "unhealthy",
                    url=agent_url,
                    status_code=response.status_code
                )
            except httpx.ConnectError:
                results[agent_id] = AgentHealthInfo(
                    status="unavailable",
                    url=agent_url,
                    error="connection_refused"
                )
            except httpx.TimeoutException:
                results[agent_id] = AgentHealthInfo(
                    status="timeout",
                    url=agent_url,
                    error="timeout"
                )
            except Exception as e:
                results[agent_id] = AgentHealthInfo(
                    status="error",
                    url=agent_url,
                    error=str(e)
                )

        # Overall status
        all_healthy = all(agent.status == "healthy" for agent in results.values())

        return {
            "proxy_status": "healthy",
            "overall_agents_status": "healthy" if all_healthy else "degraded",
            "agents": results
        }

    async def forward_request(self, agent_id: str, path: str, request: Request) -> Response:
        """
        Forward the incoming request to the target agent

        Args:
            agent_id: Target agent identifier
            path: Path to forward to the agent
            request: Incoming FastAPI request

        Returns:
            Response from the target agent

        Raises:
            AgentNotFoundException: When agent_id is not found
            AgentUnavailableException: When agent server is unreachable
            AgentTimeoutException: When agent server times out
            AgentProxyException: When there's an error forwarding the request
        """
        # Check if agent exists
        if agent_id not in self.agent_urls:
            available_agents = list(self.agent_urls.keys())
            raise AgentNotFoundException(agent_id, available_agents)

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
            raise AgentUnavailableException(agent_id)

        except httpx.TimeoutException:
            logger.error(f"Timeout when connecting to agent '{agent_id}'")
            raise AgentTimeoutException(agent_id)

        except Exception as e:
            logger.error(f"Error forwarding request to agent '{agent_id}': {str(e)}")
            raise AgentProxyException(agent_id, str(e))
