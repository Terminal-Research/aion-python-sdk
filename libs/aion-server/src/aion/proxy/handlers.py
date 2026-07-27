"""
Request handlers for AION Agent Proxy Server
"""
import logging
from typing import Dict, Any
from urllib.parse import urljoin

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from .exceptions import (
    AgentNotFoundException,
    AgentUnavailableException,
    AgentTimeoutException,
    AgentProxyException
)
from .types import AgentHealthInfo

logger = logging.getLogger(__name__)

# Upstream timeout for forwarded requests: connect/write/pool stay bounded,
# but read is unbounded - streamed agent responses (SSE task updates) may
# legitimately stay silent between chunks longer than any fixed limit.
_FORWARD_TIMEOUT = httpx.Timeout(30.0, read=None)

# Hop-by-hop framing headers that must not be copied from the upstream
# response: the proxied response is re-framed (chunked) by the server.
_EXCLUDED_RESPONSE_HEADERS = frozenset({"content-length", "transfer-encoding", "connection"})

# Headers that describe the *client's* connection to the proxy and say nothing
# about the proxy's own connection upstream. `host` would address the wrong
# server; the framing headers (RFC 9110 §7.6.1) describe a hop that ends here —
# forwarding `content-length` or `transfer-encoding` alongside a body httpx
# re-frames itself is how a request ends up declaring two different lengths.
_EXCLUDED_REQUEST_HEADERS = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "upgrade",
})


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
        Forward the incoming request to the target agent, streaming the
        response body through as it arrives.

        The upstream response is not buffered: each chunk is relayed to the
        client as soon as the agent produces it, so streaming transports
        (e.g. SSE task updates from SendStreamingMessage) deliver
        intermediate events in real time instead of one batch at completion.

        Args:
            agent_id: Target agent identifier
            path: Path to forward to the agent
            request: Incoming FastAPI request

        Returns:
            Streaming response relaying the target agent's response

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
            # Prepare headers: everything the client sent except what described
            # its own hop to us (see _EXCLUDED_REQUEST_HEADERS). httpx frames the
            # upstream request itself from `content`.
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in _EXCLUDED_REQUEST_HEADERS
            }

            # Read request body
            body = await request.body()

            # Forward the request; stream=True returns as soon as the
            # upstream response headers arrive, without reading the body
            upstream_request = self.http_client.build_request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                timeout=_FORWARD_TIMEOUT,
            )
            upstream = await self.http_client.send(upstream_request, stream=True)

        except httpx.ConnectError:
            logger.error(f"Failed to connect to agent '{agent_id}' at {agent_base_url}")
            raise AgentUnavailableException(agent_id)

        except httpx.TimeoutException:
            logger.error(f"Timeout when connecting to agent '{agent_id}'")
            raise AgentTimeoutException(agent_id)

        except Exception as e:
            logger.error(f"Error forwarding request to agent '{agent_id}': {str(e)}")
            raise AgentProxyException(agent_id, str(e))

        # Relay body bytes exactly as sent (aiter_raw skips httpx's
        # content decoding), so upstream headers - including any
        # content-encoding - stay valid; only framing headers are dropped
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in _EXCLUDED_RESPONSE_HEADERS
        }

        async def relay_upstream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except httpx.HTTPError as e:
                # Response headers are already sent - the error can only be
                # logged; closing the generator aborts the client connection
                logger.error(
                    f"Error streaming response from agent '{agent_id}': {str(e)}"
                )
            finally:
                await upstream.aclose()

        return StreamingResponse(
            relay_upstream(),
            status_code=upstream.status_code,
            headers=response_headers,
        )
